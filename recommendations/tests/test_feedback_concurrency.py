"""Real PostgreSQL multi-connection lock contention, synthetic accounts only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from anime.models import Anime, UserAnimeRecord
from recommendations.feedback import mutate_feedback, FeedbackConflict
from recommendations.models import (RecommendationAnime, UserRecommendationFeedback as Feedback,
    UserRecommendationFeedbackState as State, RecommendationFeedbackOperation as Operation)

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('8A locking tests require the dedicated local PostgreSQL runner before test DB setup.')


class FeedbackConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(settings.SETTINGS_MODULE, 'config.recommendation_pg_test_settings')
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.assertIn(connection.settings_dict['HOST'], ('127.0.0.1', 'localhost', '::1'))
        self.user = get_user_model().objects.create_user(username='feedback-race-owner')
        self.other = get_user_model().objects.create_user(username='feedback-race-other')
        for identity in (701, 702):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh='合成并发作品', nsfw=False)

    def change(self, identity=701, version=0, **extra):
        return {'bangumi_id': identity, 'expected_feedback_version': version,
                'operation_id': str(uuid4()), **extra}

    def parallel(self, calls):
        barrier, backend_pids = Barrier(len(calls)), []

        def execute(call):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pids.append(cursor.fetchone()[0])
                owner, action, changes = call
                user = get_user_model().objects.get(pk=owner)
                barrier.wait(timeout=10)
                try:
                    return mutate_feedback(user=user, action=action, changes=changes)
                except FeedbackConflict as exc:
                    return {'conflict': exc.payload['code']}
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            results = list(pool.map(execute, calls))
        self.assertEqual(len(set(backend_pids)), len(calls))
        return results

    def test_same_operation_eight_connections_one_write_seven_replays(self):
        body = self.change()
        results = self.parallel([(self.user.pk, 'hide', body)] * 8)
        self.assertEqual(sum(row['changed'] for row in results), 1)
        self.assertEqual(sum(row['replayed'] for row in results), 7)
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(Operation.objects.count(), 1)
        self.assertEqual(State.objects.get().feedback_version, 1)

    def test_distinct_operations_same_old_revision_only_one_wins(self):
        results = self.parallel([(self.user.pk, 'hide', self.change()) for _ in range(8)])
        self.assertEqual(sum(row.get('changed', False) for row in results), 1)
        self.assertEqual(sum(row.get('conflict') == 'feedback_version_conflict' for row in results), 7)
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(Operation.objects.count(), 1)

    def test_two_different_works_serialize_on_user_revision(self):
        results = self.parallel([(self.user.pk, 'hide', self.change(identity)) for identity in (701, 702)])
        self.assertEqual(sum(row.get('changed', False) for row in results), 1)
        self.assertEqual(sum('conflict' in row for row in results), 1)
        self.assertEqual(State.objects.get().feedback_version, 1)

    def test_cross_user_same_uuid_and_work_are_independent(self):
        body = self.change()
        results = self.parallel([(user.pk, 'hide', body) for user in (self.user, self.other)])
        self.assertTrue(all(row['changed'] for row in results))
        self.assertEqual(Feedback.objects.count(), 2)
        self.assertEqual(Operation.objects.count(), 2)

    def test_hide_undo_race_never_overwrites_newer_decision(self):
        mutate_feedback(user=self.user, action='hide', changes=self.change())
        results = self.parallel([
            (self.user.pk, 'undo', self.change(version=1, target_feedback_version=1)),
            (self.user.pk, 'hide', self.change(version=1)),
        ])
        # A hide on already-hidden work is a no-op; an undo wins once or the
        # stale hide gets 409. Neither interleaving can resurrect the old hide.
        self.assertEqual(sum(row.get('changed', False) for row in results), 1)
        self.assertFalse(Feedback.objects.get().is_active)
        self.assertEqual(State.objects.get().feedback_version, 2)

    def test_eight_undos_preserve_full_journal_and_change_once(self):
        shared = Anime.objects.create(bgm_id=701, chinese_title='私人原记录')
        record = UserAnimeRecord.objects.create(user=self.user, anime=shared, watch_status='on_hold',
            personal_score='0.0', review='完整长评\n' * 5000, metadata_initialized=True,
            private_base_tags=[], custom_tags=['未知标签', '二刷'])
        before = UserAnimeRecord.objects.values().get(pk=record.pk)
        mutate_feedback(user=self.user, action='hide', changes=self.change())
        body = self.change(version=1, target_feedback_version=1)
        results = self.parallel([(self.user.pk, 'undo', body)] * 8)
        self.assertEqual(sum(row['changed'] for row in results), 1)
        self.assertEqual(sum(row['replayed'] for row in results), 7)
        self.assertFalse(Feedback.objects.get().is_active)
        self.assertEqual(UserAnimeRecord.objects.values().get(pk=record.pk), before)
