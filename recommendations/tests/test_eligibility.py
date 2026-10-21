"""N2-1 synthetic-only eligibility, admin and PostgreSQL lock regression."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.db import close_old_connections, connection, DatabaseError, IntegrityError, transaction
from django.test import Client, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations.age_assurance import AgeAssurance, AgeAssuranceUnavailable
from recommendations.content_scope_contract import require_n1_scope
from recommendations.eligibility import EligibilityConflict, mutate_eligibility, own_eligibility
from recommendations.models import (RecommendationContentEligibility as Eligibility,
    RecommendationEligibilityEvent as Event, RecommendationAnime, RecommendationPreference,
    UserRecommendationFeedbackState)
from recommendations.want import add_want


class EligibilityFixtures(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.owner = get_user_model().objects.create_user(username='elig-owner')
        self.other = get_user_model().objects.create_user(username='elig-other', is_staff=True)
        self.manager = get_user_model().objects.create_user(username='elig-manager', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_content_eligibility'))
        self.now = timezone.now()
        RecommendationAnime.objects.create(bangumi_id=800, title_zh='合成安全作品', nsfw=False, tags='音乐')
        shared = Anime.objects.create(bgm_id=800, chinese_title='合成安全作品')
        self.record = UserAnimeRecord.objects.create(user=self.owner, anime=shared, personal_score=0,
            watch_status='completed', review='PRIVATE_REVIEW_SENTINEL', custom_tags=['PRIVATE_TAG_SENTINEL'])
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'], version=3)
        UserRecommendationFeedbackState.objects.create(user=self.owner, feedback_version=4)
        self.client = APIClient(); self.client.force_authenticate(self.owner)

    def proof(self, user_id):
        return AgeAssurance(user_id, 'verified', 'isolated-test-only', 'opaque-test-reference', 7,
                            self.now + timedelta(days=90))

    def payload(self, version=0, **changes):
        return {'expected_version':version, 'operation_id':uuid4(), 'reason':'隔离合成验收', **changes}

    def change(self, action, data=None, subject=None, actor=None):
        return mutate_eligibility(actor=actor or self.manager, subject_id=(subject or self.owner).pk,
                                  action=action, changes=data or self.payload())

    def grant(self):
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            return self.change('grant')


class EligibilityServiceTests(EligibilityFixtures):
    def test_default_read_has_no_writes_and_no_auto_qualification(self):
        with CaptureQueriesContext(connection) as queries:
            data = own_eligibility(self.owner)
        self.assertEqual(data['administrative_status'], 'not_requested')
        self.assertEqual(data['eligibility_version'], 0)
        self.assertFalse(data['adult_access_allowed'])
        self.assertEqual(Eligibility.objects.count(), 0)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))

    def test_prepare_and_noop_have_immutable_receipts_without_age_claim(self):
        self.assertEqual(self.change('prepare')['eligibility']['eligibility_version'], 1)
        result = self.change('prepare', self.payload(1))
        self.assertFalse(result['changed'])
        self.assertEqual(result['eligibility']['age_assurance_status'], 'unavailable')
        self.assertEqual(Event.objects.count(), 2)

    def test_missing_authority_never_grants_even_to_superuser(self):
        self.manager.is_superuser = True; self.manager.save(update_fields=['is_superuser'])
        with self.assertRaises(ValidationError):
            self.change('grant')
        self.assertFalse(Eligibility.objects.exists())
        self.assertFalse(Event.objects.exists())

    def test_permissions_are_fresh_and_self_grant_is_forbidden(self):
        with self.assertRaises(PermissionDenied):
            self.change('prepare', actor=self.other)
        self.assertTrue(self.manager.has_perm('recommendations.manage_content_eligibility'))
        self.manager.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.change('prepare')  # cached True on old actor object must not bypass revocation
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_content_eligibility'))
        with self.assertRaises(PermissionDenied):
            self.change('grant', subject=self.manager)

    def test_strict_inputs_cannot_supply_identity_proof_or_modify_status(self):
        for field, value in (('age', 99), ('assurance_provider','client'), ('assurance_reference','id'),
                             ('status','active'), ('purpose','public'), ('days',31), ('expected_version',-1)):
            with self.assertRaises(ValidationError):
                self.change('grant', self.payload(**{field:value}))
        for proof in (AgeAssurance(self.other.pk, 'verified', 'fake', 'ref', 1, self.now+timedelta(days=1)),
                      AgeAssurance(self.owner.pk, 'verified', 'fake', 'ref', 1, self.now),
                      AgeAssurance(self.owner.pk, 'verified', 'fake', 'https://not-an-opaque-reference', 1,
                                   self.now+timedelta(days=1))):
            with patch('recommendations.age_assurance.current_age_assurance', return_value=proof), self.assertRaises(ValidationError):
                self.change('grant')

    def test_grant_renew_and_expiry_cap_are_local_test_only(self):
        proof = AgeAssurance(self.owner.pk, 'verified', 'isolated-test-only', 'test-ref', 1, self.now+timedelta(days=5))
        with patch('recommendations.age_assurance.current_age_assurance', return_value=proof):
            result = self.change('grant')
            self.assertEqual(result['eligibility']['valid_until'], proof.valid_until)
            self.assertTrue(result['eligibility']['administrative_qualification_valid'])
            self.assertFalse(result['eligibility']['adult_access_allowed'])
            row = Eligibility.objects.get(user=self.owner)
            self.assertLessEqual(row.expires_at, row.assurance_valid_until)
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            renewed = self.change('renew', self.payload(1))
            self.assertEqual(renewed['eligibility']['eligibility_version'], 2)
            with self.assertRaises(ValidationError):
                self.change('renew', self.payload(2, days=1))
        with self.assertRaises(PermissionDenied):
            require_n1_scope(self.owner, 'include_adult')  # N1/serving stays locked independently

    def test_expiry_is_readonly_and_provider_loss_fails_closed(self):
        self.grant()
        before = list(Eligibility.objects.values())
        self.assertEqual(own_eligibility(self.owner)['effective_status'], 'assurance_unavailable')
        expires = Eligibility.objects.get(user=self.owner).expires_at
        with patch('recommendations.eligibility.timezone.now', return_value=expires), CaptureQueriesContext(connection) as queries:
            expired = own_eligibility(self.owner)
        self.assertEqual(expired['effective_status'], 'expired')
        self.assertFalse(expired['administrative_qualification_valid'])
        self.assertEqual(before, list(Eligibility.objects.values()))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))

    def test_provider_outage_revision_change_and_disabled_account_never_qualify(self):
        self.grant()
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=AgeAssuranceUnavailable):
            self.assertFalse(own_eligibility(self.owner)['administrative_qualification_valid'])
            with self.assertRaises(ValidationError):
                self.change('renew', self.payload(1))
        altered = AgeAssurance(self.owner.pk, 'verified', 'isolated-test-only', 'opaque-test-reference', 8,
                               self.now+timedelta(days=90))
        with patch('recommendations.age_assurance.current_age_assurance', return_value=altered):
            self.assertFalse(own_eligibility(self.owner)['administrative_qualification_valid'])
        self.owner.is_active = False; self.owner.save(update_fields=['is_active'])
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            self.assertEqual(own_eligibility(self.owner)['effective_status'], 'account_inactive')
            with self.assertRaises(PermissionDenied):
                self.change('renew', self.payload(1))

    def test_duplicate_replay_after_revoke_cannot_restore_grant(self):
        data = self.payload()
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            self.change('grant', data)
        revoked = self.change('revoke', self.payload(1))
        replay = self.change('grant', data)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation_version'], 1)
        self.assertEqual(replay['eligibility']['administrative_status'], 'revoked')
        self.assertEqual(replay['eligibility'], revoked['eligibility'])
        self.assertEqual(Event.objects.count(), 2)
        with self.assertRaises(EligibilityConflict):
            self.change('grant', {**data, 'reason':'另一个请求'})

    def test_versions_and_tombstone_prevent_stale_approval(self):
        self.change('prepare')
        with self.assertRaises(EligibilityConflict):
            self.change('revoke', self.payload(0))
        with self.assertRaises(PermissionDenied):
            mutate_eligibility(actor=self.owner, subject_id=self.other.pk, action='self_revoke',
                changes={'expected_version':0,'operation_id':uuid4(),'confirmed':True})
        stop = mutate_eligibility(actor=self.other, subject_id=self.other.pk, action='self_revoke',
            changes={'expected_version':0,'operation_id':uuid4(),'confirmed':True})
        self.assertEqual(stop['eligibility']['eligibility_version'], 1)
        with self.assertRaises(EligibilityConflict):
            self.change('grant', self.payload(), subject=self.other)

    def test_events_and_state_rollback_together_and_db_constraints_apply(self):
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof), \
             patch('recommendations.eligibility.Event.objects.create', side_effect=DatabaseError('synthetic failure')), \
             self.assertRaises(DatabaseError):
            self.change('grant')
        self.assertFalse(Eligibility.objects.exists())
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Eligibility.objects.create(user=self.owner, status='active')
        self.change('prepare')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Eligibility.objects.create(user=self.owner)

    def test_version_exhaustion_and_noop_revoke_are_explicit(self):
        self.change('prepare')
        Eligibility.objects.filter(user=self.owner).update(eligibility_version=2147483647)
        with self.assertRaises(EligibilityConflict):
            self.change('revoke', self.payload(2147483647))
        self.assertEqual(Event.objects.count(), 1)
        self.change('revoke', self.payload(), subject=self.other)
        result = self.change('revoke', self.payload(1), subject=self.other)
        self.assertFalse(result['changed'])
        self.assertEqual(result['eligibility']['eligibility_version'], 1)

    def test_qualification_operations_preserve_all_private_and_preference_fields(self):
        records = list(UserAnimeRecord.objects.values())
        preferences = list(RecommendationPreference.objects.values())
        feedback = list(UserRecommendationFeedbackState.objects.values())
        self.grant(); self.change('revoke', self.payload(1))
        self.assertEqual(records, list(UserAnimeRecord.objects.values()))
        self.assertEqual(preferences, list(RecommendationPreference.objects.values()))
        self.assertEqual(feedback, list(UserRecommendationFeedbackState.objects.values()))


class EligibilityApiTests(EligibilityFixtures):
    def test_even_synthetic_valid_grant_cannot_open_existing_recommendation_routes(self):
        self.grant()
        for identity, rating in ((9001, True), (9002, None)):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh='合成受限作品', nsfw=rating, tags='音乐')
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            self.assertFalse(self.client.get('/api/recommendations/eligibility/').data['adult_access_allowed'])
        self.assertEqual(self.client.get('/api/recommendations/', {'content_scope':'include_adult'}).status_code, 400)

    def test_own_status_private_no_store_and_no_other_user_parameters(self):
        data = self.client.get('/api/recommendations/eligibility/')
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data['Cache-Control'], 'private, no-store')
        self.assertIn('Authorization', data['Vary'])
        self.assertNotIn('assurance_reference', data.data)
        self.assertNotIn('user_id', data.data)
        self.change('prepare', subject=self.other)
        self.assertEqual(self.client.get('/api/recommendations/eligibility/', {'user_id':self.other.pk}).status_code, 400)
        self.assertEqual(self.client.get('/api/recommendations/eligibility/').data['eligibility_version'], 0)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/recommendations/eligibility/').status_code, 401)

    def test_self_stop_is_confirmed_versioned_idempotent_and_never_a_grant_api(self):
        url = '/api/recommendations/eligibility/revoke/'
        payload = {'expected_version':0,'operation_id':str(uuid4()),'confirmed':True}
        self.assertEqual(self.client.post(url, {**payload, 'user_id':self.other.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, 'confirmed':False}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 200)
        self.assertTrue(self.client.post(url, payload, format='json').data['replayed'])
        self.assertEqual(Event.objects.count(), 1)
        self.assertFalse(Eligibility.objects.filter(user=self.other).exists())
        self.assertEqual(self.client.post(url, {**payload,'operation_id':str(uuid4())}, format='json').status_code, 409)
        self.assertEqual(self.client.post('/api/recommendations/eligibility/', {'status':'active'}, format='json').status_code, 405)

    def test_missing_migration_returns_unavailable_never_qualified(self):
        with patch('recommendations.eligibility.Eligibility.objects.filter', side_effect=DatabaseError('not migrated')):
            response = self.client.get('/api/recommendations/eligibility/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('not migrated', str(response.data))


class EligibilityAdminTests(EligibilityFixtures):
    def setUp(self):
        super().setUp()
        self.browser = Client()
        self.url = reverse('admin:recommendations_eligibility_control')

    def form_payload(self, action='prepare'):
        page = self.browser.get(self.url, {'user_id':self.owner.pk})
        self.assertEqual(page.status_code, 200)
        return {'ticket':page.context['form'].initial['ticket'], 'action':action,
                'days':30,'reason':'隔离后台验收','confirmed':'on'}

    def test_dedicated_permission_and_explicit_signed_confirmation(self):
        self.browser.force_login(self.other)
        self.assertEqual(self.browser.get(self.url).status_code, 403)
        self.browser.force_login(self.manager)
        self.assertContains(self.browser.get(reverse('admin:recommendations_recommendationcontenteligibility_changelist')),
                            '选择账号并核对资格')
        payload = self.form_payload()
        self.assertFalse(Eligibility.objects.exists())
        self.assertEqual(self.browser.post(self.url, {**payload,'confirmed':''}).status_code, 200)
        self.assertFalse(Eligibility.objects.exists())
        self.assertEqual(self.browser.post(self.url, payload).status_code, 302)
        self.assertEqual(self.browser.post(self.url, payload).status_code, 302)
        self.assertEqual(Event.objects.count(), 1)

    def test_grant_rejected_without_provider_and_cannot_edit_raw_rows_or_audit(self):
        self.browser.force_login(self.manager)
        response = self.browser.post(self.url, self.form_payload('grant'))
        self.assertContains(response, '缺少服务端可信成年依据')
        self.assertNotContains(response, 'ErrorDetail')
        self.assertFalse(Eligibility.objects.exists())
        self.change('prepare')
        row = Eligibility.objects.get(user=self.owner)
        state_admin = admin.site._registry[Eligibility]
        event_admin = admin.site._registry[Event]
        fake = type('Request', (), {'user':self.manager})()
        for controller in (state_admin, event_admin):
            self.assertIsNone(controller.actions)
            self.assertFalse(controller.has_add_permission(fake))
            self.assertFalse(controller.has_change_permission(fake))
            self.assertFalse(controller.has_delete_permission(fake))
        url = reverse('admin:recommendations_recommendationcontenteligibility_change', args=[row.pk])
        self.assertEqual(self.browser.post(url, {'status':'active'}).status_code, 403)

    def test_scope_ticket_expiry_tampering_and_csrf_are_rejected(self):
        self.browser.force_login(self.manager)
        payload = self.form_payload()
        for ticket in ('tampered', signing.dumps({'actor':self.other.pk,'subject':self.owner.pk,
                'version':0,'operation':str(uuid4())}, salt='recommendations.eligibility-admin.n2-1.v1')):
            self.assertContains(self.browser.post(self.url, {**payload,'ticket':ticket}), '凭据无效')
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.manager)
        self.assertEqual(strict.post(self.url, payload).status_code, 403)
        self.assertFalse(Eligibility.objects.exists())
        with patch.object(signing.TimestampSigner, 'timestamp', return_value='1'):
            expired = signing.dumps({'actor':self.manager.pk,'subject':self.owner.pk,
                'version':0,'operation':str(uuid4())}, salt='recommendations.eligibility-admin.n2-1.v1')
        self.assertContains(self.browser.post(self.url, {**payload,'ticket':expired}), '凭据无效')


class EligibilityConcurrencyTests(EligibilityFixtures):
    def race(self, tasks):
        barrier = Barrier(len(tasks))
        def worker(task):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return ('ok', task())
            except EligibilityConflict:
                return ('conflict', None)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            return list(pool.map(worker, tasks))

    def test_duplicate_grant_lock_one_row_and_one_receipt(self):
        payload = self.payload()
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            results = self.race([lambda:self.change('grant', payload)] * 2)
        self.assertEqual([kind for kind, _ in results], ['ok','ok'])
        self.assertEqual(sum(result['replayed'] for _, result in results), 1)
        self.assertEqual((Eligibility.objects.count(), Event.objects.count()), (1,1))

    def test_competing_grants_same_version_only_one_commits(self):
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            results = self.race([lambda:self.change('grant', self.payload())] * 2)
        self.assertEqual(sorted(kind for kind, _ in results), ['conflict','ok'])
        self.assertEqual(Event.objects.count(), 1)

    def test_renew_and_revoke_conflict_preserves_latest_committed_decision(self):
        self.grant()
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=self.proof):
            results = self.race([lambda:self.change('renew', self.payload(1)), lambda:self.change('revoke', self.payload(1))])
        self.assertEqual(sorted(kind for kind, _ in results), ['conflict','ok'])
        row = Eligibility.objects.get(user=self.owner)
        self.assertEqual(row.eligibility_version, 2)
        latest = Event.objects.first()
        self.assertEqual(row.status, latest.resulting_status)

    def test_revoke_and_existing_want_share_user_lock_without_overwriting_journal(self):
        self.grant()
        before = UserAnimeRecord.objects.filter(pk=self.record.pk).values().get()
        results = self.race([lambda:self.change('revoke', self.payload(1)),
                             lambda:add_want(user=self.owner, bangumi_id=800)])
        self.assertEqual([kind for kind, _ in results], ['ok','ok'])
        self.assertEqual(Eligibility.objects.get(user=self.owner).status, 'revoked')
        self.assertEqual(UserAnimeRecord.objects.filter(pk=self.record.pk).values().get(), before)
