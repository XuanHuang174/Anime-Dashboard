"""Batch A: all requests use synthetic rows, never live upstream services."""
from unittest.mock import patch
import json

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.urls import reverse
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase
from rest_framework.exceptions import ValidationError

from anime.models import Anime, UserAnimeRecord
from anime.journal_sources import source_ticket, verify_source_ticket, normalize_bangumi_url
from recommendations.models import RecommendationAnime
from recommendations.resolution import resolved_records
from recommendations.review import review_contract


class JournalIdentitySourcesTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('source-owner')
        self.other = get_user_model().objects.create_user('source-other')
        self.client.force_authenticate(self.user)
        self.catalog = RecommendationAnime.objects.create(bangumi_id=12345, title_zh='合成作品', title_jp='Synthetic A',
            season='2026-01', status_type='TV', score=8, nsfw=False, tags='日常')
        self.payload = {'chinese_title': '合成作品', 'japanese_title': 'Synthetic A', 'airing_period': '2026-01',
                        'studio': 'Synthetic Studio', 'episodes': '12', 'base_tags': ['日常']}

    def add(self, **kwargs):
        return self.client.post('/api/records/add/', {'anime_data': self.payload, **kwargs}, format='json')

    def test_public_library_add_links_immediately_for_unrated_plan_and_preserves_shared_row(self):
        anime = Anime.objects.create(**self.payload)
        before = Anime.objects.values().get(pk=anime.pk)
        response = self.client.post('/api/records/add/', {'anime_id': anime.pk, 'watch_status': 'plan'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        row = resolved_records(self.user)[0]
        self.assertEqual(row['identity_state'], 'linked')
        self.assertEqual(row['resolved_bangumi_id'], 12345)
        self.assertEqual(response.data['anime']['confirmed_bangumi_id'], 12345)
        self.assertEqual(response.data['anime']['bangumi_url'], 'https://bgm.tv/subject/12345')
        self.assertEqual(Anime.objects.values().get(pk=anime.pk), before)
        self.assertEqual(UserAnimeRecord.objects.get().personal_score, None)
        self.assertFalse(UserAnimeRecord.objects.filter(user=self.other).exists())

    def test_verified_autofill_survives_custom_title_without_changing_public_identity(self):
        self.payload.update(chinese_title='我的自定义名称', japanese_title='', airing_period='未定档')
        response = self.add(autofill_ticket=source_ticket(self.user, 12345), bangumi_url='http://bangumi.tv/subject/12345?from=search')
        self.assertEqual(response.status_code, 201, response.data)
        record = UserAnimeRecord.objects.get()
        self.assertEqual(record.verified_bangumi_id, 12345)
        self.assertIsNone(record.anime.bgm_id)
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 12345)
        self.assertEqual(response.data['anime']['bangumi_url'], 'https://bgm.tv/subject/12345')

    def test_forged_foreign_or_expired_source_is_rejected(self):
        for ticket in ('forged', source_ticket(self.other, 12345)):
            response = self.add(autofill_ticket=ticket)
            self.assertEqual(response.status_code, 400)
        expired = signing.dumps({'owner': self.user.pk, 'subject': 12345}, salt='wrong-salt')
        self.assertEqual(self.add(autofill_ticket=expired).status_code, 400)
        ticket = source_ticket(self.user, 12345)
        import time
        with patch('django.core.signing.time.time', return_value=time.time() + 3601):
            with self.assertRaises(ValidationError):
                verify_source_ticket(self.user, ticket)
        self.assertEqual(UserAnimeRecord.objects.count(), 0)

    def test_manual_link_is_only_reference_and_cannot_claim_verified_identity(self):
        self.payload.update(chinese_title='Unknown synthetic', japanese_title='', airing_period='2025-01')
        response = self.add(bangumi_url='https://bgm.tv/subject/12345', verified_bangumi_id=12345)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(UserAnimeRecord.objects.get().verified_bangumi_id)
        self.assertEqual(resolved_records(self.user)[0]['identity_state'], 'unlinked')

    def test_source_url_conflicts_roll_back_the_whole_add(self):
        response = self.add(autofill_ticket=source_ticket(self.user, 12345), bangumi_url='https://bgm.tv/subject/999')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UserAnimeRecord.objects.count(), 0)

    def test_link_edit_is_private_and_rejects_other_subject(self):
        response = self.add(autofill_ticket=source_ticket(self.user, 12345))
        record = UserAnimeRecord.objects.get()
        before = Anime.objects.values().get(pk=record.anime_id)
        response = self.client.patch(f'/api/records/{record.pk}/details/', {
            'anime': {'bangumi_url': 'https://bgm.tv/subject/999'}}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(f'/api/records/{record.pk}/details/', {
            'anime': {'bangumi_url': 'https://bangumi.tv/subject/12345', 'baike_url': 'https://zh.moegirl.org.cn/Synthetic'}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record.refresh_from_db()
        self.assertEqual(record.private_bangumi_url, 'https://bgm.tv/subject/12345')
        self.assertEqual(Anime.objects.values().get(pk=record.anime_id), before)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/records/{record.pk}/').status_code, 404)

    def test_verified_source_outside_catalog_has_explicit_state_and_adult_is_not_silently_allowed(self):
        self.add(autofill_ticket=source_ticket(self.user, 99999))
        self.assertEqual(resolved_records(self.user)[0]['identity_state'], 'not_in_catalog')
        self.catalog.nsfw = True; self.catalog.save()
        self.payload['chinese_title'] = 'Another synthetic'
        self.add(autofill_ticket=source_ticket(self.user, 12345))
        self.assertEqual(resolved_records(self.user)[1]['identity_state'], 'unsafe_or_unknown')

    def test_all_status_review_is_independent_of_score_threshold_and_does_not_mutate_records(self):
        for index, status in enumerate(('plan', 'watching', 'on_hold', 'completed')):
            anime = Anime.objects.create(chinese_title=f'合成作品 {index}', japanese_title='Synthetic A', airing_period='2026-01')
            UserAnimeRecord.objects.create(user=self.user, anime=anime, watch_status=status, personal_score=1 if index == 3 else None)
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        all_rows = review_contract(self.user, {'scope': 'all', 'page': 1, 'page_size': 10})
        sampled = review_contract(self.user, {'scope': 'high-score', 'page': 1, 'page_size': 10})
        self.assertEqual(all_rows['count'], 4)
        self.assertEqual(sampled['count'], 0)
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)

    def test_link_validation_rejects_non_subject_and_untrusted_domains(self):
        for value in ('javascript:alert(1)', 'https://bgm.tv.evil/subject/123', 'https://bgm.tv/subject/0', 'https://user@bgm.tv/subject/1'):
            with self.assertRaises(ValidationError):
                normalize_bangumi_url(value)

    def test_backup_roundtrip_preserves_private_links_and_verified_catalog_identity(self):
        self.payload.update(chinese_title='我的自定义名称', japanese_title='Custom original')
        self.add(autofill_ticket=source_ticket(self.user, 12345))
        exported = self.client.get(reverse('anime_api:journal-export'))
        self.assertEqual(exported.status_code, 200)
        payload = json.loads(exported.content)
        self.assertEqual(payload['records'][0]['anime']['bangumi_url'], 'https://bgm.tv/subject/12345')
        restored = self.client.post(reverse('anime_api:journal-import'), payload, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(UserAnimeRecord.objects.get().private_bangumi_url, 'https://bgm.tv/subject/12345')
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 12345)

    def test_verified_source_is_owned_for_recommendation_without_duplicate_record(self):
        from recommendations.identity import recommendation_identity
        self.payload.update(chinese_title='我的自定义名称', japanese_title='')
        self.add(autofill_ticket=source_ticket(self.user, 12345))
        identity = recommendation_identity(user=self.user, bangumi_id=12345)
        self.assertEqual(identity['record_id'], UserAnimeRecord.objects.get().pk)

    def test_autofill_endpoint_returns_owner_bound_source_after_upstream_verification(self):
        data = {'bangumi_id': 12345, 'chinese_title': '合成作品', 'japanese_title': 'Synthetic A',
                'air_date': '2026-01-01', 'airing_period': '2026-01', 'studio': '', 'episodes': '12',
                'description': '', 'poster_url': '', 'tags': '日常'}
        with patch('anime.views.MetadataScraper.get_bangumi_details', return_value=data), patch('anime.views.MetadataScraper.get_moegirl_url', return_value=''):
            response = self.client.get('/api/external/autofill/', {'id': 12345})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(verify_source_ticket(self.user, response.data['autofillTicket']), 12345)
        self.assertEqual(response.data['bangumiUrl'], 'https://bgm.tv/subject/12345')

    def test_listing_links_does_not_add_one_lookup_or_transaction_per_legacy_record(self):
        def record(index):
            anime = Anime.objects.create(chinese_title=f'Legacy {index}', airing_period='2020-01')
            UserAnimeRecord.objects.create(user=self.user, anime=anime)
        record(1)
        with CaptureQueriesContext(connection) as small:
            response = self.client.get('/api/records/', {'page_size': 'all'})
        self.assertEqual(response.status_code, 200)
        for index in range(2, 7): record(index)
        with CaptureQueriesContext(connection) as large:
            response = self.client.get('/api/records/', {'page_size': 'all'})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(large), len(small) + 1)
