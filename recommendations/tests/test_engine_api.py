from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError
from rest_framework.test import APITestCase
from unittest.mock import patch

from anime.models import Anime, UserAnimeRecord
from recommendations.engine import feature_tags, public_features
from recommendations.models import RecommendationAnime, RecommendationPreference


class EngineAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='rank-owner')
        self.other = get_user_model().objects.create_user(username='rank-other')
        self.client.force_authenticate(self.user)
        for identity in range(1, 4):
            self.catalog(identity, '日常,音乐')
            anime = Anime.objects.create(bgm_id=identity, chinese_title=f'作品{identity}', base_tags=['日常', '音乐'])
            UserAnimeRecord.objects.create(user=self.user, anime=anime, watch_status='completed', personal_score='9.0')
        for identity in range(10, 25):
            self.catalog(identity, '日常,音乐' if identity < 18 else '音乐,科幻')
        self.catalog(90, '日常,音乐', nsfw=True)
        self.catalog(91, '日常,音乐', nsfw=None)

    def catalog(self, identity, tags, **kwargs):
        return RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'作品{identity}',
            tags=tags, score=8, nsfw=kwargs.pop('nsfw', False), **kwargs)

    def get(self, **params):
        return self.client.get('/api/recommendations/', params)

    def test_authentication_and_strict_queries(self):
        self.client.force_authenticate(None)
        for url in ['/api/recommendations/', '/api/recommendations/10/']:
            self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_authenticate(self.user)
        for params in [{'user_id': self.other.pk}, {'nsfw': True}, {'page': 2}, {'category': 'fake'},
                       {'page_size': 25}, {'min_score': 11}, {'page': 0}, {'type': 'fake'}]:
            self.assertEqual(self.get(**params).status_code, 400, params)
        self.assertEqual(self.client.get('/api/recommendations/?category=all&category=explore').status_code, 400)

    def test_safety_seeds_exclusion_and_truthful_evidence(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['mode'], 'personalized')
        self.assertEqual(response.data['count'], 15)
        for row in response.data['results']:
            self.assertNotIn(row['bangumi_id'], [1, 2, 3, 90, 91])
            self.assertNotIn('synopsis', row)
            self.assertNotIn('source_cover_url', row)
            self.assertNotIn('match_percentage', row)
            self.assertEqual(row['content_rating'], 'non_adult')
            evidence = row['evidence']
            self.assertEqual(evidence['source']['personal_score'], '9.0')
            self.assertIn(evidence['source']['bangumi_id'], [1, 2, 3])
            self.assertTrue(set(evidence['shared_tags']) <= set(row['tags']) & {'日常', '音乐'})
            self.assertIn(evidence['source']['title'], row['reason'])
        for identity in [90, 91, 999]:
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)

    def test_category_rankings_are_distinct(self):
        familiar = self.get(category='familiar').data['results']
        explore = self.get(category='explore').data['results']
        discover = self.get().data['results']
        self.assertEqual(familiar[0]['tags'], ['日常', '音乐'])
        self.assertTrue(all('科幻' in row['tags'] for row in explore))
        self.assertNotEqual([r['bangumi_id'] for r in familiar], [r['bangumi_id'] for r in discover])

    def test_pagination_user_bound_snapshot(self):
        first = self.get(page_size=3).data
        second = self.get(page_size=3, page=2, state_token=first['state_token'])
        self.assertEqual(second.status_code, 200)
        self.assertFalse(set(r['bangumi_id'] for r in first['results']) & set(r['bangumi_id'] for r in second.data['results']))
        self.assertEqual(self.get(page_size=3, page=2, category='explore', state_token=first['state_token']).status_code, 409)
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.get(page_size=3, page=2, state_token=first['state_token']).status_code, 409)

    def test_changes_to_rating_preferences_and_safety_invalidate_pagination(self):
        for change in [lambda: UserAnimeRecord.objects.filter(user=self.user).update(personal_score='9.1'),
                       lambda: RecommendationPreference.objects.create(user=self.user, tags=['科幻'], version=1),
                       lambda: RecommendationAnime.objects.filter(bangumi_id=10).update(nsfw=True)]:
            first = self.get(page_size=2).data
            change()
            self.assertEqual(self.get(page_size=2, page=2, state_token=first['state_token']).status_code, 409)

    def test_cache_never_bypasses_current_safety_or_invents_changed_tags(self):
        self.get()
        RecommendationAnime.objects.filter(bangumi_id=10).update(nsfw=None)
        RecommendationAnime.objects.filter(bangumi_id=11).update(tags='悬疑')
        self.assertFalse({10, 11} & {row['bangumi_id'] for row in self.get().data['results']})

    def test_cold_start_and_explicit_preferences_not_fake_personalization(self):
        self.client.force_authenticate(self.other)
        result = self.get().data
        self.assertEqual(result['mode'], 'cold_start')
        self.assertEqual(result['results'], [])
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        result = self.get().data
        self.assertEqual(result['mode'], 'preference_based')
        for row in result['results']:
            self.assertIsNone(row['evidence']['source'])
            self.assertEqual(row['evidence']['kind'], 'explicit_preferences')
            self.assertNotIn('9.0', row['reason'])

    def test_detail_private_isolation_and_external_cover_not_returned(self):
        RecommendationAnime.objects.filter(bangumi_id=10).update(cover_url='https://external.test/a.jpg', synopsis='真实简介')
        first = self.client.get('/api/recommendations/10/').data
        self.assertEqual(first['synopsis'], '真实简介')
        self.assertEqual(first['cover_url'], '')
        self.assertIsNotNone(first['evidence'])
        self.client.force_authenticate(self.other)
        other = self.client.get('/api/recommendations/10/').data
        self.assertIsNone(other['evidence'])
        self.assertIsNone(other['watch_status'])

    def test_tag_cleanup_and_idf(self):
        self.assertEqual(feature_tags('日常,搞笑,日本,2026,年度最佳,二刷,作品A,音乐', ['音乐']), {'日常', '喜剧'})
        index = public_features()
        self.assertLess(index['weights']['音乐'], index['weights']['科幻'])
        self.assertTrue(all(1 <= w <= 3 for w in index['weights'].values()))
        self.assertNotIn('rank-owner', str(index))

    def test_filter_applied_before_bounded_candidate_pool(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'番剧{i}',
            tags='音乐,日常', score=10, nsfw=False, status_type='TV') for i in range(1000, 1450)])
        self.catalog(2000, '音乐,科幻', status_type='OVA')
        response = self.get(type='OVA')
        self.assertEqual([row['bangumi_id'] for row in response.data['results']], [2000])

    def test_list_query_count_does_not_grow_with_page_size(self):
        self.get()
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as one:
            self.get(page_size=1)
        with CaptureQueriesContext(connection) as many:
            self.get(page_size=12)
        self.assertEqual(len(one), len(many))

    def test_database_error_returns_json_503(self):
        with patch('recommendations.views.rank_candidates', side_effect=DatabaseError('secret db host')):
            response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', str(response.data))

    def test_existing_records_filter_and_current_user_status(self):
        anime = Anime.objects.create(bgm_id=10, chinese_title='已在想看')
        UserAnimeRecord.objects.create(user=self.user, anime=anime, watch_status='plan')
        self.assertNotIn(10, [row['bangumi_id'] for row in self.get().data['results']])
        result = self.get(hide_recorded=False).data['results']
        self.assertEqual(next(row for row in result if row['bangumi_id'] == 10)['watch_status'], 'plan')
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        self.client.force_authenticate(self.other)
        detail = self.client.get('/api/recommendations/10/').data
        self.assertIsNone(detail['watch_status'])

    def test_empty_filters_are_honest_not_mock_results(self):
        response = self.get(min_score=9)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])

    def test_unclassified_high_scores_cannot_create_profile_or_evidence(self):
        UserAnimeRecord.objects.filter(user=self.user).update(personal_score='8.4')
        self.assertEqual(self.get().data['mode'], 'cold_start')
        UserAnimeRecord.objects.filter(user=self.user).update(personal_score='9.0')
        RecommendationAnime.objects.filter(bangumi_id__in=[1, 2, 3]).update(nsfw=None)
        self.assertEqual(self.get().data['mode'], 'cold_start')

    def test_catalog_size_budget_fails_as_json_503(self):
        with patch('recommendations.engine.MAX_CATALOG_ROWS', 1):
            response = self.get()
        self.assertEqual(response.status_code, 503)
