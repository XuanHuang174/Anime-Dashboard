"""11D: synthetic LOCAL PostgreSQL only. No live catalog/accounts or network."""
import json
import shutil
import subprocess
import time
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db import connection, DatabaseError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from anime.models import UserAnimeRecord
from recommendations import exploration_browsing as explore
from recommendations.exploration_tags import rank_tags
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.taxonomy import AVAILABLE_TAGS
from . import test_exploration_browsing as prior

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('11D requires the isolated LOCAL PostgreSQL test settings.')


class TagRankingTests(SimpleTestCase):
    def setUp(self):
        self.features = [(1, frozenset(['音乐']), 9, 'TV'), (2, frozenset(['日常']), 7, 'TV'),
            (3, frozenset(['音乐', '日常']), 8, 'TV'), (4, frozenset(['音乐', '日常', '战斗']), 10, 'TV'),
            (5, frozenset(['音乐', '日常']), 8, 'TV'), (6, frozenset(['日常']), 0, 'TV')]
        self.rows = [dict(bangumi_id=i, score=score, status_type=kind, season='2026-01') for i, _, score, kind in self.features]
        self.index = {'features': self.features, 'weights': {'音乐': 1, '日常': 3, '战斗': 1}}
        self.query = dict(include_tags=['日常', '音乐'], exclude_tags=[], match='all', sort='relevance', type='all', min_score=0)

    def rank(self, **changes):
        return rank_tags(self.index, self.rows, {**self.query, **changes}, set())

    def test_all_cosine_score_and_id_order_is_deterministic(self):
        self.assertEqual(self.rank(), ([3, 5, 4], 3))
        self.assertEqual(self.rank(exclude_tags=['战斗']), ([3, 5], 2))
        self.assertEqual(self.rank(include_tags=['悬疑']), ([], 0))

    def test_any_weights_coverage_before_cosine_and_public_score(self):
        self.assertEqual(self.rank(match='any'), ([3, 5, 4, 2, 6, 1], 6))
        self.assertEqual(self.rank(match='any', sort='score'), ([4, 1, 3, 5, 2, 6], 6))
        self.assertEqual(self.rank(sort='score'), ([4, 3, 5], 3))

    def test_exclusion_is_hard_for_both_sorts_and_owned_exclusion_before_count(self):
        for mode in ('all', 'any'):
            for sort in ('relevance', 'score'):
                ids, count = rank_tags(self.index, self.rows, {**self.query, 'match': mode, 'sort': sort,
                    'exclude_tags': ['战斗']}, {3})
                self.assertNotIn(3, ids); self.assertNotIn(4, ids); self.assertEqual(count, len(ids))


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_EPOCH='11d-tests',
    RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_ALGORITHM_VERSION='content.v1')
