"""Dual-view experiment: synthetic local PostgreSQL, never production writes."""
from copy import deepcopy
from io import StringIO
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from rest_framework.exceptions import PermissionDenied

from anime.models import UserAnimeRecord
from recommendations import dual_discovery as dual, engine, v2_controls
from recommendations.discovery_development import frozen_input_status
from recommendations.discovery_candidate import DiscoveryScorer
from recommendations.exploration_candidate import DualExplorationScorer, execute_protected_candidate
from recommendations.execution import execute_v2
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.query_phases import QueryPhases, public_server_probe
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.version_control import sign_artifact, read_artifact
from recommendations.tests import test_discovery_candidate as old_tests
from recommendations.tests import test_unused_validation as fixtures


class DualPrimitiveTests(SimpleTestCase):
    def test_protocol_offline_version_distinct_and_frozen_constants(self):
        self.assertFalse(dual.policy_contract()['serving_enabled'])
        self.assertFalse(dual.policy_contract()['parameter_search'])
        self.assertNotEqual(dual.recipe_descriptor()['recipe_revision'], old_tests.candidate.recipe_descriptor()['recipe_revision'])
        with patch.object(dual, 'RANK_SMOOTHING', 25), self.assertRaises(ValueError):
            dual.policy_contract()

    def test_two_views_reuse_real_evidence_and_close_bonus_once(self):
        helper = old_tests.DiscoveryPrimitiveTests()
        context = helper.context()
        for style in ('balanced', 'close', 'open'):
            scorer = helper.scorer(deepcopy(context), style=style, cls=dual.DualDiscoveryScorer)
            source = helper.scorer(deepcopy(context), style=style, cls=DualExplorationScorer)
            aggregate = helper.scorer(deepcopy(context), style=style, cls=DiscoveryScorer)
            for tags in (frozenset(('音乐', '成长')), frozenset(('科幻', '悬疑'))):
                self.assertEqual(scorer.score(tags, 8), source.score(tags, 8))
                self.assertEqual(scorer.aggregate_value(tags, 8), aggregate.score(tags, 8)[2])
        with patch('recommendations.ranking_v2.REQUEST_MEMO_LIMIT', 0):
            saturated = helper.scorer(deepcopy(context), cls=dual.DualDiscoveryScorer)
            self.assertEqual(saturated.aggregate_value(tags, 8), helper.scorer(deepcopy(context)).score(tags, 8)[2])

    def items(self, count=800):
        return [{'bangumi_id': i, 'tags': frozenset(('音乐',)), 'value': 1 - i / 1000,
            '_aggregate_view_value': i / 1000, 'evidence': {'kind': 'preference'}, 'reason': '真实题材'}
            for i in range(1, count + 1)]

    def test_pool_is_bounded_deduplicated_and_keeps_both_views(self):
        rows = self.items(); before = deepcopy(rows)
        context = {'mode': 'preference_based', 'seeds': [], 'preferences': {'tags': ['音乐']}}
        pool = dual.candidate_pool(rows, {'音乐': 1}, context, {})
        self.assertEqual(len(pool), 400); self.assertEqual(len({r['bangumi_id'] for r in pool}), 400)
        self.assertEqual([r['bangumi_id'] for r in pool[:2]], [1, 800])
        self.assertEqual(pool, dual.candidate_pool(list(reversed(rows)), {'音乐': 1}, context, {}))
        self.assertEqual(rows, before)
        self.assertEqual(dual.candidate_pool(rows[:3], {'音乐': 1}, context, {}).__len__(), 3)
        with self.assertRaises(ValueError):
            dual.candidate_pool(rows, {'音乐': 1}, context, {}, limit=401)

    def test_fusion_current_rows_only_stable_bounded_nonmutating(self):
        rows = self.items(3); before = deepcopy(rows)
        fused = dual.fuse(rows)
        self.assertEqual(fused, list(reversed(dual.fuse(list(reversed(rows))))))
        self.assertTrue(all(0 < r['value'] <= 1 for r in fused))
        self.assertEqual(rows, before)
        for old, new in zip(rows, fused):
            self.assertEqual(new['evidence'], old['evidence']); self.assertEqual(new['reason'], old['reason'])
            self.assertEqual(new['_source_view_value'], old['value'])
        reduced = dual.fuse(rows[:1]); self.assertEqual(reduced[0]['value'], 1)
        self.assertEqual(dual.fuse([]), [])
        with self.assertRaises(ValueError):
            dual.fuse([{**rows[0], '_aggregate_view_value': float('nan')}])

    def test_phase_wrapper_retains_no_sql_or_parameters_and_preserves_result(self):
        probe = QueryPhases()
        def execute(*args):
            return 'same-result'
        self.assertEqual(probe(execute, 'SHOW transaction_read_only', ['SECRET'], False, {}), 'same-result')
        probe(execute, 'SELECT feature_revision FROM recommendations_recommendationanime', [], False, {})
        self.assertEqual(sum(r['count'] for r in probe.summary().values()), 2)
        self.assertNotIn('SECRET', json.dumps(probe.__dict__))
        self.assertNotIn('SELECT', json.dumps(probe.__dict__))

    def test_drift_diagnostics_distinguish_changes_without_exporting_private_handles(self):
        old = {'entries': [{'account': 'PRIVATE_A', 'input': 'SECRET_OLD'},
                           {'account': 'PRIVATE_B', 'input': 'SAME'}],
               'catalog_revision': 'unchanged', 'active_account_count': 8}
        current = [{'account': 'PRIVATE_A', 'input': 'SECRET_NEW'}, {'account': 'PRIVATE_C', 'input': 'NEW'}]
        result = frozen_input_status(old, current, 'unchanged', 8)
        self.assertTrue(result['catalog_unchanged'])
        self.assertEqual(result['changed_original_inputs'], 1)
        self.assertEqual(result['missing_original_eligible_accounts'], 1)
        self.assertEqual(result['new_eligible_accounts'], 1)
        self.assertNotIn('PRIVATE', json.dumps(result)); self.assertNotIn('SECRET', json.dumps(result))


