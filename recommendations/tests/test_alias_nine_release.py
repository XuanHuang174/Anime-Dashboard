from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITransactionTestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.evidence import EvidencePresenter
from recommendations.features import extract_genres, feature_tags
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.taxonomy import (
    ALIASES, AVAILABLE_TAGS, APPROVED_ALIAS_BATCH, APPROVED_ALIAS_RELEASE,
    PREVIOUS_ALIASES, PREVIOUS_AVAILABLE_TAGS, APPROVED_GENRE_BATCH, TAXONOMY_VERSION, canonical_tag,
)
from recommendations.version_control import cache_namespace


EXPECTED = {'爱情': '恋爱', '治癒系': '治愈', '运动番': '运动', '运动系': '运动',
            '美食番': '美食', '日常番': '日常', '推理系': '推理', '恋爱番': '恋爱', '搞笑类': '喜剧'}


class AliasNineRuleTests(SimpleTestCase):
    def test_exactly_approved_nine_and_manifest_agree_without_new_genres(self):
        manifest = json.loads((Path(settings.BASE_DIR) / 'evaluation/tag-aliases.20261020.approved.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['aliases'], EXPECTED)
        self.assertEqual(APPROVED_ALIAS_BATCH, EXPECTED)
        self.assertEqual(manifest['release_version'], APPROVED_ALIAS_RELEASE)
        self.assertEqual(manifest['taxonomy_version'], 'genres.v5')  # Frozen release approval.
        self.assertEqual(len(AVAILABLE_TAGS), 41)
        self.assertEqual(AVAILABLE_TAGS, PREVIOUS_AVAILABLE_TAGS + tuple(APPROVED_GENRE_BATCH))
        self.assertEqual(len(ALIASES), 30)
        self.assertEqual(dict(PREVIOUS_ALIASES), {key: value for key, value in ALIASES.items() if key not in EXPECTED})
        for raw, target in EXPECTED.items():
            self.assertEqual(canonical_tag(raw), target)
            self.assertIsNone(canonical_tag(raw, taxonomy_version='genres.v4'))
        for raw in APPROVED_GENRE_BATCH:
            self.assertIsNone(canonical_tag(raw, taxonomy_version='genres.v5'))
            self.assertEqual(canonical_tag(raw), raw)
        for raw in ('萌', '萌豚', 'SF', '萝卜', '爱情向', '运动番2', '搞笑日常', '治愈系神作'):
            self.assertIsNone(canonical_tag(raw), raw)

    def test_feature_copy_deduplicates_and_keeps_source_and_target_title_guards(self):
        for raw, target in EXPECTED.items():
            base, custom = [raw, target, raw], [raw, '二刷', '神作']
            before = deepcopy((base, custom))
            result = extract_genres(base, custom)
            self.assertEqual(result['tags'], {target})
            self.assertEqual(result['sources'][target], ('private_base_tags', 'custom_tags'))
            self.assertEqual((base, custom), before)
            self.assertFalse(feature_tags(raw, (raw,)))
            self.assertFalse(feature_tags(raw, (target,)))
        self.assertFalse(feature_tags('', ('爱情 运动番 恋爱番',)))
        self.assertFalse(extract_genres([], [])['tags'])


class AliasNineAPIReleaseTests(APITransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='alias-nine-owner')
        self.other = get_user_model().objects.create_user(username='alias-nine-other')
        self.client.force_authenticate(self.owner)
        for identity in (1, 2, 3):
            self.catalog(identity, tags='爱情,治癒系', score=1)
            shared = Anime.objects.create(bgm_id=identity, chinese_title=f'来源{identity}', base_tags=['爱情', '治癒系'])
            UserAnimeRecord.objects.create(user=self.owner, anime=shared, watch_status='completed',
                personal_score='9.0', custom_tags=['恋爱番', '二刷'], review='PRIVATE_REVIEW_DO_NOT_READ')
        for identity in range(100, 130):
            self.catalog(identity, tags='恋爱番,治癒系,日常番' if identity % 2 else '爱情,运动系')
        self.catalog(900, nsfw=True)
        self.catalog(901, nsfw=None)
        self.catalog(902, tags='萌系')

    def catalog(self, identity, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity,
            **{'title_zh': f'公共作品{identity}', 'tags': '爱情', 'nsfw': False,
               'score': 8, 'season': '2026-01', 'status_type': 'TV', **changes})

    def test_profile_list_detail_consistent_real_personal_score_and_zero_writes(self):
        preference = RecommendationPreference.objects.create(user=self.owner, tags=['科幻'], version=4)
        records = list(UserAnimeRecord.objects.values())
        public = list(RecommendationAnime.objects.values())
        shared = list(Anime.objects.values())
        with CaptureQueriesContext(connection) as queries:
            profile = self.client.get('/api/recommendations/profile/').data
            listing = self.client.get('/api/recommendations/').data
            self.assertEqual(profile['usable_sample_count'], 3)
            self.assertEqual({item['tag'] for item in profile['tag_summary']}, {'恋爱', '治愈'})
            self.assertEqual(listing['algorithm_version'], 'content.v1')
            self.assertEqual(listing['taxonomy_version'], TAXONOMY_VERSION)
            for item in listing['results']:
                self.assertEqual(item['evidence']['source']['personal_score'], '9.0')
                self.assertTrue(set(item['evidence']['shared_tags']) <= {'恋爱', '治愈'})
                detail = self.client.get(f"/api/recommendations/{item['bangumi_id']}/",
                                         {'evidence_token': item['evidence_token']})
                self.assertEqual(detail.status_code, 200, detail.data)
                self.assertEqual(detail.data['evidence'], item['evidence'])
        self.assertTrue(listing['results'])
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for query in queries))
        self.assertEqual(list(UserAnimeRecord.objects.values()), records)
        self.assertEqual(list(RecommendationAnime.objects.values()), public)
        self.assertEqual(list(Anime.objects.values()), shared)
        preference.refresh_from_db()
        self.assertEqual((preference.tags, preference.version), (['科幻'], 4))

    def test_user_isolation_safety_and_empty_private_tags_no_catalog_fallback(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)
        self.assertFalse(self.client.get('/api/recommendations/').data['results'])
        self.client.force_authenticate(self.owner)
        for identity in (900, 901):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
        self.assertNotIn(902, [item['bangumi_id'] for item in self.client.get('/api/recommendations/').data['results']])
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=[], custom_tags=[])
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)

    def test_v4_cache_page_cursor_and_evidence_cannot_be_reused(self):
        old_key = cache_namespace('content.v1', 'genres.v4', engine.FEATURE_VERSION, engine.FEATURE_RULE_VERSION)
        cache.set(old_key, {'old': 'sentinel'})
        fresh = engine.public_features()
        self.assertEqual(fresh['taxonomy_version'], TAXONOMY_VERSION)
        self.assertEqual(cache.get(old_key), {'old': 'sentinel'})
        with patch('recommendations.engine.TAXONOMY_VERSION', 'genres.v4'):
            page = self.client.get('/api/recommendations/', {'page_size': 1}).data
            response = self.client.get('/api/recommendations/', {'browse': True})
            self.assertEqual(response.status_code, 200, response.data)
            browsing = response.data
        self.assertEqual(self.client.get('/api/recommendations/', {
            'page': 2, 'page_size': 1, 'state_token': page['state_token']}).status_code, 409)
        self.assertTrue(browsing['next_page_cursor'])
        self.assertEqual(self.client.get('/api/recommendations/', {
            'cursor': browsing['next_page_cursor']}).status_code, 409)
        context = engine.user_context(self.owner)
        context['taxonomy_version'] = 'genres.v4'
        candidate = RecommendationAnime.objects.get(bangumi_id=100)
        old = EvidencePresenter(self.owner, context, fresh['weights'], 'content.v1').present(
            candidate, feature_tags(candidate.tags))
        self.assertEqual(self.client.get('/api/recommendations/100/', {
            'evidence_token': old['evidence_token']}).status_code, 409)

    def test_canonical_only_data_keeps_identical_v1_ranking_formula_and_evidence(self):
        RecommendationAnime.objects.update(tags='恋爱,治愈,日常')
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=['恋爱', '治愈'], custom_tags=[])
        rows = list(engine.safe_catalog().order_by('bangumi_id').values('bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type'))
        context = engine.user_context(self.owner)
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True}
        ranked = []
        for rule in ('genres.v4', 'genres.v5'):
            cloned = deepcopy(context)
            cloned['taxonomy_version'] = rule
            index = engine.build_feature_index(rows, taxonomy_version=rule)
            result, _ = engine.rank_context(cloned, options, index)
            ranked.append([(item['anime'].bangumi_id, item['value'], item['evidence'], item['reason']) for item in result])
        self.assertEqual(ranked[0], ranked[1])
