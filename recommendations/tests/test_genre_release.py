from collections import Counter
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.features import extract_genres, feature_tags, FEATURE_RULE_VERSION
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_evaluation import evidence_errors
from recommendations.tag_coverage import _coverage, analyze_public_tags, attach_release_comparison, write_coverage_reports
from recommendations.tag_review_rules import GENRE_CANDIDATES, PENDING_ALIASES, validate_proposals
from recommendations.taxonomy import APPROVED_ALIASES, APPROVED_GENRES, APPROVED_GENRE_BATCH, AVAILABLE_TAGS, TAXONOMY_VERSION, canonical_tag

NEW_GENRES = ('热血', '后宫', '玄幻', '穿越', '魔法少女', '恐怖', '武侠')
OLD_GENRES = ('日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑',
    '推理', '战斗', '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情',
    '青春', '魔法', '异世界', '偶像', '美食', '旅行')


class GenreReleasePrimitiveTests(SimpleTestCase):
    def test_exactly_seven_independent_genres_added_and_unapproved_rules_stay_pending(self):
        self.assertEqual(tuple(APPROVED_GENRES), NEW_GENRES + tuple(APPROVED_GENRE_BATCH))
        self.assertEqual(AVAILABLE_TAGS, OLD_GENRES + NEW_GENRES + tuple(APPROVED_GENRE_BATCH))
        self.assertEqual(len(APPROVED_ALIASES), 23)
        self.assertEqual(set(GENRE_CANDIDATES), {'BL', '耽美', '实验', '超现实', '猎奇'})
        validate_proposals()
        for value in (*GENRE_CANDIDATES, *PENDING_ALIASES, '修仙', '转生', '惊悚', '变身', '后宮',
                      '魔法少女向', '穿越系', 'horror', '热血作品', '玄幻/奇幻'):
            self.assertIsNone(canonical_tag(value), value)
        for value in NEW_GENRES:
            self.assertEqual(feature_tags(value), {value})  # No parent/related genre propagation.
        self.assertEqual(feature_tags('魔法少女,魔法'), {'魔法少女', '魔法'})
        self.assertEqual(TAXONOMY_VERSION, 'genres.v7')
        self.assertEqual(FEATURE_RULE_VERSION, 'journal-genres.v3')
        self.assertEqual(engine.ALGORITHM_VERSION, 'content.v1')

    def test_deduplication_across_fields_keeps_provenance_without_rewriting_tags(self):
        base, custom = list(NEW_GENRES) * 2, list(NEW_GENRES) + ['神作', '二刷', '2026', '日本', '未知题材']
        old = deepcopy((base, custom))
        result = extract_genres(base, custom)
        self.assertEqual(result['tags'], set(NEW_GENRES))
        self.assertTrue(all(value == ('private_base_tags', 'custom_tags') for value in result['sources'].values()))
        self.assertEqual(result['ignored_counts']['management'], 2)
        self.assertEqual((base, custom), old)

    def test_titles_cannot_supply_or_override_genres_and_empty_stays_empty(self):
        self.assertFalse(feature_tags('', ('热血后宫玄幻穿越魔法少女恐怖武侠',)))
        for value in NEW_GENRES:
            self.assertFalse(feature_tags(value, (value,)))
        self.assertFalse(extract_genres([], [])['tags'])

    def test_public_inventory_moves_approved_labels_out_of_pending_without_simulating_others(self):
        rows = [{'bangumi_id': 1, 'title_zh': '公开作品', 'title_jp': '',
                 'tags': ','.join(NEW_GENRES) + ',BL,实验', 'status_type': 'TV'}]
        old = deepcopy(rows)
        report = analyze_public_tags(rows, {'total': 1, 'non_adult': 1, 'adult': 0, 'unknown': 0})
        self.assertEqual(report['before']['genre_count_histogram'], {'7': 1})
        self.assertEqual(report['draft_synonyms_simulation'], report['before'])
        self.assertEqual({item['tag'] for item in report['unknown_tags']}, {'BL', '实验'})
        self.assertEqual(set(report['approved_genre_release']['definitions']), set(NEW_GENRES) | set(APPROVED_GENRE_BATCH))
        self.assertFalse(report['approved_genre_release']['automatic_parent_expansion'])
        self.assertEqual(rows, old)


