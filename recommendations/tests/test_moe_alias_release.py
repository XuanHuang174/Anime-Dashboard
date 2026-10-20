from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from rest_framework.test import APITransactionTestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.evidence import EvidencePresenter
from recommendations.features import extract_genres, feature_tags
from recommendations.models import RecommendationAnime
from recommendations.preference_options import public_counts
from recommendations.tag_expansion_review import load_proposal
from recommendations.tag_coverage import analyze_public_tags
from recommendations.taxonomy import (
    ALIASES, APPROVED_MOE_ALIASES, AVAILABLE_TAGS, GENRE_EIGHT_ALIASES,
    MAX_PREFERENCE_TAGS, TAXONOMY_VERSION, MOE_AVAILABLE_TAGS, canonical_tag,
)

THIRD = ('竞技', '动物', '惊悚', '讽刺', '科普', '吸血鬼', '妖怪', '家庭', '末世', '公路片')


class MoeAliasRulesTests(SimpleTestCase):
    def test_only_three_approved_aliases_and_no_new_presets(self):
        manifest = json.loads((Path(settings.BASE_DIR) / 'evaluation/tag-moe-aliases.20261020.approved.json').read_text(encoding='utf-8'))
        expected = {'萌': '萌系', '废萌': '萌系', '萌豚': '萌系'}
        self.assertEqual(manifest['aliases'], expected)
        self.assertEqual(APPROVED_MOE_ALIASES, expected)
        self.assertEqual({key: value for key, value in ALIASES.items() if key not in GENRE_EIGHT_ALIASES}, expected)
        self.assertEqual((len(MOE_AVAILABLE_TAGS), len(AVAILABLE_TAGS), MAX_PREFERENCE_TAGS), (41, 51, 20))
        for raw in expected:
            self.assertEqual(canonical_tag(raw), '萌系')
            self.assertIsNone(canonical_tag(raw, taxonomy_version='genres.v6'))
        for raw in THIRD:
            self.assertIsNone(canonical_tag(raw, taxonomy_version='genres.v7'))
        for raw in ('卖萌', '美少女', 'SF', '萝卜', '萌豚向', '废萌番'):
            self.assertIsNone(canonical_tag(raw), raw)
        proposal = load_proposal(Path(settings.BASE_DIR) / 'evaluation/tag-genres-third.20261020.review.json')
        self.assertFalse(proposal['apply_to_production'])
        self.assertEqual([item['source'] for item in proposal['new_genres']], list(THIRD))
        row = {'bangumi_id': 1, 'title_zh': '公共作品', 'title_jp': '', 'tags': '萌', 'status_type': 'TV'}
        scope = {'total': 1, 'non_adult': 1, 'adult': 0, 'unknown': 0}
        old = analyze_public_tags([row], scope, taxonomy_version='genres.v6')
        new = analyze_public_tags([row], scope)
        self.assertEqual(old['approved_alias_release']['version'], 'aliases.2026-10-20.approved-9')
        self.assertNotIn('萌', old['approved_alias_release']['aliases'])
        self.assertEqual(new['approved_alias_release']['aliases']['萌'], '萌系')
        self.assertEqual((old['after_active']['with_effective_genres'], new['after_active']['with_effective_genres']), (0, 1))

    def test_feature_copy_dedup_and_title_guards(self):
        base, custom = ['萌系', '萌', '废萌', '萌豚'], ['萌', '废萌', '二刷', '神作']
        original = deepcopy((base, custom))
        result = extract_genres(base, custom)
        self.assertEqual(result['tags'], {'萌系'})
        self.assertEqual(result['sources']['萌系'], ('private_base_tags', 'custom_tags'))
        self.assertEqual((base, custom), original)
        for raw in APPROVED_MOE_ALIASES:
            self.assertFalse(feature_tags(raw, (raw,)))
            self.assertFalse(feature_tags(raw, ('萌系',)))
        self.assertFalse(extract_genres([], [])['tags'])
        self.assertFalse(feature_tags('', ('萌系 萌豚 废萌',)))


