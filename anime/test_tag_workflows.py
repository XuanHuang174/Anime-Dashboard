from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from anime.models import Anime, UserAnimeRecord, UserProfile, UserQuickFilter
from anime.views import BangumiCollectionBatchImportView


class TagWorkflowTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('tags-owner')
        self.other = get_user_model().objects.create_user('tags-other')
        self.client.force_authenticate(self.owner)
        self.url = reverse('anime_api:tag-presets')

    def test_presets_persist_are_idempotent_and_owned_without_mutating_records(self):
        self.assertEqual(self.client.post(self.url, {'tag': ' 群像 '}).data, {'tags': ['群像']})
        self.client.post(self.url, {'tag': '群像'})
        self.client.post(self.url, {'tag': '下饭'})
        self.assertEqual(self.client.get(self.url).data['tags'], ['群像', '下饭'])
        self.assertIn('no-store', self.client.get(self.url)['Cache-Control'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).data['tags'], [])
        self.client.delete(self.url, {'tag': '群像'}, format='json')
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(self.url).data['tags'], ['群像', '下饭'])
        self.assertEqual(self.client.delete(self.url, {'tag': '群像'}, format='json').data['tags'], ['下饭'])
        self.assertFalse(UserAnimeRecord.objects.exists())
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, [401, 403])

    def test_preset_bounds_and_private_profile_projection(self):
        self.assertEqual(self.client.post(self.url, [], format='json').status_code, 400)
        for value in ['', ' ' * 3, '字' * 51, []]:
            self.assertEqual(self.client.post(self.url, {'tag': value}, format='json').status_code, 400)
        UserProfile.objects.filter(user=self.owner).update(tag_presets=[f'tag{i}' for i in range(64)])
        self.assertEqual(self.client.post(self.url, {'tag': 'extra'}).status_code, 400)
        from anime.serializers import PublicUserProfileSerializer
        self.assertNotIn('tag_presets', PublicUserProfileSerializer(self.owner.profile).data)

    def test_negative_filters_apply_before_paging_and_honor_private_japanese_names(self):
        for title, japanese, tags in [('日常甲', 'Daily', ['日常']), ('日常乙', 'Daily ova', ['日常']), ('日常丙', 'Daily', ['日常', '剧场版'])]:
            anime = Anime.objects.create(chinese_title=title, japanese_title=japanese, airing_period='2026-1', base_tags=tags)
            UserAnimeRecord.objects.create(user=self.owner, anime=anime)
        response = self.client.post(reverse('anime_api:quick-filter-list'), {'name': '排除组合', 'tags': [],
            'exclude_tags': ['剧场版'], 'exclude_title_keywords': ['OVA'], 'match_mode': 'any'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        url = reverse('anime_api:user-anime-record-list')
        query = {'quick_filter': response.data['id']}
        complete = self.client.get(url, query)
        paged = self.client.get(url, {**query, 'page_size': 1})
        self.assertEqual(paged.data['count'], 1)
        def rows(response):
            return response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertEqual(len(rows(complete)), 1)
        self.assertEqual([r['id'] for r in rows(complete)], [r['id'] for r in rows(paged)])
        self.assertEqual(rows(complete)[0]['anime']['chinese_title'], '日常甲')

    def test_filter_conflicts_limits_and_partial_update(self):
        url = reverse('anime_api:quick-filter-list')
        for payload in [{'tags': []}, {'tags': ['日常'], 'exclude_tags': ['日常']},
                        {'tags': [], 'exclude_title_keywords': ['a'] * 5}]:
            self.assertEqual(self.client.post(url, {'name': 'invalid', **payload}, format='json').status_code, 400)
        item = UserQuickFilter.objects.create(user=self.owner, name='existing', tags=['日常'])
        detail = reverse('anime_api:quick-filter-detail', kwargs={'pk': item.pk})
        self.assertEqual(self.client.patch(detail, {'exclude_tags': ['日常']}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(detail, {'exclude_tags': ['剧场版']}, format='json').status_code, 200)

    def test_bangumi_collection_names_drop_only_outer_book_brackets(self):
        for original, expected in [('《《日常》》', '日常'), ('作品中的《章节》', '作品中的《章节》')]:
            data = BangumiCollectionBatchImportView._subject_defaults({'name_cn': original}, request_user=self.owner)
            self.assertEqual(data['chinese_title'], expected)
