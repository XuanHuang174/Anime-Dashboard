from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.score_filters import matches_score


class ScoreCeilingTests(TransactionTestCase):
    def setUp(self):
        assert connection.vendor == 'postgresql'
        assert connection.settings_dict['NAME'] == 'test_recommendation_isolated_tests'
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='score-ceiling-owner')
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'])
        RecommendationAnime.objects.bulk_create([
            RecommendationAnime(bangumi_id=i, title_zh=f'高分候选{i}', score=8,
                tags='音乐,成长', nsfw=False, season='2026-01', status_type='TV') for i in range(1, 411)
        ] + [RecommendationAnime(bangumi_id=i, title_zh=f'低分候选{i}', score=5.9 if i == 529 else 5.5,
                tags='音乐,日常', nsfw=False, season='2026-01', status_type='TV') for i in range(500, 530)] + [
            RecommendationAnime(bangumi_id=600, title_zh='边界六分', score=6, tags='音乐', nsfw=False),
            RecommendationAnime(bangumi_id=601, title_zh='零值待核实', score=0, tags='音乐', nsfw=False),
            RecommendationAnime(bangumi_id=602, title_zh='高于六分', score=6.1, tags='音乐', nsfw=False),
            RecommendationAnime(bangumi_id=603, title_zh='不安全', score=5, tags='音乐', nsfw=True),
            RecommendationAnime(bangumi_id=604, title_zh='分级未知', score=5, tags='音乐', nsfw=None),
        ])

    def get(self, **params):
        return self.client.get('/api/recommendations/', params)

    def test_ceiling_before_candidate_truncation_styles_and_safety(self):
        for style in ('balanced', 'close', 'open'):
            response = self.get(browse=True, max_score='6.0', max_score_exclusive=True, style=style)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['count'], 30)
            self.assertEqual(response.data['effective_conditions']['max_score'], '6.0')
            self.assertIs(response.data['effective_conditions']['max_score_exclusive'], True)
            self.assertTrue(all(0 < r['score'] < 6 and r['bangumi_id'] >= 500 for r in response.data['results']))

    def test_default_unchanged_and_explicit_unlimited_does_not_change_contract(self):
        for minimum in (0, 6, 7, 8, 9, 10):
            for score in (0, 0.1, 5.9, 6, 6.1, 8, 10):
                self.assertEqual(matches_score(score, {'min_score':minimum}), score >= minimum)
                self.assertEqual(matches_score(score, {'min_score':minimum, 'max_score':6}),
                                 score >= minimum and 0 < score <= 6)
                self.assertEqual(matches_score(score, {'min_score':minimum, 'max_score':6, 'max_score_exclusive':True}),
                                 score >= minimum and 0 < score < 6)
        first = self.get(browse=True).data
        explicit = self.get(browse=True, max_score=10).data
        self.assertNotIn('max_score', first['effective_conditions'])
        self.assertEqual(first['effective_conditions'], explicit['effective_conditions'])
        self.assertEqual([r['bangumi_id'] for r in first['results']], [r['bangumi_id'] for r in explicit['results']])
        # The offline-only public bootstrap reader must digest the same
        # score-filtered rows as PostgreSQL; this does not activate V2 serving.
        from recommendations import browsing, cold_public, engine
        from recommendations.public_fingerprint import public_feature_expression
        from recommendations.quality_readonly import readonly_snapshot
        from recommendations.score_filters import score_query
        with readonly_snapshot():
            rows, _ = browsing.public_browse_index(browsing.catalog_revision(), {})
            options = {'min_score':0, 'max_score':6, 'max_score_exclusive':True, 'type':'all', 'year_from':None, 'year_to':None}
            selected, _ = cold_public.SeededPublicReader(rows).read(
                score_query(engine.safe_catalog(), options), options, public_feature_expression(), engine.MAX_CATALOG_ROWS)
            self.assertEqual(len(selected), 30)
            self.assertTrue(all(0 < row[1] < 6 for row in selected))

    def test_stable_cursor_retries_and_score_context_mismatch(self):
        first = self.get(browse=True, max_score=6, max_score_exclusive=True).data
        cursor = first['next_page_cursor']
        second = self.get(cursor=cursor)
        retry = self.get(cursor=cursor)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual([r['bangumi_id'] for r in second.data['results']], [r['bangumi_id'] for r in retry.data['results']])
        self.assertFalse({r['bangumi_id'] for r in first['results']} & {r['bangumi_id'] for r in second.data['results']})
        self.assertEqual(self.get(cursor=cursor, max_score=10).status_code, 409)
        self.assertEqual(self.get(cursor=cursor, max_score_exclusive=False).status_code, 409)
        old_cursor = self.get(browse=True, max_score=6).data['next_page_cursor']
        self.assertEqual(self.get(cursor=old_cursor, max_score_exclusive=True, max_score=6).status_code, 409)
        pages = [first['results'], second.data['results']]
        last = self.get(cursor=second.data['next_batch_cursor']).data
        pages.append(last['results'])
        ids = [row['bangumi_id'] for page in pages for row in page]
        self.assertEqual(len(set(ids)), 30)
        self.assertNotIn(600, ids)
        self.assertIn(529, ids)  # 5.9 is included, exactly 6.0 is not.

    def test_legacy_paging_and_score_snapshot_isolation(self):
        first = self.get(max_score=6, max_score_exclusive=True).data
        self.assertEqual(first['count'], 30)
        self.assertTrue(all(0 < row['score'] < 6 for row in first['results']))
        second = self.get(max_score=6, max_score_exclusive=True, page=2, state_token=first['state_token'])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.get(max_score=10, page=2, state_token=first['state_token']).status_code, 409)
        self.assertEqual(self.get(max_score=6, page=2, state_token=first['state_token']).status_code, 409)

    def test_invalid_min_max_duplicate_and_undefined_content_switch_rejected(self):
        for params in ({'min_score':7, 'max_score':6}, {'max_score':11}, {'max_score':'nan'},
                       {'max_score':''}, {'max_score_exclusive':True}, {'max_score':10,'max_score_exclusive':True},
                       {'max_score':6,'min_score':6,'max_score_exclusive':True},
                       {'max_score':6,'max_score_exclusive':'invalid'}, {'include_nsfw':'true'}, {'nsfw':'true'}):
            self.assertEqual(self.get(**params).status_code, 400, params)
        self.assertEqual(self.client.get('/api/recommendations/?max_score=6&max_score=7').status_code, 400)
        self.assertEqual(self.client.get('/api/recommendations/?max_score=6&max_score_exclusive=true&max_score_exclusive=false').status_code, 400)
