"""7C synthetic API closure tests. Only the explicitly isolated PostgreSQL DB."""
import os
import subprocess
import sys
from unittest.mock import patch
from unittest import skipUnless
import requests

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITestCase

from anime.models import UserAnimeRecord
from recommendations import engine
from recommendations.models import RecommendationAnime
from .test_contracts_api import ContractFixtures


@skipUnless(settings.SETTINGS_MODULE == 'config.recommendation_pg_test_settings'
            and connection.vendor == 'postgresql', '7C acceptance requires the isolated PostgreSQL runner; no substitute.')
class ClosedLoopPostgresTests(ContractFixtures, APITestCase):
    def setUp(self):
        # Fail, do not skip or fall back to SQLite/Neon.
        self.assertEqual(settings.SETTINGS_MODULE, 'config.recommendation_pg_test_settings')
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.assertIn(connection.settings_dict['HOST'], ('127.0.0.1', 'localhost', '::1'))
        self.assertEqual(settings.RECOMMENDATION_ALGORITHM_VERSION, 'content.v1')
        super().setUp()
        cache.clear()
        self.client.force_authenticate(self.user)
        for identity in (1, 2, 3):
            self.seed(identity, score='9.0', tags=['音乐', '日常'])
        for identity in range(101, 108):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'7C合成作品{identity}',
                season='2026-04', tags='音乐,日常', nsfw=False, score=7.5)

    def page(self, **params):
        response = self.client.get('/api/recommendations/', {'page_size': 2, **params})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['algorithm_version'], 'content.v1')
        return response.data

    def test_success_invalidates_entire_detail_and_page_then_resource_proxy_stays_independent(self):
        page = self.page()
        row = page['results'][0]
        url = f'/api/recommendations/{row["bangumi_id"]}/'
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        added = self.client.post(url + 'want/', {}, format='json')
        self.assertEqual(added.status_code, 201)
        self.assertEqual(self.client.get(url, {'evidence_token': row['evidence_token']}).status_code, 409)
        self.assertEqual(self.client.get('/api/recommendations/', {'page': 2, 'page_size': 2,
            'state_token': page['state_token']}).status_code, 409)
        fresh = self.client.get(url)
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(fresh.data['watch_status'], 'plan')
        self.assertEqual(fresh.data['bangumi_id'], row['bangumi_id'])
        self.assertNotEqual(fresh.data['evidence_token'], row['evidence_token'])
        self.assertNotIn(row['bangumi_id'], [item['bangumi_id'] for item in self.page()['results']])
        created = UserAnimeRecord.objects.get(pk=added.data['record_id'])
        self.assertIsNone(created.personal_score)
        self.assertEqual(created.review, '')
        self.assertEqual(list(UserAnimeRecord.objects.exclude(pk=created.pk).order_by('pk').values()), before)
        with (override_settings(ANIME_SEARCH_API_URL='https://resource.example.test', ANIME_SEARCH_API_KEY='synthetic-key'),
              patch('anime.resource_search.requests.get', side_effect=requests.Timeout())):
            self.assertEqual(self.client.get('/api/utils/anime-search/', {'keyword': '合成关键词'}).status_code, 503)
        self.assertEqual(self.client.get(url).data, fresh.data)

    def test_existing_saved_zero_long_review_and_explicit_empty_tags_survive_repeat_ack(self):
        record = UserAnimeRecord.objects.get(user=self.user, anime__bgm_id=1)
        UserAnimeRecord.objects.filter(pk=record.pk).update(personal_score=0, review='完整私人长评\n' * 5000,
            watch_status='on_hold', private_base_tags=[], custom_tags=['未知原标签'], metadata_initialized=True)
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        for _ in range(5):
            response = self.client.post('/api/recommendations/1/want/', {}, format='json')
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.data['created'])
            self.assertEqual(response.data['watch_status'], 'on_hold')
            self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)

    def test_user_tokens_and_writes_do_not_cross_accounts(self):
        row = self.page()['results'][0]
        url = f'/api/recommendations/{row["bangumi_id"]}/'
        own = self.client.post(url + 'want/', {}, format='json')
        before = list(UserAnimeRecord.objects.filter(user=self.user).order_by('pk').values())
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url, {'evidence_token': row['evidence_token']}).status_code, 409)
        self.assertIsNone(self.client.get(url).data['watch_status'])
        other = self.client.post(url + 'want/', {}, format='json')
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(other.data['record_id'], own.data['record_id'])
        self.assertEqual(other.data['anime_id'], own.data['anime_id'])
        self.assertEqual(list(UserAnimeRecord.objects.filter(user=self.user).order_by('pk').values()), before)

    def test_warmed_adult_unknown_and_forged_inputs_fail_closed(self):
        rows = self.page()['results']
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        for row, nsfw in zip(rows, (True, None)):
            identity = row['bangumi_id']
            RecommendationAnime.objects.filter(bangumi_id=identity).update(nsfw=nsfw)
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
            self.assertNotIn(identity, [item['bangumi_id'] for item in self.page()['results']])
        for body in ({'user_id': self.other.pk}, {'personal_score': 10}, {'review': 'forged'}):
            self.assertEqual(self.client.post('/api/recommendations/107/want/', body, format='json').status_code, 400)
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)

    def test_recommendation_failure_is_local_json_and_retry_restores_same_snapshot(self):
        before = self.page()
        with patch('recommendations.views.rank_candidates', side_effect=engine.RecommendationUnavailable()):
            response = self.client.get('/api/recommendations/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('evidence', response.data)
        self.assertNotIn('mode', response.data)
        self.assertEqual(self.client.get('/api/recommendations/101/').status_code, 200)
        self.assertEqual(self.page(), before)

    def test_settings_reject_missing_remote_production_and_sqlite_before_any_connection(self):
        from scripts.verify_recommendation_7c import validate_target
        for url in ('', 'postgresql://x:y@example.neon.tech/recommendation_isolated_tests',
                    'postgresql://x:y@127.0.0.1/production', 'sqlite:///:memory:'):
            with self.assertRaises(ValueError):
                validate_target(url)
            result = subprocess.run([sys.executable, '-c', 'import config.recommendation_pg_test_settings'],
                env={**os.environ, 'RECOMMENDATION_TEST_DATABASE_URL': url}, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'OperationalError', result.stderr)  # guard fails before connection
