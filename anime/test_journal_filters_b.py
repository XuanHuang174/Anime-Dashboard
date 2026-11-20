from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord, UserQuickFilter
from anime.quick_filtering import matches_year_range
from anime.utils.scraper import MetadataScraper
from recommendations.browse_controls import filter_index
from recommendations.v2_controls import matches
from recommendations.serializers import RecommendationQuerySerializer
from recommendations.exploration_contract import ExplorationQuerySerializer
from recommendations.credits_api_contract import WorksQuery


class BatchBContractTests(SimpleTestCase):
    def test_autofill_scans_past_noise_and_canonicalizes_without_guessing(self):
        labels = ['2026年10月', '日本', 'TV', 'SILVER LINK.', '合成作品', '神作', '一刷', '未知标签',
                  '搞笑', '喜剧', '治愈系', 'slice of life', 'ゆり', '战斗']
        tags = MetadataScraper._top_tags([{'name': tag, 'count': 100 - i} for i, tag in enumerate(labels)], ('合成作品',))
        self.assertEqual(tags.split(','), ['日常', '治愈', '喜剧', '百合', '战斗'])
        self.assertEqual(MetadataScraper._top_tags([{'name': '2026'}, None, {'name': 123}]), '')

    def test_year_range_is_inclusive_and_unknown_dates_are_excluded_only_when_bounded(self):
        for period in ('2020', '2020-1', '2020年1月', '2020-02-29'):
            self.assertTrue(matches_year_range(period, 2020, 2020), period)
        for period in ('', '未知', '2020-13', '2021-02-29', '2019-12'):
            self.assertFalse(matches_year_range(period, 2020, 2020), period)
        self.assertTrue(matches_year_range('', None, None))

    def test_all_query_contracts_accept_canonical_multi_type_and_reject_forged_selections(self):
        for serializer, base in ((RecommendationQuerySerializer, {}), (ExplorationQuerySerializer, {'intent': 'similar', 'seed_bangumi_id': 1}), (WorksQuery, {})):
            query = serializer(data={**base, 'type': 'TV,OVA'})
            self.assertTrue(query.is_valid(), query.errors)
            self.assertEqual(query.validated_data['type'], 'OVA,TV')
            for value in ('all,TV', 'TV,TV', 'TV,', ' TV', 'unknown', ['TV', 'OVA']):
                invalid = serializer(data={**base, 'type': value})
                self.assertFalse(invalid.is_valid(), (serializer, value))

    def test_v1_and_v2_filter_multi_types_before_candidate_truncation(self):
        options = {'type': 'OVA,TV', 'min_score': 0, 'year_from': 2020, 'year_to': 2021}
        rows = [{'bangumi_id': i, 'status_type': kind, 'score': 8, 'season': season} for i, kind, season in
                [(1, 'TV', '2020-01'), (2, 'OVA', '2021-04'), (3, '剧场版', '2020-01'), (4, 'TV', ''), (5, 'TV', '2022-01')]]
        index = {'features': [(row['bangumi_id'], frozenset({'日常'}), 8, row['status_type']) for row in rows], 'weights': {}}
        self.assertEqual([row[0] for row in filter_index(index, rows, options)['features']], [1, 2])
        self.assertEqual([row['bangumi_id'] for row in rows if matches(row, options)], [1, 2])


class BatchBJournalApiTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('batch-b-owner')
        self.other = get_user_model().objects.create_user('batch-b-other')
        self.client.force_authenticate(self.owner)
        for n, period in enumerate(('2019-10', '2020-1', '2021-07', '', '2022-01')):
            anime = Anime.objects.create(chinese_title=f'合成日常{n}', airing_period=period, base_tags=['日常'])
            UserAnimeRecord.objects.create(user=self.owner, anime=anime)
        self.filters = reverse('anime_api:quick-filter-list')
        self.records = reverse('anime_api:user-anime-record-list')

    def test_year_only_combination_persists_and_full_paged_results_agree(self):
        created = self.client.post(self.filters, {'name': '年份组合', 'tags': [], 'year_from': 2020, 'year_to': 2021}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['year_from'], 2020)
        params = {'quick_filter': created.data['id']}
        full = self.client.get(self.records, {**params, 'page_size': 'all'}).data
        full = full if isinstance(full, list) else full['results']
        paged = self.client.get(self.records, {**params, 'page_size': 1}).data
        self.assertEqual(len(full), 2)
        self.assertEqual(paged['count'], 2)
        self.assertEqual(full[0]['id'], paged['results'][0]['id'])
        self.client.force_authenticate(self.other)
        other = self.client.get(self.filters).data
        other = other if isinstance(other, list) else other['results']
        self.assertNotIn(created.data['id'], [row['id'] for row in other])

    def test_partial_bounds_validate_together_and_exclusions_still_apply(self):
        item = UserQuickFilter.objects.create(user=self.owner, name='单边', tags=['日常'], year_from=2020)
        url = reverse('anime_api:quick-filter-detail', kwargs={'pk': item.pk})
        self.assertEqual(self.client.patch(url, {'year_to': 2019}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'year_to': 2021, 'exclude_title_keywords': ['2']}, format='json').status_code, 200)
        rows = self.client.get(self.records, {'quick_filter': item.pk, 'page_size': 'all'}).data
        rows = rows if isinstance(rows, list) else rows['results']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['anime']['chinese_title'].endswith('1'))
        for bound in (999, 10000, 'invalid'):
            self.assertEqual(self.client.patch(url, {'year_to': bound}, format='json').status_code, 400)
