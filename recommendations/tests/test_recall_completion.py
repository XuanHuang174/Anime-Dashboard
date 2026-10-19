"""One-variable trial and read-only contract on synthetic isolated PostgreSQL."""
from copy import deepcopy
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.exceptions import PermissionDenied

from anime.models import UserAnimeRecord
from recommendations import browsing, engine, recall_completion as candidate
from recommendations import completion_development as trial
from recommendations.cold_public import SeededPublicReader
from recommendations.execution import execute_v2
from recommendations.exploration_candidate import PROTECTED_VERSION
from recommendations.models import RecommendationAnime
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.version_control import sign_artifact, read_artifact
from recommendations.tests import test_unused_validation as fixtures


class CompletionPrimitiveTests(SimpleTestCase):
    def rows(self):
        return [{'bangumi_id': i, 'tags': frozenset(('音乐', '成长')) if i < 300 else frozenset(('音乐',)),
            'value': 1 - i / 1000, 'evidence': {'kind': 'preference'}, 'reason': 'actual-shared-topic'} for i in range(1, 801)]

    def test_protocol_has_stop_rule_and_cannot_silently_tune(self):
        self.assertEqual(candidate.policy_contract()['attempts'], 1)
        self.assertFalse(candidate.recipe_descriptor()['serving_enabled'])
        with patch.object(candidate, 'VERSION', 'unreviewed'), self.assertRaises(ValueError):
            candidate.policy_contract()

    def test_only_unused_slots_filled_all_originals_scores_evidence_preserved(self):
        rows = self.rows(); original = deepcopy(rows); pool = rows[:250]
        result = candidate.complete_pool(rows, pool, {'音乐': 1}, {}, {'mode': 'personalized'}, {'category': 'all'})
        self.assertEqual(len(result), 400)
        ids = {row['bangumi_id'] for row in result}
        self.assertTrue({r['bangumi_id'] for r in pool} <= ids)
        self.assertIn(300, ids); self.assertNotIn(251, ids)
        self.assertEqual(len(ids), 400); self.assertEqual(rows, original)
        self.assertTrue(all(row == original[row['bangumi_id'] - 1] for row in result))
        self.assertEqual(result, candidate.complete_pool(list(reversed(rows)), list(reversed(pool)), {'音乐': 1}, {},
            {'mode': 'personalized'}, {'category': 'all'}))

    def test_no_unrelated_unevidenced_or_nonfinite_fill_and_no_full_pool_change(self):
        rows = self.rows()
        bad = [{**rows[-1], 'bangumi_id': 900, 'tags': frozenset(('悬疑',))},
            {**rows[-1], 'bangumi_id': 901, 'evidence': {}}, {**rows[-1], 'bangumi_id': 902, 'value': float('nan')}]
        result = candidate.complete_pool([*rows[:5], *bad], rows[:1], {'音乐': 1}, {}, {'mode': 'personalized'}, {'category': 'all'})
        self.assertEqual(len(result), 5)
        pool = rows[:400]
        self.assertIs(candidate.complete_pool(rows, pool, {}, {}, {'mode': 'personalized'}, {'category': 'all'}), pool)
        with self.assertRaises(ValueError):
            candidate.complete_pool(rows, [], {}, {}, {}, {}, limit=401)
        with self.assertRaises(ValueError):
            candidate.complete_pool(rows, rows[:1] * 2, {'音乐': 1}, {}, {'mode': 'personalized'}, {'category': 'all'})

    def test_relatedness_has_no_novelty_or_public_quality_blend(self):
        self.assertEqual(candidate.relatedness(frozenset(('音乐',)), {'音乐': 1}, {}), 1)
        self.assertAlmostEqual(candidate.relatedness(frozenset(('音乐', '成长')), {'音乐': 1}, {}), 2 ** -.5)
        self.assertEqual(candidate.relatedness(frozenset(('成长',)), {'音乐': 1}, {}), 0)

    def test_cold_and_other_categories_are_exact_pool_identity(self):
        rows = self.rows(); pool = rows[:1]
        for mode, category in (('preference_based', 'all'), ('cold_start', 'all'), ('personalized', 'familiar'), ('personalized', 'explore')):
            self.assertIs(candidate.complete_pool(rows, pool, {}, {}, {'mode': mode}, {'category': category}), pool)

    def test_more_candidates_without_more_hits_is_stop_not_success(self):
        metrics = {'topic_overlap_proxy_mean': .8, 'pairwise_genre_jaccard_mean': .1, 'duplicate_genre_set_ratio': .1}
        sample = {'paired_targets': 4, 'safety_errors': 0, 'evidence_errors': 0,
            'arms': {arm: {'categories': {'all': metrics}} for arm in trial.ARMS},
            'holdout': {arm: {cat: {'hit_at_k': .25 if cat == 'all' else 0} for cat in trial.CATEGORIES} for arm in trial.ARMS},
            'destinations': {arm: {'all': {'returned': {'positions': [1, None, None, None]}}} for arm in trial.ARMS}}
        self.assertEqual(trial.summary([sample])['decision'], 'stop-current-v2-route-keep-v1')
        self.assertFalse(trial.summary([sample])['quality_gain_demonstrated'])
        sample['holdout']['candidate_v2']['all']['hit_at_k'] = .5
        sample['destinations']['candidate_v2']['all']['returned']['positions'] = [1, 2, None, None]
        self.assertTrue(trial.summary([sample])['quality_gain_demonstrated'])
        sample['destinations']['candidate_v2']['all']['returned']['positions'][0] = None
        self.assertFalse(trial.summary([sample])['quality_gain_demonstrated'])


