"""9A synthetic fixtures: independent PostgreSQL only, never real account data."""
from uuid import uuid4
from unittest.mock import patch
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations import browsing, browse_controls as controls, engine
from recommendations.feedback import mutate_feedback
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.services import patch_preferences

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('9A requires the independent local PostgreSQL test settings, never Neon or SQLite.')


class BrowseControlMathTests(SimpleTestCase):
    def test_year_only_month_date_and_zero_padded_months(self):
        for value in ('2026', '2026-1', '2026-01', '2026-4', '2026-04', '2026-07',
                      '2026-10', '2026-12-31', ' 2026-01 '):
            self.assertEqual(controls.season_year(value), 2026, value)
        for value in ('作品2026', '2026年春', '未定档', '', None, '2026-00', '2026-13',
                      '2026-02-30', '2026-4-extra', '2026-01-01foo', '999', '20260'):
            self.assertIsNone(controls.season_year(value), value)

    def test_close_really_uses_coverage_and_open_keeps_v1_category_eligibility(self):
        vector, tags = {'音乐': 1}, frozenset({'音乐', '成长'})
        base = controls.styled_score(tags, 8, vector, {}, 'all', .5, 'balanced')
        close = controls.styled_score(tags, 8, vector, {}, 'all', .5, 'close')
        self.assertNotEqual(base, close)
        for style in controls.STYLES:
            self.assertIsNone(controls.styled_score(frozenset({'悬疑'}), 10, vector, {}, 'all', 0, style))
            self.assertIsNone(controls.styled_score(frozenset({'音乐'}), 10, vector, {}, 'explore', 1, style))


