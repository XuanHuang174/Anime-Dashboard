from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import NotAuthenticated
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from anime.models import Anime, UserAnimeRecord
from recommendations.binding import bind_record
from recommendations.engine import user_context
from recommendations.extraction import _genres
from recommendations.features import extract_genres, FEATURE_RULE_VERSION
from recommendations.models import RecommendationAnime, RecommendationPreference, RecommendationRecordIdentity
from recommendations.services import profile_data
from recommendations.taxonomy import TAXONOMY_VERSION


class GenreCleaningTests(SimpleTestCase):
    def test_aliases_across_fields_are_one_feature_and_inputs_unchanged(self):
        base = ['搞笑', '喜剧', ' 真百 ', '轻百', '2026', '日本', '作品！']
        custom = ['百合', '年度最佳', '神作', '未知标签', '二刷', '喜剧']
        before = deepcopy((base, custom))
        value = extract_genres(base, custom, titles=('《作品！》',))
        self.assertEqual(value['tags'], {'喜剧', '百合'})
        self.assertEqual(value['sources']['百合'], ('private_base_tags', 'custom_tags'))
        self.assertEqual(value['ignored_counts'], {'invalid': 0, 'year': 1, 'country': 1,
                         'management': 3, 'work_title': 1, 'unknown': 1})
        self.assertEqual((base, custom), before)

    def test_strings_nfkc_known_aliases_and_never_substring_guesses(self):
        value = extract_genres('ＳＣＩ－ＦＩ，日常,真百合', ['不太喜欢日常', '音乐画风', '二刷科幻'])
        self.assertEqual(value['tags'], {'科幻', '日常', '百合'})
        self.assertEqual(value['ignored_counts']['unknown'], 3)

    def test_malformed_json_empty_values_and_non_string_labels_fail_closed(self):
        for base, custom in (({'音乐': True}, 12), (None, [True, 1, {}, None, '']), ([], [])):
            self.assertEqual(extract_genres(base, custom)['tags'], set())

    def test_all_management_year_country_and_own_title_labels_are_excluded(self):
        value = extract_genres(['2026年7月', '2026-07', '1990年代', '美国', 'Japan', '12刷', '神作', '音乐'],
                               titles=('《音乐》',))
        self.assertEqual(value['tags'], set())
        self.assertEqual(value['ignored_counts']['work_title'], 1)
        self.assertEqual(value['ignored_counts']['year'], 3)


