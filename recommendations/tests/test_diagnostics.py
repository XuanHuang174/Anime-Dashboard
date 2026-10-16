"""Synthetic PostgreSQL only. Never touches either authorized real account."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.db import connection, DatabaseError
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from anime.models import Anime, UserAnimeRecord
from recommendations.comparison import evaluate_snapshot, QueryCounter
from recommendations.diagnostics import prepare_sample, diagnose_sample, public_index
from recommendations.feedback import feedback_snapshot, mutate_feedback
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.resolution import resolved_records
from recommendations.services import preference_contract
from recommendations.quality_evaluation import input_fingerprint

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('Explicit isolated local PostgreSQL settings required before database setup.')


class DiagnosticPostgresTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.owner = get_user_model().objects.create_user(username='synthetic-diag', email='diag@example.test')
        self.sparse = get_user_model().objects.create_user(username='synthetic-sparse', email='sparse@example.test')
        for identity in range(1, 25):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'合成目录{identity}',
                score=7, tags='音乐,日常' if identity < 16 else '音乐,成长', nsfw=False)
        for identity in range(1, 5):
            anime = Anime.objects.create(bgm_id=identity, chinese_title=f'合成目录{identity}', base_tags=['音乐', '日常'])
            UserAnimeRecord.objects.create(user=self.owner, anime=anime, watch_status='completed', personal_score=9,
                                          review='PRIVATE_REVIEW_NOT_READ')

    def sample(self, owner=None):
        owner = owner or self.owner
        return prepare_sample(resolved_records(owner), preference_contract(owner), owner.pk, feedback_snapshot(owner))

    def test_current_feedback_is_in_both_arms_and_private_fingerprint_without_mutation(self):
        from uuid import uuid4
        context, summary = self.sample()
        before = list(UserAnimeRecord.objects.values())
        mutate_feedback(user=self.owner, action='hide', changes={'bangumi_id': 5,
            'expected_feedback_version': 0, 'operation_id': str(uuid4())})
        updated, summary = self.sample()
        self.assertNotEqual(input_fingerprint(context), input_fingerprint(updated))
        self.assertEqual(updated['feedback']['hidden_ids'], {5})
        with readonly_snapshot():
            _, rows = catalog_snapshot(); index, _ = public_index(rows)
            with CaptureQueriesContext(connection) as queries:
                report = diagnose_sample(updated, summary, index, top_k=5, repeats=2, max_holdouts=1)
            legacy = evaluate_snapshot(resolved_records(self.owner), preference_contract(self.owner), self.owner.pk,
                rows, top_k=5, repeats=2, max_holdouts=1, feedback=feedback_snapshot(self.owner))
        for arm in report['arms'].values():
            self.assertNotIn(5, arm['all']['metrics']['top_bangumi_ids'])
        self.assertEqual(report['checks']['hidden_rows'], 0)
        self.assertEqual(report['checks']['evidence_errors'], 0)
        self.assertTrue(report['version_tokens_isolated'])
        self.assertNotIn(5, legacy['arms']['current_v2']['categories']['all']['top_bangumi_ids'])
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        self.assertNotIn('PRIVATE_REVIEW', json.dumps(report))
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def test_empty_sparse_and_explicit_preference_states_are_not_fake_holdout_success(self):
        _, rows = catalog_snapshot(); index, _ = public_index(rows)
        for tags in ([], ['音乐']):
            RecommendationPreference.objects.update_or_create(user=self.sparse, defaults={'tags': tags})
            context, summary = self.sample(self.sparse)
            with readonly_snapshot():
                report = diagnose_sample(context, summary, index, top_k=5, repeats=2, max_holdouts=1)
            self.assertFalse(summary['holdout_eligible'])
            self.assertEqual(report['holdout']['status'], 'insufficient_evidence')
            self.assertIsNone(report['holdout']['metrics']['content.v1']['all']['hit_at_k'])
            self.assertEqual(context['mode'], 'preference_based' if tags else 'cold_start')

    def test_measurements_are_additive_and_deterministic_with_current_ranking(self):
        context, summary = self.sample(); original = deepcopy(context)
        with readonly_snapshot():
            _, rows = catalog_snapshot(); index, _ = public_index(rows)
            report = diagnose_sample(context, summary, index, top_k=5, repeats=2, max_holdouts=1)
        self.assertEqual(context, original)
        for version, arm in report['arms'].items():
            for data in arm.values():
                self.assertTrue(data['stable_order_and_evidence'])
                for run in data['runs']:
                    self.assertGreaterEqual(run['compute_ms'], run['sql_execute_ms'])
                    self.assertGreaterEqual(run['process_cpu_ms'], 0)
                    if version == 'content.v2':
                        self.assertEqual(set(run['stage_ms']), {'public_fetch', 'feature_scoring', 'pool_balance', 'hydrate_and_safety', 'rerank'})

    def test_command_scope_validation_happens_before_catalog_or_account_reads(self):
        for changes in ({}, {'user_email': ['diag@example.test']},
                        {'user_email': ['a', 'b', 'c'], 'confirm_authorized_samples': True}):
            with patch('recommendations.management.commands.diagnose_recommendations.catalog_snapshot') as catalog:
                with self.assertRaises(CommandError):
                    call_command('diagnose_recommendations', stdout=StringIO(), **changes)
                catalog.assert_not_called()

    def test_readonly_postgres_enforcement_blocks_writes_and_cleans_transaction(self):
        with self.assertRaises(DatabaseError):
            with readonly_snapshot():
                RecommendationAnime.objects.filter(bangumi_id=1).update(score=1)
        self.assertEqual(RecommendationAnime.objects.get(bangumi_id=1).score, 7)
        with readonly_snapshot():
            with connection.cursor() as cursor:
                cursor.execute('SHOW transaction_read_only'); self.assertEqual(cursor.fetchone()[0], 'on')

    def test_report_private_no_approval_no_account_data_and_no_overwrite(self):
        # Keep ignored artifacts; no recursive/bulk cleanup in this project.
        output = Path(settings.BASE_DIR) / '.tools/diagnostic-test-reports' / str(uuid4())
        options = {'user_email': [self.owner.email, self.sparse.email], 'confirm_authorized_samples': True,
                   'output_dir': output, 'max_holdouts': 1, 'top_k': 5, 'execution_context': 'isolated_test'}
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
            call_command('diagnose_recommendations', stdout=StringIO(), **options)
        data = (output / 'diagnostic.json').read_text(encoding='utf-8')
        self.assertNotIn(self.owner.email, data); self.assertNotIn('personal_score', data)
        self.assertNotIn('PRIVATE_REVIEW', data); self.assertNotIn('合成目录', data)
        self.assertFalse(json.loads(data)['approval_issued'])
        self.assertFalse((output / 'approval.json').exists())
        with self.assertRaises(CommandError):
            call_command('diagnose_recommendations', stdout=StringIO(), **options)

    def test_query_timer_does_not_retain_sql_even_on_failure(self):
        counter = QueryCounter()
        def fail(*args):
            raise ValueError('synthetic')
        with self.assertRaises(ValueError):
            counter(fail, 'SENSITIVE SQL', ['PRIVATE'], False, {})
        self.assertEqual(counter.count, 1); self.assertGreaterEqual(counter.seconds, 0)
        self.assertNotIn('SENSITIVE', repr(vars(counter)))

    def test_hidden_holdout_target_is_reported_not_unhidden_or_replaced_to_inflate_hits(self):
        context, summary = self.sample()
        context['feedback'] = {'version': 1, 'hidden_ids': frozenset({1})}
        _, rows = catalog_snapshot(); index, _ = public_index(rows)
        with readonly_snapshot():
            report = diagnose_sample(context, summary, index, top_k=5, repeats=2, max_holdouts=1)
        self.assertEqual(report['holdout']['status'], 'insufficient_evidence')
        self.assertEqual(report['holdout']['feedback_blocked_targets'], 1)
        self.assertEqual(report['holdout']['paired_targets'], 0)
        self.assertEqual(context['feedback']['hidden_ids'], {1})

    def test_command_rejects_ambiguous_account_and_public_output_without_catalog_scan(self):
        get_user_model().objects.create_user(username='synthetic-duplicate', email=self.owner.email)
        with patch('recommendations.management.commands.diagnose_recommendations.catalog_snapshot') as catalog:
            with self.assertRaisesRegex(CommandError, 'ambiguous'):
                call_command('diagnose_recommendations', user_email=[self.owner.email],
                             confirm_authorized_samples=True, stdout=StringIO())
            catalog.assert_not_called()
        with self.assertRaisesRegex(CommandError, 'private .tools'):
            call_command('diagnose_recommendations', user_email=[self.owner.email],
                confirm_authorized_samples=True, output_dir=Path(settings.BASE_DIR) / 'static', stdout=StringIO())
