from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.comparison import (ARMS, DEFAULT_LIMITS, build_comparison_report,
                                         context_snapshot, evaluate_snapshot, gate_report)
from recommendations.comparison_report import write_comparison
from recommendations.features import extract_genres, feature_tags
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.resolution import resolved_records
from recommendations.services import preference_contract
from recommendations.taxonomy import LEGACY_AVAILABLE_TAGS, LEGACY_ALIASES, canonical_tag
from recommendations.version_control import (GATE_NAMES, cache_namespace, clear_version_cache,
    rank_versions, read_artifact, release_descriptor, require_v2_approval, sign_artifact, verify_artifact)


def passing_sample():
    # Synthetic unit-test METRICS, never production journal ratings or evidence.
    category = {'topic_overlap_proxy_mean': .8, 'pairwise_genre_jaccard_mean': .4,
        'novel_genre_ratio_mean': .2, 'novel_direction_count': 2, 'associated_rows': 10,
        'evaluated_top_count': 10, 'evidence_error_count': 0, 'evidence_checked_rows': 10}
    run = {'stable_order_and_evidence': True, 'maximum_compute_ms': 30, 'maximum_sql_queries': 3,
           'runs': [{'candidate_pool': 400}]}
    arms = {arm: {'categories': {key: deepcopy(category) for key in ('all', 'familiar', 'explore')},
                  'performance': {key: deepcopy(run) for key in ('all', 'familiar', 'explore')}} for arm in ARMS}
    arms['current_v2']['categories']['all']['pairwise_genre_jaccard_mean'] = .3
    arms['current_v2']['categories']['familiar']['pairwise_genre_jaccard_mean'] = .5
    holdout = {arm: {key: {'hit_at_k': .5, 'mrr_at_k': .3, 'binary_ndcg_at_k': .4}
                     for key in ('all', 'familiar', 'explore')} for arm in ('current_v1', 'current_v2')}
    return {'arms': arms, 'passes': {'ranking': {'holdout': holdout}}, 'safety_errors': 0,
            'holdout_evidence_errors': 0, 'snapshot_isolation': True}