class CompletionPostgresTests(TransactionTestCase):
    setUp = fixtures.UnusedPostgresTests.setUp
    command = fixtures.UnusedPostgresTests.command

    def inputs(self):
        context = engine.user_context(self.owner)
        with override_settings(RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=False):
            rows, index = browsing.public_browse_index(browsing.catalog_revision(), {})
        return context, rows, index

    def test_safety_owner_feedback_and_no_private_mutation_with_same_evidence(self):
        RecommendationAnime.objects.filter(bangumi_id=16).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=None)
        context, public, index = self.inputs(); before = list(UserAnimeRecord.objects.order_by('pk').values())
        context['feedback']['hidden_ids'] = {18}; original = deepcopy(context)
        with readonly_snapshot():
            result = trial.evaluate(self.owner, context, index, [7, 8, 9, 10], SeededPublicReader(public))
            self.assertEqual(result['evidence_errors'], 0); self.assertEqual(result['safety_errors'], 0)
            with self.assertRaises(PermissionDenied):
                execute_v2(self.sparse, {}, index, context=context, exploration_policy_version=PROTECTED_VERSION,
                    recall_completion_version=candidate.VERSION)
            rows = trial.run(self.owner, context, index, 'all', 'candidate_v2', reader=SeededPublicReader(public))[0]
            self.assertFalse({16, 17, 18} & {r['anime'].bangumi_id for r in rows})
        self.assertEqual(context, original)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_no_serving_or_writable_snapshot_and_cold_exactness(self):
        context, public, index = self.inputs()
        with self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, context=context, serving_enabled=True,
                exploration_policy_version=PROTECTED_VERSION, recall_completion_version=candidate.VERSION)
        with transaction.atomic(), self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, context=context, exploration_policy_version=PROTECTED_VERSION,
                recall_completion_version=candidate.VERSION)
        for mode, tags in (('cold_start', []), ('preference_based', ['音乐'])):
            context.update(mode=mode, seeds=[], preferences={'version': 0, 'tags': tags})
            with readonly_snapshot():
                old = trial.run(self.owner, context, index, 'all', 'protected_v2', reader=SeededPublicReader(public))[0]
                new = trial.run(self.owner, context, index, 'all', 'candidate_v2', reader=SeededPublicReader(public))[0]
                self.assertEqual(output_digest(old), output_digest(new))

    def test_large_real_pipeline_retains_old_pool_fills_only_gaps_and_rechecks_safety(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=1000 + i,
            title_zh=f'独立合成{i}', tags='音乐,日常' if i < 100 else '音乐,成长', score=7, nsfw=False)
            for i in range(600)])
        context, public, index = self.inputs(); original = deepcopy(context)
        with readonly_snapshot():
            baseline = trial.CompletionObserver([], context, index['weights'])
            current = trial.CompletionObserver([], context, index['weights'])
            old, _ = trial.run(self.owner, context, index, 'all', 'protected_v2', baseline, reader=SeededPublicReader(public))
            new, _ = trial.run(self.owner, context, index, 'all', 'candidate_v2', current, reader=SeededPublicReader(public))
            self.assertLess(baseline.stages['pool']['count'], 400)
            self.assertEqual(current.stages['pool']['count'], 400)
            self.assertTrue(all(r['anime'].nsfw is False for r in new))
            old_rows = {r['anime'].bangumi_id: r for r in old}
            for row in new:
                if row['anime'].bangumi_id in old_rows:
                    before = old_rows[row['anime'].bangumi_id]
                    self.assertEqual((row['value'], row['evidence'], row['reason']),
                                     (before['value'], before['evidence'], before['reason']))
        self.assertEqual(context, original)

    def test_fixed_command_freeze_then_one_trial_and_no_authorization_no_run(self):
        self.command(freeze_only=True, output_dir=self.root / 'old-freeze')
        self.command(cohort_lock=self.root / 'old-freeze/cohort.lock.json', test_report=self.proof, output_dir=self.root / 'old-result')
        proof = sign_artifact('unused-holdout-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
            'skipped': 0, 'locks': [6, 4, 6], 'unused_validation_tests': 9, 'recall_completion_tests': 8})
        path = self.root / 'completion-tests.json'; path.write_text(json.dumps(proof), encoding='utf-8')
        options = dict(consumed_cohort=self.root / 'old-freeze/cohort.lock.json',
            consumed_result=self.root / 'old-result/evaluation.json', test_report=path, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('compare_recommendation_completion', **options, freeze_only=True, output_dir=self.root / 'denied')
        options['confirm_sitewide_readonly'] = True
        call_command('compare_recommendation_completion', **options, freeze_only=True, output_dir=self.root / 'new-freeze')
        frozen = read_artifact(self.root / 'new-freeze/cohort.lock.json', 'recall-completion-cohort')
        self.assertFalse(frozen['ranking_performed'])
        # File side effects isolated as well as DB; no deletion of old markers.
        real_path = __import__('recommendations.unused_validation', fromlist=['private_path']).private_path
        with patch('recommendations.unused_validation.private_path', side_effect=lambda p:
                   self.root / 'attempts' / p.name if 'recall-completion-attempts' in str(p) else real_path(p)):
            call_command('compare_recommendation_completion', **options, cohort_lock=self.root / 'new-freeze/cohort.lock.json',
                output_dir=self.root / 'new-result')
            report = read_artifact(self.root / 'new-result/comparison.json', 'recall-completion-comparison')
            self.assertTrue(report['ranking_performed']); self.assertFalse(report['approval_issued'])
            self.assertFalse(report['summary']['serving_approval'])
            with self.assertRaises(CommandError):
                call_command('compare_recommendation_completion', **options, cohort_lock=self.root / 'new-freeze/cohort.lock.json',
                    output_dir=self.root / 'no-second-trial')
