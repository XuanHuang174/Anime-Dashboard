"""11A: synthetic LOCAL PostgreSQL; no external requests or production records."""
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, DatabaseError
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from anime.models import Anime, UserAnimeRecord
from recommendations import content_review
from recommendations.content_scope import require_scope
from recommendations.exploration_catalog import search_catalog
from recommendations.exploration_contract import (ExplorationQuerySerializer,
    ExplorationEvidenceSerializer, capabilities, contract_descriptor, search_term, query_fingerprint)
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.serving_control import change_control, control_state

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('11A requires the isolated LOCAL PostgreSQL settings.')


class ExplorationContractTests(SimpleTestCase):
    def parse(self, values):
        query = ExplorationQuerySerializer(data=values)
        query.is_valid(raise_exception=True)
        return query.validated_data

    def test_intents_are_disjoint_and_never_require_profile(self):
        single = self.parse({'intent': 'similar', 'seed_bangumi_id': '123'})
        self.assertEqual(single['seed_bangumi_id'], 123)
        tags = self.parse({'intent': 'tags', 'include_tags': '音乐,日常,Music'})
        self.assertEqual(tags['include_tags'], ['日常', '音乐'])
        self.assertEqual(tags['match'], 'all')
        self.assertFalse(contract_descriptor()['query']['requires_profile'])
        self.assertFalse(contract_descriptor()['query']['saves_preferences'])

    @override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True)
    def test_cross_runtime_descriptor_matches_frontend_fixture(self):
        fixture = Path(settings.BASE_DIR) / 'frontend/tests/fixtures/recommendationExplorationContract.json'
        self.assertEqual(contract_descriptor(), json.loads(fixture.read_text(encoding='utf-8')))

    def test_forged_private_fields_mixed_intents_and_bad_conditions_rejected(self):
        cases = [dict(intent='similar'), dict(intent='tags'),
            dict(intent='similar', seed_bangumi_id=1, include_tags='音乐'),
            dict(intent='similar', seed_bangumi_id=True), dict(intent='similar', seed_bangumi_id='1.0'),
            dict(intent='similar', seed_bangumi_id=2147483648),
            dict(intent='tags', include_tags='音乐', seed_bangumi_id=1),
            dict(intent='tags', include_tags='音乐', exclude_tags='Music'),
            dict(intent='tags', include_tags='音乐,年度最佳'),
            dict(intent='tags', include_tags='音乐,日常,青春,恋爱,喜剧,奇幻'),
            dict(intent='tags', include_tags='音乐', user_id=12),
            dict(intent='tags', include_tags='音乐', personal_score=9),
            dict(intent='tags', include_tags='音乐', year_from='2026.0'),
            dict(intent='tags', include_tags='音乐', year_from=2026, year_to=2020),
            dict(intent='tags', include_tags='音乐', min_score=8, max_score=7),
            dict(intent='tags', include_tags='音乐', max_score_exclusive=True),
            dict(intent='tags', include_tags='音乐', page_size=24),
            dict(intent='tags', include_tags='音乐', cursor='old-token')]
        for data in cases:
            with self.subTest(data=data):
                self.assertFalse(ExplorationQuerySerializer(data=data).is_valid())
        for query in ('intent=tags&include_tags=', 'intent=tags&include_tags=音乐&include_tags=日常'):
            self.assertFalse(ExplorationQuerySerializer(data=QueryDict(query)).is_valid())

    def test_valid_any_exclusions_score_ceiling_and_year_bounds(self):
        data = self.parse({'intent': 'tags', 'include_tags': '音乐,日常', 'exclude_tags': '战斗',
            'match': 'any', 'sort': 'score', 'year_from': 2008, 'year_to': 2012,
            'min_score': 0, 'max_score': 6, 'max_score_exclusive': True, 'hide_recorded': False})
        self.assertEqual(data['match'], 'any')
        self.assertFalse(data['hide_recorded'])

    def test_query_wire_format_and_fingerprint_preserve_normalized_semantics(self):
        first = self.parse({'intent': 'tags', 'include_tags': 'Music,日常,音乐'})
        second = self.parse({'intent': 'tags', 'include_tags': '日常,音乐', 'min_score': '0.0'})
        self.assertEqual(query_fingerprint(first), query_fingerprint(second))
        self.assertNotEqual(query_fingerprint(first), query_fingerprint(
            self.parse({'intent': 'tags', 'include_tags': '日常,音乐', 'match': 'any'})))
        self.assertEqual(ExplorationQuerySerializer(instance=first).data['include_tags'], ['日常', '音乐'])
        self.assertEqual(ExplorationQuerySerializer(instance=self.parse(
            {'intent': 'similar', 'seed_bangumi_id': 12})).data['seed_bangumi_id'], 12)

    def test_search_normalizes_only_authoritative_ids_and_links(self):
        for raw in ('123', '１２３', ' https://bgm.tv/subject/123/ ', 'http://bangumi.tv/subject/123',
                    'https://chii.in/subject/123'):
            self.assertEqual(search_term(raw), {'kind': 'bangumi_id', 'value': '123'})
        for raw in ('0', '0123', '2147483648', 'https://anilist.co/anime/123',
                    'https://bgm.tv/subject/123?user_id=1', 'https://bgm.tv.evil.test/subject/123',
                    'https://user@bgm.tv/subject/123', '作品\x00名称', ' '):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                search_term(raw)

    def test_capability_requires_literal_true_and_never_advertises_future_pages(self):
        for value in (False, 'True', 1, None):
            with self.settings(RECOMMENDATION_EXPLORATION_ENABLED=value):
                self.assertFalse(capabilities()['enabled'])
        with self.settings(RECOMMENDATION_EXPLORATION_ENABLED=True):
            data = capabilities()
            self.assertTrue(data['catalog_search_enabled'])
            self.assertTrue(data['query_execution_enabled'])
            self.assertEqual(data['supported_intents'], ['similar', 'tags'])
            self.assertEqual([row['label'] for row in data['navigation']], ['个性化推荐', '单番相似', '按标签找'])
            self.assertEqual([row['available'] for row in data['navigation']], [True, True, False])

    def evidence(self, intent='similar'):
        versions = dict(query_fingerprint='a'*64, catalog_feature_version='b'*64,
            scope_revision='c'*64, taxonomy_version='genres.v8',
            ranking_policy_version='similarity.v1' if intent == 'similar' else 'tag-explore.v1')
        source = {'bangumi_id': 12, 'title': '参照作品', 'tags': ['音乐', '日常']} if intent == 'similar' else None
        return dict(contract_version='recommendations.explore.evidence.v1',
            kind='selected_work' if intent == 'similar' else 'selected_tags',
            interpretation='shared_genre_association', is_probability=False,
            shared_tags=['音乐'], source=source, versions=versions)

    def valid_evidence(self, data, intent='similar', *, query=None, candidate_id=13):
        original = self.evidence(intent)
        serializer = ExplorationEvidenceSerializer(data=data, context={
            'query': self.parse(query or ({'intent': 'similar', 'seed_bangumi_id': 12} if intent == 'similar'
                else {'intent': 'tags', 'include_tags': '音乐'})),
            'candidate_id': candidate_id, 'candidate_tags': ['音乐', '青春'],
            'selected_source': original['source'], 'evidence_context': original['versions']})
        valid = serializer.is_valid()
        if valid:
            self.assertEqual(serializer.data, data)
        return valid

    def test_evidence_uses_selected_source_or_query_never_personal_score(self):
        for intent in ('similar', 'tags'):
            self.assertTrue(self.valid_evidence(self.evidence(intent), intent))
        for key, value in [('kind', 'rated_work'), ('is_probability', True), ('is_probability', 'false'),
                           ('shared_tags', ['音乐', '青春']), ('personal_score', 9.5)]:
            data = self.evidence(); data[key] = value
            self.assertFalse(self.valid_evidence(data))
        data = self.evidence(); data['source']['personal_score'] = 9
        self.assertFalse(self.valid_evidence(data))

    def test_evidence_checks_all_any_exclusions_candidate_identity_and_actual_source(self):
        tags = self.evidence('tags')
        query = {'intent': 'tags', 'include_tags': '音乐,日常', 'match': 'any'}
        self.assertTrue(self.valid_evidence(tags, 'tags', query=query))
        self.assertFalse(self.valid_evidence(tags, 'tags', query={**query, 'match': 'all'}))
        self.assertFalse(self.valid_evidence(tags, 'tags', query={
            'intent': 'tags', 'include_tags': '音乐', 'exclude_tags': '青春'}))
        self.assertFalse(self.valid_evidence(self.evidence(), candidate_id=12))
        self.assertFalse(self.valid_evidence(self.evidence(), candidate_id=None))
        data = self.evidence(); data['source']['title'] = '另一部参照'
        self.assertFalse(self.valid_evidence(data))
        data = self.evidence(); data['versions']['scope_revision'] = 'd'*64
        self.assertFalse(self.valid_evidence(data))
        data = self.evidence(); data['source']['bangumi_id'] = 13
        self.assertFalse(self.valid_evidence(data))


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True,
    RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
class ExplorationCatalogTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='explore-empty-owner')
        self.other = get_user_model().objects.create_user(username='explore-other')
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        self.catalog(11, title_zh='星光', title_jp='ほしのひかり', tags='音乐,年度最佳,2026,日本')
        self.catalog(12, title_zh='星光之旅', score=9)
        self.catalog(13, title_zh='另一段星光', score=10)
        self.catalog(14, title_zh='无题材作品', tags='年度最佳,2026')
        self.catalog(90, title_zh='范围外星光', nsfw=True)
        self.catalog(91, title_zh='未知星光', nsfw=None)

    def catalog(self, identity, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity,
            **dict(dict(title_zh=f'合成作品{identity}', title_jp='', tags='音乐,日常', score=8,
                nsfw=False, season='2026-01', status_type='TV',
                cover_url='https://untrusted.invalid/origin.jpg'), **changes))

    def search(self, q='星光', **params):
        return self.client.get('/api/recommendations/explore/catalog/', {'q': q, **params})

    def test_empty_journal_search_exact_prefix_contains_order_and_no_writes(self):
        response = self.search()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['bangumi_id'] for row in response.data['results']], [11, 12, 13])
        self.assertEqual(response.data['results'][0]['tags'], ['音乐'])
        self.assertTrue(response.data['results'][0]['can_seed'])
        self.assertEqual(response.data['results'][0]['cover_url'], '')
        self.assertFalse(RecommendationPreference.objects.exists())
        self.assertFalse(UserAnimeRecord.objects.exists())
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('Authorization', response['Vary']); self.assertIn('Cookie', response['Vary'])

    def test_authoritative_id_link_japanese_and_missing_identity(self):
        for query in ('11', '１１', 'https://bgm.tv/subject/11/', 'ほしのひかり'):
            self.assertEqual(self.search(query).data['results'][0]['bangumi_id'], 11)
        for query in ('999999', '90', '91'):
            response = self.search(query)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['results'], [])

    def test_missing_topics_is_selectable_catalog_metadata_not_fake_recommendation(self):
        row = self.search('14').data['results'][0]
        self.assertEqual(row['tags'], [])
        self.assertFalse(row['can_seed'])
        self.assertEqual(row['unavailable_reason'], 'no_valid_topics')
        self.assertNotIn('reason', row); self.assertNotIn('evidence', row)

    def test_limits_ties_has_more_and_literal_wildcards(self):
        for i in range(101, 121): self.catalog(i, title_zh='同名作品')
        response = self.search('同名作品')
        self.assertEqual(len(response.data['results']), 15)
        self.assertTrue(response.data['has_more'])
        self.assertEqual([r['bangumi_id'] for r in response.data['results']], list(range(101, 116)))
        self.assertEqual(self.search('%%').data['results'], [])

    def test_identity_fields_never_lookup_another_user_or_modify_handbook(self):
        anime = Anime.objects.create(bgm_id=11, chinese_title='PRIVATE_TITLE')
        UserAnimeRecord.objects.create(user=self.other, anime=anime, personal_score=9,
            review='PRIVATE_REVIEW', custom_tags=['PRIVATE_TAG'])
        before = list(UserAnimeRecord.objects.values())
        response = self.search('11')
        self.assertNotIn('PRIVATE', str(response.data))
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))
        self.assertEqual(self.search(user_id=self.other.pk).status_code, 400)

    def test_authentication_read_only_and_strict_search_parameters(self):
        for query in ({}, {'q': ''}, {'q': 'a'}, {'q': 'x'*121}, {'q': '11', 'limit': 1}, {'q': '11', 'cursor': 'old'}):
            self.assertEqual(self.client.get('/api/recommendations/explore/catalog/', query).status_code, 400)
        self.assertEqual(self.client.get('/api/recommendations/explore/catalog/?q=11&q=12').status_code, 400)
        self.assertEqual(self.client.post('/api/recommendations/explore/catalog/', {'q': '11'}).status_code, 405)
        self.client.force_authenticate(None)
        self.assertEqual(self.search().status_code, 401)

    def test_disabled_foundation_and_additive_capabilities(self):
        with self.settings(RECOMMENDATION_EXPLORATION_ENABLED=False):
            response = self.search()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data['code'], 'exploration_disabled')
            caps = self.client.get('/api/recommendations/capabilities/').data
            self.assertFalse(caps['exploration']['enabled'])
            self.assertEqual(caps['algorithm_version'], 'content.v1')
            self.assertEqual(caps['browse_contract_version'], 'recommendations.browse.v1')
        data = self.client.get('/api/recommendations/explore/contract/').data
        self.assertEqual(data['query']['intents']['similar'], 'similarity.v1')
        self.assertEqual(data['evidence']['kinds']['tags'], 'selected_tags')
        self.assertTrue(data['capabilities']['query_execution_enabled'])
        self.assertEqual(self.client.get('/api/recommendations/explore/contract/', {'user_id': 1}).status_code, 400)

    def test_throttle_database_fault_and_catalog_limit_fail_explicitly(self):
        with patch('rest_framework.throttling.ScopedRateThrottle.allow_request', return_value=False), patch(
                'rest_framework.throttling.ScopedRateThrottle.wait', return_value=2):
            self.assertEqual(self.search().status_code, 429)
        with patch('recommendations.exploration_views.search_catalog', side_effect=DatabaseError('PRIVATE_URL')):
            response = self.search()
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('PRIVATE_URL', str(response.data))
        with patch('recommendations.exploration_catalog.MAX_CATALOG_ROWS', 2):
            self.assertEqual(self.search().status_code, 503)

    def test_final_scope_check_catches_catalog_safety_change(self):
        def changed(term, scope):
            data = search_catalog(term, scope)
            RecommendationAnime.objects.filter(bangumi_id=11).update(nsfw=None)
            return data
        with patch('recommendations.exploration_views.search_catalog', side_effect=changed):
            self.assertEqual(self.search('11').status_code, 409)

    def test_bounded_public_query_baseline_no_profile_or_n_plus_one(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i,
            title_zh='性能合成作品', tags='音乐,日常', nsfw=False, score=8) for i in range(1000, 50996)])
        scope = require_scope(self.owner)
        durations = []
        for _ in range(3):
            start = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                data = search_catalog(search_term('性能合成作品'), scope)
            durations.append(perf_counter() - start)
            self.assertEqual(len(queries), 2)
            self.assertEqual(len(data['results']), 15)
            self.assertTrue(data['has_more'])
            self.assertTrue(all('useranimerecord' not in q['sql'].lower() and
                'recommendationpreference' not in q['sql'].lower() for q in queries))
        print('11A synthetic 50000-safe-row service baseline seconds:', [round(d, 5) for d in durations])


