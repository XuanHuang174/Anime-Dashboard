"""D3 local PostgreSQL only; synthetic metrics/receipts, never natural score edits."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command, CommandError
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection

from anime.models import UserAnimeRecord, Anime
from recommendations import deployment, engine, browsing
from recommendations.deployment_evaluation import configuration, deployment_gates, REQUIRED_CASES
from recommendations.execution import recipe_descriptor
from recommendations.version_control import sign_artifact, canonical_bytes
from recommendations.tests import test_browsing as fixtures
from recommendations.tests.test_comparison import passing_sample


def receipts():
    tests = sign_artifact('browse-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
        'skipped': 0, 'backend_tests': 355, 'frontend_tests': 158, 'browser_checks': 13,
        'build': 'pass', 'required_cases': list(REQUIRED_CASES), 'locks': [6, 4, 6]})
    samples = [passing_sample(), passing_sample()]
    for sample in samples:
        sample.update(sample_kind='site_owner_authorized_natural_account', usable_samples=4)
        sample['passes']['ranking'].update(paired_targets=4, skipped_unpaired_targets=0)
    evaluation = sign_artifact('browse-evaluation', {'recipe': recipe_descriptor(), 'configuration': configuration(),
        'samples': samples, 'frozen_before_ranking': True, 'historical_baseline_checked': True})
    performance = sign_artifact('browse-performance', {'recipe': recipe_descriptor(), 'configuration': configuration(),
        'execution_context': configuration()['performance_scope'], 'service_identity_hash': 'a' * 64,
        'git_commit': 'b' * 40, 'sample_count': 2, 'cases': [{'wall_ms': 100, 'sql_count': 12}]})
    return tests, evaluation, performance


class DeploymentTests(TransactionTestCase):
    setUp = fixtures.BrowseAPITests.setUp
    catalog = fixtures.BrowseAPITests.catalog

    def bundle(self):
        root = Path(settings.BASE_DIR) / '.tools/d3-receipt-tests' / uuid4().hex
        root.mkdir(parents=True)
        tests, evaluation, performance = receipts()
        for name, data in [('tests', tests), ('evaluation', evaluation), ('performance', performance)]:
            (root / (name + '.json')).write_text(json.dumps(data), encoding='utf-8')
        call_command('approve_recommendation_browse', evaluation=root / 'evaluation.json', tests=root / 'tests.json',
            performance=root / 'performance.json', output_dir=root / 'approved', confirm_reviewed=True, stdout=StringIO())
        return root / 'approved/approval.json'

    def test_dispatch_gate_and_rollback(self):
        base = self.client.get('/api/recommendations/', {'browse': True}).data
        with override_settings(RECOMMENDATION_V2_BROWSE_ENABLED=True, RECOMMENDATION_V2_BROWSE_APPROVAL_FILE=''):
            self.assertEqual(self.client.get('/api/recommendations/capabilities/').status_code, 503)
            self.assertEqual(self.client.get('/api/recommendations/', {'browse': True}).status_code, 503)
        path = self.bundle()
        with override_settings(RECOMMENDATION_V2_BROWSE_ENABLED=True, RECOMMENDATION_V2_BROWSE_APPROVAL_FILE=str(path)):
            enabled = self.client.get('/api/recommendations/', {'browse': True})
            self.assertEqual(enabled.status_code, 200, enabled.data)
            cursor = enabled.data['next_page_cursor']; row = enabled.data['results'][0]
            self.assertEqual(enabled.data['algorithm_version'], 'content.v2')
            self.assertEqual(self.client.get('/api/recommendations/', {'cursor': base['next_page_cursor']}).status_code, 409)
        self.assertEqual(self.client.get('/api/recommendations/', {'cursor': cursor}).status_code, 409)
        self.assertEqual(self.client.get(f'/api/recommendations/{row["bangumi_id"]}/', {'detail_context': row['detail_context']}).status_code, 409)
        restored = self.client.get('/api/recommendations/', {'browse': True}).data
        self.assertEqual(restored['results'], base['results'])

    def test_old_approval_cannot_enable_new_recipe(self):
        root = Path(settings.BASE_DIR) / '.tools/d3-receipt-tests' / uuid4().hex
        root.mkdir(parents=True)
        path = root / 'old.json'
        path.write_text(json.dumps(sign_artifact('approval', {'gates': dict.fromkeys(('safety', 'evidence', 'relevance',
            'categories', 'performance', 'rollback'), 'pass'), 'sample_count': 2})), encoding='utf-8')
        with override_settings(RECOMMENDATION_V2_BROWSE_ENABLED=True, RECOMMENDATION_V2_BROWSE_APPROVAL_FILE=str(path)):
            with self.assertRaises(engine.RecommendationUnavailable):
                deployment.require_recipe()
        tests, evaluation, performance = receipts()
        performance['execution_context'] = 'local-client-not-deployed-performance'
        self.assertEqual(deployment_gates(evaluation, tests, performance)['performance']['status'], 'insufficient_evidence')
        evaluation['samples'][0]['arms']['current_v2']['categories']['all']['topic_overlap_proxy_mean'] = .1
        self.assertEqual(deployment_gates(evaluation, tests)['relevance']['status'], 'fail')

    def test_receipt_chain_rejects_stale_or_changed_code(self):
        path = self.bundle()
        with override_settings(RECOMMENDATION_V2_BROWSE_ENABLED=True, RECOMMENDATION_V2_BROWSE_APPROVAL_FILE=str(path)):
            self.assertEqual(deployment.require_recipe(), recipe_descriptor(serving_enabled=True))
            for name in ('tests.json', 'evaluation.json', 'performance.json'):
                receipt = path.parent / name
                original = receipt.read_bytes()
                receipt.write_bytes(original + b' ')
                if name == 'evaluation.json':
                    with self.assertRaises(engine.RecommendationUnavailable):
                        deployment.require_recipe()
                else:
                    changed = json.loads(original); changed['signature'] = '0' * 64
                    receipt.write_text(json.dumps(changed), encoding='utf-8')
                    with self.assertRaises(engine.RecommendationUnavailable):
                        deployment.require_recipe()
                receipt.write_bytes(original)
            with patch('recommendations.version_control.release_descriptor', return_value={'fingerprint': 'changed'}):
                with self.assertRaises(engine.RecommendationUnavailable):
                    deployment.require_recipe()

    def test_served_v2_http_and_details_are_bound(self):
        path = self.bundle()
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with override_settings(RECOMMENDATION_V2_BROWSE_ENABLED=True, RECOMMENDATION_V2_BROWSE_APPROVAL_FILE=str(path)):
            caps = self.client.get('/api/recommendations/capabilities/').data
            self.assertTrue(caps['execution_recipe']['serving_enabled'])
            page = self.client.get('/api/recommendations/', {'browse': True}).data
            row = page['results'][0]
            with CaptureQueriesContext(connection) as queries, patch('recommendations.execution.execute_v2', side_effect=AssertionError('no ranking')):
                detail = self.client.get(f'/api/recommendations/{row["bangumi_id"]}/', {
                    'detail_context': row['detail_context'], 'evidence_token': row['evidence_token']})
            self.assertEqual(detail.status_code, 200, detail.data)
            self.assertEqual(detail.data['evidence_token'], row['evidence_token'])
            self.assertEqual(detail.data['evidence_context']['recipe_revision'], caps['execution_recipe']['recipe_revision'])
            self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
            self.client.force_authenticate(self.other)
            self.assertEqual(self.client.get('/api/recommendations/', {'cursor': page['next_page_cursor']}).status_code, 409)
            self.assertEqual(self.client.get(f'/api/recommendations/{row["bangumi_id"]}/', {'detail_context': row['detail_context']}).status_code, 409)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_preregistration_and_no_unsafe_test_bypass(self):
        self.assertEqual(configuration()['quality_limits']['maximum_sql_queries'], 6)
        with override_settings(RECOMMENDATION_V2_TEST_BYPASS=True, RECOMMENDATION_V2_BROWSE_ENABLED=True,
                               RECOMMENDATION_V2_BROWSE_APPROVAL_FILE=''):
            with self.assertRaises(engine.RecommendationUnavailable):
                deployment.require_recipe()
        tests, evaluation, performance = receipts()
        tests['database'] = 'sqlite'
        with self.assertRaises(ValueError):
            deployment_gates(evaluation, tests, performance)

    def test_freeze_requires_sitewide_authorization_and_does_not_rank(self):
        root = Path(settings.BASE_DIR) / '.tools/d3-cohort-tests' / uuid4().hex
        with self.assertRaises(CommandError):
            call_command('evaluate_recommendation_deployment', freeze_only=True, output_dir=root, stdout=StringIO())
        with patch('recommendations.execution.execute_v2', side_effect=AssertionError('freeze cannot rank')):
            call_command('evaluate_recommendation_deployment', confirm_sitewide_readonly=True,
                freeze_only=True, output_dir=root, stdout=StringIO())
        text = (root / 'cohort.lock.json').read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE_REVIEW', text); self.assertNotIn('personal_score', text)
        self.assertEqual(json.loads(text)['active_account_count'], 2)
        with self.assertRaises(CommandError):
            call_command('evaluate_recommendation_deployment', confirm_sitewide_readonly=True,
                cohort_lock=root / 'cohort.lock.json', output_dir=root / 'evaluation', stdout=StringIO())

    def test_local_process_does_not_masquerade_as_service_performance(self):
        root = Path(settings.BASE_DIR) / '.tools/d3-performance-tests' / uuid4().hex
        self.owner.email = 'd3-local@example.test'; self.owner.save(update_fields=['email'])
        with patch.dict('os.environ', {'RENDER_SERVICE_ID': '', 'RENDER_GIT_COMMIT': ''}):
            call_command('profile_recommendation_deployment', user_email=[self.owner.email],
                         confirm_authorized_samples=True, output_dir=root, stdout=StringIO())
        report = json.loads((root / 'performance.json').read_text(encoding='utf-8'))
        self.assertEqual(report['execution_context'], 'local-client-not-deployed-performance')
        self.assertFalse(report['http_latency_measured'])
        self.assertIsNone(report['service_identity_hash'])

    def test_expanded_cohort_freeze_evaluation_and_drift_rejection_are_readonly(self):
        self.owner.email = 'd3-owner@example.test'; self.owner.save(update_fields=['email'])
        self.other.email = 'd3-other@example.test'; self.other.save(update_fields=['email'])
        self.catalog(4, tags='音乐,日常')
        anime = Anime.objects.create(bgm_id=4, chinese_title='合成来源4', base_tags=['音乐', '日常'])
        UserAnimeRecord.objects.create(user=self.owner, anime=anime, personal_score=9,
                                      watch_status='completed', review='PRIVATE_REVIEW_SENTINEL')
        root = Path(settings.BASE_DIR) / '.tools/d3-evaluation-tests' / uuid4().hex
        common = dict(user_email=[self.owner.email, self.other.email], confirm_authorized_samples=True, stdout=StringIO())
        call_command('audit_recommendation_recall', freeze_only=True, output_dir=root / 'historical-freeze', **common)
        call_command('audit_recommendation_execution', cohort_lock=root / 'historical-freeze/cohort.lock.json',
                     output_dir=root / 'historical', **common)
        tests, _, _ = receipts()
        (root / 'tests.json').write_text(json.dumps(tests), encoding='utf-8')
        call_command('evaluate_recommendation_deployment', confirm_sitewide_readonly=True,
                     freeze_only=True, output_dir=root / 'freeze', stdout=StringIO())
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            call_command('evaluate_recommendation_deployment', confirm_sitewide_readonly=True,
                cohort_lock=root / 'freeze/cohort.lock.json', historical_report=root / 'historical/recall-audit.json',
                test_attestation=root / 'tests.json', output_dir=root / 'evaluation', stdout=StringIO())
        raw = (root / 'evaluation/evaluation.json').read_text(encoding='utf-8')
        data = json.loads(raw)
        self.assertEqual(len(data['samples']), 1)
        self.assertTrue(data['frozen_before_ranking']); self.assertTrue(data['historical_baseline_checked'])
        self.assertNotIn(self.owner.email, raw); self.assertNotIn('PRIVATE_REVIEW', raw)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        UserAnimeRecord.objects.filter(pk=self.records[0].pk).update(personal_score='8.6')
        with patch('recommendations.execution.execute_v2', side_effect=AssertionError('drift must stop before ranking')):
            with self.assertRaises(CommandError):
                call_command('evaluate_recommendation_deployment', confirm_sitewide_readonly=True,
                    cohort_lock=root / 'freeze/cohort.lock.json', historical_report=root / 'historical/recall-audit.json',
                    test_attestation=root / 'tests.json', output_dir=root / 'drift', stdout=StringIO())
