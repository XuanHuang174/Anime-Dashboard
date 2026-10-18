"""Exact read parity and fixed-pair refresh on synthetic local PostgreSQL."""
from copy import deepcopy
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied

from anime.models import UserAnimeRecord
from recommendations import engine, v2_controls, discovery_development as development
from recommendations.execution import execute_v2
from recommendations.public_manifest import PublicManifestReader
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.version_control import sign_artifact, read_artifact
from recommendations.tests import test_unused_validation as fixtures
from recommendations.tests import test_discovery_candidate as discovery_tests


class PublicManifestTests(TransactionTestCase):
    setUp = fixtures.UnusedPostgresTests.setUp
    command = fixtures.UnusedPostgresTests.command
    inputs = discovery_tests.DiscoveryPostgresTests.inputs

    def run_pair(self, reader, *, options=None, arm='candidate_v2', context=None):
        context0, index = self.inputs()
        context = context or context0
        category = (options or {}).get('category', 'all')
        with readonly_snapshot(), CaptureQueriesContext(connection) as queries:
            old = development.run(self.owner, context, index, category, arm, dual=True)[0]
            new, timing = development.run(self.owner, context, index, category, arm, dual=True, public_reader=reader)
        self.assertEqual(output_digest(new), output_digest(old))
        self.assertFalse(any('"review"' in row['sql'] for row in queries))
        return new, timing

    def test_cold_warm_exact_output_and_bounded_transfer(self):
        reader = PublicManifestReader()
        _, cold = self.run_pair(reader)
        _, warm = self.run_pair(reader)
        self.assertFalse(cold['public_read']['cache_hit']); self.assertTrue(warm['public_read']['cache_hit'])
        self.assertEqual(cold['sql_queries'], 6); self.assertEqual(warm['sql_queries'], 5)
        self.assertEqual(warm['public_read']['transferred_rows'], 1)
        self.assertEqual(cold['public_read']['live_rows'], warm['public_read']['live_rows'])

    def test_direct_updates_without_timestamp_invalidate_every_ranking_field(self):
        reader = PublicManifestReader(); self.run_pair(reader)
        for changes in ({'score': 9.3}, {'season': '2026-07'}, {'status_type': 'OVA'},
                        {'tags': '音乐,悬疑'}, {'title_zh': '合成 新标题\n:'}, {'title_jp': 'テスト 🦊'}):
            RecommendationAnime.objects.filter(bangumi_id=16).update(**changes)
            _, timing = self.run_pair(reader)
            self.assertFalse(timing['public_read']['cache_hit'])
        self.assertTrue(self.run_pair(reader)[1]['public_read']['cache_hit'])

    def test_safety_removal_unknown_and_new_safe_record_revalidated(self):
        reader = PublicManifestReader(); self.run_pair(reader)
        for nsfw in (True, None):
            RecommendationAnime.objects.filter(bangumi_id=16).update(nsfw=nsfw)
            rows, _ = self.run_pair(reader)
            self.assertNotIn(16, [row['anime'].bangumi_id for row in rows])
        RecommendationAnime.objects.create(bangumi_id=101, title_zh='合成 新作品', tags='音乐', score=9, nsfw=False)
        rows, timing = self.run_pair(reader)
        self.assertFalse(timing['public_read']['cache_hit'])
        self.assertIn(101, [row['anime'].bangumi_id for row in rows])

    def test_all_arms_categories_and_styles_equal_and_query_scopes_bounded(self):
        RecommendationAnime.objects.filter(bangumi_id__gte=15).update(season='2026-01', status_type='TV')
        context, index = self.inputs(); reader = PublicManifestReader()
        from recommendations.exploration_candidate import PROTECTED_VERSION
        for category in ('all', 'familiar', 'explore'):
            for style in ('balanced', 'close', 'open'):
                options = {'category': category, 'style': style, 'year_from': 2020, 'year_to': 2026,
                           'hide_recorded': False}
                with readonly_snapshot():
                    old = execute_v2(self.owner, options, index, context=context, exploration_policy_version=PROTECTED_VERSION)[0]
                    new = execute_v2(self.owner, options, index, context=context,
                                     exploration_policy_version=PROTECTED_VERSION, public_reader=reader)[0]
                self.assertEqual(output_digest(old), output_digest(new))
                if category == 'all':
                    self.assertTrue(new)
        for score in (0, 1, 2, 3, 4, 5):
            with readonly_snapshot():
                execute_v2(self.owner, {'min_score': score}, index, context=context, public_reader=reader)
        self.assertLessEqual(len(reader._scopes), 4)

    def test_fixed_feature_index_still_detects_updated_tags_and_metadata(self):
        context, index = self.inputs(); reader = PublicManifestReader()
        with readonly_snapshot():
            execute_v2(self.owner, {}, index, context=context, public_reader=reader)
        for changes in ({'tags': '音乐,悬疑'}, {'score': 9.2, 'season': '2026-04', 'status_type': 'OVA'}):
            RecommendationAnime.objects.filter(bangumi_id=16).update(**changes)
            with readonly_snapshot():
                old = execute_v2(self.owner, {}, index, context=context)[0]
                new = execute_v2(self.owner, {}, index, context=context, public_reader=reader)[0]
                self.assertEqual(output_digest(new), output_digest(old))
                self.assertFalse(reader.last_read['cache_hit'])
                options = {'type': 'OVA', 'year_from': 2026, 'year_to': 2026, 'min_score': 8}
                old = execute_v2(self.owner, options, index, context=context)[0]
                new = execute_v2(self.owner, options, index, context=context, public_reader=reader)[0]
                self.assertEqual(output_digest(new), output_digest(old))

    def test_safety_membership_swap_same_count_cannot_reuse_old_rows(self):
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=True)
        reader = PublicManifestReader(); _, first = self.run_pair(reader)
        RecommendationAnime.objects.filter(bangumi_id=16).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=False)
        rows, second = self.run_pair(reader)
        self.assertEqual(first['public_read']['live_rows'], second['public_read']['live_rows'])
        self.assertFalse(second['public_read']['cache_hit'])
        self.assertNotIn(16, [row['anime'].bangumi_id for row in rows])

    def test_feedback_owned_filters_and_multiuser_read_cache_is_public_only(self):
        context, index = self.inputs(); reader = PublicManifestReader()
        context['feedback']['hidden_ids'] = [16]
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with readonly_snapshot():
            a = execute_v2(self.owner, {}, index, context=context, public_reader=reader)[0]
            b_context = engine.user_context(self.sparse)
            b = execute_v2(self.sparse, {}, index, context=b_context, public_reader=reader)[0]
            with self.assertRaises(PermissionDenied):
                execute_v2(self.sparse, {}, index, context=context, public_reader=reader)
        self.assertNotIn(16, [row['anime'].bangumi_id for row in a])
        self.assertIn(16, [row['anime'].bangumi_id for row in b])
        self.assertNotIn('review', repr(reader._scopes)); self.assertNotIn('seeds', repr(reader._scopes))
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_overflow_empty_scopes_writable_or_serving_rejected(self):
        context, index = self.inputs(); reader = PublicManifestReader()
        options = v2_controls.validate_options({})
        with readonly_snapshot():
            with self.assertRaises(engine.RecommendationUnavailable):
                reader.read(engine.safe_catalog(), options, engine.public_feature_expression(), 1)
            for _ in range(2):
                rows, metadata = reader.read(engine.safe_catalog().filter(bangumi_id=999999), options,
                                            engine.public_feature_expression(), engine.MAX_CATALOG_ROWS)
                self.assertEqual(rows, []); self.assertEqual(metadata, {})
            self.assertTrue(reader.last_read['cache_hit'])
        with transaction.atomic(), self.assertRaises(ValueError):
            reader.read(engine.safe_catalog(), options, engine.public_feature_expression(), engine.MAX_CATALOG_ROWS)
        for extra in ({'serving_enabled': True, 'public_reader': reader}, {'public_reader': object()}):
            with self.assertRaises(engine.RecommendationUnavailable):
                execute_v2(self.owner, {}, index, context=context, **extra)

    def prepare_refresh(self):
        self.command(freeze_only=True, output_dir=self.root / 'original')
        self.command(cohort_lock=self.root / 'original/cohort.lock.json', test_report=self.proof, output_dir=self.root / 'consumed')
        proof = sign_artifact('unused-holdout-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
            'skipped': 0, 'locks': [6, 4, 6], 'unused_validation_tests': 9, 'public_manifest_tests': 8})
        path = self.root / 'manifest-tests.json'; path.write_text(json.dumps(proof), encoding='utf-8')
        return dict(confirm_sitewide_readonly=True, consumed_cohort=self.root / 'original/cohort.lock.json',
                    consumed_result=self.root / 'consumed/evaluation.json', test_report=path, stdout=StringIO())

    def test_refreeze_only_preserves_original_pairs_despite_input_change(self):
        options = self.prepare_refresh()
        RecommendationPreference.objects.update_or_create(user=self.owner, defaults={'tags': ['音乐']})
        original = read_artifact(options['consumed_cohort'], 'unused-holdout-cohort')
        with patch.object(development, 'evaluate', side_effect=AssertionError('Freeze must not rank')):
            call_command('refreeze_recommendation_discovery', freeze_only=True, output_dir=self.root / 'fresh', **options)
        fresh = read_artifact(self.root / 'fresh/cohort.lock.json', 'discovery-refrozen-cohort')
        selected = [entry for entry in original['entries'] if entry['status'] == 'selected']
        self.assertEqual([e['targets'] for e in fresh['entries']], [e['targets'] for e in selected])
        self.assertNotEqual(fresh['entries'][0]['input'], selected[0]['input'])
        self.assertFalse(fresh['ranking_performed']); self.assertFalse(fresh['independent_validation'])

    def test_refrozen_evaluation_parity_and_no_writes_or_overwrites(self):
        options = self.prepare_refresh()
        call_command('refreeze_recommendation_discovery', freeze_only=True, output_dir=self.root / 'fresh', **options)
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        call_command('refreeze_recommendation_discovery', cohort_lock=self.root / 'fresh/cohort.lock.json',
                     output_dir=self.root / 'compared', **options)
        result = read_artifact(self.root / 'compared/comparison.json', 'discovery-refrozen-development')
        self.assertTrue(result['complete']); self.assertTrue(result['ranking_performed'])
        self.assertEqual(len(result['natural_read_parity']), 12)
        self.assertFalse(result['serving_approval']); self.assertFalse(result['independent_validation'])
        self.assertEqual(result['samples'][0]['safety_errors'], 0)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        with self.assertRaises(CommandError):
            call_command('refreeze_recommendation_discovery', freeze_only=True, output_dir=self.root / 'fresh', **options)

    def test_original_target_lost_eligibility_stops_without_replacement(self):
        options = self.prepare_refresh()
        original = read_artifact(options['consumed_cohort'], 'unused-holdout-cohort')
        pair = next(entry['targets'][0] for entry in original['entries'] if entry['status'] == 'selected')
        from recommendations.unused_validation import pair_handle
        context = engine.user_context(self.owner)
        identity = next(seed['bangumi_id'] for seed in context['seeds'] if pair_handle(self.owner.pk, seed['bangumi_id']) == pair)
        UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=identity).update(personal_score=7)
        with self.assertRaises(CommandError):
            call_command('refreeze_recommendation_discovery', freeze_only=True, output_dir=self.root / 'invalid', **options)

    def test_second_drift_after_refreeze_not_silently_accepted(self):
        options = self.prepare_refresh()
        call_command('refreeze_recommendation_discovery', freeze_only=True, output_dir=self.root / 'fresh', **options)
        RecommendationPreference.objects.update_or_create(user=self.owner, defaults={'tags': ['悬疑']})
        with patch.object(development, 'evaluate') as ranking:
            with self.assertRaises(CommandError):
                call_command('refreeze_recommendation_discovery', cohort_lock=self.root / 'fresh/cohort.lock.json',
                             output_dir=self.root / 'drift', **options)
            ranking.assert_not_called()
