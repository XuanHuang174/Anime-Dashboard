from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, DatabaseError
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITransactionTestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.features import extract_genres, feature_tags
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.preference_options import public_counts, ordered_tags, with_ordered_options
from recommendations.services import preference_contract
from recommendations.taxonomy import (
    AVAILABLE_TAGS, APPROVED_GENRE_BATCH, APPROVED_GENRE_RELEASE, TAXONOMY_VERSION, canonical_tag,
)

NEW = ('萌系', '战争', '动作', '励志', '超能力', '童话', '犯罪', '赛博朋克')


class GenreEightRulesTests(SimpleTestCase):
    def test_approval_exact_features_and_historical_rules_stay_frozen(self):
        manifest = json.loads((Path(settings.BASE_DIR) / 'evaluation/tag-genres.20261020.approved.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['new_genres'], list(NEW))
        self.assertEqual(manifest['release_version'], APPROVED_GENRE_RELEASE)
        self.assertEqual(manifest['taxonomy_version'], TAXONOMY_VERSION)
        self.assertEqual(tuple(APPROVED_GENRE_BATCH), NEW)
        self.assertEqual(len(AVAILABLE_TAGS), 41)
        for tag in NEW:
            self.assertEqual(feature_tags(tag), {tag})
            for version in ('genres.v2', 'genres.v4', 'genres.v5'):
                self.assertIsNone(canonical_tag(tag, taxonomy_version=version))
        for rejected in ('萌', '萌豚', '废萌', '卖萌', '美少女', '童话改', '动作番', '战争片',
                         '赛博', 'SF', '萝卜', '科普', 'BL', '耽美'):
            self.assertIsNone(canonical_tag(rejected), rejected)
        self.assertEqual(engine.ALGORITHM_VERSION, 'content.v1')

    def test_raw_copy_dedup_titles_and_empty_private_tags(self):
        base, custom = list(NEW) * 2, list(NEW) + ['二刷', '神作', '2026', '日本']
        before = deepcopy((base, custom))
        extracted = extract_genres(base, custom)
        self.assertEqual(extracted['tags'], set(NEW))
        self.assertTrue(all(value == ('private_base_tags', 'custom_tags') for value in extracted['sources'].values()))
        self.assertEqual((base, custom), before)
        for tag in NEW:
            self.assertFalse(feature_tags(tag, (tag,)))
        self.assertFalse(feature_tags('', ('萌系 战争 动作 童话 第三季',)))
        self.assertFalse(extract_genres([], [])['tags'])

    def test_sort_changes_only_display_ties_and_zeros_keep_canonical_order(self):
        before = tuple(AVAILABLE_TAGS)
        counts = dict.fromkeys(AVAILABLE_TAGS, 0)
        counts.update({'犯罪': 5, '萌系': 2, '日常': 2})
        displayed = ordered_tags(counts)
        self.assertEqual(displayed[:3], ['犯罪', '日常', '萌系'])
        self.assertEqual(tuple(AVAILABLE_TAGS), before)
        self.assertEqual(displayed[3:], [tag for tag in before if not counts[tag]])


class GenreEightAPITests(APITransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.user = get_user_model().objects.create_user(username='eight-topic-owner')
        self.other = get_user_model().objects.create_user(username='eight-topic-other')
        self.client.force_authenticate(self.user)

    def catalog(self, identity, tags, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity,
            **{'title_zh': f'公共作品{identity}', 'tags': tags, 'nsfw': False, 'score': 8,
               'season': '2026-01', 'status_type': 'TV', **changes})

    def seed(self):
        for identity in (1, 2, 3):
            self.catalog(identity, ','.join(NEW), score=1)
            anime = Anime.objects.create(bgm_id=identity, chinese_title=f'来源{identity}', base_tags=list(NEW))
            UserAnimeRecord.objects.create(user=self.user, anime=anime, personal_score=9,
                watch_status='completed', review='PRIVATE_REVIEW_NEVER_QUERY', custom_tags=list(NEW))
        for identity, tag in enumerate(NEW, 10):
            self.catalog(identity, tag)

    def test_safe_counts_aliases_dedup_identity_and_title_exclusion_no_private_queries(self):
        self.catalog(1, '萌系,萌系,喜剧,搞笑')
        self.catalog(2, '喜剧', score=0)
        self.catalog(3, '萌系,战争', nsfw=True)
        self.catalog(4, '萌系,战争', nsfw=None)
        self.catalog(5, '萌系', title_zh='萌系')
        self.catalog(None, '萌系')
        original = list(RecommendationAnime.objects.values())
        with CaptureQueriesContext(connection) as queries:
            counts, _ = public_counts()
        self.assertEqual((counts['喜剧'], counts['萌系'], counts['战争']), (2, 1, 0))
        sql = ' '.join(row['sql'].lower() for row in queries)
        for forbidden in ('useranimerecord', 'recommendationpreference', 'auth_user', 'review', 'synopsis', '"score"'):
            self.assertNotIn(forbidden, sql)
        self.assertEqual(list(RecommendationAnime.objects.values()), original)

    def test_warm_cache_one_select_and_direct_catalog_or_safety_changes_invalidate_counts(self):
        self.catalog(1, '喜剧')
        self.catalog(2, '战争')
        initial, revision = public_counts()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(public_counts(), (initial, revision))
        self.assertEqual(sum(row['sql'].lstrip().upper().startswith('SELECT ') for row in queries), 1)
        RecommendationAnime.objects.filter(bangumi_id=1).update(tags='战争')  # deliberately no updated_at
        changed, next_revision = public_counts()
        self.assertNotEqual(revision, next_revision)
        self.assertEqual((changed['喜剧'], changed['战争']), (0, 2))
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=None)
        self.assertEqual(public_counts()[0]['战争'], 1)

    def test_options_api_sorted_for_all_users_but_not_saved_preferences_or_rank_context(self):
        self.catalog(1, '战争')
        self.catalog(2, '战争,萌系')
        saved = RecommendationPreference.objects.create(user=self.user, tags=['萌系'], version=4)
        response = self.client.get('/api/recommendations/preferences/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['available_tags'][:2], ['战争', '萌系'])
        self.assertEqual(len(response.data['available_tags']), 41)
        self.assertEqual(response.data['tags'], ['萌系'])
        self.assertEqual(preference_contract(self.user)['available_tags'], list(AVAILABLE_TAGS))
        self.assertNotIn('option_order_status', engine.user_context(self.user)['preferences'])
        self.client.force_authenticate(self.other)
        other = self.client.get('/api/recommendations/preferences/').data
        self.assertEqual(other['available_tag_counts'], response.data['available_tag_counts'])
        self.assertEqual(other['tags'], [])
        saved.refresh_from_db()
        self.assertEqual((saved.tags, saved.version), (['萌系'], 4))

    def test_new_features_profile_evidence_and_existing_record_fields_remain_unchanged(self):
        self.seed()
        original = list(UserAnimeRecord.objects.values())
        with CaptureQueriesContext(connection) as queries:
            profile = self.client.get('/api/recommendations/profile/').data
            page = self.client.get('/api/recommendations/').data
        self.assertEqual(profile['usable_sample_count'], 3)
        self.assertEqual({item['tag'] for item in profile['tag_summary']}, set(NEW))
        self.assertEqual(page['taxonomy_version'], 'genres.v6')
        self.assertEqual(len(page['results']), 8)
        for item in page['results']:
            self.assertEqual(item['evidence']['source']['personal_score'], '9.0')
            detail = self.client.get(f"/api/recommendations/{item['bangumi_id']}/",
                                     {'evidence_token': item['evidence_token']})
            self.assertEqual(detail.status_code, 200, detail.data)
            self.assertEqual(detail.data['evidence'], item['evidence'])
        self.assertFalse(any('"review"' in row['sql'] for row in queries))
        self.assertEqual(list(UserAnimeRecord.objects.values()), original)
        UserAnimeRecord.objects.filter(user=self.user).update(private_base_tags=[], custom_tags=[])
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)

    def test_limit_save_failure_ordering_failure_and_security_are_independent(self):
        self.catalog(1, '萌系')
        url = '/api/recommendations/preferences/'
        with patch('recommendations.preference_options.public_counts', side_effect=DatabaseError('Synthetic counts outage')):
            result = self.client.patch(url, {'tags': list(NEW), 'expected_version': 0}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['option_order_status'], 'unavailable')
        self.assertNotIn('available_tag_counts', result.data)
        self.assertEqual(set(result.data['tags']), set(NEW))
        self.assertEqual(self.client.patch(url, {'tags': list(AVAILABLE_TAGS[:21])}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'tags': [], 'expected_version': 0}, format='json').status_code, 409)
        for identity, safety in ((90, True), (91, None)):
            self.catalog(identity, '萌系', nsfw=safety)
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
        self.assertEqual(self.client.get(url, {'user_id': self.other.pk}).status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(url).status_code, (401, 403))

    def test_statistics_never_run_in_profile_or_ranking_and_cache_outage_has_fresh_counts(self):
        self.catalog(1, '萌系')
        with patch('recommendations.preference_options.public_counts', side_effect=AssertionError('No presentation work in ranker')):
            self.assertEqual(self.client.get('/api/recommendations/profile/').status_code, 200)
            self.assertEqual(self.client.get('/api/recommendations/').status_code, 200)
        with patch('recommendations.preference_options.cache') as backend:
            backend.get.side_effect = ConnectionError('Synthetic cache read outage')
            backend.set.side_effect = ConnectionError('Synthetic cache write outage')
            shown = with_ordered_options(preference_contract(self.user))
        self.assertEqual(shown['available_tags'][0], '萌系')
        self.assertEqual(shown['option_order_status'], 'ready')
