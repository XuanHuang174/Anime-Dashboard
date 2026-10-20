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
from recommendations.features import extract_genres, feature_tags, FEATURE_RULE_VERSION
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.preference_options import public_counts, OPTION_ORDER_VERSION
from recommendations.tag_coverage import analyze_public_tags
from recommendations.tag_expansion_review import load_proposal, build_expansion_review
from recommendations.taxonomy import (
    ALIASES, AVAILABLE_TAGS, APPROVED_THIRD_GENRES, APPROVED_GENRE_RELEASE,
    MAX_PREFERENCE_TAGS, MOE_ALIASES, MOE_AVAILABLE_TAGS, TAXONOMY_VERSION, canonical_tag,
)
from recommendations.version_control import cache_namespace

NEW = ('竞技', '动物', '惊悚', '讽刺', '科普', '吸血鬼', '妖怪', '家庭', '末世', '公路片')
UNAPPROVED = ('末日', '废土', '灾难', '生存', '黑色幽默', '教育片', '宠物', '兽耳', '惊悚向', '吸血鬼番')


class ThirdGenreRulesTests(SimpleTestCase):
    def test_exact_ten_manifest_frozen_v7_and_aliases_unchanged(self):
        manifest = json.loads((Path(settings.BASE_DIR) / 'evaluation/tag-genres-third.20261020.approved.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['new_genres'], list(NEW))
        self.assertEqual(manifest['release_version'], APPROVED_GENRE_RELEASE)
        self.assertEqual(manifest['taxonomy_version'], TAXONOMY_VERSION)
        self.assertEqual(manifest['new_aliases'], {})
        self.assertEqual(tuple(APPROVED_THIRD_GENRES), NEW)
        self.assertEqual(AVAILABLE_TAGS, MOE_AVAILABLE_TAGS + NEW)
        self.assertEqual((len(AVAILABLE_TAGS), MAX_PREFERENCE_TAGS), (51, 20))
        self.assertEqual(ALIASES, dict(MOE_ALIASES))
        for tag in NEW:
            self.assertIsNone(canonical_tag(tag, taxonomy_version='genres.v7'))
            self.assertEqual(feature_tags(tag), {tag})  # No automatic related/parent features.
        for raw in (*UNAPPROVED, 'BL', '耽美', '卖萌', '美少女', 'SF', '萝卜'):
            self.assertIsNone(canonical_tag(raw), raw)
        self.assertEqual(engine.ALGORITHM_VERSION, 'content.v1')

    def test_feature_copies_deduplicate_and_never_infer_from_titles(self):
        base, custom = list(NEW) * 2, list(NEW) + list(UNAPPROVED) + ['二刷', '神作', '2026', '日本']
        original = deepcopy((base, custom))
        result = extract_genres(base, custom)
        self.assertEqual(result['tags'], set(NEW))
        self.assertTrue(all(sources == ('private_base_tags', 'custom_tags') for sources in result['sources'].values()))
        self.assertEqual((base, custom), original)
        for tag in NEW:
            self.assertFalse(feature_tags(tag, (tag,)))
        self.assertFalse(feature_tags('', NEW))
        self.assertFalse(extract_genres([], [])['tags'])

    def test_frozen_audit_and_original_proposal_trace_actual_release(self):
        rows = [{'bangumi_id': 1, 'title_zh': '公开作品', 'title_jp': '', 'tags': ','.join(NEW), 'status_type': 'TV'}]
        original = deepcopy(rows)
        scope = {'total': 1, 'non_adult': 1, 'adult': 0, 'unknown': 0}
        before = analyze_public_tags(rows, scope, taxonomy_version='genres.v7')
        after = analyze_public_tags(rows, scope)
        self.assertEqual(before['approved_genre_release']['version'], 'genres.2026-10-20.approved-8')
        self.assertEqual(len(before['approved_genre_release']['definitions']), 15)
        self.assertEqual(len(after['approved_genre_release']['definitions']), 25)
        self.assertEqual(before['approved_alias_release'], after['approved_alias_release'])
        self.assertEqual((before['after_active']['with_effective_genres'], after['after_active']['with_effective_genres']), (0, 1))
        self.assertEqual(after['unknown_tags'], [])
        proposal = load_proposal(Path(settings.BASE_DIR) / 'evaluation/tag-genres-third.20261020.review.json')
        reviewed = build_expansion_review(rows, scope, proposal)
        self.assertTrue(all(item['decision'] == 'already_active_in_code' for item in reviewed['review']['new_genres']))
        self.assertFalse(proposal['apply_to_production'])  # Historical draft is not runtime configuration.
        self.assertEqual(rows, original)


class ThirdGenreAPITests(APITransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='third-genre-owner')
        self.other = get_user_model().objects.create_user(username='third-genre-other')
        self.client.force_authenticate(self.owner)
        for identity in (1, 2, 3, 4):
            self.catalog(identity, ','.join(NEW), score=0)
            anime = Anime.objects.create(bgm_id=identity, chinese_title=f'来源{identity}', base_tags=list(NEW))
            UserAnimeRecord.objects.create(user=self.owner, anime=anime, watch_status='completed',
                personal_score=0 if identity == 4 else 9, custom_tags=list(NEW) + ['二刷'],
                review='PRIVATE_REVIEW_NOT_A_FEATURE')
        for identity in range(100, 130):
            self.catalog(identity, NEW[identity % len(NEW)])
        self.catalog(900, ','.join(NEW), nsfw=True)
        self.catalog(901, ','.join(NEW), nsfw=None)
        self.catalog(902, ','.join(UNAPPROVED))

    def catalog(self, identity, tags, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity,
            **{'title_zh': f'公共作品{identity}', 'tags': tags, 'nsfw': False, 'score': 8,
               'season': '2026-01', 'status_type': 'TV', **changes})

    def test_profile_list_detail_traceability_and_all_original_fields_preserved(self):
        originals = [list(model.objects.values()) for model in (UserAnimeRecord, Anime, RecommendationAnime)]
        with CaptureQueriesContext(connection) as queries:
            profile = self.client.get('/api/recommendations/profile/').data
            listing = self.client.get('/api/recommendations/').data
            self.assertEqual(profile['usable_sample_count'], 3)
            self.assertEqual({item['tag'] for item in profile['tag_summary']}, set(NEW))
            self.assertEqual((listing['algorithm_version'], listing['taxonomy_version']), ('content.v1', 'genres.v8'))
            self.assertTrue(listing['results'])
            for item in listing['results']:
                self.assertNotIn(item['bangumi_id'], (900, 901, 902))
                self.assertEqual(item['evidence']['source']['personal_score'], '9.0')
                self.assertTrue(set(item['evidence']['shared_tags']) <= set(NEW))
                self.assertTrue(item['evidence']['shared_tags'])
                detail = self.client.get(f"/api/recommendations/{item['bangumi_id']}/", {'evidence_token': item['evidence_token']})
                self.assertEqual(detail.status_code, 200, detail.data)
                self.assertEqual(detail.data['evidence'], item['evidence'])
        self.assertFalse(any('"review"' in row['sql'] for row in queries))
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
        for model, original in zip((UserAnimeRecord, Anime, RecommendationAnime), originals):
            self.assertEqual(list(model.objects.values()), original)

    def test_fifty_one_options_order_drafts_versions_and_maximum_twenty(self):
        self.catalog(501, '动物')
        self.catalog(502, '动物')
        self.catalog(503, '竞技')
        url = '/api/recommendations/preferences/'
        read = self.client.get(url).data
        self.assertEqual(len(read['available_tags']), 51)
        self.assertEqual(read['available_tags'][:2], ['动物', '竞技'])
        self.assertEqual(read['tags'], [])
        counts = read['available_tag_counts']
        self.assertEqual([counts[tag] for tag in read['available_tags']], sorted(counts.values(), reverse=True))
        self.assertFalse(RecommendationPreference.objects.filter(user=self.owner).exists())
        saved = self.client.patch(url, {'tags': list(NEW), 'expected_version': 0}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(set(saved.data['tags']), set(NEW))
        self.assertEqual(self.client.patch(url, {'tags': [], 'expected_version': 0}, format='json').status_code, 409)
        self.assertEqual(self.client.patch(url, {'tags': list(AVAILABLE_TAGS[:21])}, format='json').status_code, 400)
        cleared = self.client.patch(url, {'tags': [], 'expected_version': saved.data['version']}, format='json')
        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 3)

    def test_safety_user_isolation_and_empty_private_tags_no_fallback(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)
        self.assertFalse(self.client.get('/api/recommendations/').data['results'])
        self.client.force_authenticate(self.owner)
        for identity in (900, 901):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=[], custom_tags=[])
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['usable_sample_count'], 0)

    def test_v7_caches_cursors_and_evidence_do_not_mix_with_v8(self):
        old_key = cache_namespace('content.v1', 'genres.v7', engine.FEATURE_VERSION, FEATURE_RULE_VERSION)
        cache.set(old_key, {'old': 'sentinel'})
        self.assertEqual(engine.public_features()['taxonomy_version'], 'genres.v8')
        self.assertEqual(cache.get(old_key), {'old': 'sentinel'})
        counts, digest = public_counts()
        old_options = ':'.join(('recommendations.preference-options', OPTION_ORDER_VERSION, 'genres.v7', FEATURE_RULE_VERSION, digest))
        cache.set(old_options, {'total': 1, 'counts': {tag: 0 for tag in MOE_AVAILABLE_TAGS}})
        self.assertEqual(public_counts()[0], counts)
        with patch('recommendations.engine.TAXONOMY_VERSION', 'genres.v7'):
            page = self.client.get('/api/recommendations/', {'page_size': 1}).data
            browse = self.client.get('/api/recommendations/', {'browse': True}).data
        self.assertTrue(browse['next_page_cursor'])
        self.assertEqual(self.client.get('/api/recommendations/', {
            'page': 2, 'page_size': 1, 'state_token': page['state_token']}).status_code, 409)
        self.assertEqual(self.client.get('/api/recommendations/', {'cursor': browse['next_page_cursor']}).status_code, 409)
        context = engine.user_context(self.owner)
        context['taxonomy_version'] = 'genres.v7'
        candidate = RecommendationAnime.objects.get(bangumi_id=100)
        old = EvidencePresenter(self.owner, context, engine.public_features()['weights'], 'content.v1').present(candidate, feature_tags(candidate.tags))
        self.assertEqual(self.client.get('/api/recommendations/100/', {'evidence_token': old['evidence_token']}).status_code, 409)

    def test_old_canonical_only_v1_formula_identical_with_same_data(self):
        RecommendationAnime.objects.update(tags='音乐,日常')
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=['音乐'], custom_tags=[])
        rows = list(engine.safe_catalog().order_by('bangumi_id').values('bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type'))
        context = engine.user_context(self.owner)
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True}
        results = []
        for version in ('genres.v7', 'genres.v8'):
            cloned = deepcopy(context)
            cloned['taxonomy_version'] = version
            ranked, _ = engine.rank_context(cloned, options, engine.build_feature_index(rows, taxonomy_version=version))
            results.append([(item['anime'].bangumi_id, item['value'], item['evidence'], item['reason']) for item in ranked])
        self.assertEqual(results[0], results[1])
