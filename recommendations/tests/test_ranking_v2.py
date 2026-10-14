from copy import deepcopy
import os
import subprocess
import sys
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine, ranking_v2
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_evaluation import evidence_errors, evaluate_probe
from recommendations.quality_readonly import readonly_snapshot


def item(identity, tags, value):
    return {'bangumi_id': identity, 'tags': frozenset(tags), 'value': value,
            'evidence': {'kind': 'explicit_preferences'}, 'reason': 'fixture evidence'}


def ids(rows):
    return [row['bangumi_id'] for row in rows]


class BalancedPoolTests(SimpleTestCase):
    def test_400_pool_keeps_relevance_head_and_reserves_rare_preferred_topic(self):
        rows = [item(i, ['音乐'], .9) for i in range(1, 651)]
        rows += [item(i, ['悬疑'], .7) for i in range(1000, 1150)]
        rows += [item(3000, ['科幻'], 1)]
        pool = ranking_v2.balanced_candidate_pool(rows, {'音乐': .9, '悬疑': .1})
        self.assertEqual(len(pool), 400)
        self.assertTrue(set(range(1, 201)) <= set(ids(pool)))
        self.assertEqual(sum('悬疑' in row['tags'] for row in pool), 100)
        self.assertNotIn(3000, ids(pool))

    def test_multitag_works_do_not_let_majority_genre_take_every_stratum(self):
        rows = [item(i, ['日常'], .9) for i in range(1, 601)]
        rows += [item(i, ['日常', '音乐'], .8) for i in range(1000, 1200)]
        pool = ranking_v2.balanced_candidate_pool(rows, {'日常': .8, '音乐': .2})
        self.assertEqual(sum('音乐' in row['tags'] for row in pool), 100)

    def test_depleted_strata_refill_unused_slots_without_unrelated_filler(self):
        rows = [item(i, ['日常'], .9) for i in range(1, 501)] + [item(1000, ['音乐'], .7)]
        pool = ranking_v2.balanced_candidate_pool(rows, {'日常': .8, '音乐': .2})
        self.assertEqual(len(pool), 400)
        self.assertIn(1000, ids(pool))
        short = ranking_v2.balanced_candidate_pool([item(1, ['日常'], .7), item(2, ['科幻'], 1)], {'日常': 1})
        self.assertEqual(ids(short), [1])

    def test_deduplication_is_by_canonical_id_not_titles_or_genre_combinations(self):
        rows = [item(1, ['音乐'], .8), item(1, ['音乐', '日常'], .9), item(2, ['音乐', '日常'], .9)]
        before = deepcopy(rows)
        pool = ranking_v2.balanced_candidate_pool(rows, {'音乐': 1})
        self.assertEqual(ids(pool), [1, 2])
        self.assertEqual(pool[0]['tags'], {'音乐', '日常'})
        self.assertEqual(rows, before)

    def test_deterministic_pool_for_reversed_inputs_equal_scores_and_collision(self):
        rows = [item(i, ['音乐'] if i % 2 else ['悬疑'], .8) for i in range(1, 801)]
        rows += [item(1, ['音乐', '日常'], .8)]
        a = ranking_v2.balanced_candidate_pool(rows, {'音乐': .5, '悬疑': .5})
        b = ranking_v2.balanced_candidate_pool(reversed(rows), {'悬疑': .5, '音乐': .5})
        self.assertEqual(a, b)

    def test_empty_tags_missing_evidence_invalid_identity_and_nonfinite_scores_cannot_fill(self):
        rows = [item(1, [], 1), item(None, ['音乐'], 1), item(True, ['音乐'], 1), item(-1, ['音乐'], 1),
                item(2, ['音乐'], float('nan')), {**item(3, ['音乐'], .8), 'evidence': None},
                {**item(4, ['音乐'], .8), 'reason': ''}]
        self.assertEqual(ranking_v2.balanced_candidate_pool(rows, {'音乐': 1}), [])
        self.assertEqual(ranking_v2.balanced_candidate_pool([item(1, ['音乐'], 1)], {'音乐': 0}), [])
        for limit in (0, -1):
            self.assertEqual(ranking_v2.balanced_candidate_pool([item(1, ['音乐'], 1)], {'音乐': 1}, limit=limit), [])

    def test_one_slot_and_empty_pool(self):
        self.assertEqual(ranking_v2.balanced_candidate_pool([], {'音乐': 1}), [])
        self.assertEqual(ids(ranking_v2.balanced_candidate_pool(
            [item(2, ['音乐'], .8), item(1, ['音乐'], .9)], {'音乐': 1}, limit=1)), [1])


