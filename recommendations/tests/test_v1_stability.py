"""Stable V1 release guards; synthetic independent PostgreSQL only."""
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from anime.models import UserAnimeRecord
from recommendations import engine
from recommendations.tests import test_browsing as fixtures
from scripts.prepare_recommendation_browse_rollout import environment_patch


class V1RolloutGuards(SimpleTestCase):
    def test_both_rollout_directions_explicitly_disable_browse_v2(self):
        for enabled in (False, True):
            values = environment_patch(enabled)
            self.assertEqual(values['RECOMMENDATION_ALGORITHM_VERSION'], 'content.v1')
            self.assertEqual(values['RECOMMENDATION_V2_BROWSE_ENABLED'], 'False')
            self.assertEqual(values['RECOMMENDATION_BROWSE_ENABLED'], str(enabled))
        self.assertNotEqual(environment_patch(True)['RECOMMENDATION_BROWSE_EPOCH'],
                            environment_patch(True)['RECOMMENDATION_BROWSE_EPOCH'])


@override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
class V1StabilityTests(TransactionTestCase):
    setUp = fixtures.BrowseAPITests.setUp
    catalog = fixtures.BrowseAPITests.catalog

    def legacy(self):
        response = self.client.get('/api/recommendations/', {'page_size': 12})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['algorithm_version'], 'content.v1')
        return response.data

    def test_legacy_cache_read_outage_rebuilds_exact_results(self):
        expected = self.legacy()
        with patch('recommendations.engine.cache') as backend:
            backend.get.side_effect = ConnectionError('synthetic cache read outage')
            self.assertEqual(self.legacy(), expected)

    def test_legacy_cache_write_outage_keeps_fresh_results(self):
        expected = self.legacy(); cache.clear()
        with patch('recommendations.engine.cache') as backend:
            backend.get.return_value = None
            backend.set.side_effect = TimeoutError('synthetic cache write outage')
            self.assertEqual(self.legacy(), expected)

    def test_cache_outage_detail_keeps_same_evidence_not_mock(self):
        row = self.legacy()['results'][0]
        path = f'/api/recommendations/{row["bangumi_id"]}/'
        expected = self.client.get(path, {'evidence_token': row['evidence_token']})
        self.assertEqual(expected.status_code, 200)
        with patch('recommendations.engine.cache') as backend:
            backend.get.side_effect = TimeoutError('synthetic outage')
            backend.set.side_effect = TimeoutError('synthetic outage')
            actual = self.client.get(path, {'evidence_token': row['evidence_token']})
        self.assertEqual(actual.status_code, 200)
        self.assertEqual(actual.data, expected.data)

    def test_database_failure_still_returns_503_not_false_cache_success(self):
        cache.clear()
        with patch('recommendations.engine.cache') as backend:
            backend.get.side_effect = ConnectionError('synthetic outage')
            with patch('recommendations.engine.build_feature_index', side_effect=DatabaseError('synthetic database failure')):
                response = self.client.get('/api/recommendations/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('results', response.data)

    def test_stable_dispatch_does_not_call_any_v2_serving_or_ranking(self):
        with (patch('recommendations.engine._rank_context_v2', side_effect=AssertionError('V2 forbidden')),
              patch('recommendations.deployment.v2_browsing.browse_page', side_effect=AssertionError('V2 forbidden'))):
            legacy = self.legacy()
            caps = self.client.get('/api/recommendations/capabilities/')
            browse = self.client.get('/api/recommendations/', {'browse': True})
            self.assertEqual(caps.data['algorithm_version'], 'content.v1')
            self.assertEqual(browse.data['algorithm_version'], 'content.v1')
            self.assertEqual(browse.data['results'], legacy['results'])

    def test_public_cache_fallback_never_writes_private_records_or_private_cache(self):
        import json
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with patch('recommendations.engine.cache') as backend:
            backend.get.side_effect = ConnectionError('synthetic outage')
            self.legacy()
            payload = backend.set.call_args.args[1]
            self.assertEqual(set(payload), {'signature', 'features', 'weights', 'source_fingerprints', 'taxonomy_version'})
            self.assertNotIn('PRIVATE_REVIEW_SENTINEL', json.dumps(payload, default=str))
            self.assertNotIn('preferences', payload); self.assertNotIn('owned', payload)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_malformed_public_cache_is_a_miss_not_a_500(self):
        expected = self.legacy()
        for broken in ('invalid-cache-payload', {'signature': 'wrong'}, {'features': None}):
            with patch('recommendations.engine.cache') as backend:
                backend.get.return_value = broken
                self.assertEqual(self.legacy(), expected)

    def test_feature_off_still_serves_exact_legacy_v1_during_cache_outage(self):
        expected = self.legacy()
        with override_settings(RECOMMENDATION_BROWSE_ENABLED=False), patch('recommendations.engine.cache') as backend:
            backend.get.side_effect = ConnectionError('synthetic outage')
            self.assertEqual(self.legacy(), expected)
            unavailable = self.client.get('/api/recommendations/', {'browse': True})
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.data['fallback'], 'legacy_v1')

    def test_shared_limiter_cache_outage_is_json_503_and_never_allows_writes(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        backend = Mock(); backend.get.side_effect = ConnectionError('synthetic shared-cache outage')
        with (patch('rest_framework.throttling.SimpleRateThrottle.cache', backend),
              patch('recommendations.views.add_want') as add):
            for path, method in (('/api/recommendations/', 'get'), ('/api/recommendations/10/want/', 'post')):
                response = getattr(self.client, method)(path, {}, format='json')
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('results', response.data)
                self.assertTrue(response['Content-Type'].startswith('application/json'))
            add.assert_not_called()
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_real_rate_limit_stays_429_and_authentication_stays_401(self):
        with (patch('recommendations.views.UserRateThrottle.allow_request', return_value=False),
              patch('recommendations.views.UserRateThrottle.wait', return_value=1)):
            response = self.client.get('/api/recommendations/')
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response['Retry-After'], '1')
        self.client.force_authenticate(None)
        with patch('recommendations.views.UserRateThrottle.allow_request', side_effect=ConnectionError('synthetic outage')):
            self.assertEqual(self.client.get('/api/recommendations/').status_code, 401)