class ComparisonPrimitiveTests(SimpleTestCase):
    def test_frozen_old_rules_exactly_match_pre_5b1_vocab_and_never_mutate_active_rules(self):
        self.assertEqual(len(LEGACY_AVAILABLE_TAGS), 26)
        self.assertEqual(len(LEGACY_ALIASES), 10)
        for label in ('MUSIC', '治癒', '热血', '魔法少女'):
            self.assertIsNone(canonical_tag(label, taxonomy_version='genres.v2'))
            self.assertIsNotNone(canonical_tag(label))
        self.assertEqual(feature_tags('真百,搞笑', taxonomy_version='genres.v2'), {'百合', '喜剧'})
        raw = ['MUSIC', '音乐', '二刷', '未知', '热血']
        before = deepcopy(raw)
        self.assertEqual(extract_genres(raw, taxonomy_version='genres.v2')['tags'], {'音乐'})
        self.assertEqual(extract_genres(raw, taxonomy_version='genres.v4')['tags'], {'音乐', '热血'})
        self.assertEqual(raw, before)
        self.assertFalse(extract_genres([], [], taxonomy_version='genres.v4')['tags'])
        with self.assertRaises(ValueError):
            canonical_tag('音乐', taxonomy_version='fake')

    def test_six_gates_require_test_attestation_and_nonempty_natural_sample_metrics(self):
        sample = passing_sample()
        gates = gate_report([sample], {'status': 'pass'}, DEFAULT_LIMITS)
        self.assertEqual({gate['status'] for gate in gates.values()}, {'pass'})
        self.assertEqual(gate_report([], {'status': 'pass'}, DEFAULT_LIMITS)['evidence']['status'], 'insufficient_evidence')
        self.assertEqual(gate_report([sample], None, DEFAULT_LIMITS)['safety']['status'], 'insufficient_evidence')

    def test_any_evidence_or_safety_error_blocks_switch_including_holdouts(self):
        for key, gate in (('safety_errors', 'safety'), ('holdout_evidence_errors', 'evidence')):
            sample = passing_sample(); sample[key] = 1
            self.assertEqual(gate_report([sample], {'status': 'pass'}, DEFAULT_LIMITS)[gate]['status'], 'fail')

    def test_relevance_drop_holdout_drop_or_no_diversity_gain_never_pass(self):
        for change in ('relevance', 'holdout', 'repeat'):
            sample = passing_sample()
            if change == 'relevance':
                sample['arms']['current_v2']['categories']['all']['topic_overlap_proxy_mean'] = .7
            elif change == 'holdout':
                sample['passes']['ranking']['holdout']['current_v2']['all']['hit_at_k'] = .4
            else:
                sample['arms']['current_v2']['categories']['all']['pairwise_genre_jaccard_mean'] = .4
            self.assertEqual(gate_report([sample], {'status': 'pass'}, DEFAULT_LIMITS)['relevance']['status'], 'fail')

    def test_empty_or_unpaired_data_is_insufficient_not_a_fake_zero_pass(self):
        sample = passing_sample()
        sample['passes']['ranking']['holdout']['current_v1']['all']['hit_at_k'] = None
        self.assertEqual(gate_report([sample], {'status': 'pass'}, DEFAULT_LIMITS)['relevance']['status'], 'insufficient_evidence')
        sample = passing_sample(); sample['arms']['current_v2']['categories']['explore']['novel_direction_count'] = None
        self.assertEqual(gate_report([sample], {'status': 'pass'}, DEFAULT_LIMITS)['categories']['status'], 'insufficient_evidence')

    def test_category_identity_requires_real_novel_directions_not_forced_disjoint_ids(self):
        sample = passing_sample()
        sample['arms']['current_v2']['categories']['explore']['novel_direction_count'] = 1
        self.assertEqual(gate_report([sample], {'status': 'pass'}, DEFAULT_LIMITS)['categories']['status'], 'fail')

    def test_performance_stability_and_query_limits_checked(self):
        for key, value in (('maximum_compute_ms', 6000), ('maximum_sql_queries', 7), ('stable_order_and_evidence', False)):
            sample = passing_sample(); sample['arms']['current_v2']['performance']['all'][key] = value
            self.assertEqual(gate_report([sample], {'status': 'pass'}, DEFAULT_LIMITS)['performance']['status'], 'fail')

    def test_approval_signature_code_version_expiry_and_all_six_gates_required(self):
        payload = sign_artifact('approval', {'sample_count': 1, 'gates': dict.fromkeys(GATE_NAMES, 'pass')})
        self.assertEqual(verify_artifact(payload, 'approval'), payload)
        with self.assertRaises(ValueError):
            verify_artifact({**payload, 'sample_count': 2}, 'approval')
        with self.assertRaises(ValueError):
            verify_artifact(sign_artifact('approval', {}, lifetime_days=0), 'approval')
        with self.assertRaises(ValueError):
            verify_artifact(payload, 'tests')
        with patch('recommendations.version_control.release_descriptor', return_value={'fingerprint': 'changed'}):
            with self.assertRaises(ValueError):
                verify_artifact(payload, 'approval')

    def test_test_bypass_cannot_be_activated_in_production_settings_or_postgres(self):
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            with self.assertRaises(ValueError):
                require_v2_approval()

    def test_source_fingerprint_is_deterministic_and_contains_approval_and_test_code(self):
        first = release_descriptor()
        self.assertEqual(first, release_descriptor())
        self.assertIn('recommendations/tests/test_comparison.py', first['sources'])
        self.assertIn('recommendations/comparison.py', first['sources'])

    def test_fixed_test_command_never_accepts_arbitrary_test_modules_or_skips(self):
        from types import SimpleNamespace
        for output, code in (('\nOK (skipped=1)\n', 0), ('\nFAILED\n', 1)):
            with patch('recommendations.management.commands.verify_recommendation_release.subprocess.run',
                       return_value=SimpleNamespace(returncode=code, stdout=output, stderr='')):
                with self.assertRaises(CommandError):
                    call_command('verify_recommendation_release', stdout=StringIO())


class ComparisonDatabaseTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='compare-owner', email='authorized@example.test')
        self.other = get_user_model().objects.create_user(username='compare-other', email='other@example.test')
        for identity in (1, 2, 3, 4):
            self.catalog(identity, '音乐,日常')
            shared = Anime.objects.create(bgm_id=identity, chinese_title=f'公开作品{identity}', base_tags=['音乐', '日常'])
            UserAnimeRecord.objects.create(user=self.owner, anime=shared, watch_status='completed', personal_score='9.0',
                                          review='PRIVATE_REVIEW_NEVER_READ')
        for identity in range(10, 24):
            self.catalog(identity, '音乐,日常' if identity < 16 else '音乐,成长' if identity < 20 else '音乐,治愈')
        self.catalog(90, '音乐,日常', nsfw=True)
        self.catalog(91, '音乐,日常', nsfw=None)
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'])

    def catalog(self, identity, tags, **kwargs):
        return RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'公开作品{identity}', tags=tags,
                                                  score=8, nsfw=kwargs.get('nsfw', False))

    def evaluate(self, **changes):
        with readonly_snapshot():
            scope, rows = catalog_snapshot()
            result = evaluate_snapshot(resolved_records(self.owner), preference_contract(self.owner), self.owner.pk, rows,
                                       top_k=5, max_holdouts=2, repeats=2, **changes)
        return scope, rows, result

    def test_two_pass_matrix_same_accounts_same_snapshot_no_reviews_no_writes(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            _, _, result = self.evaluate()
        self.assertEqual(result['usable_samples'], 4)
        self.assertEqual(result['passes']['data_rules']['left'], 'legacy_v1')
        self.assertEqual(result['passes']['data_rules']['right'], 'current_v1')
        self.assertEqual(result['passes']['ranking']['right'], 'current_v2')
        self.assertEqual(result['safety_errors'], 0)
        self.assertEqual(result['holdout_evidence_errors'], 0)
        self.assertTrue(result['snapshot_isolation'])
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        payload = json.dumps(result)
        self.assertNotIn('PRIVATE_REVIEW', payload)
        self.assertNotIn('personal_score', payload)
        self.assertNotIn('公开作品', payload)
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)

    def test_unlinked_unsafe_empty_private_and_saved_zero_cannot_forge_four_samples(self):
        UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=4).update(private_base_tags=[])
        with self.assertRaisesRegex(ValueError, 'naturally usable'):
            self.evaluate()

    def test_legacy_cold_sample_remains_in_cohort_and_unpaired_folds_are_reported(self):
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=['MUSIC'])
        _, _, result = self.evaluate()
        self.assertEqual(result['arms']['legacy_v1']['mode'], 'preference_based')
        self.assertEqual(result['changed_rule_sample_counts'], {'genres.v2': 0, engine.TAXONOMY_VERSION: 4})
        self.assertEqual(result['passes']['data_rules']['paired_targets'], 0)
        self.assertEqual(result['passes']['data_rules']['skipped_unpaired_targets'], 2)
        self.assertEqual(result['passes']['ranking']['paired_targets'], 2)

    def test_version_controller_clones_context_and_rule_mismatch_is_rejected(self):
        _, rows = catalog_snapshot()
        records = resolved_records(self.owner)
        ctx = context_snapshot(records, preference_contract(self.owner), self.owner.pk, 'genres.v4')
        before = deepcopy(ctx)
        index = engine.build_feature_index(rows, taxonomy_version='genres.v4')
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True}
        outputs = rank_versions(ctx, options, index)
        self.assertEqual(set(outputs), {'content.v1', 'content.v2'})
        self.assertEqual(ctx, before)
        wrong = engine.build_feature_index(rows, taxonomy_version='genres.v2')
        with self.assertRaises(engine.RecommendationUnavailable):
            engine.rank_context(ctx, options, wrong)

    def test_versioned_caches_separate_and_targeted_clear_does_not_clear_other_version(self):
        first = engine.public_features(algorithm_version='content.v1')
        second = engine.public_features(algorithm_version='content.v2')
        keys = [cache_namespace(algorithm, engine.TAXONOMY_VERSION, engine.FEATURE_VERSION, engine.FEATURE_RULE_VERSION)
                for algorithm in ('content.v1', 'content.v2')]
        self.assertNotEqual(keys[0], keys[1])
        self.assertNotEqual(first['signature'], second['signature'])
        clear_version_cache('content.v2')
        self.assertIsNone(cache.get(keys[1]))
        self.assertIsNotNone(cache.get(keys[0]))

    def test_command_requires_explicit_scope_and_authorization_never_scans_all_users(self):
        for options in ({}, {'user_id': [self.owner.pk]}, {'catalog_only': True, 'user_id': [self.owner.pk]}):
            with self.assertRaises(CommandError):
                call_command('compare_recommendations', stdout=StringIO(), **options)
        with patch('recommendations.management.commands.compare_recommendations.evaluate_snapshot', side_effect=ValueError('stop')) as evaluate:
            with self.assertRaises(CommandError):
                call_command('compare_recommendations', user_email=[self.owner.email], confirm_authorized_samples=True, stdout=StringIO())
            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(evaluate.call_args.args[2], self.owner.pk)

    def test_ambiguous_email_or_three_samples_rejected_without_repair(self):
        get_user_model().objects.create_user(username='duplicate-email', email=self.owner.email)
        with self.assertRaisesRegex(CommandError, 'ambiguous'):
            call_command('compare_recommendations', user_email=[self.owner.email], confirm_authorized_samples=True, stdout=StringIO())
        UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=4).update(personal_score=0)
        with self.assertRaisesRegex(CommandError, 'naturally usable'):
            call_command('compare_recommendations', user_id=[self.owner.pk], confirm_authorized_samples=True, stdout=StringIO())
        self.assertEqual(UserAnimeRecord.objects.get(user=self.owner, anime__bgm_id=4).personal_score, 0)

    def test_no_test_attestation_means_no_approval_and_report_is_private_no_overwrite(self):
        scope, rows, sample = self.evaluate()
        report = build_comparison_report(scope, rows, [sample], None, top_k=5, max_holdouts=2, repeats=2)
        self.assertFalse(report['eligible_for_manual_switch'])
        with tempfile.TemporaryDirectory() as root:
            write_comparison(report, root)
            self.assertFalse((Path(root) / 'approval.json').exists())
            self.assertTrue(json.loads((Path(root) / 'manifest.json').read_text())['complete'])
            with self.assertRaises(ValueError):
                write_comparison(report, root)

    @override_settings(RECOMMENDATION_V2_TEST_BYPASS=False)
    def test_signed_all_pass_complete_report_allows_switch_then_rollback_invalidates_pages(self):
        scope, rows, sample = self.evaluate()
        report = build_comparison_report(scope, rows, [sample], None, top_k=5, max_holdouts=2, repeats=2)
        # Fixtures exercise approval mechanics only, not actual algorithm quality.
        report['gates'] = {key: {'status': 'pass', 'details': []} for key in GATE_NAMES}
        report['eligible_for_manual_switch'] = True
        report['test_attestation_verified'] = True  # Only a signed-mechanics unit fixture.
        with tempfile.TemporaryDirectory() as root:
            write_comparison(report, root)
            with override_settings(RECOMMENDATION_V2_APPROVAL_FILE=str(Path(root) / 'approval.json'),
                                   RECOMMENDATION_ALGORITHM_VERSION='content.v2'):
                require_v2_approval()
                from rest_framework.test import APIClient
                client = APIClient(); client.force_authenticate(self.owner)
                first = client.get('/api/recommendations/', {'page_size': 3}).data
                self.assertEqual(first['algorithm_version'], 'content.v2')
                with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v1'):
                    response = client.get('/api/recommendations/', {'page_size': 3, 'page': 2, 'state_token': first['state_token']})
                    self.assertEqual(response.status_code, 409)
                # Incomplete/tampered reports never unlock V2 even with a valid MAC.
                with patch('recommendations.version_control.Path.read_bytes', side_effect=OSError()):
                    with self.assertRaises(ValueError):
                        require_v2_approval()

    @override_settings(RECOMMENDATION_V2_TEST_BYPASS=False, RECOMMENDATION_ALGORITHM_VERSION='content.v2', RECOMMENDATION_V2_APPROVAL_FILE='')
    def test_no_approval_fails_closed_but_v1_rollback_needs_no_approval(self):
        from rest_framework.test import APIClient
        client = APIClient(); client.force_authenticate(self.owner)
        self.assertEqual(client.get('/api/recommendations/').status_code, 503)
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v1'):
            self.assertEqual(client.get('/api/recommendations/').status_code, 200)
