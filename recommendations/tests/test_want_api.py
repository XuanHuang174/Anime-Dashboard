from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from anime.views import BangumiCollectionBatchImportView
from recommendations.binding import bind_record
from recommendations.models import RecommendationAnime, RecommendationRecordIdentity, RecommendationIdentityEvent
from recommendations.views import RecommendationWantView
from recommendations.want import add_want, WantCapacityExceeded


@override_settings(AWS_S3_CUSTOM_DOMAIN='media.xh-anime.com')
class WantAPITests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='want-owner')
        self.other = get_user_model().objects.create_user(username='want-other')
        self.catalog = RecommendationAnime.objects.create(bangumi_id=501, title_zh='作品', title_jp='Story',
            season='2026-04', status_type='TV', nsfw=False, score=9.7, tags='日常,治愈',
            cover_url='https://media.xh-anime.com/covers/501.jpg', synopsis='真实简介')
        self.url = reverse('recommendations:want', args=[501])
        self.client.force_authenticate(self.user)
        throttle = patch.object(RecommendationWantView, 'throttle_classes', ())
        throttle.start(); self.addCleanup(throttle.stop)

    def post(self, payload=None):
        return self.client.post(self.url, {} if payload is None else payload, format='json')

    def record(self, *, direct=True, **fields):
        shared = Anime.objects.create(bgm_id=501 if direct else None, chinese_title='作品', japanese_title='Story',
                                       airing_period='2026-4', description='不应被修改的公共简介')
        return UserAnimeRecord.objects.create(user=self.user, anime=shared, **fields)

    def unchanged_existing(self, record):
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        shared = Anime.objects.filter(pk=record.anime_id).values().get()
        with CaptureQueriesContext(connection) as queries:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['created'])
        self.assertEqual(response.data['record_id'], record.pk)
        self.assertEqual(response.data['watch_status'], record.watch_status)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)
        self.assertEqual(Anime.objects.filter(pk=record.anime_id).values().get(), shared)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))

    def test_create_blank_personal_fields_no_remote_download_and_namespace_contract(self):
        with patch('anime.utils.scraper.MetadataScraper.download_bangumi_poster', side_effect=AssertionError('No remote download')):
            response = self.post()
        self.assertEqual(response.status_code, 201)
        record = UserAnimeRecord.objects.get(user=self.user)
        self.assertEqual(record.watch_status, 'plan')
        self.assertIsNone(record.personal_score)
        self.assertEqual(record.review, '')
        self.assertEqual((record.watched_eps, record.total_eps), (0, 0))
        self.assertEqual(record.private_poster_url, self.catalog.cover_url)
        self.assertEqual(record.private_description, '真实简介')
        self.assertEqual(record.anime.bgm_id, 501)
        self.assertFalse(record.anime.is_active)
        self.assertEqual(response.data['bangumi_id'], 501)
        self.assertEqual(response.data['anime_id'], record.anime_id)
        self.assertEqual(response.data['record_id'], record.pk)
        self.assertNotIn('review', response.data)
        self.assertNotIn('personal_score', response.data)

    def test_repeated_posts_keep_one_record_and_one_public_identity(self):
        created = self.post()
        for _ in range(8):
            response = self.post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['record_id'], created.data['record_id'])
        self.assertEqual(UserAnimeRecord.objects.count(), 1)
        self.assertEqual(Anime.objects.filter(bgm_id=501).count(), 1)

    def test_all_existing_states_keep_every_field_and_timestamp(self):
        record = self.record(personal_score=Decimal('9.4'), review='长评\n' * 5000, watched_eps=7,
                             total_eps=12, custom_tags=['二刷'])
        for state in ('plan', 'watching', 'completed', 'on_hold'):
            record.watch_status = state; record.save(update_fields=['watch_status'])
            self.unchanged_existing(record)

    def test_confirmed_legacy_alias_returns_original_record_without_setting_public_id(self):
        record = self.record(direct=False, watch_status='completed', personal_score='9.1', review='旧长评')
        bind_record(record_id=record.pk, user_id=self.user.pk, target_id=501, source='user_confirmed')
        self.unchanged_existing(record)
        self.assertIsNone(Anime.objects.get(pk=record.anime_id).bgm_id)
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 1)

    def test_missing_id_requires_confirmation_and_does_not_auto_link(self):
        self.record(direct=False, watch_status='watching')
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'identity_confirmation_required')
        self.assertIs(response.data['requires_identity_confirmation'], True)
        self.assertEqual(UserAnimeRecord.objects.count(), 1)
        self.assertEqual(RecommendationRecordIdentity.objects.count(), 0)

    def test_manual_all_status_confirmation_closes_old_plan_flow(self):
        record = self.record(direct=False, watch_status='plan', review='未评分的旧记录')
        url = reverse('recommendations:identities')
        self.assertEqual(self.client.get(url).data['count'], 0)
        candidate = self.client.get(url, {'scope': 'all'}).data['results'][0]['candidates'][0]
        confirmed = self.client.patch(url, {'record_id': record.pk, 'bangumi_id': 501,
            'expected_version': 0, 'ticket': candidate['ticket'], 'confirmed': True}, format='json')
        self.assertEqual(confirmed.status_code, 200)
        self.unchanged_existing(record)

    def test_different_quarter_or_punctuation_never_automatically_merges(self):
        record = self.record(direct=False)
        record.private_chinese_title = '《作品！》'; record.private_japanese_title = ''
        record.private_airing_period = '2025-10'
        record.save()
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(RecommendationRecordIdentity.objects.count(), 0)

    def test_valid_other_season_alias_does_not_block_new_season(self):
        old = RecommendationAnime.objects.create(bangumi_id=500, title_zh='作品', title_jp='Story', season='2025-10', nsfw=False)
        record = self.record(direct=False)
        record.anime.airing_period = '2025-10'; record.anime.save(update_fields=['airing_period'])
        record.private_airing_period = '2025-10'; record.save(update_fields=['private_airing_period'])
        bind_record(record_id=record.pk, user_id=self.user.pk, target_id=old.bangumi_id, source='user_confirmed')
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(UserAnimeRecord.objects.count(), 2)

    def test_stale_alias_with_custom_title_still_requires_confirmation(self):
        record = self.record(direct=False)
        bind_record(record_id=record.pk, user_id=self.user.pk, target_id=501, source='user_confirmed')
        record.private_chinese_title = '我的私有标题'; record.private_japanese_title = ''
        record.save(update_fields=['private_chinese_title', 'private_japanese_title'])
        self.assertEqual(self.post().data['code'], 'identity_confirmation_required')
        self.assertEqual(UserAnimeRecord.objects.count(), 1)

    def test_field_lengths_are_rejected_losslessly_but_owned_records_still_return(self):
        for field in ('title_zh', 'title_jp'):
            original = getattr(self.catalog, field)
            setattr(self.catalog, field, '字' * 201); self.catalog.save()
            response = self.post()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['code'], 'journal_field_conflict')
            self.assertEqual(Anime.objects.count(), 0)
            setattr(self.catalog, field, original); self.catalog.save()
        record = self.record(review='仍需保留')
        self.catalog.title_zh = '字' * 500; self.catalog.save()
        self.unchanged_existing(record)

    def test_unowned_global_release_conflict_is_admin_problem_not_title_merge(self):
        shared = Anime.objects.create(chinese_title='作品', japanese_title='Story', airing_period='2026-4')
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'public_identity_conflict')
        self.assertEqual(UserAnimeRecord.objects.count(), 0)
        shared.refresh_from_db(); self.assertIsNone(shared.bgm_id)

    @override_settings(USER_ANIME_RECORD_LIMIT=1)
    def test_capacity_rejects_new_but_not_existing(self):
        record = self.record()
        self.unchanged_existing(record)
        RecommendationAnime.objects.create(bangumi_id=502, title_zh='另一部', nsfw=False)
        response = self.client.post(reverse('recommendations:want', args=[502]), {}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'capacity_exceeded')
        self.assertEqual(Anime.objects.count(), 1)

    def test_users_share_only_public_row_not_private_records(self):
        own = self.post().data
        record = UserAnimeRecord.objects.get(pk=own['record_id'])
        record.personal_score = '9.5'; record.review = '不能复制给另一用户'; record.save()
        self.client.force_authenticate(self.other)
        other = self.post()
        self.assertEqual(other.status_code, 201)
        self.assertEqual(other.data['anime_id'], own['anime_id'])
        self.assertNotEqual(other.data['record_id'], own['record_id'])
        theirs = UserAnimeRecord.objects.get(pk=other.data['record_id'])
        self.assertIsNone(theirs.personal_score); self.assertEqual(theirs.review, '')

    def test_real_jwt_creates_only_the_token_owners_record(self):
        from rest_framework_simplejwt.tokens import AccessToken
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.other)}')
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(UserAnimeRecord.objects.get().user_id, self.other.pk)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.user).count(), 0)

    def test_other_user_alias_or_legacy_never_exposed_or_returned(self):
        record = self.record(direct=False)
        bind_record(record_id=record.pk, user_id=self.user.pk, target_id=501, source='user_confirmed')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.post().data['code'], 'public_identity_conflict')
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.other).count(), 0)

    def test_adult_unknown_and_classification_change_fail_closed_even_if_owned(self):
        self.record()
        for value in (True, None):
            self.catalog.nsfw = value; self.catalog.save()
            self.assertEqual(self.post().status_code, 404)
            self.assertEqual(self.client.get(reverse('recommendations:detail', args=[501])).status_code, 404)
        self.assertEqual(UserAnimeRecord.objects.count(), 1)

    def test_no_external_cover_fallback_or_shared_mutation(self):
        shared = Anime.objects.create(bgm_id=501, chinese_title='原公共名', airing_period='2026-4', poster_url='https://outside.test/raw.jpg')
        before = Anime.objects.filter(pk=shared.pk).values().get()
        self.catalog.cover_url = 'https://outside.test/raw.jpg'; self.catalog.save()
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(UserAnimeRecord.objects.get().private_poster_url, '')
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)

    def test_rejects_forged_body_queries_and_invalid_id(self):
        for body in ({'user_id': self.other.pk}, {'watch_status': 'completed'}, {'personal_score': 9}, [], {'anime_id': 501}):
            self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.client.post(self.url+'?user_id=2', {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(reverse('recommendations:want', args=[2147483648]), {}, format='json').status_code, 404)
        self.assertEqual(UserAnimeRecord.objects.count(), 0)
        self.client.force_authenticate(None)
        self.assertEqual(self.post().status_code, 401)

    def test_missing_alias_schema_fails_closed_without_creating(self):
        with patch('recommendations.want.recommendation_identity', return_value={
            'record_id': None, 'identity_service_ready': False}):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(UserAnimeRecord.objects.count(), 0)

    def test_downstream_database_failure_rolls_back_public_and_private_create(self):
        with patch('recommendations.want.initialize_record_tag_colors', side_effect=DatabaseError('injected failure')):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Anime.objects.count(), 0)
        self.assertEqual(UserAnimeRecord.objects.count(), 0)

    def test_hidden_shared_identity_can_be_added_without_reactivation_or_mutation(self):
        shared = Anime.objects.create(bgm_id=501, chinese_title='作品', airing_period='2026-4', is_active=False)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['anime_id'], shared.pk)
        self.assertEqual(self.post().data['record_id'], response.data['record_id'])
        self.assertEqual(UserAnimeRecord.objects.count(), 1)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)
        # Public search still hides the row, while the private owner can read it.
        public = self.client.get(reverse('anime_api:animes-list'), {'search': '作品'})
        self.assertEqual(public.data['results'], [])
        private = self.client.get(reverse('anime_api:user-anime-record-detail', args=[response.data['record_id']]))
        self.assertEqual(private.status_code, 200)
        self.assertEqual(private.data['anime']['chinese_title'], self.catalog.title_zh)

    def test_private_bangumi_import_is_not_a_global_ban(self):
        defaults = BangumiCollectionBatchImportView._subject_defaults({
            'name_cn': '作品', 'name': 'Story', 'date': '2026-04-01',
        }, request_user=self.other)
        self.assertFalse(defaults['is_active'])
        shared = Anime.objects.create(bgm_id=501, **defaults)
        old = UserAnimeRecord.objects.create(user=self.other, anime=shared, watch_status='completed',
                                            personal_score=Decimal('9.5'), review='另一用户的原长评')
        before_shared = Anime.objects.filter(pk=shared.pk).values().get()
        before_old = UserAnimeRecord.objects.filter(pk=old.pk).values().get()
        response = self.post()
        self.assertEqual(response.status_code, 201)
        added = UserAnimeRecord.objects.get(pk=response.data['record_id'])
        self.assertEqual(added.user_id, self.user.pk)
        self.assertIsNone(added.personal_score)
        self.assertEqual(added.review, '')
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before_shared)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=old.pk).values().get(), before_old)

    @override_settings(PUBLIC_SHOWCASE_USERNAME='want-other')
    def test_removal_from_showcase_does_not_ban_safe_private_additions(self):
        shared = Anime.objects.create(bgm_id=501, chinese_title='作品', airing_period='2026-4')
        holder = get_user_model().objects.create_user(username='remaining-private-owner')
        retained = UserAnimeRecord.objects.create(user=holder, anime=shared, review='保留旧记录')
        showcase = UserAnimeRecord.objects.create(user=self.other, anime=shared)
        showcase.delete()  # Real legacy signal hides, rather than deletes, shared metadata.
        shared.refresh_from_db()
        self.assertFalse(shared.is_active)
        before_shared = Anime.objects.filter(pk=shared.pk).values().get()
        before_record = UserAnimeRecord.objects.filter(pk=retained.pk).values().get()
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before_shared)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=retained.pk).values().get(), before_record)

    def test_hidden_identity_still_rejects_unsafe_content(self):
        shared = Anime.objects.create(bgm_id=501, chinese_title='作品', airing_period='2026-4', is_active=False)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        for rating in (True, None):
            self.catalog.nsfw = rating; self.catalog.save(update_fields=['nsfw'])
            self.assertEqual(self.post().status_code, 404)
            self.assertEqual(self.client.get(reverse('recommendations:detail', args=[501])).status_code, 404)
        self.assertEqual(UserAnimeRecord.objects.count(), 0)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)

    def test_hidden_missing_identity_still_requires_explicit_confirmation(self):
        record = self.record(direct=False)
        record.anime.is_active = False; record.anime.save(update_fields=['is_active'])
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'identity_confirmation_required')
        self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)
        self.assertEqual(RecommendationRecordIdentity.objects.count(), 0)
        self.assertIsNone(Anime.objects.get(pk=record.anime_id).bgm_id)

    def test_hidden_existing_states_keep_scores_reviews_and_all_fields(self):
        record = self.record(personal_score=Decimal('9.4'), review='原长评\n' * 5000, watched_eps=7, total_eps=12)
        record.anime.is_active = False; record.anime.save(update_fields=['is_active'])
        for state in ('plan', 'watching', 'completed', 'on_hold'):
            record.watch_status = state; record.save(update_fields=['watch_status'])
            self.unchanged_existing(record)

    @override_settings(PUBLIC_SHOWCASE_USERNAME='want-owner')
    def test_new_showcase_owner_metadata_retains_existing_public_visibility_policy(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertTrue(Anime.objects.get(bgm_id=501).is_active)

    @override_settings(PUBLIC_SHOWCASE_USERNAME='want-owner')
    def test_showcase_owner_does_not_reactivate_existing_hidden_shared_row(self):
        shared = Anime.objects.create(bgm_id=501, chinese_title='作品', airing_period='2026-4', is_active=False)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)


