"""Portable artwork, public source URLs and bounded admin rendering; synthetic only."""
import json
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord, FeaturedSubmission
from anime.serializers import PublicFeaturedSubmissionSerializer
from recommendations.binding import try_auto_link
from recommendations.models import RecommendationAnime


@override_settings(ALLOWED_HOSTS=['testserver'], MEDIA_URL='https://media.xh-anime.com/')
class JournalPortabilityTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('portable-owner')
        self.client.force_authenticate(self.user)
        self.anime = Anime.objects.create(chinese_title='合成作品', japanese_title='Synthetic', airing_period='2026-01',
            poster_url='https://bgm-img-proxy.xhcytus100.workers.dev/pic/old.jpg')
        self.record = UserAnimeRecord.objects.create(user=self.user, anime=self.anime)

    def export(self):
        response = self.client.get(reverse('anime_api:journal-export'))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def restore(self, data):
        return self.client.post(reverse('anime_api:journal-import'), data, format='json')

    def test_export_restore_uses_displayed_hosted_cover_without_fetching_or_rewriting(self):
        self.record.private_poster_image.name = 'anime/posters/actual.jpg'
        self.record.save(update_fields=['private_poster_image'])
        payload = self.export()
        expected = 'https://media.xh-anime.com/anime/posters/actual.jpg'
        self.assertEqual(payload['records'][0]['anime']['poster_url'], expected)
        with patch('anime.utils.scraper.MetadataScraper.download_bangumi_poster') as download:
            self.assertEqual(self.restore(payload).status_code, 200)
            download.assert_not_called()
        restored = UserAnimeRecord.objects.get(user=self.user)
        self.assertEqual(restored.private_poster_url, expected)
        self.assertFalse(restored.private_poster_image)
        self.assertEqual(self.export()['records'][0]['anime']['poster_url'], expected)

    def test_legacy_display_image_and_cover_aliases_are_not_silently_discarded(self):
        for field in ('poster_image', 'cover'):
            payload = self.export()
            item = payload['records'][0]['anime']
            item['poster_url'] = 'https://bgm-img-proxy.xhcytus100.workers.dev/pic/old.jpg' if field == 'poster_image' else ''
            item[field] = 'https://media.xh-anime.com/legacy.jpg?version=7'
            self.assertEqual(self.restore(payload).status_code, 200)
            self.assertEqual(self.export()['records'][0]['anime']['poster_url'], item[field])

    def test_explicit_urls_and_intentionally_empty_covers_round_trip(self):
        for value in ('https://media.xh-anime.com/original.jpg?x=1&y=2',
                      'https://bgm-img-proxy.xhcytus100.workers.dev/pic/original.jpg', ''):
            payload = self.export(); payload['records'][0]['anime']['poster_url'] = value
            self.assertEqual(self.restore(payload).status_code, 200)
            self.assertEqual(self.export()['records'][0]['anime']['poster_url'], value)

    def test_invalid_cover_alias_fails_before_replacing_any_records(self):
        before = list(UserAnimeRecord.objects.values())
        for value in ('blob:temporary', '/media/local.jpg', 'file:///C:/cover.jpg', 'https://user:password@example.com/a.jpg', {}):
            payload = self.export(); payload['records'][0]['anime']['poster_image'] = value
            result = self.restore(payload)
            self.assertEqual(result.status_code, 400, result.data)
            self.assertIn('poster_url', result.data[0]['anime'])
            self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def link(self):
        catalog = RecommendationAnime.objects.create(bangumi_id=12345, title_zh='合成作品', title_jp='Synthetic',
            season='2026-01', status_type='TV', score=8, nsfw=False, tags='日常')
        try_auto_link(self.record)
        self.assertTrue(hasattr(self.record, 'recommendation_identity'))
        return catalog

    def public(self):
        profile = self.user.profile; profile.is_public = True; profile.public_status = 'approved'; profile.save()
        return self.client.get('/api/public-journal/users/portable-owner/records/').data['results']

    def test_public_alias_link_matches_private_without_exposing_private_reference(self):
        self.link()
        private = self.client.get(f'/api/records/{self.record.pk}/').data
        self.client.force_authenticate(None)
        public = self.public()[0]
        self.assertEqual(private['anime']['bangumi_url'], 'https://bgm.tv/subject/12345')
        self.assertEqual(public['anime']['bangumi_url'], private['anime']['bangumi_url'])
        self.assertNotIn('identity_reference', public['anime'])
        self.assertNotIn('autofill_ticket', public['anime'])
        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            response = self.client.get('/api/showcase/')
        self.assertEqual(response.data['results'][0]['anime']['bangumi_url'], private['anime']['bangumi_url'])

    def test_public_alias_inactive_or_stale_does_not_fabricate_link(self):
        self.link()
        link = self.record.recommendation_identity
        link.active = False; link.save()
        self.assertEqual(self.public()[0]['anime']['bangumi_url'], '')
        link.active = True; link.save()
        self.record.private_chinese_title = 'changed'; self.record.save()
        self.assertEqual(self.public()[0]['anime']['bangumi_url'], '')

    def test_explicit_public_link_does_not_require_alias(self):
        self.record.private_bangumi_url = 'https://bgm.tv/subject/6789'; self.record.save()
        self.assertEqual(self.public()[0]['anime']['bangumi_url'], self.record.private_bangumi_url)

    def test_public_source_resolution_has_bounded_queries(self):
        self.link()
        self.public()  # Publish the profile before measuring the read path.
        endpoint = '/api/public-journal/users/portable-owner/records/?page_size=100'
        with CaptureQueriesContext(connection) as small:
            self.client.get(endpoint)
        others = Anime.objects.bulk_create([Anime(chinese_title=f'额外作品{i}', airing_period='2026-01') for i in range(35)])
        UserAnimeRecord.objects.bulk_create([UserAnimeRecord(user=self.user, anime=row, metadata_initialized=True) for row in others])
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 36)
        self.assertLessEqual(len(large), len(small) + 1)

    def test_new_submission_freezes_link_and_old_submission_stays_empty(self):
        self.link()
        old = FeaturedSubmission.objects.create(user=self.user, chinese_title='旧快照', audit_status='approved')
        result = self.client.post('/api/featured-submissions/submit/', {'anime_id': self.anime.pk}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['bangumi_url'], 'https://bgm.tv/subject/12345')
        self.record.private_bangumi_url = 'https://bgm.tv/subject/6789'; self.record.save()
        snapshot = FeaturedSubmission.objects.get(pk=result.data['id'])
        self.assertEqual(PublicFeaturedSubmissionSerializer(snapshot).data['bangumi_url'], 'https://bgm.tv/subject/12345')
        self.assertEqual(PublicFeaturedSubmissionSerializer(old).data['bangumi_url'], '')


