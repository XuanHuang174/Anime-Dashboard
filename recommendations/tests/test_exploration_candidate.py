"""Mechanism tests use local isolated PostgreSQL; no real account writes."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from anime.models import UserAnimeRecord
from recommendations import engine, v2_controls
from recommendations.execution import execute_v2, recipe_descriptor as reference_recipe
from recommendations.exploration_candidate import (ExplorationScorer, VERSION, WEIGHTS,
    execute_candidate, execute_dual_candidate, DUAL_VERSION, dual_pool, recipe_descriptor, policy_contract)
from recommendations.exploration_audit import read_frozen_inputs, verify_inputs
from recommendations.deployment_evaluation import configuration, evaluate_sample
from recommendations.diagnostics import public_index
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.version_control import sign_artifact, verify_artifact
from recommendations.tests import test_diagnostics as fixtures
from recommendations.models import RecommendationAnime, RecommendationPreference


class ExplorationContractTests(SimpleTestCase):
    def test_predeclared_weights_and_distinct_offline_recipe(self):
        self.assertEqual(WEIGHTS, (.5, .4, .1))
        self.assertFalse(policy_contract()['parameter_search'])
        reference, candidate = reference_recipe(), recipe_descriptor()
        self.assertNotEqual(reference['recipe_revision'], candidate['recipe_revision'])
        self.assertNotIn('exploration_policy_version', reference)
        self.assertEqual(candidate['exploration_policy_version'], VERSION)
        self.assertFalse(candidate['serving_enabled'])
        with patch('recommendations.exploration_candidate.WEIGHTS', (.4, .5, .1)):
            with self.assertRaises(ValueError):
                recipe_descriptor()

    def test_historical_input_import_does_not_relax_release_approval_validation(self):
        lock = sign_artifact('browse-cohort', {'recipe': reference_recipe(), 'configuration': configuration(),
            'entries': [], 'catalog_revision': 'fixed', 'active_account_count': 0})
        path = Path(settings.BASE_DIR) / '.tools/virtual-original-lock.json'
        # Simulate a later source fingerprint, without rewriting the old receipt.
        with patch('recommendations.version_control.release_descriptor', return_value={'fingerprint': 'later'}):
            with self.assertRaises(ValueError):
                verify_artifact(lock, 'browse-cohort')
            with patch.object(Path, 'read_bytes', return_value=json.dumps(lock).encode()):
                self.assertEqual(read_frozen_inputs(path), lock)
        corrupt = {**lock, 'entries': ['tampered']}
        with patch.object(Path, 'read_bytes', return_value=json.dumps(corrupt).encode()):
            with self.assertRaises(ValueError):
                read_frozen_inputs(path)
        wrong_kind = sign_artifact('browse-tests', {k: v for k, v in lock.items() if k not in ('signature', 'kind')})
        with patch.object(Path, 'read_bytes', return_value=json.dumps(wrong_kind).encode()):
            with self.assertRaises(ValueError):
                read_frozen_inputs(path)

    def test_input_catalog_and_target_drift_fail_closed(self):
        lock = {'entries': [{'target': 'fixed'}], 'catalog_revision': 'r', 'active_account_count': 5}
        for entries, revision, count in (([], 'r', 5), (lock['entries'], 'new', 5), (lock['entries'], 'r', 6)):
            with self.assertRaises(ValueError):
                verify_inputs(lock, entries=entries, catalog_revision=revision, active_account_count=count)
        verify_inputs(lock, entries=lock['entries'], catalog_revision='r', active_account_count=5)

    def test_dual_recipe_is_separate_and_pool_retains_both_cores_and_pattern_reserves(self):
        from recommendations import ranking_v2
        self.assertNotEqual(recipe_descriptor()['recipe_revision'], recipe_descriptor(DUAL_VERSION)['recipe_revision'])
        self.assertFalse(policy_contract(DUAL_VERSION)['parameter_search'])
        items = [{'bangumi_id': n, 'tags': frozenset(('音乐', f'pattern{n % 23}')),
                  'value': 1 - n / 10000, '_recall_reference_value': n / 10000,
                  'evidence': {'kind': 'synthetic'}, 'reason': 'actual association'} for n in range(1, 801)]
        original = deepcopy(items); vector = {'音乐': 1}
        rows = dual_pool(items, vector); identities = {r['bangumi_id'] for r in rows}
        self.assertEqual(len(rows), 400); self.assertEqual(len(identities), 400)
        for view in (items, [{**r, 'value': r['_recall_reference_value']} for r in items]):
            core = sorted(view, key=ranking_v2._order)[:200]
            reserve = [r for r in ranking_v2.balanced_candidate_pool(view, vector)
                       if r['bangumi_id'] not in {r['bangumi_id'] for r in core}]
            self.assertTrue({r['bangumi_id'] for r in core[:100] + reserve[:100]} <= identities)
        self.assertEqual(items, original)
        self.assertEqual(rows, dual_pool(list(reversed(items)), vector))
        self.assertTrue(all(r in items for r in rows))  # Original score/evidence, no reference-score replacement.
        for limit in (0, 1, 3, 24):
            self.assertEqual(len(dual_pool(items, vector, limit=limit)), limit)
        with self.assertRaises(ValueError):
            dual_pool([{k: v for k, v in row.items() if k != '_recall_reference_value'} for row in items], vector)


class ExplorationPostgresTests(TransactionTestCase):
    setUp = fixtures.DiagnosticPostgresTests.setUp
    sample = fixtures.DiagnosticPostgresTests.sample

    def inputs(self, owner=None):
        _, catalog = catalog_snapshot()
        context, _ = self.sample(owner)
        index, _ = public_index(catalog)
        return context, index

    def test_nonexplore_and_preference_modes_exact_reference_all_styles(self):
        RecommendationPreference.objects.create(user=self.sparse, tags=['音乐'])
        for owner in (self.owner, self.sparse):
            context, index = self.inputs(owner)
            for category in ('all', 'familiar', 'explore'):
                if owner == self.owner and category == 'explore':
                    continue
                for style in ('balanced', 'close', 'open'):
                    options = v2_controls.validate_options({'category': category, 'style': style})
                    with readonly_snapshot():
                        before, _, _ = execute_v2(owner, options, index, context=context)
                        for executor in (execute_candidate, execute_dual_candidate):
                            after, _, _ = executor(owner, options, index, context=context)
                            self.assertEqual(output_digest(before), output_digest(after))

    def test_formula_uses_global_association_and_novelty_preserves_actual_evidence(self):
        context, index = self.inputs()
        vector = engine.taste_vector(context, deterministic=True)
        for style in ('balanced', 'close', 'open'):
            options = v2_controls.validate_options({'category': 'explore', 'style': style})
            scorer = ExplorationScorer(context, options, index['weights'], vector, index['taxonomy_version'])
            tags = frozenset(('音乐', '成长'))
            expected = engine.candidate_score(tags, 7, vector, index['weights'], 'explore', deterministic=True)
            if style == 'close':
                expected += v2_controls.CLOSE_RELATED_BONUS * scorer._related(tags)
            evidence, reason, score = scorer.score(tags, 7)
            original = v2_controls.ControlledScorer(context, options, index['weights'], vector, index['taxonomy_version']).score(tags, 7)
            self.assertAlmostEqual(score, expected, places=14)
            self.assertEqual((evidence, reason), original[:2])
            self.assertIsNone(scorer.score(frozenset(('音乐', '日常')), 7))  # No genuinely new genre.
            self.assertIsNone(scorer.score(frozenset(('科幻',)), 7))  # No actual association/evidence.

    def test_repeatable_safe_filtered_and_zero_private_side_effects(self):
        RecommendationAnime.objects.filter(bangumi_id=16).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=None)
        RecommendationAnime.objects.filter(bangumi_id__gte=18).update(season='2026-01', status_type='TV')
        context, index = self.inputs(); original = deepcopy(context)
        records = list(UserAnimeRecord.objects.order_by('pk').values())
        options = v2_controls.validate_options({'category': 'explore', 'type': 'TV', 'year_from': 2026})
        with readonly_snapshot(), CaptureQueriesContext(connection) as queries:
            first, _, recipe = execute_dual_candidate(self.owner, options, index, context=context)
            second, _, _ = execute_dual_candidate(self.owner, options, index, context=context)
        self.assertTrue(first)
        self.assertEqual(output_digest(first), output_digest(second))
        self.assertTrue(all(row['anime'].nsfw is False and row['anime'].bangumi_id >= 18 for row in first))
        self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in queries))
        self.assertEqual(records, list(UserAnimeRecord.objects.order_by('pk').values()))
        self.assertEqual(context, original)
        self.assertEqual(recipe['trace']['candidate_pool'], len(first))

    def test_auth_owner_writable_transaction_and_serving_are_rejected(self):
        context, index = self.inputs()
        with self.assertRaises(NotAuthenticated):
            execute_candidate(AnonymousUser(), {}, index)
        with readonly_snapshot():
            with self.assertRaises(PermissionDenied):
                execute_candidate(self.sparse, {}, index, context=context)
        with self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, context=context, serving_enabled=True, exploration_policy_version=VERSION)
        with self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, context=context, serving_enabled=True, exploration_policy_version=DUAL_VERSION)
        from django.db import transaction
        with transaction.atomic():
            with self.assertRaises(engine.RecommendationUnavailable):
                execute_candidate(self.owner, {}, index, context=context)

    def test_three_arm_evaluation_keeps_original_four_targets_and_other_categories(self):
        context, index = self.inputs()
        with readonly_snapshot():
            report = evaluate_sample(self.owner, context, index, candidate_executor=execute_dual_candidate)
        self.assertEqual(set(report['arms']), {'current_v1', 'current_v2', 'candidate_v2'})
        self.assertEqual(report['passes']['ranking']['paired_targets'], 4)
        self.assertEqual(report['safety_errors'], 0)
        self.assertEqual(report['holdout_evidence_errors'], 0)
        self.assertTrue(report['snapshot_isolation'])
