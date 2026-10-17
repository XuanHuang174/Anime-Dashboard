"""D1 prepared contract ONLY; production V2 browsing remains disabled."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command, CommandError
from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.test import APIClient

from anime.models import UserAnimeRecord
from recommendations import engine, v2_controls, ranking_v2, rerank_runtime
from recommendations.execution import execute_v2, recipe_descriptor
from recommendations.diagnostics import public_index
from recommendations.models import RecommendationAnime
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.serializers import ExecutionRecipeSerializer
from recommendations.tests import test_diagnostics as fixtures


class V2ControlContractTests(SimpleTestCase):
    def test_recipe_is_complete_versioned_predeclared_and_not_served(self):
        recipe = recipe_descriptor()
        self.assertEqual(recipe['algorithm_version'], 'content.v2')
        self.assertEqual(recipe['recall_policy_version'], 'source-pattern-recall.v2')
        self.assertEqual(recipe['runtime_version'], rerank_runtime.VERSION)
        self.assertEqual(recipe['control_policy_version'], 'browse-controls.v2')
        self.assertEqual(recipe['candidate_limit'], 400); self.assertEqual(recipe['result_limit'], 120)
        self.assertFalse(recipe['serving_enabled'])
        self.assertEqual(dict(ExecutionRecipeSerializer(recipe).data), recipe)
        with patch.object(v2_controls, 'policy_contract', return_value={'tuned': True}):
            with self.assertRaises(ValueError):
                recipe_descriptor()

    def test_controls_reject_unknown_identity_version_cursor_and_bad_years(self):
        defaults = v2_controls.validate_options({})
        self.assertEqual(defaults['style'], 'balanced')
        self.assertEqual(defaults['page_size'], 12)
        for changes in ({'user_id': 1}, {'algorithm_version': 'content.v2'}, {'runtime_version': 'x'},
                        {'cursor': 'x'}, {'style': 'random'}, {'year_from': 2026, 'year_to': 2000},
                        {'year_from': '2026x'}, {'min_score': 11}, {'page_size': 25}):
            with self.assertRaises(ValidationError):
                v2_controls.validate_options(changes)

    def test_only_reliable_season_fields_define_years_and_missing_is_not_in_range(self):
        options = v2_controls.validate_options({'year_from': 2026, 'year_to': 2026})
        for season in ('2026-1', '2026-01', '2026-04', '2026-7', '2026-10', '2026-02-28', '2026'):
            self.assertTrue(v2_controls.matches(dict(score=8, status_type='TV', season=season), options))
        for season in ('', 'unknown', '2026-02-30', '2026-13', '2025-01', 'title2026'):
            self.assertFalse(v2_controls.matches(dict(score=8, status_type='TV', season=season), options))
        self.assertTrue(v2_controls.matches(dict(score=8, status_type='TV', season=''), v2_controls.validate_options({})))

    def test_style_strength_is_explicit_and_can_change_order_without_changing_evidence(self):
        items = [{'bangumi_id': n, 'tags': frozenset(tags), 'value': score,
                  'evidence': {'kind': 'synthetic'}, 'reason': 'actual shared genre'} for n, tags, score in (
            (1, ('音乐', '日常'), .9), (2, ('音乐', '日常'), .8), (3, ('音乐', '成长'), .63))]
        outputs = {}
        for style in ('balanced', 'close', 'open'):
            options = v2_controls.validate_options({'style': style})
            outputs[style], _ = v2_controls.rerank(items, {'音乐': 1}, {}, options)
        self.assertEqual(outputs['balanced'], rerank_runtime.rerank_candidates(items, {'音乐': 1}, {}, category='all')[0])
        self.assertNotEqual([row['bangumi_id'] for row in outputs['close']], [row['bangumi_id'] for row in outputs['open']])
        self.assertTrue(all(row in items for rows in outputs.values() for row in rows))


class V2ExecutionPostgresTests(TransactionTestCase):
    setUp = fixtures.DiagnosticPostgresTests.setUp
    sample = fixtures.DiagnosticPostgresTests.sample

    def inputs(self):
        _, catalog = catalog_snapshot(); context, _ = self.sample()
        index, _ = public_index(catalog)
        return context, index

    def test_balanced_exactly_matches_verified_v2c_all_categories_and_does_not_mutate(self):
        context, index = self.inputs(); original = deepcopy(context)
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        for category in ('all', 'familiar', 'explore'):
            options = v2_controls.validate_options({'category': category})
            with readonly_snapshot(), CaptureQueriesContext(connection) as queries:
                reference, _ = engine.rank_recall_candidate(deepcopy(context), options, index, runtime_version=rerank_runtime.VERSION)
                rows, weights, recipe = execute_v2(self.owner, options, index, context=context)
            self.assertEqual(output_digest(rows), output_digest(reference))
            self.assertEqual(weights, index['weights']); self.assertFalse(recipe['serving_enabled'])
            self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        self.assertEqual(context, original)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_year_filters_exclude_stale_cached_rows_and_refetched_rows_before_pool(self):
        RecommendationAnime.objects.filter(bangumi_id__gte=5).update(season='2026-01', status_type='TV', score=8)
        context, index = self.inputs()
        RecommendationAnime.objects.filter(bangumi_id=5).update(season='2015-04')
        RecommendationAnime.objects.filter(bangumi_id=6).update(season='')
        RecommendationAnime.objects.filter(bangumi_id=7).update(tags='音乐,成长', season='2020-01')
        RecommendationAnime.objects.filter(bangumi_id=8).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=9).update(nsfw=None)
        RecommendationAnime.objects.filter(bangumi_id=10).update(status_type='OVA')
        RecommendationAnime.objects.filter(bangumi_id=11).update(score=6)
        context['feedback']['hidden_ids'] = {12}; context['owned'][13] = 'plan'
        captured = []; original = __import__('recommendations.recall_v2', fromlist=['candidate_pool']).candidate_pool
        def capture(items, *args, **kwargs):
            captured.extend(item['bangumi_id'] for item in items)
            return original(items, *args, **kwargs)
        with patch('recommendations.recall_v2.candidate_pool', side_effect=capture):
            rows, _, _ = execute_v2(self.owner, dict(year_from=2026, year_to=2026, type='TV', min_score=7), index, context=context)
        self.assertFalse(set(range(1, 14)) & set(captured))
        self.assertTrue(rows); self.assertTrue(all(row['anime'].season == '2026-01' for row in rows))

    def test_more_than_400_wrong_years_cannot_starve_valid_candidates(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=n, title_zh=f'公开合成{n}',
            nsfw=False, tags='音乐,日常', score=9.9, season='2010-01') for n in range(100, 601)])
        RecommendationAnime.objects.filter(bangumi_id__gte=5, bangumi_id__lt=25).update(season='2026-04')
        context, index = self.inputs()
        rows, _, meta = execute_v2(self.owner, {'year_from': 2026}, index, context=context)
        self.assertEqual({row['anime'].bangumi_id for row in rows}, set(range(5, 25)))
        self.assertEqual(meta['trace']['candidate_pool'], 20)

    def test_hydration_rechecks_changed_season_even_after_initial_scan(self):
        # Internal hook in a synthetic writable test ONLY. Public execute_v2
        # itself always uses enforced READ ONLY REPEATABLE READ.
        RecommendationAnime.objects.filter(bangumi_id__gte=5).update(season='2026-01')
        context, index = self.inputs(); original = __import__('recommendations.recall_v2', fromlist=['candidate_pool']).candidate_pool
        def change(*args, **kwargs):
            pool = original(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=7).update(season='2020-01')
            return pool
        options = v2_controls.validate_options({'year_from': 2026})
        with patch('recommendations.recall_v2.candidate_pool', side_effect=change):
            rows, _ = engine._rank_context_v2(context, options, index, recall_policy_version='source-pattern-recall.v2',
                runtime_version=rerank_runtime.VERSION, browse_control_version=v2_controls.VERSION)
        self.assertNotIn(7, [row['anime'].bangumi_id for row in rows])

    def test_all_styles_keep_category_and_safety_rules_and_close_uses_real_v2_signal(self):
        RecommendationAnime.objects.filter(bangumi_id=7).update(nsfw=True)
        context, index = self.inputs()
        for category in ('all', 'familiar', 'explore'):
            for style in ('balanced', 'close', 'open'):
                rows, _, _ = execute_v2(self.owner, {'category': category, 'style': style}, index, context=context)
                self.assertNotIn(7, [row['anime'].bangumi_id for row in rows])
                preferred = engine.taste_vector(context, deterministic=True)
                self.assertTrue(all(row['tags'] & preferred.keys() for row in rows))
                if category == 'explore':
                    self.assertTrue(all(row['tags'] - preferred.keys() for row in rows))
                if category == 'familiar':
                    self.assertTrue(all(row['evidence']['kind'] == 'rated_work' for row in rows))
        balanced, _, _ = execute_v2(self.owner, {}, index, context=context)
        close, _, _ = execute_v2(self.owner, {'style': 'close'}, index, context=context)
        values = {row['anime'].bangumi_id: row['value'] for row in balanced}
        self.assertTrue(any(row['value'] > values[row['anime'].bangumi_id] for row in close))

    def test_own_user_auth_snapshot_and_online_v1_boundaries(self):
        context, index = self.inputs()
        with self.assertRaises(NotAuthenticated):
            execute_v2(AnonymousUser(), {}, index, context=context)
        with self.assertRaises(PermissionDenied):
            execute_v2(self.sparse, {}, index, context=context)
        with transaction.atomic(), self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, context=context)
        with patch('recommendations.execution.execute_v2', side_effect=AssertionError('D1 not served')):
            client = APIClient(); client.force_authenticate(self.owner)
            self.assertEqual(client.get('/api/recommendations/', {'browse': 'true'}).status_code, 200)
            self.assertEqual(client.get('/api/recommendations/capabilities/').data['algorithm_version'], 'content.v1')
            self.assertEqual(client.get('/api/recommendations/', {'recipe_revision': 'x'}).status_code, 400)

    def test_population_requires_authorization_and_only_exports_aggregate_counts(self):
        root = Path(settings.BASE_DIR) / '.tools/population-tests' / uuid4().hex
        with self.assertRaises(CommandError):
            call_command('audit_recommendation_population', output_dir=root, stdout=StringIO())
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            call_command('audit_recommendation_population', confirm_sitewide_readonly=True, output_dir=root, stdout=StringIO())
        text = (root / 'readiness.json').read_text(encoding='utf-8'); data = json.loads(text)
        self.assertEqual(data['counts']['active_accounts'], 2)
        self.assertEqual(data['counts']['natural_holdout_eligible_accounts'], 1)
        self.assertFalse(data['original_frozen_cohort_changed'])
        for sentinel in (self.owner.email, self.sparse.email, 'PRIVATE_REVIEW', 'personal_score', '合成目录'):
            self.assertNotIn(sentinel, text)
        self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        with self.assertRaises(CommandError):
            call_command('audit_recommendation_population', confirm_sitewide_readonly=True, max_accounts=1,
                         output_dir=root / 'bound', stdout=StringIO())

    def test_d1_fixed_cohort_audit_checks_balanced_and_actual_year_filters_privately(self):
        root = Path(settings.BASE_DIR) / '.tools/d1-audit-tests' / uuid4().hex
        common = dict(user_email=[self.owner.email, self.sparse.email], confirm_authorized_samples=True, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('audit_recommendation_execution', freeze_only=True, **common)
        call_command('audit_recommendation_recall', freeze_only=True, output_dir=root / 'freeze', **common)
        call_command('audit_recommendation_execution', cohort_lock=root / 'freeze/cohort.lock.json', output_dir=root / 'audit', **common)
        text = (root / 'audit/recall-audit.json').read_text(encoding='utf-8'); data = json.loads(text)
        self.assertFalse(data['approval_issued']); self.assertFalse(data['serving_changed'])
        self.assertTrue(all(sample['d1_balanced_equivalence'] == 'pass' for sample in data['samples']))
        self.assertEqual(len(data['samples'][0]['d1_actual_control_probes']), 9)
        self.assertNotIn(self.owner.email, text); self.assertNotIn('personal_score', text)
        self.assertNotIn('PRIVATE_REVIEW', text)
