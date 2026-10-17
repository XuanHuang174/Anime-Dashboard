"""V2-B pure selection tests + isolated PostgreSQL comparison/API guards."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from random import Random
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from anime.models import UserAnimeRecord
from recommendations import engine, ranking_v2, recall_v2
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_comparison import (ARMS, CANDIDATE_ARM, rank_arm,
    compare_sample, assert_recall_only, verify_policy, review_quality, RecallObserver)
from recommendations.recall_diagnostics import load_config
from recommendations.diagnostics import public_index
from recommendations.tests import test_diagnostics as fixtures

CONFIG = Path(settings.BASE_DIR) / 'evaluation/v2a-recall.v1.json'


def item(identity, tags, value=.8):
    return {'bangumi_id': identity, 'tags': frozenset(tags), 'value': value,
            'evidence': {'kind': 'synthetic'}, 'reason': 'synthetic real intersection'}


class RecallPoolTests(SimpleTestCase):
    def context(self, seeds=(), explicit=('音乐', '日常', '成长')):
        return {'mode': 'personalized' if seeds else 'preference_based',
                'seeds': [{'tags': frozenset(tags)} for tags in seeds], 'preferences': {'tags': list(explicit)}}

    def test_policy_preregistered_and_caps_frozen(self):
        self.assertEqual(len(verify_policy()), 64)
        self.assertEqual(recall_v2.CONTRACT['ranking_policy'], ranking_v2.POLICY_VERSION)
        with patch.object(recall_v2, 'policy_contract', return_value={'tuned': True}):
            with self.assertRaisesMessage(ValueError, 'Predeclared'):
                verify_policy()

    def test_shuffle_duplicates_stable_without_input_mutation(self):
        rows = [item(n, ('音乐', '成长') if n % 7 else ('日常', '喜剧'), .8 + (n % 9) / 1000) for n in range(1, 1000)]
        rows += [deepcopy(rows[0]), {**rows[1], 'value': .1}]
        before = deepcopy(rows); context = self.context([('音乐', '成长')]); old = deepcopy(context)
        expected = recall_v2.candidate_pool(rows, {'音乐': 2, '日常': 1}, context, {})
        Random(1024).shuffle(rows)
        actual = recall_v2.candidate_pool(rows, {'日常': 1, '音乐': 2}, context, {})
        self.assertEqual(actual, expected); self.assertEqual(len(actual), 400)
        self.assertEqual(len({row['bangumi_id'] for row in actual}), 400)
        self.assertEqual(context, old)
        self.assertEqual(sorted(rows, key=lambda row: (row['bangumi_id'], row['value'])),
                         sorted(before, key=lambda row: (row['bangumi_id'], row['value'])))
        for row in actual:
            self.assertIn(row, before)

    def test_source_pattern_gets_reserve_without_target_ids_or_score_changes(self):
        # Many preferred-genre patterns occupy old reserve queues before this
        # lower-scoring but genuinely close source-pattern work. Synthetic only.
        rows = [item(n, ('音乐', f'extra{n}'), .9) for n in range(1, 601)]
        special = item(999, ('音乐', '成长'), .8); rows.append(special)
        vector = {'音乐': 1}
        self.assertNotIn(special, ranking_v2.balanced_candidate_pool(rows, vector))
        self.assertIn(special, recall_v2.candidate_pool(rows, vector, self.context([('音乐', '成长')], ()), {}))
        self.assertEqual(special['value'], .8)

    def test_floor_prevents_weak_reserve_filler_and_pool_can_be_short(self):
        rows = [item(n, ('音乐',), 1) for n in range(1, 201)]
        rows += [item(n, ('音乐', '成长'), .2) for n in range(201, 901)]
        chosen = recall_v2.candidate_pool(rows, {'音乐': 1}, self.context([('音乐', '成长')]), {})
        self.assertEqual(len(chosen), 200)
        self.assertTrue(all(row['value'] == 1 for row in chosen))

    def test_invalid_unrelated_evidenceless_rows_not_added_to_fill_quota(self):
        rows = [item(n, ('音乐',)) for n in range(1, 8)]
        rows += [item(10, ('悬疑',)), item(11, ('音乐',), float('nan')),
                 item(12, ('音乐',), float('inf')), item(True, ('音乐',)), item(-1, ('音乐',)),
                 {**item(13, ('音乐',)), 'evidence': None}, {**item(14, ('音乐',)), 'reason': ''}]
        self.assertEqual([row['bangumi_id'] for row in recall_v2.candidate_pool(
            rows, {'音乐': 1}, self.context(), {})], list(range(1, 8)))
        for limit in (-1, 401, True, 1.5):
            with self.assertRaises(ValueError):
                recall_v2.candidate_pool(rows, {'音乐': 1}, self.context(), {}, limit=limit)
        self.assertEqual(recall_v2.candidate_pool(rows, {'音乐': 1}, self.context(), {}, limit=0), [])
        self.assertEqual(recall_v2.candidate_pool([], {'音乐': 1}, self.context(), {}), [])

    def test_preference_only_routes_have_no_trusted_seed_assumptions_and_are_deterministic(self):
        rows = [item(n, ('音乐', '成长') if n % 2 else ('音乐', '日常')) for n in range(1, 801)]
        context = self.context(explicit=('音乐',)); original = deepcopy(context)
        a = recall_v2.candidate_pool(rows, {'音乐': 1}, context, {})
        b = recall_v2.candidate_pool(list(reversed(rows)), {'音乐': 1}, context, {})
        self.assertEqual(a, b); self.assertEqual(len(a), 400); self.assertEqual(context, original)
        self.assertTrue(all(row['tags'] & {'音乐'} for row in a))

    def test_feature_pattern_is_not_series_dedup_and_relevance_head_retained(self):
        rows = [item(n, ('音乐',), 1 - n / 10000) for n in range(1, 701)]
        pool = recall_v2.candidate_pool(rows, {'音乐': 1}, self.context(), {})
        ids = {row['bangumi_id'] for row in pool}
        self.assertEqual(len(ids), 400); self.assertTrue(set(range(1, 201)) <= ids)

    def test_sources_duplicated_or_reordered_do_not_amplify_route_quota(self):
        rows = [item(n, ('音乐', '成长') if n % 2 else ('音乐', '日常')) for n in range(1, 901)]
        context = self.context([('音乐', '成长'), ('音乐', '日常')], ())
        first = recall_v2.candidate_pool(rows, {'音乐': 1}, context, {})
        context['seeds'] = list(reversed(context['seeds'])) * 3
        self.assertEqual(first, recall_v2.candidate_pool(rows, {'音乐': 1}, context, {}))

    def test_audit_success_is_not_quality_acceptance_and_regressions_are_visible(self):
        cell = {'conditional_pool_recall': 1., 'hit_at_10': 0., 'mrr_at_10': 0., 'binary_ndcg_at_10': 0.}
        result = {'baseline': {arm: {'all': {'metrics': {'topic_overlap_proxy_mean': .8}}}
                               for arm in ('content.v2', CANDIDATE_ARM)},
                  'holdout_sets': {'expanded': {'paired_count': 12,
                    'metrics': {arm: {'all': deepcopy(cell)} for arm in ('content.v2', CANDIDATE_ARM)}}}}
        self.assertEqual(review_quality(result)['status'], 'no_proven_top10_gain')
        candidate = result['holdout_sets']['expanded']['metrics'][CANDIDATE_ARM]['all']
        candidate['conditional_pool_recall'] = .5
        self.assertEqual(review_quality(result)['status'], 'not_accepted_regression')
        candidate['conditional_pool_recall'] = 1.
        candidate['hit_at_10'] = .1
        self.assertEqual(review_quality(result)['status'], 'requires_independent_validation')
        result['holdout_sets']['expanded']['paired_count'] = 0
        self.assertEqual(review_quality(result)['status'], 'insufficient_holdout_evidence')
        with self.assertRaisesMessage(ValueError, 'Unknown comparison arm'):
            rank_arm(None, {}, {}, {}, 'content.v3', 'all')

    def test_explore_repairs_old_floor_loss_by_preserving_complete_frozen_pool(self):
        rows = [item(n, ('音乐', '成长'), 1.) for n in range(1, 201)]
        rows += [item(n, ('音乐', '冒险'), .7) for n in range(201, 801)]
        context, vector = self.context(), {'音乐': 1}
        old = ranking_v2.balanced_candidate_pool(rows, vector)
        previous = recall_v2.candidate_pool(rows, vector, context, {}, category='explore',
                                           policy_version=recall_v2.PREVIOUS_VERSION)
        fixed = recall_v2.candidate_pool(rows, vector, context, {}, category='explore')
        self.assertEqual(len(previous), 200)
        self.assertEqual(fixed, old); self.assertEqual(len(fixed), 400)
        probe = RecallObserver(201); probe.audit_items('eligible', rows)
        self.assertTrue(probe.reserve_probe['previous_gate_excludes'])
        self.assertIn(201, [row['bangumi_id'] for row in fixed])
        # The repair is category-wide, not a special case for a test identity.
        self.assertTrue(set(row['bangumi_id'] for row in old) <= set(row['bangumi_id'] for row in fixed))

    def test_repair_does_not_change_discovery_familiar_or_previous_policy(self):
        rows = [item(n, ('音乐', '成长') if n % 3 else ('日常', '冒险'), .8 + (n % 20) / 1000)
                for n in range(1, 1000)]
        vector, context = {'音乐': 1, '日常': 1}, self.context([('音乐', '成长')])
        previous = recall_v2.previous_candidate_pool(rows, vector, context, {})
        for category in ('all', 'familiar'):
            self.assertEqual(recall_v2.candidate_pool(rows, vector, context, {}, category=category), previous)
        self.assertEqual(recall_v2.candidate_pool(rows, vector, context, {}, category='explore',
                         policy_version=recall_v2.PREVIOUS_VERSION), previous)
        with self.assertRaises(ValueError):
            recall_v2.policy_contract('unknown')
        with self.assertRaises(ValueError):
            recall_v2.candidate_pool(rows, vector, context, {}, category='unknown')


class RecallOptimizationPostgresTests(TransactionTestCase):
    setUp = fixtures.DiagnosticPostgresTests.setUp
    sample = fixtures.DiagnosticPostgresTests.sample

    def inputs(self, owner=None):
        config, _ = load_config(CONFIG)
        _, rows = catalog_snapshot(); catalog = {row['bangumi_id']: row for row in rows}
        context, summary = self.sample(owner)
        index, _ = public_index(rows)
        return config, catalog, context, summary, index

    def test_all_arms_repeat_same_frozen_scores_evidence_and_no_writes_or_review_reads(self):
        config, catalog, context, summary, index = self.inputs()
        before = list(UserAnimeRecord.objects.order_by('pk').values()); original = deepcopy(context)
        from recommendations.recall_diagnostics import catalog_states
        with CaptureQueriesContext(connection) as queries, readonly_snapshot():
            report = compare_sample(self.owner, context, summary, index, catalog, catalog_states(), config)
        self.assertEqual(report['recall_only_invariant'], 'pass'); self.assertEqual(context, original)
        self.assertEqual(set(report['baseline']), set(ARMS))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        self.assertFalse(any('"review"' in row['sql'] or row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
        for category in config['categories']:
            # Small pool: selection must not change any output field at all.
            left = report['baseline']['content.v2'][category]['runs'][0]
            right = report['baseline'][CANDIDATE_ARM][category]['runs'][0]
            self.assertEqual(left['noninterference']['output_hmac'], right['noninterference']['output_hmac'])
            self.assertEqual(left['runs']['control']['sql_count'], right['runs']['control']['sql_count'])

    def test_large_pool_changes_membership_only_and_keeps_hard_caps(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=n, title_zh=f'公开合成{n}',
            tags='音乐,日常' if n % 2 else '音乐,成长', score=6 + n % 20 / 10, nsfw=False) for n in range(100, 850)])
        config, _, context, _, index = self.inputs()
        with readonly_snapshot():
            _, old = rank_arm(self.owner, context, index, config['baseline_filters'], 'content.v2', 'all')
            rows, candidate = rank_arm(self.owner, context, index, config['baseline_filters'], CANDIDATE_ARM, 'all')
        assert_recall_only(old, candidate)
        self.assertLessEqual(candidate['pipeline']['stages']['pool']['count'], 400)
        self.assertGreaterEqual(candidate['pipeline']['stages']['pool']['count'], 200)
        self.assertEqual(len(rows), 120)

    def test_recall_invariant_detects_same_count_but_changed_score_or_evidence(self):
        config, _, context, _, index = self.inputs()
        _, old = rank_arm(self.owner, context, index, config['baseline_filters'], 'content.v2', 'all')
        changed = deepcopy(old); changed['pipeline']['item_hmacs']['eligible'] = 'changed'
        with self.assertRaisesMessage(ValueError, 'base scores/evidence'):
            assert_recall_only(old, changed)
        changed = deepcopy(old); changed['pipeline']['stages']['pool']['count'] = 401
        with self.assertRaisesMessage(ValueError, 'cap changed'):
            assert_recall_only(old, changed)

    def test_unsafe_unknown_hidden_owned_unsupported_genres_never_enter_pool(self):
        RecommendationAnime.objects.filter(bangumi_id=5).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=6).update(nsfw=None)
        RecommendationAnime.objects.filter(bangumi_id=9).update(tags='神作,二刷')
        RecommendationAnime.objects.filter(bangumi_id=10).update(tags='悬疑')
        config, _, context, _, index = self.inputs()
        context['feedback']['hidden_ids'] = {7}; context['owned'][8] = 'plan'
        captured = []; original = recall_v2.candidate_pool
        def capture(items, *args, **kwargs):
            captured.extend(row['bangumi_id'] for row in items)
            return original(items, *args, **kwargs)
        with patch.object(recall_v2, 'candidate_pool', side_effect=capture), readonly_snapshot():
            engine.rank_recall_candidate(context, {**config['baseline_filters'], 'category': 'all'}, index)
        self.assertFalse(set(range(1, 11)) & set(captured))

    def test_reclassification_after_pool_and_unsafe_source_fail_closed(self):
        config, _, context, _, index = self.inputs()
        original = recall_v2.candidate_pool
        def reclassify(*args, **kwargs):
            rows = original(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=7).update(nsfw=True)  # isolated synthetic DB only
            return rows
        with patch.object(recall_v2, 'candidate_pool', side_effect=reclassify):
            rows, _ = engine.rank_recall_candidate(deepcopy(context), {**config['baseline_filters'], 'category': 'all'}, index)
        self.assertNotIn(7, [row['anime'].bangumi_id for row in rows])
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=None)
        with readonly_snapshot(), self.assertRaises(engine.RecommendationSnapshotChanged):
            engine.rank_recall_candidate(deepcopy(context), {**config['baseline_filters'], 'category': 'all'}, index)

    def test_actual_filters_precede_recall_and_no_unrelated_explore_results(self):
        RecommendationAnime.objects.filter(bangumi_id=7).update(status_type='TV', score=9)
        RecommendationAnime.objects.filter(bangumi_id=8).update(status_type='TV', score=6)
        config, _, context, _, index = self.inputs()
        options = {**config['baseline_filters'], 'category': 'all', 'type': 'TV', 'min_score': 8}
        with readonly_snapshot():
            rows, _ = engine.rank_recall_candidate(deepcopy(context), options, index)
        self.assertEqual([row['anime'].bangumi_id for row in rows], [7])
        with readonly_snapshot():
            rows, _ = engine.rank_recall_candidate(deepcopy(context), {**config['baseline_filters'], 'category': 'explore'}, index)
        preferred = set(engine.taste_vector(context))
        self.assertTrue(all(row['tags'] & preferred and row['tags'] - preferred for row in rows))

    def test_sparse_account_insufficient_holdouts_not_zero_accuracy_and_no_cross_user_evidence(self):
        RecommendationPreference.objects.create(user=self.sparse, tags=['音乐'])
        config, catalog, context, summary, index = self.inputs(self.sparse)
        from recommendations.recall_diagnostics import catalog_states
        with readonly_snapshot():
            result = compare_sample(self.sparse, context, summary, index, catalog, catalog_states(), config)
            rows, _ = rank_arm(self.sparse, context, index, config['baseline_filters'], CANDIDATE_ARM, 'all')
        self.assertEqual(result['holdout_sets']['expanded']['status'], 'insufficient_evidence')
        self.assertIsNone(result['holdout_sets']['expanded']['metrics'][CANDIDATE_ARM]['all']['hit_at_10'])
        self.assertTrue(all(row['evidence']['kind'] == 'explicit_preferences' and row['evidence']['source'] is None for row in rows))

    def test_default_v1_original_v2_never_call_candidate_and_http_rejects_override(self):
        config, _, context, _, index = self.inputs()
        with patch.object(recall_v2, 'candidate_pool', side_effect=AssertionError('Offline only')):
            for version in ('content.v1', 'content.v2'):
                engine.rank_context(deepcopy(context), {**config['baseline_filters'], 'category': 'all'}, index, algorithm_version=version)
            client = APIClient(); client.force_authenticate(self.owner)
            for key in ('recall_policy_version', 'v2_recall_version', 'algorithm_version'):
                self.assertEqual(client.get('/api/recommendations/', {key: recall_v2.VERSION}).status_code, 400)
            self.assertEqual(client.get('/api/recommendations/').status_code, 200)
        self.assertEqual(engine.configured_algorithm(), 'content.v1')
        with self.assertRaises(engine.RecommendationUnavailable):
            engine._rank_context_v2(deepcopy(context), {**config['baseline_filters'], 'category': 'all'}, index,
                recall_policy_version='unknown')

    def test_existing_a_lock_required_report_private_and_replay_refuses_drift(self):
        root = Path(settings.BASE_DIR) / '.tools/recall-b-tests' / uuid4().hex
        common = dict(user_email=[self.owner.email, self.sparse.email], confirm_authorized_samples=True, stdout=StringIO())
        with patch('recommendations.management.commands.audit_recommendation_recall.catalog_snapshot') as scan:
            with self.assertRaisesMessage(CommandError, 'existing V2-A'):
                call_command('compare_recommendation_recall', freeze_only=True, **common)
            scan.assert_not_called()
        call_command('audit_recommendation_recall', freeze_only=True, output_dir=root / 'freeze', **common)
        with CaptureQueriesContext(connection) as queries:
            call_command('compare_recommendation_recall', cohort_lock=root / 'freeze/cohort.lock.json', output_dir=root / 'comparison', **common)
        text = (root / 'comparison/recall-audit.json').read_text(encoding='utf-8')
        report = json.loads(text)
        self.assertEqual(report['online_default'], 'content.v1'); self.assertFalse(report['approval_issued'])
        self.assertTrue(report['frozen_scoring_and_reranking'])
        self.assertEqual(report['comparison_arms'], list(ARMS))
        for sentinel in (self.owner.email, self.sparse.email, 'personal_score', 'PRIVATE_REVIEW', '合成目录'):
            self.assertNotIn(sentinel, text)
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        with self.assertRaises(CommandError):
            call_command('compare_recommendation_recall', cohort_lock=root / 'freeze/cohort.lock.json', output_dir=root / 'comparison', **common)
        # Synthetic-only drift: no refreeze or target substitution is permitted.
        RecommendationAnime.objects.filter(bangumi_id=7).update(score=9)
        with patch('recommendations.recall_comparison.rank_arm', side_effect=AssertionError('No ranking after drift')) as rank:
            with self.assertRaisesMessage(CommandError, 'Frozen cohort'):
                call_command('compare_recommendation_recall', cohort_lock=root / 'freeze/cohort.lock.json', output_dir=root / 'drift', **common)
            rank.assert_not_called()
        self.assertFalse((root / 'drift/manifest.json').exists())