class DifferentiatedRerankTests(SimpleTestCase):
    def rerank(self, rows, category='all', vector=None, limit=120):
        return ranking_v2.rerank_candidates(rows, vector or {'音乐': 1}, {}, category=category, limit=limit)

    def test_familiar_is_light_discovery_moderate_explore_stronger(self):
        rows = [item(1, ['音乐', '成长'], .9), item(2, ['音乐', '成长'], .86),
                item(3, ['音乐', '日常'], .83), item(4, ['音乐', '悬疑'], .80)]
        self.assertEqual(ids(self.rerank(rows, 'familiar'))[:2], [1, 2])
        self.assertEqual(ids(self.rerank(rows))[:2], [1, 3])
        self.assertEqual(ids(self.rerank(rows, 'explore')), [1, 3, 4, 2])

    def test_explore_rewards_different_new_directions_not_abandoning_shared_preferences(self):
        rows = [item(1, ['音乐', '日常', '成长'], .90), item(2, ['音乐', '成长'], .89),
                item(3, ['音乐', '日常', '悬疑'], .85), item(4, ['音乐', '日常'], .99),
                item(5, ['科幻', '悬疑'], 1)]
        vector = {'音乐': .5, '日常': .5}
        self.assertEqual(ids(self.rerank(rows, 'explore', vector))[:2], [1, 3])
        self.assertEqual(ids(self.rerank(rows, 'all', vector))[:3], [4, 2, 1])
        self.assertNotIn(5, ids(self.rerank(rows, 'explore', vector)))

    def test_bounded_penalty_cannot_promote_weak_relevance_over_a_large_base_gap(self):
        for category in ranking_v2.REPETITION_STRENGTH:
            strength = ranking_v2.REPETITION_STRENGTH[category]
            rows = [item(1, ['音乐', '成长'], .99), item(2, ['音乐', '成长'], .9),
                    item(3, ['音乐', '悬疑'], .9 - strength - .001)]
            self.assertEqual(ids(self.rerank(rows, category))[:2], [1, 2])

    def test_first_result_is_highest_relevance_and_ties_use_id(self):
        rows = [item(3, ['音乐', '成长'], .9), item(2, ['音乐', '成长'], .9), item(1, ['音乐', '悬疑'], .89)]
        for category in ranking_v2.REPETITION_STRENGTH:
            self.assertEqual(ids(self.rerank(rows, category))[0], 2)

    def test_repetition_uses_whole_prefix_not_only_last_five(self):
        rows = [item(1, ['音乐', '成长'], 2)]
        rows += [item(i, ['音乐', f'方向{i}'], 1.5 - i * .03) for i in range(2, 7)]
        rows += [item(7, ['音乐', '成长'], .9), item(8, ['音乐', '悬疑'], .87)]
        self.assertEqual(ids(self.rerank(rows))[-2:], [8, 7])

    def test_narrow_taste_can_repeat_no_hard_quota_or_category_exclusion(self):
        rows = [item(i, ['音乐', '成长'], .9) for i in range(1, 9)]
        for category in ranking_v2.REPETITION_STRENGTH:
            self.assertEqual(ids(self.rerank(rows, category)), list(range(1, 9)))

    def test_rerank_deterministic_bounded_and_does_not_mutate_or_expose_penalty(self):
        rows = [item(i, ['音乐', '日常'] if i % 2 else ['音乐', '成长'], .9) for i in range(1, 150)]
        before = deepcopy(rows)
        a, b = self.rerank(rows), self.rerank(reversed(rows))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 120)
        self.assertEqual(rows, before)
        self.assertTrue(all('match_percentage' not in row and 'penalty' not in row for row in a))
        self.assertEqual(self.rerank(rows, limit=0), [])

    def test_weighted_overlap_is_order_invariant_and_empty_safe(self):
        self.assertEqual(ranking_v2.weighted_overlap(set(), set(), {}), 0)
        self.assertEqual(ranking_v2.weighted_overlap({'音乐', '日常'}, {'音乐', '成长'},
                                                    {'音乐': 3, '日常': 1, '成长': 2}), .5)

    def test_v2_math_evidence_and_ranking_stable_across_worker_hash_seeds(self):
        code = '''
import django, json
django.setup()
from recommendations.engine import taste_vector, evidence_for, candidate_score
from recommendations.ranking_v2 import balanced_candidate_pool, rerank_candidates
genres = ['音乐', '日常', '治愈', '成长', '亲情', '校园', '友情']
seeds = [{'bangumi_id': i, 'title': f'作品{i}', 'score': 9 + i / 10,
          'tags': frozenset(genres[:3+i]),
          'feature_sources': {tag: ('private_base_tags',) for tag in genres[:3+i]}}
         for i in (1, 2, 3)]
context = {'mode': 'personalized', 'seeds': seeds, 'owned': {},
           'preferences': {'version': 1, 'tags': ['音乐', '日常']}}
weights = {tag: 1.234567890123 + i / 10 for i, tag in enumerate(genres)}
vector = taste_vector(context, deterministic=True)
items = []
for i in range(20, 40):
    tags = frozenset(genres[i % 3:i % 3 + 4])
    evidence, reason, affinity = evidence_for(tags, context, weights, deterministic=True)
    value = candidate_score(tags, 8, vector, weights, 'all', affinity, deterministic=True)
    items.append({'bangumi_id': i, 'tags': tags, 'value': value, 'evidence': evidence, 'reason': reason})
pool = balanced_candidate_pool(items, vector, limit=12)
rows = rerank_candidates(pool, vector, weights, category='all')
print(json.dumps({'vector': vector, 'rows': [{**item, 'tags': sorted(item['tags'])} for item in rows]},
                 sort_keys=True, ensure_ascii=True))
'''
        outputs = []
        for seed in ('1', '2', '12345'):
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30,
                env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.recommendation_test_settings', 'PYTHONHASHSEED': seed})
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])


