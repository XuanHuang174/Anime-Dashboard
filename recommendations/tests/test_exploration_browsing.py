"""11B: LOCAL synthetic PostgreSQL only; no live accounts/catalog or network."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
from time import perf_counter

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db import connection, close_old_connections, DatabaseError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations import exploration_browsing as explore, content_review
from recommendations.content_scope import require_scope
from recommendations.exploration_contract import ExplorationQuerySerializer
from recommendations.feedback import mutate_feedback, current_version
from recommendations.matching import record_fingerprint, catalog_fingerprint
from recommendations.models import (RecommendationAnime, RecommendationPreference,
    RecommendationRecordIdentity)
from recommendations.resolution import record_row
from recommendations.serving_control import change_control, control_state

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('11B requires isolated LOCAL PostgreSQL test settings.')


class SingleWorkRankingTests(SimpleTestCase):
    def test_real_weighted_cosine_quality_tie_and_stable_id_order(self):
        rows = [dict(bangumi_id=i, score=score, status_type='TV', season='2026-01')
                for i, score in [(1, 7), (2, 8), (3, 8), (4, 10), (5, 10)]]
        features = [(1, frozenset(['音乐', '日常']), 7, 'TV'),
            (2, frozenset(['音乐', '日常']), 8, 'TV'), (3, frozenset(['音乐', '日常']), 8, 'TV'),
            (4, frozenset(['音乐']), 10, 'TV'), (5, frozenset(['悬疑']), 10, 'TV')]
        index = {'weights': {'音乐': 2, '日常': 1, '悬疑': 3}, 'features': features}
        query = {'type': 'all', 'min_score': 0, 'year_from': None, 'year_to': None}
        ids, count = explore.rank_similar(index, rows, query, frozenset(['音乐', '日常']), set())
        self.assertEqual(ids, [2, 3, 1, 4]); self.assertEqual(count, 4)
        self.assertEqual(explore.rank_similar(index, rows, query, frozenset(), set()), ([], 0))


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_EPOCH='11b-tests',
    RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_ALGORITHM_VERSION='content.v1')
class SingleWorkSnapshotTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='11b-owner')
        self.other = get_user_model().objects.create_user(username='11b-other')
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        self.catalog(1, tags='音乐,日常')
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i,
            title_zh=f'合成探索{i}', tags=('音乐,日常', '音乐,青春', '音乐,奇幻')[i % 3],
            score=8, nsfw=False, season='2026-01', status_type='TV') for i in range(10, 155) if i not in (90, 91)])
        self.catalog(200, tags='悬疑'); self.catalog(90, nsfw=True); self.catalog(91, nsfw=None)

    def catalog(self, identity, **changes):
        defaults = dict(title_zh=f'合成探索{identity}', title_jp='', tags='音乐', score=8,
            nsfw=False, season='2026-01', status_type='TV', synopsis='PUBLIC_SYNOPSIS',
            cover_url='https://untrusted.invalid/COVER_SENTINEL')
        return RecommendationAnime.objects.create(bangumi_id=identity, **{**defaults, **changes})

    def start(self, **changes):
        return self.client.get('/api/recommendations/explore/', {
            'intent': 'similar', 'seed_bangumi_id': 1, **changes})

    def next(self, token, **extra):
        return self.client.get('/api/recommendations/explore/', {'cursor': token, **extra})

    def details(self, item, *, strict=True, **extra):
        return self.client.get(f'/api/recommendations/explore/{item["bangumi_id"]}/', {
            'detail_context': item['detail_context'],
            **({'evidence_token': item['evidence_token']} if strict else {}), **extra})

    def hide(self, identity):
        return mutate_feedback(user=self.owner, action='hide', changes={'bangumi_id': identity,
            'expected_feedback_version': current_version(self.owner), 'operation_id': str(uuid4())})

    def record(self, identity, **changes):
        anime = Anime.objects.create(bgm_id=identity, chinese_title=f'私人来源{identity}')
        return UserAnimeRecord.objects.create(user=self.owner, anime=anime,
            **{'watch_status': 'plan', 'review': 'PRIVATE_REVIEW_SENTINEL', 'custom_tags': ['PRIVATE_TAG'], **changes})

    def test_empty_journal_has_real_source_evidence_and_distinct_counts(self):
        before = list(RecommendationAnime.objects.values())
        response = self.start()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(data['contract_version'], 'recommendations.explore.v1')
        self.assertEqual(data['algorithm_version'], 'similarity.v1')
        self.assertEqual(data['eligible_count'], 143) # IDs 90/91 were replaced by restricted rows.
        self.assertEqual(data['returned_count'], 120); self.assertEqual(len(data['results']), 12)
        self.assertEqual(data['selected_seed']['bangumi_id'], 1)
        for item in data['results']:
            self.assertNotIn(item['bangumi_id'], [1, 90, 91, 200])
            self.assertEqual(item['evidence']['kind'], 'selected_work')
            self.assertEqual(item['evidence']['source']['bangumi_id'], 1)
            self.assertEqual(set(item['evidence']['shared_tags']), set(item['tags']) & {'音乐', '日常'})
            self.assertNotIn('personal_score', str(item))
            self.assertIn('参照作品', item['reason'])
        self.assertFalse(RecommendationPreference.objects.exists()); self.assertFalse(UserAnimeRecord.objects.exists())
        self.assertEqual(before, list(RecommendationAnime.objects.values()))
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_missing_seed_no_topics_zero_match_and_seed_not_filtered_by_result_controls(self):
        self.assertEqual(self.start(seed_bangumi_id=999999).status_code, 404)
        RecommendationAnime.objects.filter(bangumi_id=1).update(tags='年度最佳,2026,日本')
        empty = self.start().data
        self.assertEqual(empty['empty_reason'], 'seed_without_topics')
        self.assertEqual(empty['results'], []); self.assertIsNone(empty['next_page_cursor'])
        RecommendationAnime.objects.filter(bangumi_id=1).update(tags='音乐,日常')
        empty = self.start(type='WEB').data
        self.assertEqual(empty['empty_reason'], 'no_matches')
        self.catalog(201, tags='音乐', status_type='WEB', season='2010-07')
        self.assertEqual([r['bangumi_id'] for r in self.start(type='WEB', year_from=2010, year_to=2010).data['results']], [201])

    def test_hard_filters_before_400_and_public_score_upper_bounds(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i,
            title_zh=f'旧作品{i}', tags='音乐,日常', score=10, nsfw=False,
            season='2000-01', status_type='TV') for i in range(1000, 1450)])
        self.catalog(2000, score=7.5, status_type='OVA', season='2026-04')
        self.catalog(2001, score=10, status_type='OVA', season='')
        self.catalog(2002, score=5.9, status_type='OVA', season='2026-04')
        self.catalog(2003, score=0, status_type='OVA', season='2026-04')
        data = self.start(type='OVA', min_score=7, year_from=2026, year_to=2026).data
        self.assertEqual([r['bangumi_id'] for r in data['results']], [2000])
        data = self.start(type='OVA', max_score=6, max_score_exclusive=True).data
        self.assertEqual([r['bangumi_id'] for r in data['results']], [2002])

    def test_owned_all_states_and_hidden_seed_remain_valid_reference(self):
        for i, status in zip([1, 12, 15, 18], ['completed', 'watching', 'plan', 'on_hold']):
            self.record(i, watch_status=status)
        self.hide(1); self.hide(21)
        first = self.start().data
        self.assertTrue(all(r['bangumi_id'] not in [1, 12, 15, 18, 21] for r in first['results']))
        visible = self.start(hide_recorded=False).data
        lookup = {r['bangumi_id']: r for r in visible['results']}
        for i, status in zip([12, 15, 18], ['watching', 'plan', 'on_hold']):
            self.assertEqual(lookup[i]['watch_status'], status)
        self.assertNotIn(1, lookup); self.assertNotIn(21, lookup)

    def test_score_preferences_and_reviews_do_not_participate_or_get_selected(self):
        record = self.record(1, personal_score=1)
        RecommendationPreference.objects.create(user=self.owner, tags=['悬疑'], version=1)
        with CaptureQueriesContext(connection) as queries:
            first = self.start().data
        sql = '\n'.join(q['sql'].lower() for q in queries)
        for forbidden in ('"personal_score"', '"custom_tags"', '"review"', '"private_description"',
                          '"private_base_tags"', '"anime"."base_tags"', 'recommendationpreference'):
            self.assertNotIn(forbidden, sql)
        UserAnimeRecord.objects.filter(pk=record.pk).update(personal_score=10, review='CHANGED_PRIVATE')
        RecommendationPreference.objects.filter(user=self.owner).update(tags=['战斗'], version=2)
        second = self.next(first['next_page_cursor'])
        self.assertEqual(second.status_code, 200, second.data)
        self.assertNotIn('PRIVATE', json.dumps(second.data))

    def test_private_alias_ownership_is_validated_without_profile_reads(self):
        anime = Anime.objects.create(chinese_title='旧私人身份')
        record = UserAnimeRecord.objects.create(user=self.owner, anime=anime, watch_status='plan')
        target = RecommendationAnime.objects.get(bangumi_id=12)
        RecommendationRecordIdentity.objects.create(record=record, catalog_anime=target, source='user_confirmed',
            record_fingerprint=record_fingerprint(record_row(record)), catalog_fingerprint=catalog_fingerprint(target),
            matching_version='dual-title-quarter.v1', active=True, version=1)
        self.assertNotIn(12, [r['bangumi_id'] for r in self.start().data['results']])
        UserAnimeRecord.objects.filter(pk=record.pk).update(metadata_initialized=True, private_chinese_title='已改名')
        self.assertIn(12, [r['bangumi_id'] for r in self.start().data['results']])

    def test_all_pages_are_fixed_deduplicated_and_never_rerank(self):
        data = self.start().data
        seen, snapshots = [], set()
        while True:
            seen.extend(r['bangumi_id'] for r in data['results']); snapshots.add(data['snapshot_id'])
            token = data['next_page_cursor'] or data['next_batch_cursor']
            if not token: break
            with patch('recommendations.exploration_browsing.rank_similar', side_effect=AssertionError('rerank')):
                response = self.next(token); repeated = self.next(token)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([r['bangumi_id'] for r in response.data['results']], [r['bangumi_id'] for r in repeated.data['results']])
            data = response.data
        self.assertEqual(len(seen), 120); self.assertEqual(len(set(seen)), 120); self.assertEqual(len(snapshots), 1)

    def test_cursor_owner_namespace_tampering_expiry_epoch_and_parameters(self):
        first = self.start().data; cursor = first['next_page_cursor']
        self.assertEqual(self.next(cursor, intent='similar').status_code, 400)
        self.assertEqual(self.next('altered' + cursor).status_code, 400)
        self.assertEqual(self.next(first['snapshot_context']).status_code, 400)
        self.client.force_authenticate(self.other); self.assertEqual(self.next(cursor).status_code, 409)
        self.client.force_authenticate(self.owner)
        body = signing.loads(cursor, salt=explore.CURSOR_SALT)
        self.assertEqual(len(body['owner']), 64); self.assertNotEqual(body['owner'], str(self.owner.pk))
        self.assertNotIn('personal_score', str(body)); self.assertNotIn('PRIVATE', str(body))
        expired = {**body, 'issued': int(time.time()) - 901, 'expires': int(time.time()) - 1}
        self.assertEqual(self.next(signing.dumps(expired, salt=explore.CURSOR_SALT)).status_code, 409)
        malformed = {**body, 'offset': 1}
        self.assertEqual(self.next(signing.dumps(malformed, salt=explore.CURSOR_SALT)).status_code, 409)
        with self.settings(RECOMMENDATION_EXPLORATION_ENABLED=False):
            self.assertEqual(self.next(cursor).status_code, 503)
        with self.settings(RECOMMENDATION_EXPLORATION_EPOCH='new-release'):
            self.assertEqual(self.next(cursor).status_code, 409)

    def test_catalog_direct_update_owner_status_and_feedback_invalidate_context(self):
        first = self.start().data
        context = lambda: self.client.get('/api/recommendations/explore/context/', {'snapshot_context': first['snapshot_context']})
        self.assertEqual(context().status_code, 200)
        RecommendationAnime.objects.filter(bangumi_id=200).update(score=9)
        response = self.next(first['next_page_cursor'])
        self.assertEqual(response.status_code, 409); self.assertEqual(response.data['code'], 'exploration_snapshot_changed')
        self.assertEqual(context().status_code, 409)
        first = self.start().data; self.record(12)
        self.assertEqual(self.next(first['next_page_cursor']).status_code, 409)
        first = self.start().data; self.hide(15)
        self.assertEqual(self.next(first['next_page_cursor']).status_code, 409)

    def test_list_and_detail_evidence_identical_and_bound_to_work_owner(self):
        data = self.start().data; item = data['results'][0]
        with patch('recommendations.exploration_browsing.rank_similar', side_effect=AssertionError('rerank')):
            response = self.details(item)
        self.assertEqual(response.status_code, 200, response.data)
        for key in ('evidence', 'evidence_token', 'evidence_context', 'reason', 'tags'):
            self.assertEqual(response.data[key], item[key])
        self.assertIn('synopsis', response.data)
        other = {**item, 'bangumi_id': data['results'][1]['bangumi_id']}
        self.assertEqual(self.details(other).status_code, 409)
        self.assertEqual(self.details({**item, 'evidence_token': 'f'*64}).status_code, 409)
        self.client.force_authenticate(self.other); self.assertEqual(self.details(item).status_code, 409)

    def test_want_then_fresh_whole_detail_without_extending_context_deadline(self):
        data = self.start().data; item = data['results'][0]
        before = list(UserAnimeRecord.objects.values())
        response = self.client.post(f'/api/recommendations/{item["bangumi_id"]}/want/', {}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(before) + 1, UserAnimeRecord.objects.count())
        self.assertEqual(self.details(item).status_code, 409)
        with patch('recommendations.exploration_browsing.rank_similar', side_effect=AssertionError('rerank')):
            detail = self.details(item, strict=False)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['watch_status'], 'plan')
        self.assertEqual(detail.data['snapshot_expires_at'], data['snapshot_expires_at'])
        self.assertNotEqual(detail.data['evidence_token'], item['evidence_token'])
        self.assertEqual(self.next(data['next_page_cursor']).status_code, 409)

    def test_fresh_detail_refuses_hidden_or_no_longer_matching_candidate(self):
        item = self.start().data['results'][0]; self.hide(item['bangumi_id'])
        self.assertEqual(self.details(item, strict=False).status_code, 409)
        item = self.start().data['results'][0]
        RecommendationAnime.objects.filter(bangumi_id=item['bangumi_id']).update(tags='悬疑')
        self.assertEqual(self.details(item, strict=False).status_code, 409)

    def test_final_read_committed_check_catches_source_change_after_snapshot(self):
        original = explore.browse_page
        def changed(*args, **kwargs):
            data = original(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=None)
            return data
        with patch('recommendations.exploration_browsing.browse_page', side_effect=changed):
            self.assertEqual(self.start().status_code, 409)

    def test_cache_fault_and_tag_intent_never_fallback_to_personal(self):
        original = cache.get
        def get(key, *args, **kwargs):
            if str(key).startswith('recommendations.browse.public'):
                raise RuntimeError('synthetic-public-cache')
            return original(key, *args, **kwargs)
        with patch('recommendations.browsing.cache.get', side_effect=get):
            response = self.start()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(len(data['results']), 12)
        with patch('recommendations.browsing.cache.get', side_effect=RuntimeError('synthetic-all-cache')):
            self.assertEqual(self.start().status_code, 503)
        tags = self.client.get('/api/recommendations/explore/', {'intent': 'tags', 'include_tags': '音乐'})
        self.assertEqual(tags.status_code, 200, tags.data)
        self.assertEqual(tags.data['intent'], 'tags')
        self.assertEqual(tags.data['results'][0]['evidence']['kind'], 'selected_tags')
        for params in ({'intent':'similar'}, {'intent':'similar','seed_bangumi_id':1,'user_id':self.other.pk}):
            self.assertEqual(self.client.get('/api/recommendations/explore/', params).status_code, 400)
        self.client.force_authenticate(None); self.assertEqual(self.start().status_code, 401)

    def test_concurrent_same_cursor_reads_have_same_order(self):
        cursor = self.start().data['next_page_cursor']
        def read(_):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(self.owner)
                response = client.get('/api/recommendations/explore/', {'cursor': cursor})
                return response.status_code, [r['bangumi_id'] for r in response.data.get('results', [])]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(read, range(2)))
        self.assertEqual(results[0], results[1]); self.assertEqual(results[0][0], 200)


@override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True)
class AdultSingleWorkTests(SingleWorkSnapshotTests):
    def approve(self):
        manager = get_user_model().objects.create_superuser(username='11b-manager', password=None)
        change_control(manager, {'enabled':True, 'expected_version':control_state(admin=True)['version'],
            'operation_id':str(uuid4()), 'reason':'Synthetic 11B only', 'confirmed':True})
        anime = RecommendationAnime.objects.get(bangumi_id=90)
        content_review.mutate_review(actor=manager, bangumi_id=90, action='approve', changes={
            'expected_version':0, 'expected_catalog_revision':content_review.catalog_revision(anime),
            'expected_policy_version':content_review.POLICY_VERSION, 'operation_id':uuid4(),
            'reason':'Synthetic 11B only', 'evidence_reference':'synthetic-only',
            'exclusions_confirmed':True, 'general_adult_confirmed':True})

    def test_adult_seed_permanent_review_details_and_revocation(self):
        self.approve()
        self.assertEqual(self.start(seed_bangumi_id=90).status_code, 404)
        first = self.start(seed_bangumi_id=90, content_scope='include_adult')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertIsNone(first.data['content_scope_context']['scope_valid_until'])
        self.assertEqual(first.data['selected_seed']['cover_url'], '')
        item = first.data['results'][0]
        self.assertEqual(self.details(item, content_scope='include_adult').status_code, 200)
        self.assertEqual(item['evidence']['source']['bangumi_id'], 90)
        RecommendationAnime.objects.filter(bangumi_id=90).update(tags='音乐,成长')
        self.assertEqual(self.next(first.data['next_page_cursor'], content_scope='include_adult').status_code, 409)


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_EPOCH='11b-performance',
    RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_ALGORITHM_VERSION='content.v1')
class SingleWorkPerformanceTests(TransactionTestCase):
    setUp = SingleWorkSnapshotTests.setUp
    catalog = SingleWorkSnapshotTests.catalog
    start = SingleWorkSnapshotTests.start
    next = SingleWorkSnapshotTests.next

    def test_50000_catalog_cold_warm_and_continuation_are_bounded(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i,
            title_zh=f'规模合成{i}', tags='音乐,日常', score=8, nsfw=False,
            season='2026-01', status_type='TV') for i in range(1000, 50853)])
        self.assertEqual(RecommendationAnime.objects.count(), 50000)
        observations = []
        for phase in ('cold', 'warm'):
            started = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                response = self.start()
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(len(response.data['results']), 12)
            self.assertEqual(response.data['returned_count'], 120)
            self.assertGreater(response.data['eligible_count'], 400)
            self.assertLessEqual(len(queries), 36)
            self.assertTrue(all('personal_score' not in q['sql'] and 'recommendationpreference' not in q['sql'] for q in queries))
            observations.append({'phase': phase, 'seconds': round(perf_counter()-started, 4), 'sql':len(queries)})
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries, patch(
                'recommendations.exploration_browsing.rank_similar', side_effect=AssertionError('rerank')), patch(
                'recommendations.exploration_browsing.public_browse_index', side_effect=AssertionError('rebuild-index')):
            following = self.next(response.data['next_page_cursor'])
        self.assertEqual(following.status_code, 200, following.data)
        self.assertLessEqual(len(queries), 36)
        observations.append({'phase':'continuation','seconds':round(perf_counter()-started,4),'sql':len(queries)})
        print('11B 50000-row synthetic API:', json.dumps(observations))
