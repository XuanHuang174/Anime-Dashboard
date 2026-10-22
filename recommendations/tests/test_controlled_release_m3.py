"""M3 release drills. Synthetic local PostgreSQL only; never live credentials."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import APIException

from anime.models import UserAnimeRecord
from recommendations.tests import test_controlled_serving_m2 as fixtures
from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
from recommendations.content_scope import require_scope
from recommendations.eligibility import EligibilityConflict
from recommendations.feedback import mutate_feedback
from recommendations.models import (RecommendationAnime, RecommendationContentEligibility as Eligibility,
    RecommendationContentReview as Review, RecommendationServingControl as Control,
    RecommendationServingControlEvent as ControlEvent, UserRecommendationFeedback as Feedback)
from recommendations.serving_control import change_control, control_state
from recommendations.want import add_want


@override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True,
    RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
class ControlledReleaseDrills(TransactionTestCase):
    setUp = fixtures.ControlledServingTests.setUp
    change = fixtures.ControlledServingTests.change
    control = fixtures.ControlledServingTests.control
    review = fixtures.ControlledServingTests.review
    enable = fixtures.ControlledServingTests.enable
    get = fixtures.ControlledServingTests.get

    def revoke_review(self):
        anime = RecommendationAnime.objects.get(bangumi_id=90)
        return mutate_review(actor=self.manager, bangumi_id=90, action='revoke', changes={
            'expected_version': Review.objects.get(anime=anime).review_version,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION,
            'operation_id': uuid4(), 'reason': 'M3 isolated revocation drill'})

    def waiting_operation(self, operation, locked_change):
        """Prove an actual PostgreSQL lock wait, not just a thread having started."""
        started = Event(); backend = []
        def task():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()'); backend.append(cursor.fetchone()[0])
                started.set()
                try:
                    return operation()
                except APIException as exc:
                    return exc.status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                locked_change('lock')
                future = pool.submit(task)
                self.assertTrue(started.wait(3))
                deadline = monotonic() + 5
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s', [backend[0]])
                        row = cursor.fetchone()
                    if row and row[0] == 'Lock':
                        break
                    self.assertLess(monotonic(), deadline, 'Worker never entered a PostgreSQL lock wait')
                    sleep(.02)
                locked_change('change')
            return future.result(timeout=15)

    def test_audited_close_reopen_never_resurrects_old_cursor_or_evidence(self):
        self.enable(); journal = list(UserAnimeRecord.objects.values())
        old = self.get(browse=True, page_size=2).data
        card = old['results'][0]
        self.control(False)
        self.assertEqual(self.get().status_code, 403)
        safe = self.client.get('/api/recommendations/').data
        self.assertTrue(all(r['content_rating'] == 'non_adult' for r in safe['results']))
        self.control(True)
        self.assertEqual(self.get(cursor=old['next_page_cursor']).status_code, 409)
        self.assertEqual(self.get(f'{card["bangumi_id"]}/', evidence_token=card['evidence_token']).status_code, 409)
        fresh = self.get(browse=True, page_size=2)
        self.assertEqual(fresh.status_code, 200)
        self.assertNotEqual(old['content_scope_context'], fresh.data['content_scope_context'])
        self.assertEqual(fresh.data['algorithm_version'], 'content.v1')
        self.assertEqual(journal, list(UserAnimeRecord.objects.values()))

    def test_emergency_runtime_off_then_audited_rearm_preserves_safe_v1(self):
        self.enable(); old = self.get(browse=True, page_size=2).data
        safe = self.client.get('/api/recommendations/').data['results']
        with override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=False):
            self.assertEqual(self.get().status_code, 403)
            self.control(False)  # mandatory persistent invalidation BEFORE re-enabling runtime
            self.assertFalse(control_state(admin=True)['enabled'])
            after = self.client.get('/api/recommendations/').data['results']
            self.assertEqual([(r['bangumi_id'], r['score'], r['reason']) for r in safe],
                             [(r['bangumi_id'], r['score'], r['reason']) for r in after])
        self.assertEqual(self.get().status_code, 403)
        self.control(True)
        self.assertEqual(self.get(cursor=old['next_page_cursor']).status_code, 409)

    def test_replayed_old_enable_receipt_does_not_reopen_control(self):
        self.enable()
        self.control(False)
        payload = {'enabled': True, 'expected_version': 2, 'operation_id': str(uuid4()),
                   'reason': 'M3 replay fixture', 'confirmed': True}
        self.assertTrue(change_control(self.manager, payload)['enabled'])
        closed = self.control(False)
        count = ControlEvent.objects.count()
        replay = change_control(self.manager, payload)
        self.assertTrue(replay['replayed']); self.assertFalse(replay['enabled'])
        self.assertEqual(replay['version'], closed['version'])
        self.assertEqual(ControlEvent.objects.count(), count)

    def test_grant_expiry_without_counter_bump_blocks_reads_and_writes(self):
        scope = self.enable(); before = list(UserAnimeRecord.objects.values())
        grant = Eligibility.objects.get(user=self.owner)
        with patch('recommendations.content_scope.timezone.now', return_value=grant.expires_at + timedelta(seconds=1)):
            self.assertEqual(self.get('90/').status_code, 403)
            with self.assertRaises(APIException) as failure:
                add_want(user=self.owner, bangumi_id=90, scope_context=scope)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(Eligibility.objects.get(pk=grant.pk).eligibility_version, grant.eligibility_version)
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))
        self.assertEqual(self.client.get('/api/recommendations/').status_code, 200)

    def test_review_expiry_is_final_filter_even_with_warm_snapshot(self):
        self.enable()
        first = self.get(browse=True, page_size=2).data
        expiry = timezone.now() + timedelta(seconds=60)
        Review.objects.filter(anime__bangumi_id=90).update(expires_at=expiry)
        with patch('recommendations.content_scope.timezone.now', return_value=expiry + timedelta(seconds=1)):
            self.assertEqual(self.get(cursor=first['next_page_cursor']).status_code, 409)
            self.assertEqual(self.get('90/').status_code, 404)
            fresh = self.get(browse=True, page_size=24)
        self.assertEqual(fresh.status_code, 200)
        self.assertNotIn(90, [r['bangumi_id'] for r in fresh.data['results']])

    def test_unknown_reclassification_undo_never_restores_recommendation(self):
        scope = self.enable(); before = list(UserAnimeRecord.objects.values())
        hidden = mutate_feedback(user=self.owner, action='hide', scope_context=scope, changes={
            'bangumi_id': 90, 'expected_feedback_version': 0, 'operation_id': uuid4()})
        RecommendationAnime.objects.filter(bangumi_id=90).update(nsfw=None)
        with self.assertRaises(APIException):
            mutate_feedback(user=self.owner, action='undo', scope_context=scope, changes={
                'bangumi_id': 90, 'expected_feedback_version': hidden['feedback_version'],
                'target_feedback_version': hidden['feedback']['feedback_version'], 'operation_id': uuid4()})
        self.assertTrue(Feedback.objects.get(user=self.owner, bangumi_id=90).is_active)
        self.assertEqual(self.get('90/').status_code, 404)
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))

    def test_waiting_want_observes_user_revocation_after_lock(self):
        scope = self.enable(); before = list(UserAnimeRecord.objects.values())
        def change(stage):
            if stage == 'lock': get_user_model().objects.select_for_update().get(pk=self.owner.pk)
            else: self.change(action='revoke', version=2)
        result = self.waiting_operation(lambda: add_want(user=self.owner, bangumi_id=90, scope_context=scope), change)
        self.assertEqual(result, 409)
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))

    def test_waiting_want_observes_work_revocation_after_catalog_lock(self):
        scope = self.enable(); before = list(UserAnimeRecord.objects.values())
        def change(stage):
            if stage == 'lock': RecommendationAnime.objects.select_for_update().get(bangumi_id=90)
            else: self.revoke_review()
        result = self.waiting_operation(lambda: add_want(user=self.owner, bangumi_id=90, scope_context=scope), change)
        self.assertEqual(result, 409)
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))

    def test_waiting_feedback_observes_revocation_and_writes_nothing(self):
        scope = self.enable(); before = list(UserAnimeRecord.objects.values())
        def change(stage):
            if stage == 'lock': get_user_model().objects.select_for_update().get(pk=self.owner.pk)
            else: self.change(action='revoke', version=2)
        result = self.waiting_operation(lambda: mutate_feedback(user=self.owner, action='hide', scope_context=scope,
            changes={'bangumi_id': 90, 'expected_feedback_version': 0, 'operation_id': uuid4()}), change)
        self.assertEqual(result, 409); self.assertFalse(Feedback.objects.exists())
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))

    def test_waiting_admin_control_rechecks_superuser_permission(self):
        self.enable(); count = ControlEvent.objects.count()
        def change(stage):
            if stage == 'lock': Control.objects.select_for_update().get(pk=1)
            else: get_user_model().objects.filter(pk=self.manager.pk).update(is_superuser=False)
        result = self.waiting_operation(lambda: self.control(False), change)
        self.assertEqual(result, 403); self.assertTrue(control_state(admin=True)['enabled'])
        self.assertEqual(ControlEvent.objects.count(), count)

    def test_competing_approval_rejection_exactly_one_current_decision(self):
        self.change(); barrier = Barrier(2)
        def task(action):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try: return self.change(action=action, version=1)['eligibility']['eligibility_version']
                except EligibilityConflict: return 'conflict'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(task, ('approve', 'reject')))
        self.assertEqual(sorted(results, key=str), [2, 'conflict'])
        self.assertEqual(Eligibility.objects.get(user=self.owner).eligibility_version, 2)
        self.assertFalse(Eligibility.objects.filter(user=self.other).exists())

    def test_disabled_account_never_inherits_active_grant(self):
        self.enable()
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        with self.assertRaises(APIException) as failure:
            require_scope(self.owner, 'include_adult')
        self.assertEqual(failure.exception.status_code, 403)
        self.assertEqual(self.client.get('/api/recommendations/admin/control/').status_code, 403)
