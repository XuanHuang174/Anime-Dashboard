"""Exact constraints + isolated PostgreSQL, never synthetic production writes."""
from copy import deepcopy
from fractions import Fraction
from io import StringIO
from pathlib import Path
from random import Random
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from anime.models import UserAnimeRecord
from recommendations import engine, v2_controls, ranking_v2
from recommendations import protected_exploration as guard
from recommendations.exploration_candidate import (execute_protected_candidate, PROTECTED_VERSION,
    execute_dual_candidate, recipe_descriptor, policy_contract, dual_pool)
from recommendations.execution import execute_v2
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.diagnostics import public_index
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.tests import test_diagnostics as fixtures


def row(identity, tags, score=.7):
    return {'bangumi_id': identity, 'tags': frozenset(tags), 'value': score,
            '_recall_reference_value': score, 'evidence': {'kind': 'synthetic'}, 'reason': 'shared genre'}


class ProtectionPrimitiveTests(SimpleTestCase):
    def test_policy_is_preregistered_batch_sized_and_cannot_serve(self):
        policy = policy_contract(PROTECTED_VERSION)
        self.assertEqual(policy['protection'], guard.policy_contract())
        self.assertEqual(policy['protection']['head_size'], 24)
        self.assertEqual(policy['protection']['incumbent_reserve'], 96)
        self.assertFalse(policy['parameter_search'])
        self.assertFalse(policy['protection']['selection_uses_holdout_ids'])
        self.assertFalse(recipe_descriptor(PROTECTED_VERSION)['serving_enabled'])
        with patch.object(guard, 'HEAD_SIZE', 10):
            with self.assertRaises(ValueError):
                recipe_descriptor(PROTECTED_VERSION)

    def test_exact_prefix_floor_latest_position_and_no_score_or_evidence_mutation(self):
        context = {'seeds': [{'tags': frozenset(('音乐', '日常'))}], 'preferences': {'tags': []}}
        rows = [row(1, ('音乐', '成长', '科幻')), row(2, ('音乐', '成长')),
                row(3, ('音乐', '日常', '成长')), row(4, ('音乐', '日常', '科幻'))]
        original = deepcopy(rows)
        reference = [rows[0], rows[2], rows[1], rows[3]]
        result, stats = guard.protect_prefix(rows, reference, context)
        self.assertEqual(result[0], rows[0])  # Already sufficient first position is not touched.
        self.assertEqual(stats['protected_prefixes'], 4)
        self.assertGreater(stats['repair_swaps'], 0)
        for k in range(1, 5):
            measure = lambda seq: sum((guard._overlap(r['tags'], [context['seeds'][0]['tags']]) for r in seq[:k]), Fraction())
            self.assertGreaterEqual(measure(result), measure(reference))
        self.assertEqual(rows, original)
        self.assertTrue(all(any(r is source for source in rows) for r in result))

    def test_fixed_randomized_prefix_properties_and_determinism(self):
        rng = Random(20261018)
        genres = ('音乐', '日常', '成长', '科幻', '悬疑', '亲情')
        context = {'seeds': [{'tags': frozenset(('音乐', '日常'))}], 'preferences': {'tags': ['科幻']}}
        for _ in range(20):
            rows = [row(i, rng.sample(genres, rng.randrange(1, 6))) for i in range(1, 61)]
            refs = list(rows); rng.shuffle(refs)
            result, stats = guard.protect_prefix(rows, refs[:24], context)
            self.assertEqual(result, guard.protect_prefix(rows, refs[:24], context)[0])
            sources = [s['tags'] for s in context['seeds']] + [frozenset(context['preferences']['tags'])]
            for k in range(1, 25):
                self.assertGreaterEqual(sum((guard._overlap(r['tags'], sources) for r in result[:k]), Fraction()),
                                        sum((guard._overlap(r['tags'], sources) for r in refs[:k]), Fraction()))
            self.assertTrue(stats['prefix_floor_passed'])

    def test_pool_and_return_protect_incumbents_without_expanding_hydration(self):
        rows = [row(i, ('音乐', f'pattern{i % 17}'), 1 - i / 10000) for i in range(1, 801)]
        for r in rows:
            r['_recall_reference_value'] = r['bangumi_id'] / 10000
        vector, options = {'音乐': 1}, v2_controls.validate_options({'category': 'explore'})
        pool, plan = guard.prepare_pool(rows, vector, {}, options, proposal_pool=dual_pool(rows, vector))
        ids = {r['bangumi_id'] for r in pool}
        self.assertEqual(len(ids), 400)
        self.assertTrue(set(plan['incumbent_ids'] + plan['reference_ids']) <= ids)
        self.assertEqual(len(plan['incumbent_ids']), 96)
        proposal, _ = v2_controls.rerank(pool, vector, {}, options)
        context = {'seeds': [{'tags': frozenset(('音乐',))}], 'preferences': {'tags': []}}
        result, stats = guard.rerank(pool, proposal, plan, context)
        self.assertEqual(len(result), 120)
        self.assertTrue(set(plan['incumbent_ids']) <= {r['bangumi_id'] for r in result})
        self.assertEqual(stats['incumbent_retained'], 96)
        self.assertEqual(result, guard.rerank(pool, proposal, plan, context)[0])

    def test_missing_hydrated_rows_are_dropped_never_resurrected(self):
        rows = [row(1, ('音乐', '成长')), row(2, ('音乐', '日常', '成长'))]
        context = {'seeds': [{'tags': frozenset(('音乐',))}], 'preferences': {'tags': []}}
        result, stats = guard.rerank(rows, rows, {'reference_ids': [1, 2, 99], 'incumbent_ids': [1, 99]}, context)
        self.assertEqual({r['bangumi_id'] for r in result}, {1, 2})
        self.assertEqual(stats['reference_dropped'], 1)
        self.assertEqual(stats['incumbent_validated'], 1)
        self.assertEqual(guard.rerank([], [], {'reference_ids': [99], 'incumbent_ids': [99]}, context)[0], [])
        with self.assertRaises(ValueError):
            guard.rerank(rows, [row(99, ('音乐',))], {'reference_ids': [], 'incumbent_ids': []}, context)
        with self.assertRaises(ValueError):
            guard.protect_prefix(rows + rows, rows, context)


