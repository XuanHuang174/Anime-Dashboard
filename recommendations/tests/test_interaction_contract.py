"""7A confirmed writes vs fresh detail contexts; isolated synthetic accounts only."""
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import UserAnimeRecord
from recommendations.models import RecommendationAnime
from .test_contracts_api import ContractFixtures


class InteractionContractTests(ContractFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.force_authenticate(self.user)
        self.seeds = [self.seed(identity, score='9.0', tags=['音乐', '日常']) for identity in (1, 2, 3)]
        for identity in range(101, 109):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'当前作品{identity}',
                title_jp=f'Original {identity}', season='2026-04', score=7.5, tags='音乐,日常', nsfw=False)

    def page(self):
        response = self.client.get('/api/recommendations/', {'page_size': 2})
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_confirmed_add_invalidates_old_evidence_and_reloads_whole_subject_after_it_leaves_list(self):
        first = self.page()
        row = first['results'][0]
        url = f'/api/recommendations/{row["bangumi_id"]}/'
        before = list(UserAnimeRecord.objects.filter(user=self.user).order_by('pk').values())
        result = self.client.post(url + 'want/', {}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertTrue(result.data['created'])
        self.assertEqual(result.data['watch_status'], 'plan')
        old = self.client.get(url, {'evidence_token': row['evidence_token']})
        self.assertEqual(old.status_code, 409)
        self.assertNotIn('evidence', old.data)
        with CaptureQueriesContext(connection) as queries:
            fresh = self.client.get(url)
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(fresh.data['watch_status'], 'plan')
        self.assertEqual((fresh.data['title_zh'], fresh.data['season']), (row['title_zh'], row['season']))
        self.assertNotEqual(fresh.data['evidence_token'], row['evidence_token'])
        self.assertNotEqual(fresh.data['evidence_context']['profile_version'], row['evidence_context']['profile_version'])
        self.assertFalse(any(q['sql'].strip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in queries))
        fresh_token = self.client.get(url, {'evidence_token': fresh.data['evidence_token']})
        self.assertEqual(fresh_token.status_code, 200)
        self.assertEqual(fresh_token.data, fresh.data)
        self.assertNotIn(row['bangumi_id'], [item['bangumi_id'] for item in self.page()['results']])
        self.assertEqual(list(UserAnimeRecord.objects.filter(pk__in=[record['id'] for record in before])
                              .order_by('pk').values()), before)
        created = UserAnimeRecord.objects.get(pk=result.data['record_id'])
        self.assertIsNone(created.personal_score)
        self.assertEqual(created.review, '')

    def test_an_add_of_a_different_work_also_invalidates_current_detail(self):
        first = self.page()['results']
        a, b = first[0], first[1]
        self.assertEqual(self.client.post(f'/api/recommendations/{a["bangumi_id"]}/want/', {}, format='json').status_code, 201)
        url = f'/api/recommendations/{b["bangumi_id"]}/'
        self.assertEqual(self.client.get(url, {'evidence_token': b['evidence_token']}).status_code, 409)
        fresh = self.client.get(url)
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(fresh.data['bangumi_id'], b['bangumi_id'])
        self.assertIsNone(fresh.data['watch_status'])

    def test_existing_ack_preserves_all_personal_fields_and_does_not_require_an_invented_new_revision(self):
        record = self.seeds[0]
        UserAnimeRecord.objects.filter(pk=record.pk).update(review='原始长评完整保留', custom_tags=['二刷'])
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        old = self.client.get('/api/recommendations/1/').data
        for _ in range(3):
            result = self.client.post('/api/recommendations/1/want/', {}, format='json')
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.data['created'])
            self.assertEqual(result.data['watch_status'], 'completed')
            self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)
        # A fresh read is required by the UI, but an idempotent no-op need not
        # fabricate a new version/token when the effective inputs did not change.
        self.assertEqual(self.client.get('/api/recommendations/1/').data['evidence_token'], old['evidence_token'])

    def test_failed_or_forged_add_never_establishes_new_context_and_other_user_is_isolated(self):
        row = self.page()['results'][0]
        url = f'/api/recommendations/{row["bangumi_id"]}/'
        for body in ({'user_id': self.other.pk}, {'personal_score': 0}, {'record_id': self.seeds[0].pk}):
            self.assertEqual(self.client.post(url + 'want/', body, format='json').status_code, 400)
        self.assertEqual(self.client.get(url, {'evidence_token': row['evidence_token']}).status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url, {'evidence_token': row['evidence_token']}).status_code, 409)
        own = self.client.get(url)
        self.assertEqual(own.status_code, 200)
        self.assertIsNone(own.data['watch_status'])
        self.assertIsNone(own.data['evidence'])
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.other).count(), 0)

    def test_unsafe_work_cannot_be_loaded_for_resource_entry_even_after_a_safe_card_was_seen(self):
        row = self.page()['results'][0]
        for nsfw in (True, None):
            RecommendationAnime.objects.filter(bangumi_id=row['bangumi_id']).update(nsfw=nsfw)
            url = f'/api/recommendations/{row["bangumi_id"]}/'
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url + 'want/', {}, format='json').status_code, 404)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.user).count(), 3)
