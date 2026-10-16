"""V2 preference-only discovery revision; local isolated PostgreSQL fixtures."""
from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations import engine, ranking_v2
from recommendations.comparison import measured_rank
from recommendations.diagnostics import (prepare_sample, diagnose_sample, public_index,
                                         PREVIOUS_V2_ARM, target_diagnostic)
from recommendations.feedback import feedback_snapshot, mutate_feedback
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.resolution import resolved_records
from recommendations.services import preference_contract

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('Explicit isolated PostgreSQL required; never run this against real accounts.')


def preferences():
    return {'mode': 'preference_based', 'seeds': [], 'owned': {}, 'preferences': {'tags': ['音乐'], 'version': 0}}


def scorer(context=None, category='all', policy=None):
    context = context or preferences()
    return engine.V2RequestScorer(context, {'category': category}, {},
        engine.taste_vector(context, deterministic=True), 'genres.v4', policy_version=policy)


def item(identity, tags, value):
    return {'bangumi_id': identity, 'tags': frozenset(tags), 'value': value,
            'evidence': {'kind': 'explicit_preferences'}, 'reason': 'synthetic evidence'}


class PreferenceDiscoveryMathTests(SimpleTestCase):
    def test_previous_policy_is_frozen_and_new_single_preference_does_not_require_exact_genre_set(self):
        raw = frozenset({'音乐', '成长'})
        old = scorer(policy=ranking_v2.PREVIOUS_POLICY_VERSION)
        current = scorer()
        before = old.score(raw, 8)[2]
        self.assertAlmostEqual(before, .5 * .5 + .2 * .5 + .15 / 2 ** .5 + .1 * .8 + .05 * .5)
        self.assertGreater(current.score(raw, 8)[2], before)
        self.assertLess(current.score(frozenset({'音乐'}), 8)[2] - current.score(raw, 8)[2], .25)

    def test_high_relevance_gap_cannot_be_overridden_with_weak_many_topic_filler(self):
        strong = ranking_v2.base_score(category='all', nearest=1, affinity=1, related=1, novelty=0,
            source_rating=0, public_rating=8, preference_discovery=True, preference_coverage=1)
        weak = ranking_v2.base_score(category='all', nearest=.1, affinity=.1, related=.1, novelty=.9,
            source_rating=0, public_rating=8, preference_discovery=True, preference_coverage=1)
        self.assertGreater(strong - weak, ranking_v2.REPETITION_STRENGTH['all'])
        rows = [item(n, ['音乐'], strong) for n in range(1, 20)] + [item(100, ['音乐', '成长'], weak)]
        result = ranking_v2.rerank_candidates(rows, {'音乐': 1}, {}, category='all', limit=10)
        self.assertNotIn(100, [row['bangumi_id'] for row in result])

    def test_personalized_all_and_both_other_categories_are_exactly_unchanged(self):
        context = preferences()
        context.update(mode='personalized', seeds=[{'bangumi_id': 1, 'title': '合成来源', 'score': 9,
            'tags': frozenset({'音乐'}), 'feature_sources': {'音乐': ('private_base_tags',)}}])
        for mode_context in (context, preferences()):
            for category in ('all', 'familiar', 'explore'):
                if mode_context['mode'] == 'preference_based' and category == 'all':
                    continue
                a, b = scorer(deepcopy(mode_context), category), scorer(deepcopy(mode_context), category, ranking_v2.PREVIOUS_POLICY_VERSION)
                for tags in ({'音乐'}, {'音乐', '成长'}, {'音乐', '日常'}):
                    self.assertEqual(a.score(frozenset(tags), 8), b.score(frozenset(tags), 8))

    def test_no_false_personal_rating_and_no_unrelated_or_quantity_filler(self):
        current = scorer()
        self.assertIsNone(current.score(frozenset({'悬疑'}), 8))
        evidence, reason, _ = current.score(frozenset({'音乐', '成长'}), 8)
        self.assertEqual(evidence['kind'], 'explicit_preferences')
        self.assertIsNone(evidence['source'])
        self.assertNotIn('打了', reason)
        rows = [item(n, ['音乐'], .9) for n in range(1, 4)]
        self.assertEqual(len(ranking_v2.rerank_candidates(rows, {'音乐': 1}, {}, category='all', limit=10)), 3)

    def test_policy_and_target_reason_bounds_are_not_silent_fallbacks(self):
        with self.assertRaises(engine.RecommendationUnavailable):
            scorer(policy='arbitrary')
        for reason, measurement, tags in (
            ('outside_candidate_pool', {'target': {'eligible_rank': 500, 'pool_rank': None}}, {'音乐'}),
            ('outside_returned_limit', {'target': {'eligible_rank': 20, 'pool_rank': 20, 'validated_position': 20}}, {'音乐'}),
            ('no_effective_catalog_genres', {}, set()),
            ('no_training_topic_overlap', {}, {'悬疑'}),
        ):
            result = target_diagnostic(preferences(), 'all', 99, {99: frozenset(tags)}, [], measurement, ordinal=1, top_k=10)
            self.assertEqual(result['reason'], reason)
            self.assertNotIn('bangumi_id', result)
        self.assertEqual(target_diagnostic(preferences(), 'explore', 99, {99: frozenset({'音乐'})},
            [], {}, ordinal=1, top_k=10)['reason'], 'explore_requires_new_topic')


class PreferenceDiscoveryPostgresTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.owner = get_user_model().objects.create_user(username='synthetic-pref-v3')
        self.other = get_user_model().objects.create_user(username='synthetic-pref-v3-other')
        for user in (self.owner, self.other):
            RecommendationPreference.objects.create(user=user, tags=['音乐'])
        RecommendationAnime.objects.bulk_create([
            RecommendationAnime(bangumi_id=n, title_zh=f'合成作品{n}', tags='音乐', score=8, nsfw=False)
            for n in range(1, 701)] + [
            RecommendationAnime(bangumi_id=701, title_zh='合成成长', tags='音乐,成长', score=8, nsfw=False),
            RecommendationAnime(bangumi_id=702, title_zh='合成日常', tags='音乐,日常', score=8, nsfw=False),
            RecommendationAnime(bangumi_id=703, title_zh='合成悬疑', tags='音乐,悬疑', score=8, nsfw=False),
            RecommendationAnime(bangumi_id=800, title_zh='合成无关', tags='悬疑', score=10, nsfw=False),
            RecommendationAnime(bangumi_id=801, title_zh='合成成人', tags='音乐,成长', score=10, nsfw=True),
            RecommendationAnime(bangumi_id=802, title_zh='合成未知', tags='音乐,成长', score=10, nsfw=None)])

    def sample(self, owner=None):
        owner = owner or self.owner
        return prepare_sample(resolved_records(owner), preference_contract(owner), owner.pk, feedback_snapshot(owner))

    def test_same_snapshot_previous_current_comparison_improves_narrow_discovery_without_writes(self):
        before = list(RecommendationPreference.objects.values())
        with readonly_snapshot():
            context, summary = self.sample(); _, rows = catalog_snapshot(); index, _ = public_index(rows)
            report = diagnose_sample(context, summary, index, top_k=10, repeats=2, max_holdouts=1, compare_previous_v2=True)
        old = report['arms'][PREVIOUS_V2_ARM]['all']['metrics']
        new = report['arms']['content.v2']['all']['metrics']
        self.assertEqual(old['pairwise_genre_jaccard_mean'], 1)
        self.assertLess(new['pairwise_genre_jaccard_mean'], old['pairwise_genre_jaccard_mean'])
        self.assertGreater(new['novel_direction_count'], 0)
        self.assertEqual(new['associated_rows'], new['evaluated_top_count'])
        self.assertEqual(report['checks']['unsafe_rows'], 0); self.assertEqual(report['checks']['evidence_errors'], 0)
        for category in ('familiar', 'explore'):
            self.assertEqual(report['arms']['content.v2'][category]['metrics'], report['arms'][PREVIOUS_V2_ARM][category]['metrics'])
        self.assertEqual(list(RecommendationPreference.objects.values()), before)

    def test_trace_is_observation_only_and_reports_pool_exclusion_in_both_engines(self):
        with readonly_snapshot():
            context, _ = self.sample(); _, rows = catalog_snapshot(); index, _ = public_index(rows)
            options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True}
            for version in ('content.v1', 'content.v2'):
                plain, _ = measured_rank(context, options, index, version)
                traced, measurement = measured_rank({**context, '_diagnostic_target_id': 699}, options, index, version)
                self.assertEqual([row['anime'].bangumi_id for row in plain], [row['anime'].bangumi_id for row in traced])
                self.assertIsNotNone(measurement['target']['eligible_rank'])
                self.assertIsNone(measurement['target']['pool_rank'])
                self.assertEqual(set(measurement['target']), {'eligible_rank', 'pool_rank', 'validated_position'})

    def test_current_feedback_owned_records_and_safety_are_shared_by_all_three_arms(self):
        mutate_feedback(user=self.owner, action='hide', changes={'bangumi_id': 701,
            'expected_feedback_version': 0, 'operation_id': str(uuid4())})
        anime = Anime.objects.create(bgm_id=702, chinese_title='既有合成记录')
        record = UserAnimeRecord.objects.create(user=self.owner, anime=anime, personal_score=0,
            review='PRIVATE_LONG_REVIEW', watch_status='on_hold', private_base_tags=[], custom_tags=['原私人标签'])
        before = UserAnimeRecord.objects.values().get(pk=record.pk)
        with readonly_snapshot():
            context, summary = self.sample(); _, rows = catalog_snapshot(); index, _ = public_index(rows)
            report = diagnose_sample(context, summary, index, top_k=10, repeats=2, max_holdouts=1, compare_previous_v2=True)
            other, _ = self.sample(self.other)
            self.assertEqual(other['feedback']['hidden_ids'], frozenset())
        for arm in report['arms'].values():
            for data in arm.values():
                self.assertFalse({701, 702, 800, 801, 802} & set(data['metrics']['top_bangumi_ids']))
        self.assertEqual(UserAnimeRecord.objects.values().get(pk=record.pk), before)
        self.assertEqual(report['checks']['hidden_rows'], 0)

    def test_positive_control_real_holdout_can_hit_and_is_not_still_excluded_as_owned(self):
        # A deliberately known synthetic positive CONTROL in the isolated DB,
        # never fabricated scores in either real authorized account.
        for identity in range(1, 5):
            anime = Anime.objects.create(bgm_id=identity, chinese_title=f'合成作品{identity}', base_tags=['音乐'])
            UserAnimeRecord.objects.create(user=self.owner, anime=anime, watch_status='completed', personal_score=9)
        with readonly_snapshot():
            context, summary = self.sample(); _, rows = catalog_snapshot(); index, _ = public_index(rows)
            report = diagnose_sample(context, summary, index, top_k=10, repeats=2, max_holdouts=1, compare_previous_v2=True)
        for version in ('content.v1', 'content.v2', PREVIOUS_V2_ARM):
            fold = report['holdout']['runs'][version]['all'][0]
            self.assertEqual(fold['hit_at_k'], 1)
            self.assertEqual(fold['target_diagnostic']['reason'], 'hit_top_k')
            self.assertEqual(fold['target_diagnostic']['returned_rank'], 1)
            self.assertNotIn('target_id', json.dumps(fold))

    def test_offline_policy_and_target_markers_are_not_http_query_inputs(self):
        client = APIClient(); client.force_authenticate(self.owner)
        for query in ({'v2_policy_version': ranking_v2.PREVIOUS_POLICY_VERSION},
                      {'_diagnostic_target_id': 1}):
            self.assertEqual(client.get('/api/recommendations/', query).status_code, 400)

    def test_policy_change_invalidates_v2_namespace_and_snapshot_but_not_v1(self):
        from recommendations.version_control import cache_namespace
        context, _ = self.sample()
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': 10}
        def tokens():
            return [engine.snapshot_token(self.owner.pk, context, options, [], version, weights={})
                    for version in ('content.v1', 'content.v2')]
        current = tokens()
        namespace = cache_namespace('content.v2', 'genres.v4', 'genres-idf.v4', 'journal-genres.v3')
        with patch.object(ranking_v2, 'POLICY_VERSION', ranking_v2.PREVIOUS_POLICY_VERSION):
            previous = tokens()
            self.assertNotEqual(namespace, cache_namespace('content.v2', 'genres.v4', 'genres-idf.v4', 'journal-genres.v3'))
        self.assertEqual(current[0], previous[0])
        self.assertNotEqual(current[1], previous[1])