class GenreReleaseAPITests(APITestCase):
    preferences_url = '/api/recommendations/preferences/'

    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='genre-owner')
        self.other = get_user_model().objects.create_user(username='genre-other')
        self.client.force_authenticate(self.owner)
        for identity in (1, 2, 3):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'标准来源{identity}',
                tags=','.join(NEW_GENRES), nsfw=False)
            shared = Anime.objects.create(bgm_id=identity, chinese_title=f'标准来源{identity}', base_tags=list(NEW_GENRES))
            UserAnimeRecord.objects.create(user=self.owner, anime=shared, watch_status='completed',
                personal_score='9.0', custom_tags=list(NEW_GENRES), review='PRIVATE_REVIEW_NEVER_USED')
        for identity, value in enumerate(NEW_GENRES, 10):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'公开候选{identity}',
                                                tags=value, score=8, nsfw=False)
        for identity, value, nsfw in ((90, '后宫,恐怖', True), (91, '热血,武侠', None),
                                      (92, '魔法', False), (93, '修仙,转生,惊悚', False), (94, '', False)):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'其他候选{identity}',
                                                tags=value, score=8, nsfw=nsfw)

    def test_profile_recommendation_evidence_and_original_rows_stay_consistent(self):
        preference = RecommendationPreference.objects.create(user=self.owner, tags=['日常'], version=4)
        previous = list(UserAnimeRecord.objects.order_by('pk').values())
        shared = list(Anime.objects.order_by('pk').values())
        catalog = list(RecommendationAnime.objects.order_by('pk').values())
        saved = RecommendationPreference.objects.filter(pk=preference.pk).values().get()
        with CaptureQueriesContext(connection) as queries:
            profile = self.client.get('/api/recommendations/profile/').data
            result = self.client.get('/api/recommendations/').data
        self.assertFalse(profile['cold_start'])
        self.assertEqual(profile['usable_sample_count'], 3)
        self.assertEqual(profile['tag_summary'], [{'tag': value, 'sample_count': 3} for value in NEW_GENRES])
        self.assertEqual(profile['extraction']['explicit_preferences']['tags'], ['日常'])
        self.assertEqual({item['bangumi_id'] for item in result['results']}, set(range(10, 17)))
        context = engine.user_context(self.owner)
        options = {'category': 'all', 'min_score': 0, 'type': 'all', 'hide_recorded': True, 'page_size': 12}
        ranked = engine.rank_candidates(self.owner, options)[1]
        for item in ranked:
            self.assertEqual(evidence_errors(item, context), [])
            self.assertEqual(item['evidence']['shared_tag_sources'], {
                next(iter(item['tags'])): ['private_base_tags', 'custom_tags']})
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for query in queries))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), previous)
        self.assertEqual(list(Anime.objects.order_by('pk').values()), shared)
        self.assertEqual(list(RecommendationAnime.objects.order_by('pk').values()), catalog)
        self.assertEqual(RecommendationPreference.objects.filter(pk=preference.pk).values().get(), saved)

    def test_manual_choices_are_available_but_not_selected_automatically_and_remain_idempotent(self):
        self.client.force_authenticate(self.other)
        first = self.client.get(self.preferences_url).data
        self.assertEqual(set(first['available_tags']), set(AVAILABLE_TAGS))
        counts = first['available_tag_counts']
        self.assertEqual([counts[tag] for tag in first['available_tags']], sorted(counts.values(), reverse=True))
        self.assertEqual(first['tags'], [])
        self.assertEqual(RecommendationPreference.objects.filter(user=self.other).count(), 0)
        chosen = self.client.patch(self.preferences_url, {'tags': list(NEW_GENRES), 'expected_version': 0}, format='json')
        self.assertEqual(chosen.status_code, 200)
        self.assertEqual(chosen.data['tags'], list(NEW_GENRES))
        self.assertEqual(chosen.data['version'], 1)
        same = self.client.patch(self.preferences_url, {'tags': list(reversed(NEW_GENRES)), 'expected_version': 1}, format='json')
        self.assertEqual(same.data['version'], 1)
        result = self.client.get('/api/recommendations/').data
        self.assertEqual(result['mode'], 'preference_based')
        # Another user's journal must NOT hide those public works for this user.
        self.assertEqual({item['bangumi_id'] for item in result['results']}, {1, 2, 3, *range(10, 17)})
        for item in result['results']:
            self.assertEqual(item['evidence']['kind'], 'explicit_preferences')
            self.assertIsNone(item['evidence']['source'])
        for invalid in ('BL', '耽美', '实验', '超现实', '猎奇', '修仙', '转生', '惊悚'):
            self.assertEqual(self.client.patch(self.preferences_url, {'add_tags': [invalid]}, format='json').status_code, 400)
        self.assertEqual(self.client.get(self.preferences_url).data['tags'], list(NEW_GENRES))
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)

    def test_empty_private_tags_never_backfill_from_new_genre_catalog_or_shared_rows(self):
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=[], custom_tags=[])
        result = self.client.get('/api/recommendations/profile/').data
        self.assertEqual(result['usable_sample_count'], 0)
        self.assertEqual(result['tag_summary'], [])
        self.assertTrue(result['cold_start'])
        self.assertEqual(self.client.get('/api/recommendations/').data['results'], [])

    def test_saved_zero_unrated_and_other_states_are_observations_not_likes(self):
        records = list(UserAnimeRecord.objects.filter(user=self.owner).order_by('pk'))
        for record, changes in zip(records, ({'personal_score': 0}, {'personal_score': None}, {'watch_status': 'watching'})):
            UserAnimeRecord.objects.filter(pk=record.pk).update(**changes)
        result = self.client.get('/api/recommendations/profile/').data
        self.assertEqual(result['usable_sample_count'], 0)
        self.assertEqual(result['extraction']['record_stats']['unrated_record_count'], 1)
        self.assertEqual(result['extraction']['record_stats']['rated_record_count'], 2)
        self.assertEqual(result['extraction']['common_topics']['feature_work_count'], 3)
        self.assertEqual(result['extraction']['explicit_preferences']['tags'], [])
        self.assertEqual(self.client.get('/api/recommendations/').data['results'], [])

    def test_limit_and_version_conflicts_remain_intact_with_33_available_topics(self):
        response = self.client.patch(self.preferences_url, {'tags': list(AVAILABLE_TAGS[:20]), 'expected_version': 0}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.patch(self.preferences_url, {'add_tags': ['热血'], 'expected_version': 1}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.preferences_url, {'tags': ['热血'], 'expected_version': 0}, format='json').status_code, 409)
        self.assertEqual(len(self.client.get(self.preferences_url).data['tags']), 20)

    def test_clearing_manual_choices_keeps_automatic_profile_and_owner_isolation(self):
        self.client.patch(self.preferences_url, {'tags': ['恐怖'], 'expected_version': 0}, format='json')
        self.assertEqual(self.client.patch(self.preferences_url, {'tags': [], 'expected_version': 1}, format='json').status_code, 200)
        result = self.client.get('/api/recommendations/profile/').data
        self.assertEqual(result['usable_sample_count'], 3)
        self.assertEqual(result['extraction']['explicit_preferences']['tags'], [])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)
        self.assertIsNone(self.client.get('/api/recommendations/10/').data['evidence'])
        for identity in (90, 91):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)

    def test_v3_cache_and_page_tokens_are_not_reused_after_genre_expansion(self):
        sentinel = {'signature': 'old', 'features': [], 'weights': {}}
        cache.set('recommendations.public-features.v3', sentinel)
        self.assertTrue(engine.public_features()['features'])
        self.assertEqual(cache.get('recommendations.public-features.v3'), sentinel)
        self.assertEqual(engine.CACHE_KEY, 'recommendations.public-features.v4')
        for field, value in (('TAXONOMY_VERSION', 'genres.v3'), ('FEATURE_VERSION', 'genres-idf.v3'),
                             ('FEATURE_RULE_VERSION', 'journal-genres.v2')):
            with patch(f'recommendations.engine.{field}', value):
                token = self.client.get('/api/recommendations/', {'page_size': 1}).data['state_token']
            self.assertEqual(self.client.get('/api/recommendations/', {'page': 2, 'page_size': 1, 'state_token': token}).status_code, 409)
        token = self.client.get('/api/recommendations/', {'page_size': 1}).data['state_token']
        self.assertEqual(self.client.get('/api/recommendations/', {'page': 2, 'page_size': 1, 'state_token': token}).status_code, 200)


