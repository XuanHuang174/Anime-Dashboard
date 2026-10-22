"""Synthetic PostgreSQL tests only. No live account grants or content approvals."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from anime.models import UserAnimeRecord
from recommendations.tests import test_eligibility as fixtures
from recommendations.tests import test_controlled_release_m3 as lock_fixtures
from recommendations.eligibility import EligibilityConflict, own_eligibility
from recommendations.eligibility_admin import EligibilityAdminForm
from recommendations.models import (RecommendationContentEligibility as Eligibility,
    RecommendationEligibilityEvent as Event, RecommendationServingControl,
    RecommendationContentReview)


class SelfApprovalAuditMigrationTests(TransactionTestCase):
    def test_history_stays_false_pending_stays_pending_and_old_writer_has_db_default(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        executor = MigrationExecutor(connection); leaves = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([('recommendations', '0010_controlled_serving_m2')])
            apps = executor.loader.project_state([('recommendations', '0010_controlled_serving_m2')]).apps
            user = apps.get_model('auth', 'User').objects.create(username='self-migration-synthetic')
            old_grant = apps.get_model('recommendations', 'RecommendationContentEligibility').objects.create(
                user_id=user.pk, status='pending', eligibility_version=1, authorization_source='manual',
                purpose='manual_access', requested_at=timezone.now())
            old_event = apps.get_model('recommendations', 'RecommendationEligibilityEvent')
            values = dict(eligibility_id=old_grant.pk, action='apply', request_fingerprint='a' * 64,
                previous_status='not_requested', resulting_status='pending', previous_version=0,
                applied_version=1, changed=True, reason='合成历史事件')
            first = old_event.objects.create(operation_id=uuid4(), **values)
            MigrationExecutor(connection).migrate(leaves)
            self.assertFalse(Event.objects.get(pk=first.pk).is_self_approval)
            self.assertEqual(Eligibility.objects.get(pk=old_grant.pk).status, 'pending')
            # A pre-deploy model omits the new NOT NULL field. PostgreSQL's
            # persistent default must keep old instances writing during rollout.
            legacy_writer = old_event.objects.create(operation_id=uuid4(), **values)
            self.assertFalse(Event.objects.get(pk=legacy_writer.pk).is_self_approval)
        finally:
            MigrationExecutor(connection).migrate(leaves)


class SuperuserSelfApprovalTests(TransactionTestCase):
    setUp = fixtures.EligibilityFixtures.setUp
    payload = fixtures.EligibilityFixtures.payload
    own_payload = fixtures.EligibilityFixtures.own_payload
    change = fixtures.EligibilityFixtures.change
    apply = fixtures.EligibilityFixtures.apply
    waiting_operation = lock_fixtures.ControlledReleaseDrills.waiting_operation

    def self_payload(self, **changes):
        return self.payload(1, confirmed=True, self_approval_confirmed=True, **changes)

    def test_explicit_self_approval_audited_and_no_business_side_effects(self):
        self.apply(user=self.manager)
        before = list(UserAnimeRecord.objects.values())
        result = self.change('approve', self.self_payload(), subject=self.manager)
        self.assertTrue(result['eligibility']['manual_authorization_valid'])
        self.assertFalse(result['eligibility']['adult_access_allowed'])
        self.assertEqual(result['eligibility']['age_assurance_status'], 'not_performed')
        event = Event.objects.get(action='approve')
        self.assertTrue(event.is_self_approval)
        self.assertEqual(event.actor_id, event.eligibility.user_id)
        self.assertEqual(event.reason, '内部原因不可向用户泄露')
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))
        self.assertFalse(RecommendationServingControl.objects.exists())
        self.assertFalse(RecommendationContentReview.objects.exists())

    def test_missing_false_or_coerced_confirmation_never_grants(self):
        self.apply(user=self.manager)
        for field in ('confirmed', 'self_approval_confirmed'):
            for value in (None, False, 1, 'true', 'false'):
                data = self.self_payload(); data[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    self.change('approve', data, subject=self.manager)
            data = self.self_payload(); data.pop(field)
            with self.assertRaises(ValidationError): self.change('approve', data, subject=self.manager)
        self.assertEqual(Eligibility.objects.get(user=self.manager).status, 'pending')
        self.assertEqual(Event.objects.count(), 1)

    def test_staff_normal_or_stale_superuser_cannot_self_grant(self):
        for user in (self.owner, self.other):
            self.apply(user=user)
            with self.assertRaises(PermissionDenied):
                self.change('approve', self.self_payload(), subject=user, actor=user)
        self.apply(user=self.manager)
        get_user_model().objects.filter(pk=self.manager.pk).update(is_superuser=False)
        with self.assertRaises(PermissionDenied):
            self.change('approve', self.self_payload(), subject=self.manager)
        self.assertFalse(Event.objects.filter(action='approve').exists())

    def test_still_requires_pending_application_and_bounded_reason_duration(self):
        with self.assertRaises(EligibilityConflict):
            self.change('approve', {**self.self_payload(), 'expected_version': 0}, subject=self.manager)
        self.apply(user=self.manager)
        for changes in ({'reason': ''}, {'reason': '  '}, {'days': 31}, {'days': 0}):
            with self.assertRaises(ValidationError):
                self.change('approve', {**self.self_payload(), **changes}, subject=self.manager)
        self.assertEqual(Event.objects.count(), 1)

    def test_receipt_replay_is_current_not_a_regrant_after_revocation(self):
        self.apply(user=self.manager); data = self.self_payload()
        self.change('approve', data, subject=self.manager)
        self.assertTrue(self.change('approve', data, subject=self.manager)['replayed'])
        self.change('revoke', self.payload(2), subject=self.manager)
        replay = self.change('approve', data, subject=self.manager)
        self.assertTrue(replay['replayed'])
        self.assertFalse(replay['eligibility']['manual_authorization_valid'])
        self.assertEqual(Event.objects.filter(is_self_approval=True).count(), 1)

    def test_other_approval_fingerprint_replay_compatible_and_not_self_labeled(self):
        self.apply(); data = self.payload(1)
        self.change('approve', data)
        original = Event.objects.get(action='approve').request_fingerprint
        replay = self.change('approve', {**data, 'confirmed': True, 'self_approval_confirmed': False})
        self.assertTrue(replay['replayed'])
        self.assertEqual(original, Event.objects.get(action='approve').request_fingerprint)
        self.assertFalse(Event.objects.get(action='approve').is_self_approval)

    def test_database_rejects_self_audit_flag_for_nonapproval(self):
        self.apply(user=self.manager)
        event = Event.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.filter(pk=event.pk).update(is_self_approval=True)

    def test_signed_module3_self_confirmation_and_no_ordinary_user_capability(self):
        self.apply(user=self.manager)
        self.client.force_authenticate(self.manager)
        url = f'/api/recommendations/admin/applications/{self.manager.pk}/'
        response = self.client.get(url)
        self.assertTrue(response.data['is_self']); self.assertTrue(response.data['self_approval_allowed'])
        body = {'ticket': response.data['ticket'], 'action': 'approve', 'reason': '测试本人授权', 'days': 2, 'confirmed': True}
        denied = self.client.post(url, body, format='json')
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.data['code'], 'self_approval_confirmation_required')
        self.assertEqual(self.client.post(url, {**body, 'self_approval_confirmed': 'true'}, format='json').status_code, 400)
        accepted = self.client.post(url, {**body, 'self_approval_confirmed': True}, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertTrue(Event.objects.get(action='approve').is_self_approval)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {**body, 'self_approval_confirmed': True}, format='json').status_code, 403)

    def test_django_admin_requires_distinct_self_checkbox(self):
        self.apply(user=self.manager); client = Client(); client.force_login(self.manager)
        url = reverse('admin:recommendations_eligibility_control')
        response = client.get(url, {'user_id': self.manager.pk})
        ticket = response.context_data['form'].initial['ticket']
        data = {'ticket': ticket, 'action': 'approve', 'reason': '本人授权隔离测试', 'days': 3, 'confirmed': 'on'}
        denied = client.post(url, data)
        self.assertEqual(denied.status_code, 200)
        self.assertIn('self_approval_confirmed', denied.context_data['form'].errors)
        self.assertEqual(client.post(url, {**data, 'self_approval_confirmed': 'on'}).status_code, 302)
        self.assertTrue(Event.objects.get(action='approve').is_self_approval)
        other_form = EligibilityAdminForm(is_self=False)
        self.assertNotIn('self_approval_confirmed', other_form.fields)

    def test_self_audit_failure_rolls_back_grant(self):
        self.apply(user=self.manager)
        with patch('recommendations.eligibility.Event.objects.create', side_effect=IntegrityError('synthetic')):
            with self.assertRaises(IntegrityError):
                self.change('approve', self.self_payload(), subject=self.manager)
        self.assertEqual(Eligibility.objects.get(user=self.manager).status, 'pending')
        self.assertEqual(Event.objects.count(), 1)

    def test_competing_self_approvals_one_version_one_decision(self):
        self.apply(user=self.manager); barrier = Barrier(2)
        def task():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try: return self.change('approve', self.self_payload(), subject=self.manager)['operation_version']
                except EligibilityConflict: return 'conflict'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: task(), range(2)))
        self.assertEqual(sorted(results, key=str), [2, 'conflict'])
        self.assertEqual(Event.objects.filter(is_self_approval=True).count(), 1)

    def test_self_approval_rechecks_role_after_actual_user_lock_wait(self):
        self.apply(user=self.manager)
        def change(stage):
            if stage == 'lock': get_user_model().objects.select_for_update().get(pk=self.manager.pk)
            else: get_user_model().objects.filter(pk=self.manager.pk).update(is_superuser=False)
        result = self.waiting_operation(
            lambda: self.change('approve', self.self_payload(), subject=self.manager), change)
        self.assertEqual(result, 403)
        self.assertFalse(own_eligibility(self.manager)['manual_authorization_valid'])
        self.assertFalse(Event.objects.filter(is_self_approval=True).exists())