@override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v2')
class V2EngineAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='v2-owner')
        self.other = get_user_model().objects.create_user(username='v2-other')
        self.client.force_authenticate(self.owner)
        for identity in (1, 2, 3):
            self.catalog(identity)
            shared = Anime.objects.create(bgm_id=identity, chinese_title=f'公开作品{identity}', base_tags=['音乐', '日常'])
            UserAnimeRecord.objects.create(user=self.owner, anime=shared, watch_status='completed',
                                          personal_score='9.0', review='DO_NOT_READ_PRIVATE_REVIEW')
        for identity in range(10, 25):
            self.catalog(identity, tags='音乐,日常' if identity < 18 else '音乐,成长')

    def catalog(self, identity, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'公开作品{identity}',
            **{'tags': '音乐,日常', 'nsfw': False, 'score': 8, 'status_type': 'TV', **changes})

    def get(self, **params):
        return self.client.get('/api/recommendations/', params)

    def test_v2_real_evidence_owned_exclusion_and_no_private_changes_or_review_queries(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            response = self.get(page_size=24)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['algorithm_version'], 'content.v2')
        self.assertEqual(response.data['count'], 15)
        context = engine.user_context(self.owner)
        for row in response.data['results']:
            self.assertNotIn(row['bangumi_id'], (1, 2, 3))
            self.assertEqual(evidence_errors({**row, 'tags': frozenset(row['tags'])}, context), [])
            self.assertNotIn('value', row)
            self.assertNotIn('match_percentage', row)
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)

    def test_safe_current_metadata_not_stale_feature_rows_controls_pool_and_evidence(self):
        index = engine.public_features()
        RecommendationAnime.objects.filter(bangumi_id=10).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=11).update(nsfw=None)
        RecommendationAnime.objects.filter(bangumi_id=12).update(tags='悬疑')
        RecommendationAnime.objects.filter(bangumi_id=13).update(tags='二刷,未定义题材')
        self.catalog(90, nsfw=True)
        self.catalog(91, nsfw=None)
        with patch('recommendations.engine.public_features', return_value=index):
            response = self.get(page_size=24)
        self.assertFalse({10, 11, 12, 13, 90, 91} & {row['bangumi_id'] for row in response.data['results']})
        for identity in (10, 11, 90, 91):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)

    def test_safety_change_between_pool_and_hydration_drops_row(self):
        original = ranking_v2.balanced_candidate_pool
        def flip(*args, **kwargs):
            selected = original(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=10).update(nsfw=None)
            RecommendationAnime.objects.filter(bangumi_id=11).update(tags='悬疑')
            return selected
        with patch('recommendations.engine.ranking_v2.balanced_candidate_pool', side_effect=flip):
            response = self.get(page_size=24)
        self.assertEqual(response.status_code, 200)
        self.assertFalse({10, 11} & {row['bangumi_id'] for row in response.data['results']})

    def test_source_becoming_unsafe_mid_scan_requires_fresh_profile_not_leaked_evidence(self):
        original = ranking_v2.balanced_candidate_pool
        def flip(*args, **kwargs):
            selected = original(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=None)
            return selected
        with patch('recommendations.engine.ranking_v2.balanced_candidate_pool', side_effect=flip):
            response = self.get(page_size=24)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('公开作品1', str(response.data))
        self.assertEqual(self.get().data['mode'], 'cold_start')

    def test_invalid_evidence_and_empty_features_never_occupy_rare_genre_quota(self):
        self.catalog(90, tags='音乐')
        original = engine.evidence_for
        def reject_music_only(tags, *args, **kwargs):
            return (None, '', 0) if tags == {'音乐'} else original(tags, *args, **kwargs)
        with patch('recommendations.engine.evidence_for', side_effect=reject_music_only):
            response = self.get(page_size=24)
        self.assertNotIn(90, [row['bangumi_id'] for row in response.data['results']])

    def test_db_balancing_retains_rare_preference_beyond_first_400(self):
        # Preference-only fixture; rare work ranks below all majority works, but
        # is kept in the balanced pool even if Top-120 relevance later omits it.
        RecommendationPreference.objects.create(user=self.other, tags=['日常', '音乐'])
        RecommendationAnime.objects.bulk_create([
            RecommendationAnime(bangumi_id=i, title_zh=f'公开作品{i}', tags='日常', score=10, nsfw=False)
            for i in range(1000, 1500)])
        self.catalog(2000, tags='音乐,成长,友情,青春,治愈', score=0)
        self.client.force_authenticate(self.other)
        captured = []
        original = ranking_v2.balanced_candidate_pool
        def capture(*args, **kwargs):
            selected = original(*args, **kwargs)
            captured.extend(ids(selected))
            return selected
        with patch('recommendations.engine.ranking_v2.balanced_candidate_pool', side_effect=capture):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertIn(2000, captured)
        self.assertEqual(len(captured), 400)

    def test_filters_run_before_balancing_no_unrelated_fill(self):
        self.catalog(50, tags='音乐,成长', status_type='OVA', score=9)
        self.catalog(51, tags='悬疑', status_type='OVA', score=10)
        response = self.get(type='OVA', min_score=8.5)
        self.assertEqual([row['bangumi_id'] for row in response.data['results']], [50])
        empty = self.get(type='OVA', min_score=10)
        self.assertEqual(empty.data['results'], [])
        self.assertEqual(empty.data['mode'], 'personalized')

    def test_familiar_personalized_cannot_use_explicit_only_evidence(self):
        RecommendationPreference.objects.create(user=self.owner, tags=['悬疑'])
        self.catalog(50, tags='悬疑')
        self.assertNotIn(50, [row['bangumi_id'] for row in self.get(category='familiar', page_size=24).data['results']])
        self.assertIn(50, [row['bangumi_id'] for row in self.get(page_size=24).data['results']])

    def test_explore_keeps_shared_preference_and_requires_new_topic(self):
        self.catalog(90, tags='科幻,悬疑')
        rows = self.get(category='explore', page_size=24).data['results']
        self.assertEqual({row['bangumi_id'] for row in rows}, set(range(18, 25)))
        self.assertTrue(all(set(row['tags']) & {'音乐', '日常'} and '成长' in row['tags'] for row in rows))

    def test_no_sequel_guessing_same_title_and_tags_remain_distinct(self):
        RecommendationAnime.objects.filter(bangumi_id__in=[10, 11]).update(title_zh='同名作品', season='2026-01')
        RecommendationAnime.objects.filter(bangumi_id=11).update(season='2026-04')
        all_ids = [row['bangumi_id'] for row in self.get(page_size=24).data['results']]
        familiar_ids = [row['bangumi_id'] for row in self.get(category='familiar', page_size=24).data['results']]
        self.assertTrue({10, 11} <= set(all_ids) & set(familiar_ids))

    def test_pagination_repeatable_and_user_bound(self):
        first = self.get(page_size=3).data
        repeated = self.get(page_size=3).data
        self.assertEqual(first, repeated)
        second = self.get(page_size=3, page=2, state_token=first['state_token'])
        self.assertEqual(second.status_code, 200)
        self.assertFalse({row['bangumi_id'] for row in first['results']} & {row['bangumi_id'] for row in second.data['results']})
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.get(page_size=3, page=2, state_token=first['state_token']).status_code, 409)
        self.assertEqual(self.get().data['mode'], 'preference_based')

    def test_profile_preferences_catalog_and_policy_changes_invalidate_pages(self):
        changes = [
            lambda: UserAnimeRecord.objects.filter(user=self.owner).update(personal_score='9.1'),
            lambda: RecommendationPreference.objects.create(user=self.owner, tags=['科幻'], version=1),
            lambda: RecommendationAnime.objects.filter(bangumi_id=10).update(nsfw=True),
            lambda: UserAnimeRecord.objects.create(user=self.owner, anime=Anime.objects.create(
                chinese_title='未关联新手账'), watch_status='plan'),
        ]
        for change in changes:
            first = self.get(page_size=2).data
            change()
            self.assertEqual(self.get(page_size=2, page=2, state_token=first['state_token']).status_code, 409)
        first = self.get(page_size=2).data
        with patch.dict(ranking_v2.REPETITION_STRENGTH, {'all': .11}):
            self.assertEqual(self.get(page_size=2, page=2, state_token=first['state_token']).status_code, 409)

    def test_algorithm_change_invalidates_even_when_result_ids_are_same(self):
        first = self.get(page_size=2).data
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v1'):
            self.assertEqual(self.get(page_size=2, page=2, state_token=first['state_token']).status_code, 409)
            self.assertEqual(self.get().data['algorithm_version'], 'content.v1')

    def test_sample_demotion_and_private_tag_change_require_new_preference_based_stream(self):
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'])
        first = self.get(page_size=2).data
        UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=1).update(personal_score='8.4')
        self.assertEqual(self.get(page_size=2, page=2, state_token=first['state_token']).status_code, 409)
        fresh = self.get(page_size=2).data
        self.assertEqual(fresh['mode'], 'preference_based')
        self.assertTrue(all(row['evidence']['kind'] == 'explicit_preferences' for row in fresh['results']))
        UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=2).update(private_base_tags=[])
        self.assertEqual(self.get(page_size=2, page=2, state_token=fresh['state_token']).status_code, 409)

    def test_private_empty_tags_do_not_get_catalog_backfill_and_saved_zero_not_positive_sample(self):
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=[], custom_tags=[])
        self.assertEqual(self.get().data['mode'], 'cold_start')
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=['音乐', '日常'], personal_score=0)
        self.assertEqual(self.get().data['mode'], 'cold_start')
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'])
        rows = self.get().data['results']
        self.assertTrue(rows)
        self.assertTrue(all(row['evidence']['kind'] == 'explicit_preferences' for row in rows))

    def test_no_auth_algorithm_query_or_unknown_configuration_escape(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.get().status_code, 401)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.get(algorithm_version='content.v2').status_code, 400)
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='unsupported'):
            self.assertEqual(self.get().status_code, 503)

    def test_bounded_scan_and_no_per_card_queries(self):
        self.get()
        with CaptureQueriesContext(connection) as one:
            self.get(page_size=1)
        with CaptureQueriesContext(connection) as many:
            self.get(page_size=12)
        self.assertEqual(len(one), len(many))
        with patch('recommendations.engine.MAX_CATALOG_ROWS', 2):
            self.assertEqual(self.get().status_code, 503)

    def test_v2_executes_in_enforced_readonly_transaction_and_v1_baseline_is_pinned(self):
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': 12}
        index = engine.public_features()
        with readonly_snapshot():
            context, rows, _, _ = engine.rank_candidates(self.owner, options)
        self.assertEqual(context['algorithm_version'], 'content.v2')
        self.assertTrue(rows)
        original = engine.rank_context
        selected = []
        def capture(*args, **kwargs):
            selected.append(kwargs.get('algorithm_version', engine.ALGORITHM_VERSION))
            return original(*args, **kwargs)
        with patch('recommendations.quality_evaluation.rank_context', side_effect=capture):
            evaluate_probe(index, name='fixture', tags=['音乐'], top_k=3)
        self.assertEqual(selected, ['content.v1'] * 3)

    def test_v2_explore_base_weights_prioritize_relatedness_and_not_match_probability(self):
        self.assertEqual(ranking_v2.EXPLORATION_WEIGHTS, (.65, .25, .1))
        score = engine.candidate_score({'音乐', '成长'}, 8, {'音乐': 1}, {}, 'explore',
                                       exploration_weights=ranking_v2.EXPLORATION_WEIGHTS, deterministic=True)
        self.assertAlmostEqual(score, .65 / (2 ** .5) + .25 / 2 + .1 * .8)