class GenreReleaseComparisonTests(SimpleTestCase):
    def test_v3_baseline_supported_and_current_future_versions_rejected(self):
        rows = [{'bangumi_id': 1, 'title_zh': '公共作品', 'title_jp': '', 'tags': '武侠', 'status_type': 'TV'}]
        current = analyze_public_tags(rows, {'total': 1, 'non_adult': 1, 'adult': 0, 'unknown': 0})
        for version, valid in (('genres.v3', True), ('genres.v2', True), ('genres.v4', True),
                               ('genres.v5', True), ('genres.v6', True), ('genres.v7', False)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as root:
                prior = deepcopy(current)
                prior['versions']['active_taxonomy'] = version
                prior['before'] = prior['after_active'] = _coverage(Counter({0: 1}))
                write_coverage_reports(prior, root)
                if valid:
                    changed = deepcopy(current)
                    attach_release_comparison(changed, root)
                    self.assertEqual(changed['approved_release_comparison']['before_taxonomy'], version)
                    self.assertEqual(changed['approved_release_comparison']['additional_covered_works'], 1)
                else:
                    with self.assertRaises(ValueError):
                        attach_release_comparison(deepcopy(current), root)


class GenreReleaseAuditTests(TransactionTestCase):
    def test_command_public_only_readonly_and_exported_pending_list_has_just_five_candidates(self):
        RecommendationAnime.objects.create(bangumi_id=1, title_zh='安全公开作品', tags=','.join(NEW_GENRES) + ',BL,实验', nsfw=False)
        RecommendationAnime.objects.create(bangumi_id=2, title_zh='ADULT_SECRET', tags='恐怖', nsfw=True)
        RecommendationAnime.objects.create(bangumi_id=3, title_zh='UNKNOWN_SECRET', tags='热血', nsfw=None)
        old = list(RecommendationAnime.objects.order_by('pk').values())
        with tempfile.TemporaryDirectory() as root:
            with CaptureQueriesContext(connection) as queries, patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP')):
                call_command('audit_recommendation_tags', output_dir=Path(root), stdout=StringIO())
            report = json.loads((Path(root) / 'coverage.json').read_text(encoding='utf-8'))
            review = json.loads((Path(root) / 'mapping-review.json').read_text(encoding='utf-8'))
            self.assertEqual(report['scope']['non_adult'], 1)
            self.assertEqual(report['before']['genre_count_histogram'], {'7': 1})
            self.assertEqual({item['tag'] for item in review['candidate_genres_not_simulated']}, {'BL', '实验'})
            output = ''.join(path.read_text(encoding='utf-8') for path in Path(root).iterdir())
            self.assertNotIn('ADULT_SECRET', output)
            self.assertNotIn('UNKNOWN_SECRET', output)
            sql = ' '.join(query['sql'] for query in queries).lower()
            for forbidden in ('anime_useranimerecord', 'recommendationpreference', 'auth_user', '"review"', 'synopsis'):
                self.assertNotIn(forbidden, sql)
            self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for query in queries))
        self.assertEqual(list(RecommendationAnime.objects.order_by('pk').values()), old)