class TagExplorationTests(TransactionTestCase):
    catalog = prior.SingleWorkSnapshotTests.catalog
    record = prior.SingleWorkSnapshotTests.record
    details = prior.SingleWorkSnapshotTests.details
    hide = prior.SingleWorkSnapshotTests.hide

    def setUp(self):
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='11d-owner')
        self.other = get_user_model().objects.create_user(username='11d-other')
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        for i, tags, score in [(1, '音乐,日常', 8), (2, '音乐', 9), (3, '音乐,日常,战斗', 10),
                (4, '日常', 0), (5, '音乐,奇幻', 7.5), (6, '悬疑', 9),
                (7, 'Music,日常,音乐', 8), (8, '音乐,年度最佳,2026,日本', 6), (9, '年度最佳,日本', 0)]:
            self.catalog(i, tags=tags, score=score)
        self.catalog(90, nsfw=True, tags='音乐,日常'); self.catalog(91, nsfw=None, tags='音乐,日常')

    def start(self, **changes):
        return self.client.get('/api/recommendations/explore/', {'intent': 'tags', 'include_tags': '音乐,日常', **changes})

    def options(self, **params):
        return self.client.get('/api/recommendations/explore/options/', params)

    def verify_options_wire(self, data):
        node = shutil.which('node')
        self.assertIsNotNone(node, '11D wire check requires Node')
        root = Path(settings.BASE_DIR)
        result = subprocess.run([node, str(root / 'frontend/tests/fixtures/verifyExplorationOptions.mjs')],
            input=json.dumps(data), capture_output=True, encoding='utf-8', cwd=root, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual(result.stdout, '51')

    def next(self, token, **extra):
        return self.client.get('/api/recommendations/explore/', {'cursor': token, **extra})

    def test_empty_journal_all_any_and_public_evidence_are_independent(self):
        first = self.start()
        self.assertEqual(first.status_code, 200, first.data)
        data = first.data
        self.assertEqual([r['bangumi_id'] for r in data['results']], [1, 7, 3])
        self.assertEqual(data['eligible_count'], 3); self.assertEqual(data['returned_count'], 3)
        self.assertIsNone(data['selected_seed']); self.assertEqual(data['algorithm_version'], 'tag-explore.v1')
        self.assertEqual(data['query']['include_tags'], ['日常', '音乐'])
        for row in data['results']:
            evidence = row['evidence']
            self.assertEqual(evidence['kind'], 'selected_tags'); self.assertIsNone(evidence['source'])
            self.assertFalse(evidence['is_probability']); self.assertEqual(evidence['shared_tags'], ['日常', '音乐'])
            self.assertIn('命中 2/2', row['reason']); self.assertNotIn('喜欢', row['reason'])
            self.assertEqual(evidence['versions'], data['evidence_context'])
        any_match = self.start(match='any', exclude_tags='战斗')
        self.assertEqual(any_match.status_code, 200, any_match.data)
        self.assertEqual({r['bangumi_id'] for r in any_match.data['results']}, {1, 2, 4, 5, 7, 8})
        self.assertTrue(any('命中 1/2' in r['reason'] for r in any_match.data['results']))
        self.assertFalse(RecommendationPreference.objects.exists()); self.assertFalse(UserAnimeRecord.objects.exists())
        self.assertEqual(first['Cache-Control'], 'private, no-store')

    def test_score_sort_missing_score_last_and_zero_never_passes_score_ceiling(self):
        response = self.start(match='any', sort='score')
        self.assertEqual(response.status_code, 200, response.data)
        scores = [r['score'] for r in response.data['results']]
        self.assertEqual(scores, sorted(scores, reverse=True)); self.assertEqual(scores[-1], 0)
        self.catalog(300, score=5.9, tags='音乐,日常')
        low = self.start(match='any', max_score=6, max_score_exclusive=True)
        self.assertEqual([r['bangumi_id'] for r in low.data['results']], [300])

    def test_alias_deduplication_canonical_query_and_strict_invalid_conditions(self):
        response = self.start(include_tags='Music,日常,音乐', exclude_tags='戰鬥,战斗')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['query']['include_tags'], ['日常', '音乐'])
        self.assertEqual(response.data['query']['exclude_tags'], ['战斗'])
        for params in [dict(include_tags=''), dict(include_tags='音乐', exclude_tags='Music'),
            dict(include_tags='年度最佳'), dict(include_tags='音乐,日常,青春,恋爱,奇幻,悬疑'),
            dict(seed_bangumi_id=1), dict(match='maybe'), dict(sort='personal'), dict(user_id=self.other.pk),
            dict(personal_score=9), dict(year_from=2027, year_to=2020), dict(page_size=24)]:
            self.assertEqual(self.start(**params).status_code, 400, params)
        self.assertEqual(self.client.get('/api/recommendations/explore/?intent=tags&include_tags=音乐&include_tags=日常').status_code, 400)
        self.assertEqual(self.client.get('/api/recommendations/explore/', {'intent': 'tags', 'exclude_tags': '音乐'}).status_code, 400)

    def test_no_matches_does_not_relax_all_or_exclusions(self):
        for params in [dict(include_tags='音乐,悬疑'), dict(include_tags='音乐,战斗', exclude_tags='日常'), dict(type='WEB'), dict(year_from=2030)]:
            response = self.start(**params)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['eligible_count'], 0); self.assertEqual(response.data['results'], [])
            self.assertEqual(response.data['empty_reason'], 'no_matches')
            self.assertIsNone(response.data['next_page_cursor']); self.assertIsNone(response.data['next_batch_cursor'])

    def test_full_catalog_hard_filters_precede_recall_caps_and_unknown_years(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'合成高分{i}',
            tags='音乐,日常,战斗', score=10, nsfw=False, season='2000-01', status_type='TV') for i in range(1000, 1450)])
        self.catalog(2000, tags='音乐,日常', score=7.5, season='2010-07', status_type='OVA')
        self.catalog(2001, tags='音乐,日常', score=10, season='', status_type='OVA')
        response = self.start(type='OVA', year_from=2010, year_to=2010, min_score=7, exclude_tags='战斗')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([r['bangumi_id'] for r in response.data['results']], [2000])

    def test_all_owned_states_and_hidden_feedback_are_hard_filters(self):
        for i, status in zip([1, 2, 3, 7], ['completed', 'watching', 'plan', 'on_hold']):
            self.record(i, watch_status=status)
        self.hide(5)
        response = self.start(match='any')
        self.assertEqual({r['bangumi_id'] for r in response.data['results']}, {4, 8})
        visible = self.start(match='any', hide_recorded=False).data['results']
        self.assertNotIn(5, [r['bangumi_id'] for r in visible])
        self.assertEqual({r['bangumi_id']: r['watch_status'] for r in visible if r['bangumi_id'] in [1, 2, 3, 7]},
                         {1: 'completed', 2: 'watching', 3: 'plan', 7: 'on_hold'})

    def test_private_scores_reviews_preferences_are_neither_selected_nor_snapshot_inputs(self):
        record = self.record(1, personal_score=1)
        prefs = RecommendationPreference.objects.create(user=self.owner, tags=['悬疑'], version=1)
        with CaptureQueriesContext(connection) as queries:
            response = self.start(hide_recorded=False)
        self.assertEqual(response.status_code, 200, response.data)
        sql = '\n'.join(q['sql'].lower() for q in queries)
        for forbidden in ('"personal_score"', '"review"', '"custom_tags"', '"private_base_tags"', 'recommendationpreference'):
            self.assertNotIn(forbidden, sql)
        UserAnimeRecord.objects.filter(pk=record.pk).update(personal_score=10, review='CHANGED_PRIVATE')
        RecommendationPreference.objects.filter(pk=prefs.pk).update(tags=['音乐'], version=2)
        token = response.data['snapshot_context']
        check = self.client.get('/api/recommendations/explore/context/', {'snapshot_context': token})
        self.assertEqual(check.status_code, 200, check.data)
        self.assertEqual(check.data['intent'], 'tags'); self.assertIsNone(check.data['selected_seed'])
        self.assertNotIn('PRIVATE', json.dumps(response.data))

    def test_more_than_120_counts_and_stable_pages_never_rerank(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'合成分页{i}',
            tags='音乐,日常', score=8, nsfw=False, season='2026-01', status_type='TV') for i in range(1000, 1140)])
        response = self.start(); self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(data['eligible_count'], 143); self.assertEqual(data['returned_count'], 120)
        first_snapshot, seen = data['snapshot_id'], []
        while True:
            seen.extend(row['bangumi_id'] for row in data['results'])
            cursor = data['next_page_cursor'] or data['next_batch_cursor']
            if not cursor:
                break
            with patch('recommendations.exploration_browsing.rank_tags', side_effect=AssertionError('rerank')):
                next_page = self.next(cursor); repeated = self.next(cursor)
            self.assertEqual(next_page.status_code, 200, next_page.data)
            self.assertEqual([r['bangumi_id'] for r in next_page.data['results']], [r['bangumi_id'] for r in repeated.data['results']])
            data = next_page.data; self.assertEqual(data['snapshot_id'], first_snapshot)
        self.assertEqual(len(seen), 120); self.assertEqual(len(set(seen)), 120)

    def test_details_use_same_tag_proof_and_fresh_detail_after_want_preserves_deadline(self):
        data = self.start().data; item = data['results'][0]
        response = self.details(item)
        self.assertEqual(response.status_code, 200, response.data)
        for key in ('evidence', 'evidence_context', 'evidence_token', 'reason', 'tags'):
            self.assertEqual(response.data[key], item[key])
        self.assertEqual(response.data['query'], data['query']); self.assertIsNone(response.data['selected_seed'])
        self.assertEqual(self.client.post(f'/api/recommendations/{item["bangumi_id"]}/want/', {}, format='json').status_code, 201)
        self.assertEqual(self.details(item).status_code, 409)
        with patch('recommendations.exploration_browsing.rank_tags', side_effect=AssertionError('rerank')):
            fresh = self.details(item, strict=False)
        self.assertEqual(fresh.status_code, 200, fresh.data); self.assertEqual(fresh.data['watch_status'], 'plan')
        self.assertEqual(fresh.data['snapshot_expires_at'], data['snapshot_expires_at'])
        self.assertNotEqual(fresh.data['evidence_token'], item['evidence_token'])

    def test_wrong_candidate_owner_intent_or_proof_is_not_a_valid_detail(self):
        data = self.start().data; item = data['results'][0]
        self.assertEqual(self.details({**item, 'bangumi_id': data['results'][1]['bangumi_id']}).status_code, 409)
        self.assertEqual(self.details({**item, 'evidence_token': 'f'*64}).status_code, 409)
        single = self.client.get('/api/recommendations/explore/', {'intent': 'similar', 'seed_bangumi_id': 2}).data
        selected = next(r for r in single['results'] if r['bangumi_id'] == item['bangumi_id'])
        self.assertEqual(self.details({**item, 'evidence_token': selected['evidence_token']}).status_code, 409)
        self.client.force_authenticate(self.other); self.assertEqual(self.details(item).status_code, 409)

    def test_fresh_detail_still_enforces_hard_tag_exclusions_and_hidden_state(self):
        item = self.start(exclude_tags='战斗').data['results'][0]
        RecommendationAnime.objects.filter(bangumi_id=item['bangumi_id']).update(tags='音乐,日常,战斗')
        self.assertEqual(self.details(item, strict=False).status_code, 409)
        item = self.start().data['results'][0]; self.hide(item['bangumi_id'])
        self.assertEqual(self.details(item, strict=False).status_code, 409)

    def test_cursor_canonical_topic_lists_tampering_expiry_and_epoch(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'合成签名{i}',
            tags='音乐,日常', score=8, nsfw=False, season='2026-01') for i in range(1000, 1030)])
        first = self.start(exclude_tags='战斗', match='any', sort='score').data; cursor = first['next_page_cursor']
        self.assertEqual(self.next(cursor).status_code, 200)
        self.assertEqual(self.next(cursor, include_tags='悬疑').status_code, 400)
        self.assertEqual(self.next(first['snapshot_context']).status_code, 400)
        body = signing.loads(cursor, salt=explore.CURSOR_SALT)
        malformed = [{**body, 'policy': 'similarity.v1'}, {**body, 'offset': 1},
            {**body, 'query': {**body['query'], 'include_tags': ['音乐,日常']}},
            {**body, 'query': {**body['query'], 'include_tags': ['音乐', '日常', '音乐']}},
            {**body, 'issued': int(time.time()) - 901, 'expires': int(time.time()) - 1}]
        for value in malformed:
            self.assertEqual(self.next(signing.dumps(value, salt=explore.CURSOR_SALT)).status_code, 409)
        self.client.force_authenticate(self.other); self.assertEqual(self.next(cursor).status_code, 409)
        self.client.force_authenticate(self.owner)
        with self.settings(RECOMMENDATION_EXPLORATION_EPOCH='new-11d'):
            self.assertEqual(self.next(cursor).status_code, 409)
        with self.settings(RECOMMENDATION_EXPLORATION_ENABLED=False):
            self.assertEqual(self.next(cursor).status_code, 503)

    def test_options_have_51_standard_counts_including_zeros_without_private_filters(self):
        before = self.options(); self.assertEqual(before.status_code, 200, before.data)
        data = before.data; counts = {row['tag']: row['count'] for row in data['tags']}
        self.assertEqual([row['tag'] for row in data['tags']], list(AVAILABLE_TAGS))
        self.assertEqual(counts['音乐'], 6); self.assertEqual(counts['日常'], 4); self.assertEqual(counts['战斗'], 1)
        self.assertEqual(counts['青春'], 0); self.assertEqual(data['catalog_count'], 9)
        self.assertEqual(data['count_definition'], 'scoped_catalog_without_result_filters')
        self.verify_options_wire(data)
        self.record(1); self.hide(3)
        with CaptureQueriesContext(connection) as queries:
            after = self.options()
        self.assertEqual(after.data['tags'], data['tags']); self.assertEqual(after.data['catalog_count'], data['catalog_count'])
        sql = '\n'.join(q['sql'].lower() for q in queries)
        self.assertNotIn('anime_useranimerecord', sql); self.assertNotIn('recommendationfeedback', sql)
        self.assertEqual(before['Cache-Control'], 'private, no-store')
        for params in ({'type': 'TV'}, {'include_tags': '音乐'}, {'min_score': 9}, {'user_id': self.other.pk}, {'q': '音乐'}):
            self.assertEqual(self.options(**params).status_code, 400)

    def test_catalog_status_and_feedback_changes_expire_context(self):
        for change in (lambda: RecommendationAnime.objects.filter(bangumi_id=6).update(tags='音乐'),
                       lambda: self.record(1), lambda: self.hide(7)):
            first = self.start().data; change()
            response = self.client.get('/api/recommendations/explore/context/', {'snapshot_context': first['snapshot_context']})
            self.assertEqual(response.status_code, 409); self.assertEqual(response.data['code'], 'exploration_snapshot_changed')

    def test_final_catalog_check_refuses_late_options_or_result_changes(self):
        from recommendations.exploration_tags import tag_options
        original = explore.browse_page
        def changed_options(*args, **kwargs):
            data = tag_options(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=1).update(tags='悬疑')
            return data
        with patch('recommendations.exploration_tags.tag_options', side_effect=changed_options):
            self.assertEqual(self.options().status_code, 409)
        def changed_result(*args, **kwargs):
            data = original(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=2).update(score=5)
            return data
        with patch('recommendations.exploration_browsing.browse_page', side_effect=changed_result):
            self.assertEqual(self.start().status_code, 409)

    def test_options_and_results_fail_explicitly_without_legacy_fallback(self):
        original = cache.get
        def public_cache_failure(key, *args, **kwargs):
            if str(key).startswith('recommendations.browse.public'):
                raise RuntimeError('synthetic-public-cache')
            return original(key, *args, **kwargs)
        with patch('recommendations.browsing.cache.get', side_effect=public_cache_failure):
            self.assertEqual(self.options().status_code, 200); self.assertEqual(self.start().status_code, 200)
        with patch('recommendations.browsing.cache.get', side_effect=RuntimeError('synthetic-cache')):
            self.assertEqual(self.options().status_code, 503); self.assertEqual(self.start().status_code, 503)
        with patch('recommendations.exploration_tags.catalog_revision', side_effect=DatabaseError('synthetic-db')):
            self.assertEqual(self.options().status_code, 503)
        with self.settings(RECOMMENDATION_EXPLORATION_ENABLED=False):
            self.assertEqual(self.start().status_code, 503); self.assertEqual(self.options().status_code, 503)
        self.client.force_authenticate(None)
        self.assertEqual(self.start().status_code, 401); self.assertEqual(self.options().status_code, 401)

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True)
    def test_permanent_adult_scope_counts_candidates_details_and_revocation(self):
        prior.AdultSingleWorkTests.approve(self)
        safe = self.options().data
        adult = self.options(content_scope='include_adult')
        self.assertEqual(adult.status_code, 200, adult.data)
        self.assertIsNone(adult.data['content_scope_context']['scope_valid_until'])
        self.verify_options_wire(adult.data)
        self.assertEqual(adult.data['catalog_count'], safe['catalog_count'] + 1)
        self.assertEqual(next(row['count'] for row in adult.data['tags'] if row['tag'] == '音乐'), 7)
        response = self.start(content_scope='include_adult')
        self.assertEqual(response.status_code, 200, response.data)
        item = next(row for row in response.data['results'] if row['bangumi_id'] == 90)
        self.assertEqual(item['cover_url'], ''); self.assertIsNone(item['evidence']['source'])
        self.assertEqual(self.details(item, content_scope='include_adult').status_code, 200)
        self.assertNotIn(91, [row['bangumi_id'] for row in response.data['results']])
        self.assertNotIn(90, [row['bangumi_id'] for row in self.start().data['results']])
        RecommendationAnime.objects.filter(bangumi_id=90).update(tags='音乐,日常,奇幻')
        self.assertEqual(self.details(item, content_scope='include_adult').status_code, 409)
        self.assertEqual(self.options(content_scope='include_adult').status_code, 403)
        self.assertEqual(self.options().data['catalog_count'], safe['catalog_count'])


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_EPOCH='11d-scale')
class TagExplorationScaleTests(TransactionTestCase):
    setUp = TagExplorationTests.setUp
    catalog = TagExplorationTests.catalog
    start = TagExplorationTests.start
    options = TagExplorationTests.options
    next = TagExplorationTests.next

    def test_50000_rows_counts_full_matches_and_continuation_have_bounded_reads(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'规模标签{i}',
            tags='音乐,日常', score=8, nsfw=False, season='2026-01', status_type='TV') for i in range(1000, 50989)])
        self.assertEqual(RecommendationAnime.objects.count(), 50000)
        observations = []
        for phase in ('cold', 'warm'):
            started = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                response = self.start()
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['eligible_count'], 49992); self.assertEqual(response.data['returned_count'], 120)
            self.assertLessEqual(len(queries), 36)
            observations.append({'phase': phase, 'seconds': round(perf_counter() - started, 4), 'sql': len(queries)})
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            options = self.options()
        self.assertEqual(options.status_code, 200, options.data)
        self.assertEqual(options.data['catalog_count'], 49998)
        self.assertEqual(next(row['count'] for row in options.data['tags'] if row['tag'] == '音乐'), 49995)
        self.assertLessEqual(len(queries), 30)
        observations.append({'phase': 'options', 'seconds': round(perf_counter() - started, 4), 'sql': len(queries)})
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries, patch('recommendations.exploration_browsing.rank_tags',
                side_effect=AssertionError('rerank')), patch('recommendations.exploration_browsing.public_browse_index',
                side_effect=AssertionError('rebuild')):
            following = self.next(response.data['next_page_cursor'])
        self.assertEqual(following.status_code, 200, following.data)
        self.assertLessEqual(len(queries), 36)
        observations.append({'phase': 'continuation', 'seconds': round(perf_counter() - started, 4), 'sql': len(queries)})
        print('11D 50000-row synthetic API:', json.dumps(observations))
        self.catalog(60000, nsfw=None)
        self.assertEqual(self.start().status_code, 503); self.assertEqual(self.options().status_code, 503)
