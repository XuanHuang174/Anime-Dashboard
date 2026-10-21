"""M1 synthetic-only manual authorization, admin, migration and PostgreSQL races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.db import close_old_connections, connection, DatabaseError, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations.eligibility import EligibilityConflict, mutate_eligibility, own_eligibility
from recommendations.eligibility_admin import ADMIN_SALT
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
        self.manager = get_user_model().objects.create_superuser(username='elig-manager', password=None)
        self.other.user_permissions.add(Permission.objects.get(codename='manage_content_eligibility'))
        self.now = timezone.now()
        RecommendationAnime.objects.create(bangumi_id=800, title_zh='合成安全作品', nsfw=False, tags='音乐')
        shared = Anime.objects.create(bgm_id=800, chinese_title='合成安全作品')
        self.record = UserAnimeRecord.objects.create(user=self.owner, anime=shared, personal_score=0,
            watch_status='completed', review='PRIVATE_REVIEW_SENTINEL', custom_tags=['PRIVATE_TAG_SENTINEL'])
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'], version=3)
        UserRecommendationFeedbackState.objects.create(user=self.owner, feedback_version=4)
        self.client = APIClient(); self.client.force_authenticate(self.owner)

    def payload(self, version=0, **changes):
        return {'expected_version':version, 'operation_id':uuid4(), 'reason':'内部原因不可向用户泄露', **changes}

    def own_payload(self, version=0, **changes):
        return {'expected_version':version, 'operation_id':uuid4(), 'confirmed':True, **changes}

    def change(self, action, data=None, subject=None, actor=None):
        return mutate_eligibility(actor=actor or self.manager, subject_id=(subject or self.owner).pk,
                                  action=action, changes=data or self.payload())

    def apply(self, data=None, user=None):
        user = user or self.owner
        return self.change('apply', data or self.own_payload(), subject=user, actor=user)

    def approve(self):
        self.apply()
        return self.change('approve', self.payload(1))


class EligibilityServiceTests(EligibilityFixtures):
    def test_default_read_has_no_writes_or_false_age_claim(self):
        with CaptureQueriesContext(connection) as queries:
            data = own_eligibility(self.owner)
        self.assertEqual(data['administrative_status'], 'not_requested')
        self.assertEqual(data['eligibility_version'], 0)
        self.assertEqual(data['age_assurance_status'], 'not_performed')
        self.assertTrue(data['can_apply'])
        self.assertFalse(data['adult_access_allowed'])
        self.assertFalse(Eligibility.objects.exists())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))

    def test_apply_replay_and_pending_noop_preserve_time_and_one_state(self):
        payload = self.own_payload()
        self.apply(payload)
        before = Eligibility.objects.get(user=self.owner)
        replay = self.apply(payload)
        self.assertTrue(replay['replayed'])
        result = self.apply(self.own_payload(1))
        self.assertFalse(result['changed'])
        after = Eligibility.objects.get(user=self.owner)
        self.assertEqual(before.requested_at, after.requested_at)
        self.assertEqual(after.authorization_source, 'manual')
        self.assertEqual(after.eligibility_version, 1)
        self.assertEqual((Eligibility.objects.count(), Event.objects.count()), (1, 2))
        with self.assertRaises(EligibilityConflict):
            self.apply({**payload, 'operation_id':uuid4()})

    def test_approve_requires_user_application_and_legacy_routes_are_disabled(self):
        for action in ('approve', 'reject'):
            with self.assertRaises(EligibilityConflict): self.change(action)
        for action in ('prepare', 'grant', 'renew'):
            with self.assertRaises(ValidationError): self.change(action)
        self.assertFalse(Eligibility.objects.exists())
        # Even patching an external proof cannot silently reinstate the old path.
        with patch('recommendations.age_assurance.current_age_assurance', side_effect=AssertionError('not called')):
            self.approve()
            self.assertTrue(own_eligibility(self.owner)['manual_authorization_valid'])

    def test_only_fresh_active_superuser_approves_and_self_approval_is_forbidden(self):
        self.apply()
        for action in ('approve', 'reject', 'revoke'):
            with self.assertRaises(PermissionDenied):
                self.change(action, self.payload(1), actor=self.other)
        self.manager.is_superuser = False
        self.manager.save(update_fields=['is_superuser'])
        self.manager.is_superuser = True  # stale in-memory actor must not bypass DB
        with self.assertRaises(PermissionDenied): self.change('approve', self.payload(1))
        self.manager.is_superuser = True; self.manager.save(update_fields=['is_superuser'])
        self.apply(user=self.manager)
        with self.assertRaises(PermissionDenied):
            self.change('approve', self.payload(1), subject=self.manager)

    def test_admin_approval_has_30_day_cap_no_age_material_and_stays_closed(self):
        self.apply()
        for field, value in (('days',31), ('user_id',self.other.pk), ('status','active'),
                             ('assurance_provider','manual-as-proof'), ('expected_version',True)):
            with self.assertRaises(ValidationError):
                self.change('approve', self.payload(1, **{field:value}))
        result = self.change('approve', self.payload(1))
        data = result['eligibility']
        row = Eligibility.objects.get(user=self.owner)
        self.assertTrue(data['manual_authorization_valid'])
        self.assertEqual(data['age_assurance_status'], 'not_performed')
        self.assertFalse(data['adult_access_allowed'])
        self.assertEqual(row.expires_at-row.granted_at, timedelta(days=30))
        self.assertEqual((row.assurance_provider,row.assurance_reference,row.assurance_version,row.assurance_valid_until), ('','',0,None))
        with self.assertRaises(EligibilityConflict): self.apply(self.own_payload(2))

    def test_rejection_separates_internal_reason_and_public_message_and_can_reapply(self):
        self.apply()
        result = self.change('reject', self.payload(1, public_message='请核对申请后重试。'))
        self.assertEqual(result['eligibility']['decision_message'], '请核对申请后重试。')
        self.assertNotIn('内部原因', str(result))
        self.assertTrue(result['eligibility']['can_apply'])
        self.apply(self.own_payload(2))
        row = Eligibility.objects.get(user=self.owner)
        self.assertEqual((row.status,row.eligibility_version,row.decision_message), ('pending',3,''))
        self.assertIsNone(row.decided_at)

    def test_expiry_is_readonly_and_requires_a_new_application(self):
        self.approve()
        row = Eligibility.objects.get(user=self.owner)
        before = list(Eligibility.objects.values())
        with patch('recommendations.eligibility.timezone.now', return_value=row.expires_at), CaptureQueriesContext(connection) as queries:
            data = own_eligibility(self.owner)
        self.assertEqual(data['effective_status'], 'expired')
        self.assertTrue(data['can_apply'])
        self.assertEqual(before, list(Eligibility.objects.values()))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        with patch('recommendations.eligibility.timezone.now', return_value=row.expires_at):
            self.apply(self.own_payload(2))
        self.assertEqual(Eligibility.objects.get(user=self.owner).status, 'pending')

    def test_disabled_subject_and_admin_are_denied(self):
        self.apply()
        self.owner.is_active=False; self.owner.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied): self.change('approve', self.payload(1))
        with self.assertRaises(PermissionDenied): self.apply(self.own_payload(1))
        self.assertEqual(own_eligibility(self.owner)['effective_status'], 'account_inactive')
        self.manager.is_active=False; self.manager.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied): self.change('reject', self.payload(1))

    def test_duplicate_approval_after_revoke_never_restores_and_uuid_body_conflicts(self):
        self.apply()
        payload=self.payload(1)
        self.change('approve',payload)
        revoked=self.change('revoke',self.payload(2))
        replay=self.change('approve',payload)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['eligibility'],revoked['eligibility'])
        self.assertEqual(Event.objects.count(),3)
        with self.assertRaises(EligibilityConflict):
            self.change('approve',{**payload,'reason':'不同内容'})

    def test_delayed_original_application_cannot_replace_an_approval(self):
        payload=self.own_payload()
        self.apply(payload)
        self.change('approve',self.payload(1))
        replay=self.apply(payload)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['eligibility']['effective_status'],'active')
        self.assertFalse(replay['eligibility']['can_apply'])
        self.assertEqual(Event.objects.count(),2)

    def test_stop_tombstone_blocks_stale_approval_and_other_user_actions(self):
        stop=self.change('self_revoke',self.own_payload(),actor=self.owner)
        self.assertEqual(stop['eligibility']['eligibility_version'],1)
        with self.assertRaises(EligibilityConflict): self.apply()
        for action in ('apply','self_revoke'):
            with self.assertRaises(PermissionDenied):
                self.change(action,self.own_payload(),subject=self.other,actor=self.owner)
        noop=self.change('self_revoke',self.own_payload(1),actor=self.owner)
        self.assertFalse(noop['changed'])

    def test_audit_failure_rolls_back_application_and_approval(self):
        with patch('recommendations.eligibility.Event.objects.create', side_effect=DatabaseError('synthetic')), self.assertRaises(DatabaseError):
            self.apply()
        self.assertFalse(Eligibility.objects.exists())
        self.apply()
        with patch('recommendations.eligibility.Event.objects.create', side_effect=DatabaseError('synthetic')), self.assertRaises(DatabaseError):
            self.change('approve',self.payload(1))
        self.assertEqual(Eligibility.objects.get(user=self.owner).status,'pending')
        self.assertEqual(Event.objects.count(),1)

    def test_database_constraints_no_false_proof_duplicate_or_unknown_states(self):
        self.apply()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Eligibility.objects.create(user=self.owner)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Eligibility.objects.filter(user=self.owner).update(status='active')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Eligibility.objects.filter(user=self.owner).update(assurance_provider='fake-verified')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Eligibility.objects.filter(user=self.owner).update(status='unknown')

    def test_version_exhaustion_fails_without_new_receipt(self):
        self.apply()
        Eligibility.objects.filter(user=self.owner).update(eligibility_version=2147483647)
        with self.assertRaises(EligibilityConflict):
            self.change('approve',self.payload(2147483647))
        self.assertEqual(Event.objects.count(),1)

    def test_all_journal_preference_feedback_and_other_user_fields_unchanged(self):
        before=[list(model.objects.values()) for model in (UserAnimeRecord,RecommendationPreference,UserRecommendationFeedbackState)]
        other=own_eligibility(self.other)
        self.approve();self.change('revoke',self.payload(2))
        self.assertEqual(before,[list(model.objects.values()) for model in (UserAnimeRecord,RecommendationPreference,UserRecommendationFeedbackState)])
        self.assertEqual(other,own_eligibility(self.other))


class EligibilityApiTests(EligibilityFixtures):
    def test_approval_never_opens_adult_unknown_detail_want_or_scope(self):
        self.approve()
        for identity,rating in ((9001,True),(9002,None)):
            RecommendationAnime.objects.create(bangumi_id=identity,title_zh='合成受限作品',nsfw=rating,tags='音乐')
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code,404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/',{},format='json').status_code,404)
        self.assertFalse(self.client.get('/api/recommendations/eligibility/').data['adult_access_allowed'])
        self.assertEqual(self.client.get('/api/recommendations/',{'content_scope':'include_adult'}).status_code,403)

    def test_own_status_private_no_store_and_no_other_user_parameters(self):
        data=self.client.get('/api/recommendations/eligibility/')
        self.assertEqual(data.status_code,200)
        self.assertEqual(data['Cache-Control'],'private, no-store')
        self.assertIn('Authorization',data['Vary'])
        for field in ('assurance_reference','reason','user_id','granted_by'):
            self.assertNotIn(field,data.data)
        self.apply(user=self.other)
        self.assertEqual(self.client.get('/api/recommendations/eligibility/',{'user_id':self.other.pk}).status_code,400)
        self.assertEqual(self.client.get('/api/recommendations/eligibility/').data['eligibility_version'],0)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/recommendations/eligibility/').status_code,401)

    def test_application_api_is_strict_confirmed_idempotent_and_not_an_approval(self):
        url='/api/recommendations/eligibility/apply/'
        payload={**self.own_payload(),'operation_id':str(uuid4())}
        for field,value in [('user_id',self.other.pk),('status','active'),('days',30),('confirmed',False),('confirmed','true'),('expected_version',True)]:
            self.assertEqual(self.client.post(url,{**payload,field:value},format='json').status_code,400)
        self.assertEqual(self.client.post(url+'?user_id=1',payload,format='json').status_code,400)
        first=self.client.post(url,payload,format='json')
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.data['eligibility']['effective_status'],'pending')
        self.assertTrue(self.client.post(url,payload,format='json').data['replayed'])
        self.assertEqual(Event.objects.count(),1)
        self.assertFalse(Eligibility.objects.filter(user=self.other).exists())
        self.assertEqual(self.client.post('/api/recommendations/eligibility/',{'status':'active'},format='json').status_code,405)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(url,payload,format='json').status_code,401)

    def test_self_stop_is_versioned_and_cannot_be_used_for_other_accounts(self):
        url='/api/recommendations/eligibility/revoke/'
        payload={**self.own_payload(),'operation_id':str(uuid4())}
        self.assertEqual(self.client.post(url,{**payload,'user_id':self.other.pk},format='json').status_code,400)
        self.assertEqual(self.client.post(url,payload,format='json').status_code,200)
        self.assertTrue(self.client.post(url,payload,format='json').data['replayed'])

    def test_missing_migration_returns_503_never_fake_empty_or_qualified(self):
        with patch('recommendations.eligibility.Eligibility.objects.filter',side_effect=DatabaseError('not migrated')):
            response=self.client.get('/api/recommendations/eligibility/')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('not migrated',str(response.data))


class EligibilityAdminTests(EligibilityFixtures):
    def setUp(self):
        super().setUp()
        self.browser=Client()
        self.url=reverse('admin:recommendations_eligibility_control')

    def form_payload(self,action='approve'):
        page=self.browser.get(self.url,{'user_id':self.owner.pk})
        self.assertEqual(page.status_code,200)
        return {'ticket':page.context['form'].initial['ticket'],'action':action,'days':30,
                'reason':'内部审批原因','confirmed':'on','public_message':''}

    def test_only_superuser_can_view_control_state_and_events(self):
        self.browser.force_login(self.other)
        for url in [self.url,reverse('admin:recommendations_recommendationcontenteligibility_changelist'),
                    reverse('admin:recommendations_recommendationeligibilityevent_changelist')]:
            self.assertEqual(self.browser.get(url).status_code,403)
        self.browser.force_login(self.manager)
        self.assertContains(self.browser.get(reverse('admin:recommendations_recommendationcontenteligibility_changelist')),'核对账号与访问申请')
        self.assertFalse(Eligibility.objects.exists())

    def test_explicit_application_signed_confirmation_and_no_bulk_or_raw_edit(self):
        self.browser.force_login(self.manager)
        payload=self.form_payload()
        self.assertContains(self.browser.post(self.url,payload),'必须先有用户')
        self.apply()
        payload=self.form_payload()
        self.assertEqual(self.browser.post(self.url,{**payload,'confirmed':''}).status_code,200)
        self.assertEqual(Eligibility.objects.get(user=self.owner).status,'pending')
        self.assertEqual(self.browser.post(self.url,payload).status_code,302)
        self.assertEqual(self.browser.post(self.url,payload).status_code,302)
        self.assertEqual(Event.objects.count(),2)
        row=Eligibility.objects.get(user=self.owner)
        fake=type('Request',(),{'user':self.manager})()
        for controller in (admin.site._registry[Eligibility],admin.site._registry[Event]):
            self.assertIsNone(controller.actions)
            self.assertFalse(controller.has_add_permission(fake))
            self.assertFalse(controller.has_change_permission(fake))
            self.assertFalse(controller.has_delete_permission(fake))
        url=reverse('admin:recommendations_recommendationcontenteligibility_change',args=[row.pk])
        self.assertEqual(self.browser.post(url,{'status':'active'}).status_code,403)

    def test_ticket_expiry_tampering_other_actor_legacy_salt_and_csrf_are_rejected(self):
        self.browser.force_login(self.manager)
        payload=self.form_payload()
        for ticket in ['tampered',signing.dumps({'actor':self.other.pk,'subject':self.owner.pk,'version':0,'operation':str(uuid4())},salt=ADMIN_SALT),
                       signing.dumps({'actor':self.manager.pk,'subject':self.owner.pk,'version':0,'operation':str(uuid4())},salt='recommendations.eligibility-admin.n2-1.v1')]:
            self.assertContains(self.browser.post(self.url,{**payload,'ticket':ticket}),'凭据无效')
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.manager)
        self.assertEqual(strict.post(self.url,payload).status_code,403)
        with patch.object(signing.TimestampSigner,'timestamp',return_value='1'):
            expired=signing.dumps({'actor':self.manager.pk,'subject':self.owner.pk,'version':0,'operation':str(uuid4())},salt=ADMIN_SALT)
        self.assertContains(self.browser.post(self.url,{**payload,'ticket':expired}),'凭据无效')
        self.assertFalse(Eligibility.objects.exists())


class EligibilityConcurrencyTests(EligibilityFixtures):
    def race(self,tasks):
        barrier=Barrier(len(tasks))
        def worker(task):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return ('ok',task())
            except EligibilityConflict:
                return ('conflict',None)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            return list(pool.map(worker,tasks))

    def test_same_uuid_application_coalesces_one_row_and_receipt(self):
        payload=self.own_payload()
        results=self.race([lambda:self.apply(payload)]*2)
        self.assertEqual([kind for kind,_ in results],['ok','ok'])
        self.assertEqual(sum(result['replayed'] for _,result in results),1)
        self.assertEqual((Eligibility.objects.count(),Event.objects.count()),(1,1))

    def test_different_uuid_same_version_application_conflicts(self):
        results=self.race([lambda:self.apply()]*2)
        self.assertEqual(sorted(kind for kind,_ in results),['conflict','ok'])
        self.assertEqual(Event.objects.count(),1)

    def test_duplicate_approval_is_idempotent(self):
        self.apply();payload=self.payload(1)
        results=self.race([lambda:self.change('approve',payload)]*2)
        self.assertEqual(sum(result['replayed'] for _,result in results),1)
        self.assertEqual(Event.objects.count(),2)

    def test_approve_and_reject_same_pending_version_only_one_commits(self):
        self.apply()
        results=self.race([lambda:self.change('approve',self.payload(1)),lambda:self.change('reject',self.payload(1))])
        self.assertEqual(sorted(kind for kind,_ in results),['conflict','ok'])
        row=Eligibility.objects.get(user=self.owner)
        self.assertEqual(row.eligibility_version,2)
        self.assertEqual(row.status,Event.objects.first().resulting_status)

    def test_approve_and_owner_stop_only_one_commits(self):
        self.apply()
        results=self.race([lambda:self.change('approve',self.payload(1)),
                           lambda:self.change('self_revoke',self.own_payload(1),actor=self.owner)])
        self.assertEqual(sorted(kind for kind,_ in results),['conflict','ok'])
        self.assertEqual(Eligibility.objects.get(user=self.owner).eligibility_version,2)

    def test_revoke_and_existing_want_preserve_all_journal_fields(self):
        self.approve()
        before=UserAnimeRecord.objects.filter(pk=self.record.pk).values().get()
        results=self.race([lambda:self.change('revoke',self.payload(2)),lambda:add_want(user=self.owner,bangumi_id=800)])
        self.assertEqual([kind for kind,_ in results],['ok','ok'])
        self.assertEqual(UserAnimeRecord.objects.filter(pk=self.record.pk).values().get(),before)

    def test_admin_revoked_while_waiting_user_lock_cannot_commit_approval(self):
        self.apply()
        application='m1-approval-lock-'+uuid4().hex
        def writer():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('application_name',%s,false)",[application])
                try: self.change('approve',self.payload(1))
                except PermissionDenied: return 403
                return 200
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=self.owner.pk)
                pending=pool.submit(writer)
                waiting=False; deadline=monotonic()+5
                while monotonic()<deadline:
                    with connection.cursor() as cursor:
                        # This observer holds the target row in a transaction;
                        # refresh pg_stat_activity's cached statistics snapshot.
                        cursor.execute('SELECT pg_stat_clear_snapshot()')
                        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock')",[application])
                        waiting=cursor.fetchone()[0]
                    if waiting: break
                    sleep(.02)
                self.assertTrue(waiting,'approval did not reach actual PostgreSQL row lock contention')
                get_user_model().objects.filter(pk=self.manager.pk).update(is_superuser=False)
            self.assertEqual(pending.result(timeout=15),403)
        self.assertEqual(Eligibility.objects.get(user=self.owner).status,'pending')
        self.assertEqual(Event.objects.count(),1)


class EligibilityMigrationTests(TransactionTestCase):
    def test_migration_preserves_legacy_records_receipts_and_never_auto_approves(self):
        self.assertEqual(connection.vendor,'postgresql')
        self.assertEqual(connection.settings_dict['NAME'],'test_recommendation_isolated_tests')
        executor=MigrationExecutor(connection)
        leaves=executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([('recommendations','0008_content_review')])
            apps=executor.loader.project_state([('recommendations','0008_content_review')]).apps
            User=apps.get_model('auth','User')
            user=User.objects.create(username='legacy-migration')
            Old=apps.get_model('recommendations','RecommendationContentEligibility')
            now=timezone.now()
            row=Old.objects.create(user_id=user.pk,status='active',eligibility_version=7,
                granted_at=now,expires_at=now+timedelta(days=2),assurance_provider='old-provider',
                assurance_reference='old-opaque-ref',assurance_version=3,assurance_valid_until=now+timedelta(days=3))
            OldEvent=apps.get_model('recommendations','RecommendationEligibilityEvent')
            OldEvent.objects.create(eligibility_id=row.pk,operation_id=uuid4(),action='grant',request_fingerprint='f'*64,
                previous_status='pending',resulting_status='active',previous_version=6,applied_version=7,
                changed=True,reason='历史合成事件',resulting_expires_at=row.expires_at)
            executor=MigrationExecutor(connection);executor.migrate(leaves)
            row=Eligibility.objects.get(pk=row.pk)
            self.assertEqual((row.authorization_source,row.eligibility_version,row.assurance_reference),('legacy',7,'old-opaque-ref'))
            self.assertIsNone(row.requested_at)
            self.assertEqual(Event.objects.count(),1)
            actual=get_user_model().objects.get(pk=user.pk)
            self.assertEqual(own_eligibility(actual)['effective_status'],'legacy_unavailable')
            self.assertFalse(own_eligibility(actual)['manual_authorization_valid'])
            self.assertTrue(own_eligibility(actual)['can_apply'])
        finally:
            MigrationExecutor(connection).migrate(leaves)
