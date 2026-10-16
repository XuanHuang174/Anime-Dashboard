"""8C: synthetic-only closure and real PostgreSQL lock boundaries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from rest_framework.exceptions import NotFound
from rest_framework.test import APITestCase, APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations.feedback import mutate_feedback, FeedbackConflict
from recommendations.models import (RecommendationAnime, RecommendationPreference,
    UserRecommendationFeedback as Feedback, UserRecommendationFeedbackState as State,
    RecommendationFeedbackOperation as Operation)
from recommendations.want import add_want

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('8C requires the explicitly isolated local PostgreSQL configuration before DB setup.')


class FeedbackClosureFixtures:
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.assertIn(connection.settings_dict['HOST'], ('127.0.0.1', 'localhost', '::1'))
        self.assertEqual(settings.RECOMMENDATION_ALGORITHM_VERSION, 'content.v1')
        cache.clear()
        self.user = get_user_model().objects.create_user(username='8c-synthetic-owner')
        self.other = get_user_model().objects.create_user(username='8c-synthetic-other')
        for owner in (self.user, self.other):
            RecommendationPreference.objects.create(user=owner, tags=['音乐'], version=4)
        for identity in (701, 702, 703, 704):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'8C合成作品{identity}',
                nsfw=False, tags='音乐,日常', score=7, season='2026-04', status_type='TV')

    def body(self, identity=701, version=0, **extra):
        return {'bangumi_id': identity, 'expected_feedback_version': version,
                'operation_id': str(uuid4()), **extra}

    def client_for(self, owner):
        client = APIClient(); client.force_authenticate(owner)
        return client


class FeedbackClosureAPITests(FeedbackClosureFixtures, APITestCase):
    def test_other_users_complete_snapshots_and_feedback_are_unchanged(self):
        a, b = self.client_for(self.user), self.client_for(self.other)
        before = {category: b.get('/api/recommendations/', {'category': category}).data
                  for category in ('all', 'familiar', 'explore')}
        self.assertEqual(a.post('/api/recommendations/feedback/', self.body(), format='json').status_code, 200)
        for category, snapshot in before.items():
            self.assertTrue(snapshot['results'])
            self.assertEqual(b.get('/api/recommendations/', {'category': category}).data, snapshot)
        self.assertEqual(b.get('/api/recommendations/feedback/hidden/').data['feedback_version'], 0)
        self.assertEqual(b.get('/api/recommendations/feedback/hidden/').data['count'], 0)
        self.assertEqual(a.get('/api/recommendations/feedback/hidden/').data['count'], 1)

    def test_reclassification_before_undo_fails_closed_for_adult_and_unknown(self):
        client = self.client_for(self.user)
        # Warm public features before changing classification, just like a real cache.
        self.assertEqual(client.get('/api/recommendations/').status_code, 200)
        for identity, nsfw in ((701, True), (702, None)):
            version = State.objects.filter(user=self.user).values_list('feedback_version', flat=True).first() or 0
            hidden = client.post('/api/recommendations/feedback/', self.body(identity, version), format='json')
            self.assertEqual(hidden.status_code, 200)
            revision = hidden.data['feedback_version']
            RecommendationAnime.objects.filter(bangumi_id=identity).update(nsfw=nsfw)
            self.assertEqual(client.post('/api/recommendations/feedback/undo/', self.body(identity, revision,
                target_feedback_version=revision), format='json').status_code, 404)
            self.assertEqual(State.objects.get(user=self.user).feedback_version, revision)
            self.assertTrue(Feedback.objects.get(user=self.user, bangumi_id=identity).is_active)
            self.assertEqual(client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertNotIn(identity, [r['bangumi_id'] for r in client.get('/api/recommendations/').data['results']])

    def test_reclassification_after_successful_undo_still_cannot_restore_candidate(self):
        client = self.client_for(self.user)
        hidden = client.post('/api/recommendations/feedback/', self.body(), format='json').data
        self.assertEqual(client.post('/api/recommendations/feedback/undo/', self.body(version=1,
            target_feedback_version=hidden['feedback']['feedback_version']), format='json').status_code, 200)
        for nsfw in (True, None):
            RecommendationAnime.objects.filter(bangumi_id=701).update(nsfw=nsfw)
            self.assertNotIn(701, [r['bangumi_id'] for r in client.get('/api/recommendations/').data['results']])
            self.assertEqual(client.get('/api/recommendations/701/').status_code, 404)

    def test_hide_undo_round_trip_never_reuses_original_page_or_evidence_epoch(self):
        client = self.client_for(self.user)
        original = client.get('/api/recommendations/', {'page_size': 1}).data
        client.post('/api/recommendations/feedback/', self.body(), format='json')
        client.post('/api/recommendations/feedback/undo/', self.body(version=1, target_feedback_version=1), format='json')
        fresh = client.get('/api/recommendations/', {'page_size': 1}).data
        self.assertEqual([r['bangumi_id'] for r in original['results']], [r['bangumi_id'] for r in fresh['results']])
        self.assertNotEqual(original['state_token'], fresh['state_token'])
        self.assertEqual(client.get('/api/recommendations/', {'page_size': 1, 'page': 2,
            'state_token': original['state_token']}).status_code, 409)
        row = original['results'][0]
        self.assertEqual(client.get(f'/api/recommendations/{row["bangumi_id"]}/',
            {'evidence_token': row['evidence_token']}).status_code, 409)
        self.assertEqual(fresh['evidence_context']['preference_version'], original['evidence_context']['preference_version'])
        self.assertEqual(fresh['evidence_context']['profile_version'], original['evidence_context']['profile_version'])
        self.assertEqual(fresh['evidence_context']['feedback_version'], 2)


class FeedbackBoundaryConcurrencyTests(FeedbackClosureFixtures, TransactionTestCase):
    def parallel(self, calls):
        barrier, pids = Barrier(len(calls)), []
        def execute(call):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute('SELECT pg_backend_pid()'); pids.append(cursor.fetchone()[0])
                owner, action, body = call
                user = get_user_model().objects.get(pk=owner)
                barrier.wait(timeout=10)
                if action == 'want':
                    return add_want(user=user, bangumi_id=body['bangumi_id'])
                try:
                    return mutate_feedback(user=user, action=action, changes=body)
                except FeedbackConflict as exc:
                    return {'conflict': exc.payload['code']}
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            results = list(pool.map(execute, calls))
        self.assertEqual(len(set(pids)), len(calls))
        return results

    def test_existing_want_and_hide_eight_way_race_preserves_every_private_field(self):
        shared = Anime.objects.create(bgm_id=701, chinese_title='原公共标题')
        record = UserAnimeRecord.objects.create(user=self.user, anime=shared, personal_score='0.0',
            review='8C完整私人长评\n' * 5000, watch_status='on_hold', metadata_initialized=True,
            private_base_tags=[], custom_tags=['未知标签', '二刷'], watched_eps=7, total_eps=12)
        before = UserAnimeRecord.objects.values().get(pk=record.pk)
        public = Anime.objects.values().get(pk=shared.pk)
        body = self.body()
        rows = self.parallel([(self.user.pk, 'hide', body)] * 4 +
                             [(self.user.pk, 'want', {'bangumi_id': 701})] * 4)
        self.assertEqual(sum(r.get('changed', False) for r in rows), 1)
        self.assertEqual(sum(r.get('replayed', False) for r in rows), 3)
        self.assertTrue(all(not r['created'] for r in rows if 'created' in r))
        self.assertEqual(UserAnimeRecord.objects.values().get(pk=record.pk), before)
        self.assertEqual(Anime.objects.values().get(pk=shared.pk), public)
        self.assertEqual(Feedback.objects.count(), 1); self.assertEqual(Operation.objects.count(), 1)

    def test_new_want_and_hide_compete_without_duplicates_or_personal_defaults(self):
        body = self.body()
        rows = self.parallel([(self.user.pk, 'hide', body)] * 4 +
                             [(self.user.pk, 'want', {'bangumi_id': 701})] * 4)
        self.assertEqual(sum(r.get('created', False) for r in rows), 1)
        self.assertEqual(sum(r.get('changed', False) for r in rows), 1)
        self.assertEqual(Feedback.objects.count(), 1); self.assertEqual(UserAnimeRecord.objects.count(), 1)
        record = UserAnimeRecord.objects.get()
        self.assertEqual(record.watch_status, 'plan'); self.assertIsNone(record.personal_score); self.assertEqual(record.review, '')
        self.assertTrue(Feedback.objects.get().is_active)

    def test_different_users_mixed_writes_do_not_change_other_users_snapshot(self):
        shared = Anime.objects.create(bgm_id=701, chinese_title='B已有作品')
        record = UserAnimeRecord.objects.create(user=self.other, anime=shared, personal_score='9.1',
            watch_status='watching', review='B原长评', custom_tags=['自定义'])
        before = UserAnimeRecord.objects.values().get(pk=record.pk)
        client = self.client_for(self.other)
        snapshot = client.get('/api/recommendations/', {'hide_recorded': False}).data
        self.parallel([(self.user.pk, 'hide', self.body()), (self.other.pk, 'want', {'bangumi_id': 701})])
        self.assertEqual(client.get('/api/recommendations/', {'hide_recorded': False}).data, snapshot)
        self.assertEqual(UserAnimeRecord.objects.values().get(pk=record.pk), before)
        self.assertFalse(Feedback.objects.filter(user=self.other).exists())

    def test_late_hide_receipt_retries_after_undo_never_resurrect_hidden_state(self):
        body = self.body()
        mutate_feedback(user=self.user, action='hide', changes=body)
        mutate_feedback(user=self.user, action='undo', changes=self.body(version=1, target_feedback_version=1))
        before = Feedback.objects.values().get()
        rows = self.parallel([(self.user.pk, 'hide', body)] * 8)
        self.assertTrue(all(r['replayed'] and not r['changed'] and not r['feedback']['is_active'] for r in rows))
        self.assertEqual(Feedback.objects.values().get(), before)
        self.assertEqual(State.objects.get().feedback_version, 2); self.assertEqual(Operation.objects.count(), 2)

    def test_catalog_lock_reclassification_wins_before_waiting_undo(self):
        for identity, nsfw in ((701, True), (702, None)):
            revision = State.objects.filter(user=self.user).values_list('feedback_version', flat=True).first() or 0
            result = mutate_feedback(user=self.user, action='hide', changes=self.body(identity, revision))
            version = result['feedback_version']; ready = Event(); worker = {}
            def undo():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '10s'")
                        cursor.execute('SELECT pg_backend_pid()'); worker['pid'] = cursor.fetchone()[0]
                    user = get_user_model().objects.get(pk=self.user.pk); ready.set()
                    try:
                        return mutate_feedback(user=user, action='undo', changes=self.body(identity, version, target_feedback_version=version))
                    except NotFound:
                        return 'blocked_404'
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    RecommendationAnime.objects.select_for_update().get(bangumi_id=identity)
                    future = pool.submit(undo); self.assertTrue(ready.wait(5))
                    waiting = False; until = monotonic() + 5
                    while monotonic() < until:
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [worker['pid']])
                            waiting = cursor.fetchone()[0] > 0
                        if waiting: break
                        sleep(.01)
                    self.assertTrue(waiting, 'Worker must actually wait on the PostgreSQL catalog lock')
                    RecommendationAnime.objects.filter(bangumi_id=identity).update(nsfw=nsfw)
                self.assertEqual(future.result(timeout=10), 'blocked_404')
            self.assertTrue(Feedback.objects.get(user=self.user, bangumi_id=identity).is_active)
            self.assertEqual(State.objects.get(user=self.user).feedback_version, version)

    def test_user_lock_timeout_is_json_503_then_exact_operation_retry_succeeds_once(self):
        body = self.body()
        def post():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '200ms'")
                    cursor.execute("SET statement_timeout = '3s'")
                client = self.client_for(get_user_model().objects.get(pk=self.user.pk))
                return client.post('/api/recommendations/feedback/', body, format='json')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=self.user.pk)
                response = pool.submit(post).result(timeout=5)
                self.assertEqual(response.status_code, 503)
                self.assertIn('application/json', response['Content-Type'])
        self.assertEqual(Feedback.objects.count(), 0); self.assertEqual(Operation.objects.count(), 0)
        response = post(); self.assertEqual(response.status_code, 200)
        replay = post(); self.assertTrue(replay.data['replayed'])
        self.assertEqual(Feedback.objects.count(), 1); self.assertEqual(Operation.objects.count(), 1)