@override_settings(ALLOWED_HOSTS=['testserver'])
class FeaturedAdminRenderingTests(APITestCase):
    def test_detail_render_queries_do_not_grow_with_private_record_count(self):
        user = get_user_model().objects.create_superuser('admin-synthetic', 'admin@example.com', 'synthetic-only')
        anime = Anime.objects.create(chinese_title='合成来源', airing_period='2026-1')
        record = UserAnimeRecord.objects.create(user=user, anime=anime)
        item = FeaturedSubmission.objects.create(user=user, source_anime=anime, source_record=record, chinese_title='合成投稿')
        client = Client(); client.force_login(user)
        path = reverse('admin:anime_featuredsubmission_change', args=[item.pk])
        with CaptureQueriesContext(connection) as small:
            response = client.get(path)
        self.assertEqual(response.status_code, 200)
        others = Anime.objects.bulk_create([Anime(chinese_title=f'无关作品{i}', airing_period='2026-1') for i in range(150)])
        UserAnimeRecord.objects.bulk_create([UserAnimeRecord(user=user, anime=row, metadata_initialized=True) for row in others])
        with CaptureQueriesContext(connection) as large:
            response = client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(large), len(small) + 2)
        self.assertNotContains(response, '无关作品149')
        model_admin = admin.site._registry[FeaturedSubmission]
        request = RequestFactory().get(path); request.user = user
        form = model_admin.get_form(request, obj=item)
        self.assertNotIn('source_record', form.base_fields)
        self.assertNotIn('user', form.base_fields)
        self.assertIn('audit_status', form.base_fields)
