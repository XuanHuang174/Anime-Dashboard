"""Development ablation on synthetic, independent PostgreSQL only."""
from copy import deepcopy
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied

from anime.models import UserAnimeRecord
from recommendations import engine, discovery_candidate as candidate, discovery_development as development
from recommendations import unused_validation as validation, v2_controls
from recommendations.execution import execute_v2, recipe_descriptor as reference_recipe
from recommendations.exploration_candidate import DualExplorationScorer, execute_protected_candidate
from recommendations.quality_readonly import readonly_snapshot
from recommendations.quality_audit import catalog_snapshot
from recommendations.diagnostics import public_index
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.recall_diagnostics import output_digest
from recommendations.version_control import read_artifact, sign_artifact
from recommendations.tests import test_unused_validation as unused_tests


class DiscoveryPrimitiveTests(SimpleTestCase):
    def context(self):
        sets = [('音乐', '日常')] * 9 + [('科幻', '悬疑')]
        return {'mode': 'personalized', 'preferences': {'tags': []}, 'seeds': [
            {'bangumi_id': i, 'title': 'synthetic', 'score': 9, 'tags': frozenset(tags),
             'feature_sources': {tag: ['catalog'] for tag in tags}} for i, tags in enumerate(sets, 1)]}

    def scorer(self, context, category='all', style='balanced', cls=candidate.DiscoveryScorer):
        return cls(context, v2_controls.validate_options({'category': category, 'style': style}), {},
                   engine.taste_vector(context, deterministic=True), None)

    def test_policy_is_separate_offline_and_no_weight_search(self):
        body = candidate.policy_contract()
        self.assertEqual(body['ablation'], 'base-score-only')
        self.assertFalse(body['serving_enabled']); self.assertFalse(body['parameter_search'])
        self.assertNotEqual(candidate.recipe_descriptor()['recipe_revision'], reference_recipe()['recipe_revision'])
        with patch.object(candidate, 'WEIGHTS', (.8, .15, .05)):
            with self.assertRaises(ValueError):
                candidate.policy_contract()

    def test_common_exact_set_cannot_override_stronger_aggregate_taste(self):
        context = self.context()
        popular = frozenset(('音乐', '日常', '成长'))
        rare_exact = frozenset(('科幻', '悬疑'))
        old = self.scorer(deepcopy(context), cls=DualExplorationScorer)
        new = self.scorer(deepcopy(context))
        # Nearest-set dominance can rank a rare exact match over the user's
        # overwhelmingly supported aggregate preference. No target IDs involved.
        self.assertGreater(old.score(rare_exact, 7)[2], old.score(popular, 7)[2])
        self.assertGreater(new.score(popular, 7)[2], new.score(rare_exact, 7)[2])
        for tags in (popular, rare_exact):
            self.assertEqual(new.score(tags, 7)[:2], old.score(tags, 7)[:2])
            self.assertEqual(new.score(tags, 7)[2], engine.candidate_score(tags, 7, new.vector, {}, 'all', deterministic=True))
        self.assertIsNone(new.score(frozenset(('运动',)), 7))

    def test_close_bonus_exactly_once_and_saturated_memo_same_arithmetic(self):
        context, tags = self.context(), frozenset(('音乐', '成长'))
        balanced = self.scorer(deepcopy(context)); close = self.scorer(deepcopy(context), style='close')
        expected = balanced.score(tags, 8)[2] + v2_controls.CLOSE_RELATED_BONUS * close._related(tags)
        self.assertEqual(close.score(tags, 8)[2], expected)
        with patch('recommendations.ranking_v2.REQUEST_MEMO_LIMIT', 0):
            saturated = self.scorer(deepcopy(context), style='close')
            self.assertEqual(saturated.score(tags, 8), close.score(tags, 8))
            self.assertEqual(saturated.patterns, {})

    def test_preference_only_and_other_categories_identical_to_previous(self):
        for mode, category in (('personalized', 'familiar'), ('personalized', 'explore'), ('preference_based', 'all')):
            context = self.context(); context['mode'] = mode
            if mode == 'preference_based':
                context['seeds'] = []; context['preferences']['tags'] = ['音乐']
            for style in ('balanced', 'close', 'open'):
                new = self.scorer(deepcopy(context), category, style)
                old = self.scorer(deepcopy(context), category, style, DualExplorationScorer)
                for tags in (frozenset(('音乐', '成长')), frozenset(('科幻', '日常'))):
                    self.assertEqual(new.score(tags, 7), old.score(tags, 7))