@override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True,
    RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True)
class ExplorationAdultCatalogTests(ExplorationCatalogTests):
    # Only extra adult tests inherit setup/helpers; base tests also run with gates open.
    def approve(self):
        self.manager = get_user_model().objects.create_superuser(username='explore-manager', password=None)
        change_control(self.manager, dict(enabled=True, expected_version=control_state(admin=True)['version'],
            operation_id=str(uuid4()), reason='Synthetic 11A test', confirmed=True))
        anime = RecommendationAnime.objects.get(bangumi_id=90)
        content_review.mutate_review(actor=self.manager, bangumi_id=90, action='approve', changes={
            'expected_version': 0, 'expected_catalog_revision': content_review.catalog_revision(anime),
            'expected_policy_version': content_review.POLICY_VERSION, 'operation_id': uuid4(),
            'reason': 'Synthetic 11A only', 'evidence_reference': 'synthetic-only',
            'exclusions_confirmed': True, 'general_adult_confirmed': True})

    def test_permanent_review_search_scope_and_neutral_cover(self):
        self.approve()
        self.assertEqual(self.search('90').data['results'], [])
        data = self.search('90', content_scope='include_adult')
        self.assertEqual(data.status_code, 200, data.data)
        self.assertIsNone(data.data['content_scope_context']['scope_valid_until'])
        self.assertEqual(data.data['results'][0]['content_rating'], 'adult')
        self.assertEqual(data.data['results'][0]['cover_url'], '')
        self.assertEqual(self.search('91', content_scope='include_adult').data['results'], [])

    def test_review_change_rejects_old_scope_precondition(self):
        self.approve()
        scope = require_scope(self.owner, 'include_adult')
        RecommendationAnime.objects.filter(bangumi_id=90).update(tags='音乐,成长')
        response = self.client.get('/api/recommendations/explore/catalog/',
            {'q': '90', 'content_scope': 'include_adult'}, HTTP_X_RECOMMENDATION_SCOPE=scope['scope_revision'])
        self.assertEqual(response.status_code, 409)