class ProtectionPostgresTests(TransactionTestCase):
    setUp = fixtures.DiagnosticPostgresTests.setUp
    sample = fixtures.DiagnosticPostgresTests.sample

    def inputs(self, owner=None):
        _, catalog = catalog_snapshot()
        context, _ = self.sample(owner)
        index, _ = public_index(catalog)
        return context, index

    def test_all_other_categories_and_preference_modes_are_exact_all_styles(self):
        RecommendationPreference.objects.create(user=self.sparse, tags=['音乐'])
        for owner in (self.owner, self.sparse):
            context, index = self.inputs(owner)
            for category in ('all', 'familiar', 'explore'):
                if owner == self.owner and category == 'explore':
                    continue
                for style in ('balanced', 'close', 'open'):
                    opts = v2_controls.validate_options({'category': category, 'style': style})
                    with readonly_snapshot():
                        baseline, _, _ = execute_v2(owner, opts, index, context=context)
                        result, _, _ = execute_protected_candidate(owner, opts, index, context=context)
                    self.assertEqual(output_digest(baseline), output_digest(result))

    def test_large_live_pool_is_safe_stable_readonly_and_same_sql_budget(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'合成保护{i}',
            tags='音乐,成长' if i % 2 else '音乐,日常,科幻', score=6 + (i % 20) / 10,
            nsfw=False, season='2026-01', status_type='TV') for i in range(25, 625)])
        context, index = self.inputs()
        RecommendationAnime.objects.filter(bangumi_id=30).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=31).update(nsfw=None)
        context['feedback']['hidden_ids'] = {32}
        original = deepcopy(context); records = list(UserAnimeRecord.objects.values())
        for style in ('balanced', 'close', 'open'):
            opts = v2_controls.validate_options({'category': 'explore', 'style': style, 'year_from': 2026})
            with readonly_snapshot(), CaptureQueriesContext(connection) as queries:
                first, _, metadata = execute_protected_candidate(self.owner, opts, index, context=context)
                again, _, _ = execute_protected_candidate(self.owner, opts, index, context=context)
            self.assertEqual(output_digest(first), output_digest(again))
            self.assertEqual(len(first), 120)
            self.assertTrue(all(r['anime'].nsfw is False and r['anime'].bangumi_id not in (30, 31, 32) for r in first))
            self.assertLessEqual(metadata['trace']['candidate_pool'], 400)
            self.assertLessEqual(len(queries), 12)  # Two complete calls, no N+1.
            self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in queries))
            self.assertTrue(metadata['trace']['runtime']['protection']['prefix_floor_passed'])
        self.assertEqual(context, original)
        self.assertEqual(records, list(UserAnimeRecord.objects.values()))
        with self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, serving_enabled=True, exploration_policy_version=PROTECTED_VERSION)

    def test_frozen_comparison_command_uses_isolated_data_and_refuses_drift(self):
        root = Path(settings.BASE_DIR) / '.tools' / ('synthetic-protection-' + uuid4().hex)
        call_command('evaluate_recommendation_deployment', confirm_sitewide_readonly=True, freeze_only=True,
                     output_dir=root / 'freeze', stdout=StringIO())
        call_command('compare_recommendation_exploration', confirm_sitewide_readonly=True,
                     cohort_lock=root / 'freeze/cohort.lock.json', output_dir=root / 'comparison', stdout=StringIO())
        self.assertTrue((root / 'comparison/comparison.json').exists())
        RecommendationAnime.objects.filter(bangumi_id=5).update(score=6)
        with self.assertRaises(CommandError):
            call_command('compare_recommendation_exploration', confirm_sitewide_readonly=True,
                         cohort_lock=root / 'freeze/cohort.lock.json', output_dir=root / 'drift', stdout=StringIO())
        self.assertFalse((root / 'drift').exists())
        with patch('recommendations.engine.user_context') as private_reads:
            with self.assertRaises(CommandError):
                call_command('compare_recommendation_exploration', cohort_lock=root / 'freeze/cohort.lock.json',
                             output_dir=root / 'not-authorized', stdout=StringIO())
            private_reads.assert_not_called()
