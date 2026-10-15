"""6C end-to-end API transitions. Synthetic fixtures in isolated SQLite only."""
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import UserAnimeRecord
from recommendations.binding import bind_record
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.version_control import cache_namespace, clear_version_cache
from recommendations import engine
from .test_contracts_api import ContractFixtures


class StateLoopTests(ContractFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(settings.SETTINGS_MODULE, 'config.recommendation_test_settings')
        self.assertEqual(connection.vendor, 'sqlite')
        cache.clear()
        self.client.force_authenticate(self.user)
        for identity in range(101, 111):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'候选{identity}',
                score=7.5, tags='音乐,日常' if identity < 106 else '音乐,成长', nsfw=False)

    def page(self, **query):
        result = self.client.get('/api/recommendations/', {'page_size': 2, **query})
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def detail(self, row, token=True):
        return self.client.get(f'/api/recommendations/{row["bangumi_id"]}/',
                               {'evidence_token': row['evidence_token']} if token else {})

    def trusted(self):
        return [self.seed(identity, score='9.0', tags=['音乐', '日常']) for identity in (1, 2, 3)]

    def assert_stale(self, page):
        self.assertEqual(self.client.get('/api/recommendations/', {'page_size': 2, 'page': 2,
                         'state_token': page['state_token']}).status_code, 409)
        result = self.detail(page['results'][0])
        self.assertEqual(result.status_code, 409)
        self.assertNotIn('evidence', result.data)

    def test_empty_explicit_personalized_and_downgrade_are_authoritative(self):
        self.assertEqual(self.page()['mode'], 'cold_start')
        detail = self.client.get('/api/recommendations/101/').data
        self.assertIsNone(detail['evidence'])
        self.assertEqual(detail['reason'], '')
        RecommendationPreference.objects.create(user=self.user, tags=['音乐'], version=4)
        explicit = self.page()
        self.assertEqual(explicit['mode'], 'preference_based')
        self.assertTrue(all(row['evidence']['kind'] == 'explicit_preferences' for row in explicit['results']))
        records = self.trusted()
        self.assert_stale(explicit)
        full = self.page()
        self.assertEqual(full['mode'], 'personalized')
        self.assertEqual(full['cold_start_state']['code'], 'ready')
        for row in full['results']:
            self.assertEqual(row['evidence']['kind'], 'rated_work')
            self.assertEqual(row['evidence'], self.detail(row).data['evidence'])
        UserAnimeRecord.objects.filter(pk=records[-1].pk).update(personal_score=0)
        self.assert_stale(full)
        downgraded = self.page()
        self.assertEqual(downgraded['mode'], 'preference_based')
        self.assertEqual(downgraded['cold_start_state']['code'], 'insufficient_samples')
        self.assertEqual(downgraded['cold_start_state']['unrated_record_count'], 0)
        self.assertEqual(downgraded['evidence_context']['preference_version'], 4)

    def test_private_tag_clear_does_not_refill_from_catalog_and_invalidates_both_views(self):
        records = self.trusted()
        before = self.page()
        UserAnimeRecord.objects.filter(pk=records[-1].pk).update(private_base_tags=[], custom_tags=[], metadata_initialized=True)
        self.assert_stale(before)
        fresh = self.page()
        self.assertEqual(fresh['cold_start_state']['code'], 'missing_genres')
        self.assertEqual(fresh['results'], [])
        detail = self.detail(before['results'][0], token=False).data
        self.assertIsNone(detail['evidence'])

    def test_confirmed_identity_refreshes_profile_without_changing_preferences(self):
        for identity in (1, 2):
            self.seed(identity, tags=['音乐'])
        record = self.seed(3, linked=False, tags=['音乐'])
        RecommendationPreference.objects.create(user=self.user, tags=['音乐'], version=6)
        before = self.page()
        self.assertEqual(before['cold_start_state']['code'], 'pending_identity')
        bind_record(record_id=record.pk, user_id=self.user.pk, target_id=3, source='user_confirmed')
        self.assert_stale(before)
        fresh = self.page()
        self.assertEqual(fresh['mode'], 'personalized')
        self.assertEqual(fresh['cold_start_state']['usable_sample_count'], 3)
        preference = RecommendationPreference.objects.get(user=self.user)
        self.assertEqual((preference.tags, preference.version), (['音乐'], 6))

    def test_category_and_user_snapshots_cannot_be_replayed(self):
        self.trusted()
        first = self.page()
        for category in ('familiar', 'explore'):
            result = self.client.get('/api/recommendations/', {'category': category, 'page_size': 2,
                'page': 2, 'state_token': first['state_token']})
            self.assertEqual(result.status_code, 409)
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        self.client.force_authenticate(self.other)
        self.assert_stale(first)
        own = self.page()
        self.assertTrue(all(row['evidence']['kind'] == 'explicit_preferences' for row in own['results']))

    def test_isolated_v2_switch_and_rollback_never_reuse_the_other_versions_tokens(self):
        self.trusted()
        v1 = self.page()
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v2'):
            self.assert_stale(v1)
            v2 = self.page()
            self.assertEqual(v2['algorithm_version'], 'content.v2')
            self.assertEqual(self.detail(v2['results'][0]).status_code, 200)
            clear_version_cache('content.v2')
            key = cache_namespace('content.v1', engine.TAXONOMY_VERSION, engine.FEATURE_VERSION, engine.FEATURE_RULE_VERSION)
            self.assertIsNotNone(cache.get(key))
        self.assert_stale(v2)
        rolled_back = self.page()
        self.assertEqual(rolled_back, v1)

    def test_detail_reads_and_repeated_want_preserve_existing_fields_exactly(self):
        records = self.trusted()
        UserAnimeRecord.objects.filter(pk=records[0].pk).update(review='仅隔离测试使用的长评', custom_tags=['自定义原标签'])
        before = list(UserAnimeRecord.objects.filter(user=self.user).order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            page = self.page()
            self.assertEqual(self.detail(page['results'][0]).status_code, 200)
        self.assertFalse(any(q['sql'].strip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in queries))
        self.assertFalse(any('"review"' in q['sql'].lower() for q in queries))
        for _ in range(3):
            result = self.client.post('/api/recommendations/1/want/', {}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertFalse(result.data['created'])
            self.assertEqual(result.data['watch_status'], 'completed')
        self.assertEqual(list(UserAnimeRecord.objects.filter(user=self.user).order_by('pk').values()), before)

    def test_safety_is_hard_filtered_across_warmed_list_detail_and_want(self):
        self.trusted()
        first = self.page()
        identity = first['results'][0]['bangumi_id']
        for nsfw in (True, None):
            RecommendationAnime.objects.filter(bangumi_id=identity).update(nsfw=nsfw)
            self.assertNotIn(identity, [row['bangumi_id'] for row in self.page()['results']])
            self.assertEqual(self.detail(first['results'][0]).status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.user).count(), 3)

    def test_transient_backend_failure_is_not_a_cold_profile_and_retry_is_clean(self):
        self.trusted()
        first = self.page()
        with patch('recommendations.views.rank_candidates', side_effect=engine.RecommendationUnavailable()):
            failure = self.client.get('/api/recommendations/')
        self.assertEqual(failure.status_code, 503)
        self.assertNotIn('mode', failure.data)
        self.assertNotIn('evidence', failure.data)
        self.assertEqual(self.page(), first)