class MoeAliasAPITests(APITransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='moe-alias-owner')
        self.other = get_user_model().objects.create_user(username='moe-alias-other')
        self.client.force_authenticate(self.owner)
        for identity, tag in enumerate(APPROVED_MOE_ALIASES, 1):
            self.catalog(identity, tag)
            anime = Anime.objects.create(bgm_id=identity, chinese_title=f'来源{identity}', base_tags=[tag])
            UserAnimeRecord.objects.create(user=self.owner, anime=anime, watch_status='completed',
                personal_score=9, custom_tags=['萌系', tag], review='PRIVATE_REVIEW_UNCHANGED')
        for identity in range(100, 130):
            self.catalog(identity, '萌,废萌,萌豚,萌系')
        self.catalog(900, '萌', nsfw=True)
        self.catalog(901, '萌', nsfw=None)
        self.catalog(902, '卖萌')

    def catalog(self, identity, tags, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity,
            **{'title_zh': f'公共作品{identity}', 'tags': tags, 'nsfw': False, 'score': 8,
               'season': '2026-01', 'status_type': 'TV', **changes})

    def test_real_profile_evidence_counts_isolation_and_original_fields_unchanged(self):
        original = list(UserAnimeRecord.objects.values())
        public = list(RecommendationAnime.objects.values())
        shared = list(Anime.objects.values())
        profile = self.client.get('/api/recommendations/profile/').data
        self.assertEqual(profile['usable_sample_count'], 3)
        self.assertEqual({item['tag'] for item in profile['tag_summary']}, {'萌系'})
        listing = self.client.get('/api/recommendations/').data
        self.assertEqual(listing['algorithm_version'], 'content.v1')
        self.assertEqual(listing['taxonomy_version'], TAXONOMY_VERSION)
        self.assertTrue(listing['results'])
        for item in listing['results']:
            self.assertEqual(item['evidence']['shared_tags'], ['萌系'])
            self.assertEqual(item['evidence']['source']['personal_score'], '9.0')
            detail = self.client.get(f"/api/recommendations/{item['bangumi_id']}/", {'evidence_token': item['evidence_token']})
            self.assertEqual(detail.status_code, 200, detail.data)
            self.assertEqual(detail.data['evidence'], item['evidence'])
        self.assertEqual(public_counts()[0]['萌系'], 33)
        self.assertEqual(list(UserAnimeRecord.objects.values()), original)
        self.assertEqual(list(RecommendationAnime.objects.values()), public)
        self.assertEqual(list(Anime.objects.values()), shared)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)
        self.assertFalse(self.client.get('/api/recommendations/').data['results'])
        self.client.force_authenticate(self.owner)
        for identity in (900, 901):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=[], custom_tags=[])
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)

    def test_v6_page_and_browse_cursor_rejected_after_rule_change(self):
        with patch('recommendations.engine.TAXONOMY_VERSION', 'genres.v6'):
            page = self.client.get('/api/recommendations/', {'page_size': 1}).data
            browse = self.client.get('/api/recommendations/', {'browse': True}).data
        self.assertTrue(browse['next_page_cursor'])
        self.assertEqual(self.client.get('/api/recommendations/', {
            'page': 2, 'page_size': 1, 'state_token': page['state_token']}).status_code, 409)
        self.assertEqual(self.client.get('/api/recommendations/', {'cursor': browse['next_page_cursor']}).status_code, 409)
        context = engine.user_context(self.owner)
        context['taxonomy_version'] = 'genres.v6'
        candidate = RecommendationAnime.objects.get(bangumi_id=100)
        old = EvidencePresenter(self.owner, context, engine.public_features()['weights'], 'content.v1').present(
            candidate, feature_tags(candidate.tags))
        self.assertEqual(self.client.get('/api/recommendations/100/', {
            'evidence_token': old['evidence_token']}).status_code, 409)

    def test_canonical_only_v1_formula_identical_across_v6_and_v7(self):
        RecommendationAnime.objects.update(tags='萌系,日常')
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=['萌系'], custom_tags=[])
        rows = list(engine.safe_catalog().order_by('bangumi_id').values('bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type'))
        context = engine.user_context(self.owner)
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True}
        results = []
        for version in ('genres.v6', 'genres.v7'):
            cloned = deepcopy(context)
            cloned['taxonomy_version'] = version
            ranked, _ = engine.rank_context(cloned, options, engine.build_feature_index(rows, taxonomy_version=version))
            results.append([(item['anime'].bangumi_id, item['value'], item['evidence'], item['reason']) for item in ranked])
        self.assertEqual(results[0], results[1])