class ExtractionAPITests(APITestCase):
    url = '/api/recommendations/profile/'

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='feature-owner')
        self.other = get_user_model().objects.create_user(username='feature-other')
        self.client.force_authenticate(self.user)

    def work(self, identity, *, user=None, score='9.0', status='completed', nsfw=False,
             base=None, custom=None, catalog_tags='日常,音乐', linked=True):
        RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'作品{identity}', title_jp=f'Original{identity}',
            season='2026-04', nsfw=nsfw, tags=catalog_tags, score=10)
        shared = Anime.objects.create(bgm_id=identity if linked else None, chinese_title=f'作品{identity}',
            japanese_title=f'Original{identity}', airing_period='2026-4', is_active=False,
            base_tags=['日常'] if base is None else base)
        return UserAnimeRecord.objects.create(user=user or self.user, anime=shared, personal_score=score,
            watch_status=status, custom_tags=[] if custom is None else custom, review='私人长评不参与推断')

    def get(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_empty_additive_typed_contract_and_no_get_writes(self):
        with CaptureQueriesContext(connection) as queries:
            data = self.get()
        self.assertEqual(data['contract_version'], 'recommendations.3a.v1')
        self.assertEqual(data['extraction']['contract_version'], 'recommendations.extraction.v1')
        self.assertEqual(data['extraction']['rule_version'], FEATURE_RULE_VERSION)
        self.assertEqual(data['extraction']['taxonomy_version'], TAXONOMY_VERSION)
        self.assertEqual(data['extraction']['explicit_preferences']['tags'], [])
        self.assertEqual(data['extraction']['trusted_profile']['usable_work_count'], 0)
        self.assertIs(data['extraction']['common_topics']['requires_confirmation'], True)
        self.assertIs(data['extraction']['common_topics']['used_for_recommendation'], False)
        self.assertEqual(RecommendationPreference.objects.count(), 0)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))

    def test_explicit_trusted_and_observations_are_separate_without_overwrite(self):
        preference = RecommendationPreference.objects.create(user=self.user, tags=['科幻'], version=7)
        self.work(1, base=['音乐'])
        self.work(2, score=None, base=['治愈'])
        before = RecommendationPreference.objects.filter(pk=preference.pk).values().get()
        data = self.get()['extraction']
        self.assertEqual(data['explicit_preferences']['tags'], ['科幻'])
        self.assertEqual([tag['tag'] for tag in data['trusted_profile']['tag_summary']], ['音乐'])
        self.assertEqual([tag['tag'] for tag in data['common_topics']['tag_summary']], ['治愈'])
        self.assertEqual(RecommendationPreference.objects.filter(pk=preference.pk).values().get(), before)
        self.assertEqual(user_context(self.user)['mode'], 'preference_based')

    def test_null_is_unrated_but_zero_is_rated_and_never_positive(self):
        self.work(1, score=None)
        self.work(2, score=Decimal('0.0'))
        self.work(3, score='8.4')
        self.work(4, score='10.0', status='watching')
        data = self.get()
        self.assertEqual(data['extraction']['record_stats']['rated_record_count'], 3)
        self.assertEqual(data['extraction']['record_stats']['unrated_record_count'], 1)
        self.assertEqual(data['eligible_sample_count'], 0)
        self.assertEqual(data['extraction']['common_topics']['work_count'], 4)
        self.assertEqual(user_context(self.user)['seeds'], [])
        self.assertEqual(user_context(self.user)['mode'], 'cold_start')

    def test_catalog_score_cannot_replace_personal_score_or_completed_status(self):
        for identity, score, status in ((1, '8.4', 'completed'), (2, None, 'completed'),
                                       (3, '9.9', 'plan'), (4, '9.9', 'on_hold')):
            self.work(identity, score=score, status=status)
        self.assertEqual(self.get()['usable_sample_count'], 0)
        self.work(5, score='8.5')
        RecommendationAnime.objects.filter(bangumi_id=5).update(score=0)
        self.assertEqual(self.get()['usable_sample_count'], 1)

    def test_core_custom_tags_and_aliases_count_once_and_keep_raw_data(self):
        record = self.work(1, base=['真百', '轻百', '日本'], custom=['百合', '二刷', '年度最佳', '未知标签'])
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        data = self.get()
        self.assertEqual(data['tag_summary'], [{'tag': '百合', 'sample_count': 1}])
        origins = data['extraction']['trusted_profile']['tag_summary'][0]['source_work_counts']
        self.assertEqual(origins, {'private_base_tags': 1, 'shared_base_tags': 0, 'custom_tags': 1})
        self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)
        self.assertNotIn('未知标签', str(data))
        self.assertNotIn('年度最佳', str(data))

    def test_three_custom_genre_samples_are_ready_and_trace_real_sources(self):
        for identity in (1, 2, 3):
            self.work(identity, base=[], custom=['音乐', '二刷'])
        RecommendationAnime.objects.create(bangumi_id=100, title_zh='候选作品', nsfw=False, tags='音乐', score=8)
        data = self.get()
        self.assertEqual(data['usable_sample_count'], 3)
        self.assertFalse(data['cold_start'])
        response = self.client.get('/api/recommendations/')
        self.assertEqual(response.data['mode'], 'personalized')
        evidence = response.data['results'][0]['evidence']
        self.assertEqual(evidence['kind'], 'rated_work')
        self.assertEqual(evidence['source']['personal_score'], '9.0')
        self.assertEqual(evidence['shared_tag_sources'], {'音乐': ['custom_tags']})
        self.assertEqual(evidence['feature_rule_version'], FEATURE_RULE_VERSION)
        self.assertNotIn('record_id', evidence)

    def test_common_topics_never_generate_a_taste_vector_or_evidence(self):
        self.work(1, score=None, base=['科幻'])
        self.work(2, status='watching', base=['音乐'])
        RecommendationAnime.objects.create(bangumi_id=100, title_zh='候选', tags='科幻,音乐', nsfw=False)
        response = self.client.get('/api/recommendations/')
        self.assertEqual(response.data['mode'], 'cold_start')
        self.assertEqual(response.data['results'], [])
        detail = self.client.get('/api/recommendations/100/')
        self.assertIsNone(detail.data['evidence'])
        self.assertTrue(self.get()['extraction']['common_topics']['tag_summary'])

    def test_private_empty_tags_are_not_replaced_by_shared_or_catalog_genres(self):
        records = [self.work(i, base=['音乐'], catalog_tags='日常') for i in (1, 2, 3)]
        for record in records:
            record.private_base_tags = []; record.save(update_fields=['private_base_tags'])
        data = self.get()
        self.assertEqual(data['eligible_sample_count'], 3)
        self.assertEqual(data['usable_sample_count'], 0)
        self.assertEqual(data['readiness'], 'needs_tags')
        self.assertTrue(data['cold_start'])
        self.assertEqual(data['tag_summary'], [])
        UserAnimeRecord.objects.filter(pk=records[0].pk).update(metadata_initialized=False)
        fallback = self.get()['extraction']['trusted_profile']['tag_summary'][0]
        self.assertEqual(fallback['source_work_counts']['shared_base_tags'], 1)
        self.assertEqual(fallback['source_work_counts']['private_base_tags'], 0)

    def test_one_usable_work_among_three_linked_high_scores_is_not_personalized(self):
        self.work(1, base=['音乐'])
        self.work(2, base=['年度最佳'])
        self.work(3, base=[])
        data = self.get()
        self.assertEqual(data['eligible_sample_count'], 3)
        self.assertEqual(data['usable_sample_count'], 1)
        self.assertTrue(data['cold_start'])
        self.assertEqual(self.client.get('/api/recommendations/').data['mode'], 'cold_start')

    def test_duplicate_bangumi_work_selects_highest_personal_score_and_counts_once(self):
        first = self.work(1, score='8.5', base=['音乐'])
        shared = Anime.objects.create(chinese_title='私人别名', japanese_title='Alias', airing_period='2026-4', base_tags=['真百'])
        duplicate = UserAnimeRecord.objects.create(user=self.user, anime=shared, watch_status='completed',
            personal_score='9.5', custom_tags=['轻百', '年度最佳'])
        bind_record(record_id=duplicate.pk, user_id=self.user.pk, target_id=1, source='user_confirmed')
        data = self.get()
        self.assertEqual(data['eligible_sample_count'], 1)
        self.assertEqual(data['diagnostics']['linked_high_score_record_count'], 2)
        self.assertEqual(data['tag_summary'], [{'tag': '百合', 'sample_count': 1}])
        self.assertEqual(data['extraction']['common_topics']['work_count'], 0)
        self.assertEqual(user_context(self.user)['seeds'][0]['score'], 9.5)
        self.assertEqual(UserAnimeRecord.objects.get(pk=first.pk).personal_score, Decimal('8.5'))

    def test_common_duplicates_do_not_inflate_counts_or_enter_trusted_samples(self):
        self.work(1, score=None, base=['真百'])
        shared = Anime.objects.create(chinese_title='别名', airing_period='2026-4', base_tags=['轻百'])
        duplicate = UserAnimeRecord.objects.create(user=self.user, anime=shared, watch_status='plan')
        bind_record(record_id=duplicate.pk, user_id=self.user.pk, target_id=1, source='user_confirmed')
        data = self.get()
        self.assertEqual(data['extraction']['common_topics']['work_count'], 1)
        self.assertEqual(data['extraction']['common_topics']['tag_summary'][0]['sample_count'], 1)
        self.assertEqual(data['usable_sample_count'], 0)

    def test_unsafe_unknown_and_unlinked_rows_never_reach_genre_extraction(self):
        self.work(1, base=['治愈'])
        self.work(2, nsfw=True, base=['音乐'], custom=['科幻'])
        self.work(3, nsfw=None, base=['战斗'])
        self.work(4, linked=False, base=['悬疑'])
        self.work(5, score=None, nsfw=True, base=['科幻'])
        self.work(6, status='watching', nsfw=None, base=['魔法'])
        with patch('recommendations.extraction._genres', wraps=_genres) as genres:
            data = self.get()
        self.assertEqual(genres.call_count, 1)
        self.assertEqual(data['tag_summary'], [{'tag': '治愈', 'sample_count': 1}])
        self.assertEqual(data['extraction']['common_topics']['tag_summary'], [])
        self.assertNotIn('音乐', str(data['extraction']['trusted_profile']))
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=None)
        self.assertEqual(self.get()['usable_sample_count'], 0)

    def test_malformed_custom_json_does_not_interpret_dictionary_keys_as_genres(self):
        record = self.work(1, base=[], custom={'音乐': True})
        self.assertEqual(self.get()['usable_sample_count'], 0)
        self.assertEqual(UserAnimeRecord.objects.get(pk=record.pk).custom_tags, {'音乐': True})

    def test_no_review_query_no_remote_requests_no_linking_or_journal_writes(self):
        record = self.work(1, custom=['科幻', '二刷'])
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        with (patch('requests.sessions.Session.request', side_effect=AssertionError('No external requests')),
              patch('urllib.request.urlopen', side_effect=AssertionError('No external requests')),
              CaptureQueriesContext(connection) as queries):
            data = self.get()
        self.assertFalse(any('"review"' in q['sql'] for q in queries))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)
        self.assertNotIn(record.review, str(data))
        self.assertEqual(RecommendationRecordIdentity.objects.count(), 0)

    def test_real_jwt_owner_isolation_query_rejection_and_anonymous_service_guard(self):
        self.work(1, base=['音乐'])
        self.work(2, user=self.other, base=['科幻'])
        RecommendationPreference.objects.create(user=self.other, tags=['奇幻'])
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.other)}')
        data = self.get()
        self.assertEqual(data['tag_summary'], [{'tag': '科幻', 'sample_count': 1}])
        self.assertEqual(data['extraction']['explicit_preferences']['tags'], ['奇幻'])
        self.assertEqual(self.client.get(self.url, {'user_id': self.user.pk}).status_code, 400)
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        with self.assertRaises(NotAuthenticated):
            profile_data(AnonymousUser())

    def test_provenance_change_invalidates_pagination_even_with_same_genres(self):
        for identity in (1, 2, 3):
            self.work(identity, base=['音乐'])
        for identity in (100, 101, 102):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'候选{identity}', nsfw=False, tags='音乐', score=8)
        first = self.client.get('/api/recommendations/', {'page_size': 1}).data
        row = UserAnimeRecord.objects.get(user=self.user, anime__bgm_id=1)
        row.private_base_tags = []; row.custom_tags = ['音乐']; row.save(update_fields=['private_base_tags', 'custom_tags'])
        response = self.client.get('/api/recommendations/', {'page_size': 1, 'page': 2, 'state_token': first['state_token']})
        self.assertEqual(response.status_code, 409)