class DiscoveryPostgresTests(TransactionTestCase):
    setUp = unused_tests.UnusedPostgresTests.setUp
    command = unused_tests.UnusedPostgresTests.command

    def inputs(self):
        context = engine.user_context(self.owner)
        _, rows = catalog_snapshot(); index, _ = public_index(rows)
        return context, index

    def test_own_user_safety_readonly_and_other_category_parity(self):
        context, index = self.inputs(); original = deepcopy(context)
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        RecommendationAnime.objects.filter(bangumi_id=16).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=None)
        for category in ('all', 'familiar', 'explore'):
            with readonly_snapshot(), CaptureQueriesContext(connection) as queries:
                old, _, _ = execute_protected_candidate(self.owner, {'category': category}, index, context=context)
                rows, _, meta = candidate.execute_candidate(self.owner, {'category': category}, index, context=context)
                repeated, _, _ = candidate.execute_candidate(self.owner, {'category': category}, index, context=context)
                with self.assertRaises(PermissionDenied):
                    candidate.execute_candidate(self.sparse, {'category': category}, index, context=context)
            self.assertFalse(meta['serving_enabled'])
            self.assertEqual(output_digest(rows), output_digest(repeated))
            self.assertTrue(all(row['anime'].nsfw is False for row in rows))
            if category != 'all':
                self.assertEqual(output_digest(rows), output_digest(old))
            self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('UPDATE ', 'DELETE ', 'INSERT ')) for q in queries))
        self.assertEqual(context, original)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_candidate_cannot_serve_or_bypass_expected_protected_dependency(self):
        context, index = self.inputs()
        for extra in ({'serving_enabled': True}, {'exploration_policy_version': 'unknown'}, {}):
            with self.assertRaises(engine.RecommendationUnavailable):
                execute_v2(self.owner, {}, index, context=context, discovery_policy_version=candidate.VERSION, **extra)
        empty_owner = get_user_model().objects.create_user(username='synthetic-empty-discovery')
        for tags in ([], ['音乐']):
            RecommendationPreference.objects.update_or_create(user=empty_owner, defaults={'tags': tags})
            sparse = engine.user_context(empty_owner)
            with readonly_snapshot():
                old = execute_protected_candidate(empty_owner, {}, index, context=sparse)[0]
                new = candidate.execute_candidate(empty_owner, {}, index, context=sparse)[0]
            self.assertEqual(output_digest(old), output_digest(new))

    def test_development_requires_consumed_archive_and_never_issues_approval(self):
        self.command(freeze_only=True, output_dir=self.root / 'freeze')
        with self.assertRaises(ValueError):
            development.consumed_inputs(self.root / 'freeze/cohort.lock.json', self.proof)
        self.command(cohort_lock=self.root / 'freeze/cohort.lock.json', test_report=self.proof, output_dir=self.root / 'evaluation')
        proof = sign_artifact('unused-holdout-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
            'skipped': 0, 'locks': [6, 4, 6], 'unused_validation_tests': 9, 'discovery_development_tests': 8})
        new_proof = self.root / 'development-tests.json'; new_proof.write_text(json.dumps(proof), encoding='utf-8')
        options = {'confirm_sitewide_readonly': True, 'prior_site_lock': self.site, 'prior_history_lock': self.history,
            'consumed_cohort': self.root / 'freeze/cohort.lock.json', 'consumed_result': self.root / 'evaluation/evaluation.json',
            'test_report': new_proof, 'output_dir': self.root / 'development', 'stdout': StringIO()}
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        call_command('compare_recommendation_discovery', **options)
        report = read_artifact(self.root / 'development/comparison.json', 'discovery-development')
        self.assertFalse(report['independent_validation']); self.assertFalse(report['approval_issued'])
        self.assertTrue(report['complete']); self.assertEqual(report['samples'][0]['paired_targets'], 4)
        self.assertEqual(report['samples'][0]['safety_errors'], 0); self.assertEqual(report['samples'][0]['evidence_errors'], 0)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        with self.assertRaises(CommandError):
            call_command('compare_recommendation_discovery', **options)
        RecommendationAnime.objects.filter(bangumi_id=6).update(score=6)
        options['output_dir'] = self.root / 'drift'
        with patch.object(development, 'evaluate') as ranking:
            with self.assertRaises(CommandError):
                call_command('compare_recommendation_discovery', **options)
            ranking.assert_not_called()

    def test_archive_tamper_and_missing_authority_stop_before_private_reads(self):
        data = sign_artifact('unused-holdout-cohort', {'configuration': 'synthetic'})
        path = self.root / 'tampered.json'; data['configuration'] = 'changed'
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            development.read_archive(path, 'unused-holdout-cohort')
        with patch.object(engine, 'user_context') as reads:
            with self.assertRaises(CommandError):
                call_command('compare_recommendation_discovery', prior_site_lock=self.site, prior_history_lock=self.history,
                    consumed_cohort=path, consumed_result=path, test_report=self.proof,
                    output_dir=self.root / 'unauthorized', stdout=StringIO())
            reads.assert_not_called()
