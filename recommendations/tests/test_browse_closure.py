"""9C local PostgreSQL rollback, exact public cache and concurrent-read checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from pathlib import Path
from uuid import uuid4
import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, close_old_connections
from django.test import TransactionTestCase, override_settings
from django.core.management.base import CommandError
from django.conf import settings
from rest_framework.test import APIClient

from anime.models import UserAnimeRecord
from recommendations import browsing, engine
from recommendations.serializers import RecommendationQuerySerializer
from recommendations.tests.test_browsing import BrowseAPITests


class BrowseClosureTests(TransactionTestCase):
    setUp = BrowseAPITests.setUp
    catalog = BrowseAPITests.catalog
    get = BrowseAPITests.get
    start = BrowseAPITests.start
    follow = BrowseAPITests.follow
    ids = BrowseAPITests.ids

    def measured(self, **params):
        query = RecommendationQuerySerializer(data={'browse': True, **params})
        query.is_valid(raise_exception=True)
        metrics = {}
        page = browsing.browse_page(self.owner, query.validated_data, query.initial_data, metrics=metrics)
        return page, metrics

    def test_capability_is_authenticated_strict_private_and_never_selects_v2(self):
        path = '/api/recommendations/capabilities/'
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['browse_enabled'])
        self.assertEqual(response.data['algorithm_version'], 'content.v1')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get(path, {'user_id': self.other.pk}).status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(path).status_code, 401)
        self.client.force_authenticate(self.owner)
        with override_settings(RECOMMENDATION_BROWSE_ENABLED=False):
            self.assertFalse(self.client.get(path).data['browse_enabled'])
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v2'):
            self.assertEqual(self.client.get(path).status_code, 503)

    def test_off_returns_explicit_rollback_and_legacy_remains_exactly_v1(self):
        old = self.get().data
        cursor = self.start()['next_page_cursor']
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with override_settings(RECOMMENDATION_BROWSE_ENABLED=False):
            for params, status in (({'browse': True}, 503), ({'cursor': cursor}, 409)):
                result = self.get(**params)
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.data['code'], 'browse_disabled')
                self.assertEqual(result.data['fallback'], 'legacy_v1')
            self.assertEqual(self.get().data, old)
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)

    def test_epoch_rotation_prevents_old_cursor_resurrection_after_reenable(self):
        first = self.start()
        with override_settings(RECOMMENDATION_BROWSE_ENABLED=False, RECOMMENDATION_BROWSE_EPOCH='rollback-2'):
            self.assertEqual(self.get(cursor=first['next_page_cursor']).status_code, 409)
        with override_settings(RECOMMENDATION_BROWSE_ENABLED=True, RECOMMENDATION_BROWSE_EPOCH='rollback-2'):
            self.assertEqual(self.get(cursor=first['next_page_cursor']).status_code, 409)
            second = self.start()
            self.assertEqual(self.follow(second['next_page_cursor'])['snapshot_id'], second['snapshot_id'])

    def test_public_cache_hit_keeps_order_evidence_and_never_reranks_continuations(self):
        first, cold = self.measured()
        second, warm = self.measured()
        self.assertEqual(cold['public_cache'], 'miss')
        self.assertEqual(warm['public_cache'], 'hit')
        self.assertEqual(first['results'], second['results'])
        self.assertIn('rank_ms', warm)
        with patch('recommendations.browsing.rank_controlled', side_effect=AssertionError('rerank forbidden')):
            _, metrics = self.measured(cursor=first['next_page_cursor'])
        self.assertEqual(metrics['public_cache'], 'not_used')
        self.assertNotIn('rank_ms', metrics)

    def test_direct_update_safety_change_cannot_be_hidden_by_a_warm_public_cache(self):
        from recommendations.models import RecommendationAnime
        cursor = self.start()['next_page_cursor']
        RecommendationAnime.objects.filter(bangumi_id=10).update(nsfw=True)
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        result, metrics = self.measured()
        self.assertEqual(metrics['public_cache'], 'miss')
        self.assertNotIn(10, [row['bangumi_id'] for row in result['results']])

    def test_cache_outage_or_disabled_cache_builds_exact_fresh_data_not_fake_results(self):
        expected = self.start()['results']
        with patch('recommendations.browsing.cache.get', side_effect=RuntimeError('synthetic cache unavailable')):
            actual, metrics = self.measured()
        self.assertEqual(metrics['public_cache'], 'unavailable')
        self.assertEqual(actual['results'], expected)
        with override_settings(RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=False):
            fresh, metrics = self.measured()
        self.assertEqual(metrics['public_cache'], 'disabled')
        self.assertEqual(fresh['results'], expected)

    def test_private_responses_never_enter_cache_and_namespaces_are_disjoint(self):
        with patch('recommendations.browsing.cache.set', wraps=cache.set) as write:
            self.start()
        key, value = write.call_args.args
        self.assertTrue(key.startswith(browsing.PUBLIC_CACHE_NAMESPACE + ':'))
        self.assertEqual(set(value), {'revision', 'rows', 'index'})
        self.assertNotIn('profile', value)
        self.assertNotIn('owned', value)
        self.assertTrue(all(set(row) == {'bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type', 'season'} for row in value['rows']))
        from recommendations.version_control import cache_namespace
        old = cache_namespace('content.v1', engine.TAXONOMY_VERSION, engine.FEATURE_VERSION, engine.FEATURE_RULE_VERSION)
        self.assertNotEqual(old, key)

    def test_old_response_read_in_parallel_is_idempotent_and_does_not_write(self):
        first = self.start()
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        barrier = Barrier(4)
        def worker():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.owner.pk)
                client = APIClient(); client.force_authenticate(user); barrier.wait(timeout=10)
                result = client.get('/api/recommendations/', {'cursor': first['next_page_cursor']})
                return result.status_code, result.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: worker(), range(4)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertTrue(all(data['results'] == results[0][1]['results'] for _, data in results))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        self.assertEqual(connection.vendor, 'postgresql')

    def test_natural_profile_diagnostic_refuses_unconfirmed_or_discovered_users(self):
        from recommendations.management.commands.profile_recommendation_browse import Command
        command = Command()
        for emails, confirmed in (([], True), (['a@example.invalid'], False),
                                  (['a@example.invalid'] * 2, True),
                                  (['a@example.invalid', 'b@example.invalid', 'c@example.invalid'], True)):
            with patch('recommendations.management.commands.profile_recommendation_browse.get_user_model') as users:
                with self.assertRaises(CommandError):
                    command.handle(user_email=emails, confirm_authorized_samples=confirmed, output_dir=None)
                users.assert_not_called()
        # Exact missing account does not fall back to scanning/selecting a user.
        with self.assertRaisesMessage(CommandError, 'Exact account missing'):
            command.handle(user_email=['missing@example.invalid'], confirm_authorized_samples=True, output_dir=None)

    def test_natural_measure_is_postgres_readonly_and_does_not_select_reviews(self):
        from recommendations.management.commands.profile_recommendation_browse import measure
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        statements = []
        def observe(execute, sql, params, many, context):
            statements.append(sql.lower())
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe):
            page, metrics, digest = measure(self.owner, {'style': 'balanced'})
        self.assertEqual(page['algorithm_version'], 'content.v1')
        self.assertGreater(metrics['sql_count'], 0)
        self.assertEqual(len(digest), 64)
        self.assertTrue(any('read only' in sql for sql in statements))
        self.assertFalse(any('review' in sql or 'private_description' in sql for sql in statements))
        self.assertFalse(any(sql.lstrip().startswith(('insert ', 'update ', 'delete ')) for sql in statements))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)

    def test_complete_natural_diagnostic_emits_only_anonymous_metrics_without_side_effects(self):
        from recommendations.management.commands.profile_recommendation_browse import Command
        # Synthetic account in the isolated DB only. Artifacts remain ignored;
        # no recursive cleanup is performed.
        self.owner.email = 'synthetic-9c@example.invalid'; self.owner.save(update_fields=['email'])
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        target = Path(settings.BASE_DIR) / '.tools/acceptance-9c/diagnostic-tests' / uuid4().hex
        Command(stdout=StringIO()).handle(user_email=[self.owner.email], confirm_authorized_samples=True, output_dir=target)
        raw = (target / 'performance.json').read_text(encoding='utf-8')
        report = json.loads(raw)
        self.assertEqual(report['online_default'], 'content.v1')
        self.assertFalse(report['private_writes'])
        self.assertEqual(len(report['release_fingerprint']), 64)
        self.assertEqual(len(report['samples']), 1)
        self.assertEqual(len(report['samples'][0]['cases']), 9)
        for forbidden in ('PRIVATE_REVIEW_SENTINEL', self.owner.email, '来源', 'next_page_cursor\": \"', 'evidence_token'):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
