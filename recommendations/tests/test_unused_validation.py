"""New-target protocol tests; local synthetic PostgreSQL only."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied

from anime.models import Anime, UserAnimeRecord
from recommendations import engine, browsing, unused_validation as validation
from recommendations.deployment_evaluation import cohort_entry, configuration as deployment_config
from recommendations.execution import recipe_descriptor as reference_recipe
from recommendations.exploration_candidate import recipe_descriptor, PROTECTED_VERSION
from recommendations.models import RecommendationAnime
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.diagnostics import public_index
from recommendations.recall_diagnostics import freeze_cohort, load_config
from recommendations.version_control import read_artifact, sign_artifact
from recommendations.tests import test_diagnostics as fixtures


class UnusedProtocolTests(SimpleTestCase):
    def context(self):
        return {'_owner_id': 42, 'taxonomy_version': 'genres.v4', 'mode': 'personalized',
            'preferences': {'version': 1, 'tags': ['音乐']}, 'feedback': {'version': 0, 'hidden_ids': {6}},
            'seeds': [{'bangumi_id': i, 'score': 9, 'tags': frozenset(('音乐',)),
                       'feature_sources': {'音乐': ['catalog']}} for i in range(1, 15)],
            'owned': {i: 'completed' for i in range(1, 15)}, 'profile': {'hidden_target_labels': 'discard'},
            'explanation_seeds': [{'bangumi_id': 7}], '_ranking_trace': {'secret_old_results': True}}

    def test_preregistered_protocol_rejects_tuning(self):
        config = validation.configuration()
        self.assertFalse(config['strict_unseen_user_blind_test'])
        self.assertEqual(config['targets_per_account'], 4)
        self.assertEqual(config['split'], 'joint-target-removal')
        with patch.object(Path, 'read_text', return_value=json.dumps({**config, 'targets_per_account': 5})):
            with self.assertRaises(ValueError):
                validation.configuration()

    def test_unused_selection_is_stable_excludes_prefix_prior_pairs_and_legacy_account(self):
        from recommendations.evidence import _token
        context = self.context(); history = {'samples': [{'sets': {'expanded': []}}]}
        site = {'entries': [{'targets': [_token('recommendations.deployment.target.v1', [42, 9])]}]}
        targets, entry = validation.select(context, site, history)
        self.assertEqual(entry['status'], 'selected')
        self.assertFalse(set(targets) & {1, 2, 3, 4, 5, 9})
        clone = deepcopy(context); clone['seeds'].reverse()
        self.assertEqual(targets, validation.select(clone, site, history)[0])
        frozen = {'entries': [entry], 'release_fingerprint': 'before'}
        self.assertEqual(validation.attempt_path(frozen), validation.attempt_path({**frozen, 'release_fingerprint': 'after'}))
        history['samples'][0]['sets']['expanded'] = [_token(validation.COHORT_CONTRACT + '.target', [42, 7])]
        self.assertEqual(validation.select(context, site, history)[1]['status'], 'legacy_account_excluded')
        context['seeds'] = context['seeds'][:7]
        self.assertFalse(validation.select(context, site, {'samples': []})[0])

    def test_joint_split_removes_all_labels_aliases_and_cached_summaries_without_mutation(self):
        context = self.context(); original = deepcopy(context)
        training = validation.joint_training(context, [7, 8, 9, 10])
        self.assertFalse({7, 8, 9, 10} & {s['bangumi_id'] for s in training['seeds']})
        self.assertFalse({7, 8, 9, 10} & set(training['owned']))
        self.assertNotIn('profile', training); self.assertNotIn('explanation_seeds', training)
        self.assertNotIn('_ranking_trace', training)
        self.assertEqual(context, original)
        self.assertEqual(training['preferences'], context['preferences'])
        self.assertEqual(training['feedback'], context['feedback'])
        with self.assertRaises(ValueError):
            validation.joint_training(context, [1, 1, 2, 3])

    def test_denominator_and_observer_keep_ineligible_targets_without_exporting_identity(self):
        rows = [{'anime': SimpleNamespace(bangumi_id=500)}]
        metrics = validation.holdout_metrics(rows, [500, 600, 700, 800])
        self.assertEqual(metrics['hit_at_k'], .25)
        self.assertEqual(metrics['mrr_at_k'], .25)
        observer = validation.Observer([500, 800]); observer.stage('pool', [600, 500])
        self.assertEqual(observer.stages['pool'], {'count': 2, 'positions': [2, None]})
        self.assertNotIn('500', json.dumps(observer.stages))

    def test_zero_hits_and_missing_gates_cannot_be_success(self):
        categories = ('all', 'familiar', 'explore')
        sample = {'paired_targets': 4, 'safety_errors': 0,
            'arms': {arm: {} for arm in validation.ARMS},
            'holdout': {arm: {cat: {'hit_at_k': 0} for cat in categories} for arm in validation.ARMS}}
        gates = {key: {'status': 'pass', 'details': []} for key in ('safety', 'evidence', 'relevance', 'categories', 'performance', 'rollback')}
        with patch.object(validation, 'gate_report', return_value=gates):
            result = validation.verdict([sample, sample], True)
            self.assertEqual(result['status'], 'insufficient_evidence')
            self.assertIn('zero_holdout_hits_is_not_quality_success', result['insufficient_reasons'])
            sample['holdout']['candidate_v2']['all']['hit_at_k'] = .25
            gates['categories']['status'] = 'insufficient_evidence'
            self.assertEqual(validation.verdict([sample, sample], True)['status'], 'insufficient_evidence')


class UnusedPostgresTests(TransactionTestCase):
    def setUp(self):
        fixtures.DiagnosticPostgresTests.setUp(self)
        for owner, identities in ((self.owner, range(5, 15)), (self.sparse, range(25, 39))):
            for i in identities:
                RecommendationAnime.objects.get_or_create(bangumi_id=i, defaults={'title_zh': f'合成验证{i}',
                    'score': 7, 'tags': '音乐,日常', 'nsfw': False})
                anime = Anime.objects.create(bgm_id=i, chinese_title=f'合成验证{i}', base_tags=['音乐', '日常'])
                UserAnimeRecord.objects.create(user=owner, anime=anime, watch_status='completed', personal_score=9,
                                               review='SYNTHETIC_REVIEW_PRESERVED')
        self.root = Path(settings.BASE_DIR) / '.tools' / ('synthetic-unused-' + uuid4().hex)
        self.root.mkdir(parents=True)
        contexts = [(u, engine.user_context(u)) for u in (self.owner, self.sparse)]
        site = sign_artifact('browse-cohort', {'configuration': deployment_config(), 'recipe': reference_recipe(),
            'catalog_revision': browsing.catalog_revision(), 'active_account_count': 2,
            'entries': [cohort_entry(u, c) for u, c in contexts]})
        config, sha = load_config(Path(settings.BASE_DIR) / 'evaluation/v2a-recall.v1.json')
        history = freeze_cohort([(self.sparse, contexts[1][1], {})], config, sha, 'synthetic-catalog', 'original')
        self.site, self.history = self.root / 'site.json', self.root / 'history.json'
        self.site.write_text(json.dumps(site), encoding='utf-8')
        self.history.write_text(json.dumps(history), encoding='utf-8')
        proof = sign_artifact('unused-holdout-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
                                                     'skipped': 0, 'locks': [6, 4, 6], 'unused_validation_tests': 9})
        self.proof = self.root / 'synthetic-tests.json'; self.proof.write_text(json.dumps(proof), encoding='utf-8')
        self.inventory = patch.object(validation, 'legacy_inventory', return_value={'conservative_prefix': 5, 'synthetic': True})
        self.inventory.start(); self.addCleanup(self.inventory.stop)
        # PG test primary keys intentionally repeat across complete suite runs.
        # Isolate FILE state as well as DB state; never erase production-style
        # consumption markers to make a synthetic repeat pass.
        original_attempt_path = validation.attempt_path
        self.attempts = patch.object(validation, 'attempt_path', side_effect=lambda cohort:
            self.root / 'attempts' / original_attempt_path(cohort).name)
        self.attempts.start(); self.addCleanup(self.attempts.stop)

    def command(self, **changes):
        call_command('validate_recommendation_holdouts', confirm_sitewide_readonly=True,
            prior_site_lock=self.site, prior_history_lock=self.history, stdout=StringIO(), **changes)

    def test_freeze_cannot_rank_and_excludes_entire_legacy_account(self):
        with patch.object(validation, 'evaluate', side_effect=AssertionError('Freeze cannot rank')):
            self.command(freeze_only=True, output_dir=self.root / 'freeze')
        lock = read_artifact(self.root / 'freeze/cohort.lock.json', 'unused-holdout-cohort')
        self.assertEqual([e['status'] for e in lock['entries']], ['selected', 'legacy_account_excluded'])
        self.assertEqual(len(lock['entries'][0]['targets']), 4)
        self.assertFalse(lock['ranking_performed'])

    def test_one_attempt_joint_evaluation_keeps_private_fields_and_refuses_reuse(self):
        self.command(freeze_only=True, output_dir=self.root / 'freeze')
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        self.command(cohort_lock=self.root / 'freeze/cohort.lock.json', test_report=self.proof, output_dir=self.root / 'evaluation')
        report = read_artifact(self.root / 'evaluation/evaluation.json', 'unused-holdout-evaluation')
        self.assertEqual(report['samples'][0]['paired_targets'], 4)
        self.assertEqual(report['samples'][0]['training_samples'], 10)
        self.assertEqual(report['samples'][0]['evidence_errors'], 0)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        frozen = read_artifact(self.root / 'freeze/cohort.lock.json', 'unused-holdout-cohort')
        self.assertTrue(validation.attempt_path(frozen).exists())
        with patch.object(validation, 'evaluate') as ranking:
            with self.assertRaises(CommandError):
                self.command(cohort_lock=self.root / 'freeze/cohort.lock.json', test_report=self.proof, output_dir=self.root / 'again')
            ranking.assert_not_called()
        with self.assertRaises(CommandError):
            self.command(freeze_only=True, output_dir=self.root / 'refreeze')

    def test_drift_and_missing_authorization_stop_before_ranking(self):
        self.command(freeze_only=True, output_dir=self.root / 'freeze')
        frozen = read_artifact(self.root / 'freeze/cohort.lock.json', 'unused-holdout-cohort')
        RecommendationAnime.objects.filter(bangumi_id=6).update(score=6)
        with self.assertRaises(CommandError):
            self.command(cohort_lock=self.root / 'freeze/cohort.lock.json', test_report=self.proof, output_dir=self.root / 'drift')
        self.assertFalse(validation.attempt_path(frozen).exists())
        with patch.object(engine, 'user_context') as reads:
            with self.assertRaises(CommandError):
                call_command('validate_recommendation_holdouts', prior_site_lock=self.site, prior_history_lock=self.history,
                             freeze_only=True, output_dir=self.root / 'unauthorized', stdout=StringIO())
            reads.assert_not_called()

    def test_repeatable_training_only_evaluation_is_readonly_and_owner_bound(self):
        site = read_artifact(self.site, 'browse-cohort'); history = validation.read_history(self.history)
        context = engine.user_context(self.owner)
        targets, _ = validation.select(context, site, history)
        _, rows = catalog_snapshot(); index, _ = public_index(rows)
        with readonly_snapshot(), CaptureQueriesContext(connection) as queries:
            report = validation.evaluate(self.owner, context, index, targets)
            with self.assertRaises(PermissionDenied):
                validation.run_arm(self.sparse, validation.joint_training(context, targets), index, 'all', 'current_v1')
        self.assertEqual(report['safety_errors'], 0)
        self.assertEqual(report['evidence_errors'], 0)
        self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