class BrowseAPITests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='browse-owner')
        self.other = get_user_model().objects.create_user(username='browse-other')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.records = []
        for identity in (1, 2, 3):
            self.catalog(identity, tags='音乐,日常', score=1)
            anime = Anime.objects.create(bgm_id=identity, chinese_title=f'来源{identity}', base_tags=['音乐', '日常'])
            self.records.append(UserAnimeRecord.objects.create(user=self.owner, anime=anime,
                personal_score='9.0', watch_status='completed', review='PRIVATE_REVIEW_SENTINEL',
                custom_tags=['二刷'], watched_eps=12, total_eps=12))
        RecommendationAnime.objects.bulk_create([
            RecommendationAnime(bangumi_id=n, title_zh=f'合成作品{n}',
                tags=('音乐,日常', '音乐,成长', '音乐,科幻')[n % 3], score=8,
                nsfw=False, season='2026-01', status_type='TV') for n in range(10, 150)])
        self.catalog(9001, nsfw=True); self.catalog(9002, nsfw=None)
        self.catalog(9003, tags='悬疑')

    def catalog(self, identity, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity,
            **{'title_zh': f'合成作品{identity}', 'tags': '音乐,成长', 'score': 8,
               'nsfw': False, 'season': '2026-01', 'status_type': 'TV', **changes})

    def get(self, **params):
        return self.client.get('/api/recommendations/', params)

    def start(self, **params):
        response = self.get(browse=True, **params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def follow(self, cursor, **params):
        response = self.get(cursor=cursor, **params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def ids(self, page):
        return [row['bangumi_id'] for row in page['results']]

    def order(self, first):
        result, page = [], first
        while True:
            result.extend(self.ids(page))
            cursor = page['next_page_cursor'] or page['next_batch_cursor']
            if not cursor:
                return result
            page = self.follow(cursor)

    def test_auth_unknown_duplicate_invalid_and_mixed_parameters_are_400(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.get(browse=True).status_code, 401)
        self.client.force_authenticate(self.owner)
        cases = ({'user_id': self.other.pk}, {'algorithm_version': 'content.v2'}, {'algorithm': 'content.v2'},
                 {'style': 'random'}, {'year_from': 2027, 'year_to': 2026}, {'year_from': 999},
                 {'year_to': 10000}, {'year_from': ''}, {'year_from': '2026xx'}, {'page_size': 25},
                 {'page': 2}, {'state_token': 'a' * 64}, {'cursor': ''}, {'cursor': 'a' * 8193},
                 {'cursor': 'nonsense'}, {'min_score': 11}, {'type': 'nonsense'})
        for params in cases:
            self.assertEqual(self.get(browse=True, **params).status_code, 400, params)
        for name in ('style', 'year_from', 'cursor', 'browse', 'category', 'hide_recorded'):
            self.assertEqual(self.client.get(f'/api/recommendations/?{name}=1&{name}=2').status_code, 400)
        self.assertEqual(self.get(browse=False, style='balanced').status_code, 400)
        self.assertEqual(self.get(cursor=self.start()['next_page_cursor'], page=1).status_code, 400)

    def test_balanced_exactly_equals_frozen_v1_order_and_evidence_in_all_categories(self):
        for category in ('all', 'familiar', 'explore'):
            options = {'category': category, 'type': 'TV', 'min_score': 7, 'hide_recorded': True, 'page_size': 12}
            context, ranked, _, _ = engine.rank_candidates(self.owner, options)
            expected = [row['anime'].bangumi_id for row in ranked]
            first = self.start(**options)
            self.assertEqual(self.order(first), expected)
            legacy = self.get(**options)
            self.assertNotIn('browse_contract_version', legacy.data)
            self.assertEqual(first['results'], legacy.data['results'])
            self.assertEqual(first['algorithm_version'], 'content.v1')
            self.assertEqual(first['style'], 'balanced')
            self.assertTrue(context['seeds'])

    def test_explicit_new_parameters_opt_in_but_old_requests_and_tokens_stay_unchanged(self):
        a = self.get(page_size=3).data
        for params in ({'style': 'balanced'}, {'year_from': 2026}, {'year_to': 2026}):
            self.assertIn('browse_contract_version', self.get(**params).data)
        b = self.get(page_size=3).data
        self.assertEqual(a, b)
        self.assertEqual(self.get(page=2, page_size=3, state_token=a['state_token']).status_code, 200)

    def test_schema_limits_batches_heroes_no_duplicates_and_no_reranking_on_continue(self):
        first = self.start()
        self.assertEqual(first['count'], 120)
        self.assertEqual(first['batch_count'], 24)
        self.assertEqual(first['page_size'], 12)
        self.assertEqual(first['batch_hero_bangumi_id'], self.ids(first)[0])
        with patch('recommendations.browsing.rank_controlled', side_effect=AssertionError('reranking forbidden')):
            all_ids = self.order(first)
        self.assertEqual(len(all_ids), 120)
        self.assertEqual(len(set(all_ids)), 120)
        for field in ('browse_contract_version', 'snapshot_id', 'control_policy_version', 'effective_conditions',
                      'batch', 'page', 'count', 'batch_count', 'next_page_cursor', 'next_batch_cursor', 'exhausted'):
            self.assertIn(field, first)
        self.assertTrue(first['next_page_cursor'])
        second = self.follow(first['next_batch_cursor'])
        self.assertEqual(second['batch'], 2)
        self.assertEqual(second['page'], 1)
        self.assertEqual(second['snapshot_id'], first['snapshot_id'])
        self.assertEqual(second['batch_hero_bangumi_id'], self.ids(second)[0])
        self.assertFalse(set(self.ids(first)) & set(self.ids(second)))

    def test_retry_uses_same_position_and_expiry_and_does_not_need_shared_cache(self):
        first = self.start(style='open', year_from=2026, min_score=7)
        cache.clear()  # Simulates another worker with empty in-process cache.
        one = self.follow(first['next_page_cursor'])
        two = self.follow(first['next_page_cursor'])
        self.assertEqual(one['results'], two['results'])
        for field in ('snapshot_id', 'snapshot_expires_at', 'effective_conditions', 'batch', 'page'):
            self.assertEqual(one[field], two[field])
        self.assertEqual(first['snapshot_expires_at'], two['snapshot_expires_at'])
        self.assertNotIn('state_token', one)

    def test_page_sizes_that_do_not_divide_24_never_cross_batch_boundary(self):
        first = self.start(page_size=7)
        page, sizes = first, []
        while True:
            sizes.append(len(page['results']))
            self.assertEqual(page['batch'], 1)
            if not page['next_page_cursor']:
                break
            page = self.follow(page['next_page_cursor'])
        self.assertEqual(sizes, [7, 7, 7, 3])
        self.assertEqual(self.follow(page['next_batch_cursor'])['batch'], 2)

    def test_year_type_score_and_owned_filters_all_precede_400_pool(self):
        RecommendationAnime.objects.all().exclude(bangumi_id__in=(1, 2, 3)).delete()
        RecommendationAnime.objects.bulk_create([
            RecommendationAnime(bangumi_id=n, title_zh=f'旧年份{n}', tags='音乐,日常',
                score=10, nsfw=False, season='2000-1', status_type='TV') for n in range(1000, 1450)])
        target = self.catalog(2000, score=7.5, season='2026-04', status_type='OVA')
        self.catalog(2001, score=10, season='', status_type='OVA')
        self.catalog(2002, score=6, season='2026-4', status_type='OVA')
        self.catalog(2003, score=9, season='2027-01', status_type='OVA')
        self.catalog(2004, score=9, season='2026-13', status_type='OVA')
        self.catalog(2005, score=10, season='2026-01', status_type='OVA')
        owned = Anime.objects.create(bgm_id=2005, chinese_title='已有作品')
        UserAnimeRecord.objects.create(user=self.owner, anime=owned, watch_status='plan')
        for style in controls.STYLES:
            result = self.start(style=style, type='OVA', min_score=7, year_from=2026, year_to=2026)
            self.assertEqual(self.ids(result), [target.bangumi_id])
            self.assertEqual(result['count'], 1)
            self.assertTrue(result['exhausted'])
        result = self.start(type='OVA')
        self.assertIn(2001, self.ids(result))  # No year restriction: missing year is allowed.
        self.assertIn(2004, self.ids(result))
        self.assertIn(2005, self.ids(self.start(type='OVA', hide_recorded=False)))

    def test_non_default_styles_have_observable_order_changes_and_real_evidence(self):
        results = {style: self.start(style=style) for style in controls.STYLES}
        self.assertNotEqual(self.ids(results['close']), self.ids(results['open']))
        self.assertEqual(len({page['control_policy_version'] for page in results.values()}), 1)
        for page in results.values():
            for row in page['results']:
                self.assertTrue(row['evidence']['shared_tags'])
                self.assertEqual(row['evidence']['kind'], 'rated_work')
                self.assertEqual(row['evidence']['source']['personal_score'], '9.0')
                self.assertNotIn(row['bangumi_id'], (1, 2, 3, 9001, 9002, 9003))
                self.assertNotIn('value', row)
                self.assertNotIn('match_percentage', row)

    def test_evidence_matches_detail_without_calculating_a_list(self):
        first = self.start(style='open')
        row = first['results'][0]
        with patch('recommendations.engine.rank_candidates', side_effect=AssertionError('list forbidden')):
            response = self.client.get(f'/api/recommendations/{row["bangumi_id"]}/',
                                       {'evidence_token': row['evidence_token']})
        self.assertEqual(response.status_code, 200, response.data)
        for key in ('evidence', 'reason', 'evidence_context', 'evidence_token'):
            self.assertEqual(response.data[key], row[key])

    def test_cursor_conditions_are_bound_and_omitted_conditions_inherit(self):
        first = self.start(style='close', year_from=2020, year_to=2026)
        cursor = first['next_page_cursor']
        self.assertEqual(self.follow(cursor)['effective_conditions'], first['effective_conditions'])
        for changes in ({'style': 'open'}, {'category': 'familiar'}, {'year_from': 2021},
                        {'year_to': 2025}, {'min_score': 9}, {'type': 'OVA'},
                        {'hide_recorded': False}, {'page_size': 24}):
            self.assertEqual(self.get(cursor=cursor, **changes).status_code, 409, changes)
        self.assertEqual(self.get(cursor=cursor, style='close', year_from=2020).status_code, 200)

    def test_user_cursor_isolation_and_tamper_protection(self):
        cursor = self.start()['next_page_cursor']
        self.assertEqual(self.get(cursor=cursor + 'x').status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        own = self.start()
        self.assertEqual(own['count'], 0)
        self.assertEqual(own['mode'], 'cold_start')
        self.assertNotIn('PRIVATE_REVIEW', json.dumps(own))

    def test_fixed_expiration_and_policy_or_algorithm_change_invalidate(self):
        cursor = self.start()['next_page_cursor']
        with patch('recommendations.browsing.time.time', return_value=10**12):
            self.assertEqual(self.get(cursor=cursor).status_code, 409)
        with patch('recommendations.browsing.CONTROL_POLICY_VERSION', 'browse-controls.future'):
            self.assertEqual(self.get(cursor=cursor).status_code, 409)
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v2'):
            with patch('recommendations.engine._rank_context_v2', side_effect=AssertionError('V2 forbidden')):
                self.assertEqual(self.get(cursor=cursor).status_code, 409)
                self.assertEqual(self.get(browse=True).status_code, 503)

    def test_incompatible_signed_snapshot_is_409_not_an_unhandled_error(self):
        cursor = self.start()['next_page_cursor']
        original = signing.loads(cursor, salt=browsing.CURSOR_SALT)
        for key, value in (('weights', None), ('revision', 3), ('offset', -1),
                           ('ids', [1, 1]), ('options', {}), ('snapshot', 'bad')):
            body = {**original, key: value}
            broken = signing.dumps(body, salt=browsing.CURSOR_SALT, compress=True)
            self.assertEqual(self.get(cursor=broken).status_code, 409, key)
        self.assertEqual(self.get(cursor=cursor).status_code, 200)

    def test_source_identity_and_policy_versions_invalidate_existing_snapshot(self):
        cursor = self.start()['next_page_cursor']
        Anime.objects.filter(pk=self.records[0].anime_id).update(bgm_id=None)
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        Anime.objects.filter(pk=self.records[0].anime_id).update(bgm_id=1)
        cursor = self.start()['next_page_cursor']
        with patch('recommendations.browsing.SELECTION_RULE_VERSION', 'association-evidence.future'):
            self.assertEqual(self.get(cursor=cursor).status_code, 409)

    def test_hidden_and_owned_works_are_excluded_before_400_in_every_style(self):
        RecommendationAnime.objects.bulk_create([
            RecommendationAnime(bangumi_id=n, title_zh=f'已记录{n}', tags='音乐,日常',
                score=10, nsfw=False, season='2026-01', status_type='TV') for n in range(1000, 1450)])
        shared = Anime.objects.bulk_create([
            Anime(bgm_id=n, chinese_title=f'已有作品{n}') for n in range(1000, 1450)])
        UserAnimeRecord.objects.bulk_create([
            UserAnimeRecord(user=self.owner, anime=anime, watch_status='plan') for anime in shared])
        for style in controls.STYLES:
            result = self.start(style=style)
            self.assertTrue(result['count'])
            self.assertTrue(all(value < 1000 for value in self.ids(result)))
        # Feedback exclusion is equally early, even with hide_recorded disabled.
        from recommendations.models import UserRecommendationFeedback
        from django.utils import timezone
        UserRecommendationFeedback.objects.bulk_create([
            UserRecommendationFeedback(user=self.owner, bangumi_id=n, is_active=True,
                feedback_version=1, hidden_at=timezone.now()) for n in range(1000, 1450)])
        for style in controls.STYLES:
            result = self.start(style=style, hide_recorded=False)
            self.assertTrue(result['count'])
            self.assertTrue(all(value < 1000 for value in self.ids(result)))

    def test_direct_catalog_updates_and_safety_changes_invalidate_all_old_batches(self):
        for changes in ({'season': '2027-04'}, {'tags': '音乐,冒险'}, {'score': 9},
                        {'title_zh': '修订中文标题'}, {'cover_url': 'https://media.xh-anime.com/covers/10.jpg'},
                        {'synopsis': '公开简介更新'}, {'nsfw': True}, {'nsfw': None}):
            target = RecommendationAnime.objects.get(bangumi_id=10)
            previous = {key: getattr(target, key) for key in changes}
            cursor = self.start()['next_page_cursor']
            RecommendationAnime.objects.filter(pk=target.pk).update(**changes)
            self.assertEqual(self.get(cursor=cursor).status_code, 409, changes)
            RecommendationAnime.objects.filter(pk=target.pk).update(**previous)

    def test_new_and_removed_catalog_rows_invalidate(self):
        cursor = self.start()['next_page_cursor']
        row = self.catalog(8000)
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        cursor = self.start()['next_page_cursor']
        row.delete()
        self.assertEqual(self.get(cursor=cursor).status_code, 409)

    def test_even_non_seed_score_private_tags_and_progress_changes_invalidate(self):
        anime = Anime.objects.create(bgm_id=40, chinese_title='低分记录')
        record = UserAnimeRecord.objects.create(user=self.owner, anime=anime, watch_status='plan',
                                               personal_score=0, review='PRIVATE_REVIEW_ZERO')
        for changes in ({'personal_score': '1.0'}, {'custom_tags': ['神作']}, {'private_base_tags': []},
                        {'metadata_initialized': True}, {'watched_eps': 1}, {'private_chinese_title': '私人标题'}):
            cursor = self.start()['next_page_cursor']
            record.refresh_from_db()
            original = {key: getattr(record, key) for key in changes}
            if original == changes:  # Exercise an actual change, not a no-op.
                changes = {'private_base_tags': ['音乐']}
                original = {'private_base_tags': record.private_base_tags}
            UserAnimeRecord.objects.filter(pk=record.pk).update(**changes)
            self.assertEqual(self.get(cursor=cursor).status_code, 409, changes)
            UserAnimeRecord.objects.filter(pk=record.pk).update(**original)

    def test_preference_feedback_and_want_success_force_new_browse_context(self):
        cursor = self.start()['next_page_cursor']
        patch_preferences(user=self.owner, changes={'tags': ['音乐']})
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        cursor = self.start()['next_page_cursor']
        feedback = mutate_feedback(user=self.owner, action='hide', changes={'bangumi_id': 10,
            'expected_feedback_version': 0, 'operation_id': uuid4()})
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        self.assertNotIn(10, self.order(self.start()))
        cursor = self.start()['next_page_cursor']
        mutate_feedback(user=self.owner, action='undo', changes={'bangumi_id': 10,
            'expected_feedback_version': feedback['feedback_version'], 'operation_id': uuid4(),
            'target_feedback_version': feedback['feedback']['feedback_version']})
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        cursor = self.start()['next_page_cursor']
        self.assertEqual(self.client.post('/api/recommendations/11/want/', {}, format='json').status_code, 201)
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        self.assertNotIn(11, self.order(self.start()))

    def test_foreign_feedback_and_journal_do_not_invalidate_owner(self):
        cursor = self.start()['next_page_cursor']
        mutate_feedback(user=self.other, action='hide', changes={'bangumi_id': 10,
            'expected_feedback_version': 0, 'operation_id': uuid4()})
        shared = Anime.objects.get(bgm_id=1)
        UserAnimeRecord.objects.create(user=self.other, anime=shared, personal_score=0,
                                      custom_tags=['二刷'], review='OTHER_PRIVATE_REVIEW')
        self.assertEqual(self.get(cursor=cursor).status_code, 200)

    def test_zero_side_effects_no_review_select_no_external_calls_and_no_private_cursor_payload(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        preferences = list(RecommendationPreference.objects.values())
        catalog = list(RecommendationAnime.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as captured:
            first = self.start(style='open')
            self.follow(first['next_page_cursor'])
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        self.assertEqual(list(RecommendationPreference.objects.values()), preferences)
        self.assertEqual(list(RecommendationAnime.objects.order_by('pk').values()), catalog)
        for query in captured:
            sql = query['sql']
            self.assertNotRegex(sql, r'(?i)\b(INSERT|UPDATE|DELETE)\s+(INTO|FROM|"|[a-z])')
            self.assertNotIn('"review"', sql)
            self.assertNotIn('"private_description"', sql)
        self.assertTrue(any('REPEATABLE READ, READ ONLY' in row['sql'] for row in captured))
        body = signing.loads(first['next_page_cursor'], salt=browsing.CURSOR_SALT)
        for field in ('profile', 'seeds', 'journal', 'personal_score', 'user_id'):
            self.assertNotIn(field, body)
        self.assertNotIn('PRIVATE_REVIEW', json.dumps(body))
        self.assertEqual(self.get(browse=True)['Cache-Control'], 'private, no-store')

    def test_cold_start_explicit_preferences_and_empty_filter_are_distinct(self):
        self.client.force_authenticate(self.other)
        cold = self.start()
        self.assertEqual(cold['mode'], 'cold_start')
        self.assertEqual(cold['count'], 0)
        self.assertIsNone(cold['next_batch_cursor'])
        self.assertIsNone(cold['batch_hero_bangumi_id'])
        self.assertTrue(cold['exhausted'])
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        explicit = self.start(style='close')
        self.assertEqual(explicit['mode'], 'preference_based')
        self.assertEqual(explicit['results'][0]['evidence']['kind'], 'explicit_preferences')
        self.assertIsNone(explicit['results'][0]['evidence']['source'])
        empty = self.start(year_from=2030)
        self.assertEqual(empty['mode'], 'preference_based')
        self.assertEqual(empty['count'], 0)
        self.assertEqual(empty['results'], [])

    def test_disabled_feature_preserves_legacy_api_and_postgresql_readonly_is_enforced(self):
        with override_settings(RECOMMENDATION_BROWSE_ENABLED=False):
            self.assertEqual(self.get(browse=True).status_code, 503)
            self.assertEqual(self.get().status_code, 200)
        # Test the actual transaction, not a mock claiming that no writes occur.
        original = browsing.rank_controlled
        def forbidden_write(*args, **kwargs):
            get_user_model().objects.filter(pk=self.owner.pk).update(first_name='forbidden')
            return original(*args, **kwargs)
        with patch('recommendations.browsing.rank_controlled', side_effect=forbidden_write):
            self.assertEqual(self.get(browse=True).status_code, 503)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, '')

    def test_catalog_budget_and_unavailable_database_fail_closed(self):
        with patch('recommendations.engine.MAX_CATALOG_ROWS', 1):
            self.assertEqual(self.get(browse=True).status_code, 503)
        with transaction.atomic():
            self.assertEqual(self.get(browse=True).status_code, 503)