@skipUnless(connection.vendor == 'postgresql', 'Real row-lock races require an isolated PostgreSQL test database')
class WantConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='want-race-owner')
        self.other = get_user_model().objects.create_user(username='want-race-other')
        for identity in (701, 702):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'作品{identity}', nsfw=False)

    def parallel(self, calls):
        barrier = Barrier(len(calls))
        backend_pids = []
        def execute(args):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pids.append(cursor.fetchone()[0])
                user = get_user_model().objects.get(pk=args[0]); barrier.wait(timeout=10)
                try:
                    return add_want(user=user, bangumi_id=args[1])
                except WantCapacityExceeded:
                    return {'capacity': True}
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            results = list(pool.map(execute, calls))
        # Prove workers competed on distinct real PostgreSQL connections rather
        # than one serialized connection or a SQLite simulation.
        self.assertEqual(len(set(backend_pids)), len(calls))
        return results

    def test_same_user_repeat_race_creates_exactly_one(self):
        results = self.parallel([(self.user.pk, 701)] * 8)
        self.assertEqual(sum(row['created'] for row in results), 1)
        self.assertEqual(len({row['record_id'] for row in results}), 1)
        self.assertEqual(UserAnimeRecord.objects.count(), 1)

    def test_cross_user_same_subject_race_shares_one_public_row(self):
        results = self.parallel([(self.user.pk, 701), (self.other.pk, 701)])
        self.assertTrue(all(row['created'] for row in results))
        self.assertEqual(Anime.objects.count(), 1)
        self.assertEqual(UserAnimeRecord.objects.count(), 2)

    def test_parallel_existing_record_preserves_score_review_and_all_fields(self):
        shared = Anime.objects.create(bgm_id=701, chinese_title='旧公共作品', airing_period='2026-4')
        record = UserAnimeRecord.objects.create(user=self.user, anime=shared, watch_status='completed',
            personal_score=Decimal('0.0'), review='完整长评\n' * 5000, watched_eps=12, total_eps=12,
            metadata_initialized=True, private_base_tags=[], custom_tags=['二刷', '音乐'])
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        results = self.parallel([(self.user.pk, 701)] * 8)
        self.assertTrue(all(not row['created'] for row in results))
        self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)

    @override_settings(USER_ANIME_RECORD_LIMIT=1)
    def test_capacity_race_different_subjects_only_creates_one(self):
        results = self.parallel([(self.user.pk, 701), (self.user.pk, 702)])
        self.assertEqual(sum(bool(row.get('capacity')) for row in results), 1)
        self.assertEqual(UserAnimeRecord.objects.count(), 1)
        self.assertEqual(Anime.objects.count(), 1)