class DualPostgresTests(TransactionTestCase):
    setUp = fixtures.UnusedPostgresTests.setUp
    command = fixtures.UnusedPostgresTests.command
    inputs = old_tests.DiscoveryPostgresTests.inputs

    def test_safety_owner_context_profile_parity_and_no_private_mutations(self):
        context, index = self.inputs(); before = list(UserAnimeRecord.objects.order_by('pk').values())
        original = deepcopy(context)
        RecommendationAnime.objects.filter(bangumi_id=16).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=None)
        with readonly_snapshot():
            for category in ('all', 'familiar', 'explore'):
                baseline = execute_protected_candidate(self.owner, {'category': category}, index, context=context)[0]
                phases = QueryPhases()
                with connection.execute_wrapper(phases):
                    rows, _, meta = dual.execute_candidate(self.owner, {'category': category}, index, context=context)
                repeated = dual.execute_candidate(self.owner, {'category': category}, index, context=context)[0]
                self.assertEqual(output_digest(rows), output_digest(repeated))
                if category != 'all':
                    self.assertEqual(output_digest(rows), output_digest(baseline))
                self.assertEqual(sum(r['count'] for r in phases.summary().values()), 5)
                self.assertTrue(all(r['anime'].nsfw is False for r in rows))
                self.assertFalse(meta['serving_enabled'])
            with self.assertRaises(PermissionDenied):
                dual.execute_candidate(self.sparse, {}, index, context=context)
            server = public_server_probe()
            self.assertGreaterEqual(server['server_execution_ms'], 0)
            self.assertFalse(server['sql_or_parameters_exported'])
        self.assertEqual(context, original)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_no_serving_and_no_writable_probe_or_sparse_changes(self):
        context, index = self.inputs()
        with self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, context=context, discovery_policy_version=dual.VERSION, serving_enabled=True)
        with transaction.atomic(), self.assertRaises(ValueError):
            public_server_probe()
        empty = get_user_model().objects.create_user(username='synthetic-empty-dual')
        for tags in ([], ['音乐']):
            RecommendationPreference.objects.update_or_create(user=empty, defaults={'tags': tags})
            context = engine.user_context(empty)
            with readonly_snapshot():
                old = execute_protected_candidate(empty, {}, index, context=context)[0]
                new = dual.execute_candidate(empty, {}, index, context=context)[0]
            self.assertEqual(output_digest(old), output_digest(new))

    def test_fixed_five_arm_command_never_approves_serving(self):
        self.command(freeze_only=True, output_dir=self.root / 'freeze')
        self.command(cohort_lock=self.root / 'freeze/cohort.lock.json', test_report=self.proof, output_dir=self.root / 'evaluated')
        proof = sign_artifact('unused-holdout-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
            'skipped': 0, 'locks': [6, 4, 6], 'unused_validation_tests': 9,
            'discovery_development_tests': 8, 'dual_discovery_tests': 8})
        path = self.root / 'dual-tests.json'; path.write_text(json.dumps(proof), encoding='utf-8')
        options = dict(confirm_sitewide_readonly=True, dual_view=True, prior_site_lock=self.site,
            prior_history_lock=self.history, consumed_cohort=self.root / 'freeze/cohort.lock.json',
            consumed_result=self.root / 'evaluated/evaluation.json', test_report=path,
            output_dir=self.root / 'dual', stdout=StringIO())
        call_command('compare_recommendation_discovery', **options)
        report = read_artifact(self.root / 'dual/comparison.json', 'discovery-development')
        self.assertEqual(len(report['arms']), 5)
        self.assertEqual(report['policy']['version'], dual.VERSION)
        self.assertFalse(report['approval_issued']); self.assertFalse(report['independent_validation'])
        self.assertTrue(report['public_server_probe']['read_only'])
        with self.assertRaises(CommandError):
            call_command('compare_recommendation_discovery', **options)

    def test_current_timing_probe_has_no_quality_claim_and_requires_authorization(self):
        path = self.root / 'timing.json'
        with self.assertRaises(CommandError):
            call_command('profile_recommendation_discovery', prior_site_lock=self.site, output=path, stdout=StringIO())
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        call_command('profile_recommendation_discovery', confirm_sitewide_readonly=True,
            prior_site_lock=self.site, output=path, stdout=StringIO())
        result = read_artifact(path, 'discovery-query-profile')
        self.assertFalse(result['quality_evaluated']); self.assertFalse(result['approval_issued'])
        self.assertEqual(result['accounts_read'], 1); self.assertEqual(result['targets_selected'], 0)
        self.assertEqual(len(result['arms']), 5)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        self.assertNotIn('SYNTHETIC_REVIEW_PRESERVED', path.read_text(encoding='utf-8'))
