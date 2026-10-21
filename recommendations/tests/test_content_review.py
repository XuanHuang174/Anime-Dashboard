"""Synthetic-only review ledger/admin/closed-chain regression on local PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event as Signal
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.db import close_old_connections, connection, IntegrityError, transaction
from django.test import Client, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from anime.models import UserAnimeRecord
from recommendations import content_review as service
from recommendations.content_review_admin import SALT
from recommendations.content_scope import require_scope
from recommendations.models import (RecommendationAnime, RecommendationPreference,
    RecommendationContentReview as Review, RecommendationContentReviewEvent as Event,
    RecommendationContentReviewState as State, UserRecommendationFeedback as Feedback)
from recommendations.tests import test_content_scope_n2 as scope_fixtures
from recommendations.want import add_want
from recommendations.feedback import mutate_feedback
from rest_framework.exceptions import APIException


class ReviewFixtures(TransactionTestCase):
    def setUp(self):
        scope_fixtures.ClosedScopeTests.setUp(self)
        self.reviewer=get_user_model().objects.create_user(username='review-manager',is_staff=True)
        self.reviewer.user_permissions.add(Permission.objects.get(codename='manage_content_review'))
        self.adult=RecommendationAnime.objects.get(bangumi_id=90)
        RecommendationAnime.objects.create(bangumi_id=92,title_zh='合成待审核作品',nsfw=True,tags='音乐')

    def payload(self,version=0,work=90,**changes):
        return {'expected_version':version,'expected_catalog_revision':service.catalog_revision(
            RecommendationAnime.objects.get(bangumi_id=work)), 'expected_policy_version':service.POLICY_VERSION,
            'operation_id':uuid4(),'reason':'隔离人工审核合成记录',**changes}

    def change(self,action='prepare',version=0,work=90,payload=None,actor=None):
        return service.mutate_review(actor=actor or self.reviewer,bangumi_id=work,action=action,
            changes=payload if payload is not None else self.payload(version,work))

    def approve(self,version=0,work=90):
        return self.change('approve',work=work,payload=self.payload(version,work,
            evidence_reference='internal-synthetic-only',exclusions_confirmed=True))

    def page(self,**query): return scope_fixtures.ClosedScopeTests.page(self,**query)


class ReviewServiceTests(ReviewFixtures):
    def test_absence_is_pending_no_reads_create_rows_or_claim_access(self):
        with CaptureQueriesContext(connection) as queries:
            data=service.release_decision(self.adult)
            scope=require_scope(self.owner)
        self.assertEqual(data['effective_status'],'unreviewed'); self.assertFalse(data['review_valid'])
        self.assertFalse(data['adult_access_allowed']); self.assertEqual(scope['content_review_version'],0)
        self.assertFalse(Review.objects.exists()); self.assertFalse(Event.objects.exists()); self.assertFalse(State.objects.exists())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ','UPDATE ','DELETE ')) for q in queries))

    def test_dedicated_permission_and_fresh_permission_revocation(self):
        for user in (self.owner,self.other):
            with self.assertRaises(PermissionDenied): self.change(actor=user)
        self.assertTrue(self.reviewer.has_perm(service.MANAGE_PERMISSION))
        self.reviewer.user_permissions.clear()
        with self.assertRaises(PermissionDenied): self.change()
        self.assertFalse(Review.objects.exists())

    def test_prepare_noop_receipt_and_monotonic_epoch(self):
        first=self.change(); self.assertTrue(first['changed'])
        scope=require_scope(self.owner)
        self.assertEqual(scope['content_review_version'],1)
        second=self.change(version=1)
        self.assertFalse(second['changed']); self.assertEqual(require_scope(self.owner),scope)
        self.assertEqual(Event.objects.count(),2)
        self.change('reject',version=1)
        self.assertEqual(State.objects.get().version,2)

    def test_unknown_safe_or_missing_evidence_never_approved(self):
        for work in (1,91):
            with self.assertRaises(ValidationError): self.approve(work=work)
        for changes in ({'evidence_reference':'internal'}, {'exclusions_confirmed':True},
                        {'exclusions_confirmed':False,'evidence_reference':'internal'}):
            with self.assertRaises(ValidationError): self.change('approve',payload=self.payload(**changes))
        self.assertFalse(Review.objects.exists()); self.assertFalse(State.objects.exists())

    def test_strict_inputs_old_policy_and_changed_catalog_are_rejected(self):
        for changes in ({'user_id':1},{'status':'approved'},{'days':31},{'expected_version':True},
                        {'evidence_reference':'https://external.invalid/file'}):
            with self.assertRaises(ValidationError): self.change(payload=self.payload(**changes))
        for changes in ({'expected_catalog_revision':'0'*64},{'expected_policy_version':'old-policy'}):
            with self.assertRaises(service.ReviewConflict): self.change(payload=self.payload(**changes))
        payload=self.payload(); RecommendationAnime.objects.filter(pk=self.adult.pk).update(synopsis='新合成资料')
        with self.assertRaises(service.ReviewConflict): self.change(payload=payload)
        self.assertFalse(State.objects.exists())

    def test_approval_is_fingerprint_bound_expiring_and_still_closed(self):
        result=self.approve()['review']; self.assertTrue(result['review_valid'])
        self.assertFalse(result['adult_access_allowed']); self.assertFalse(result['serving_enabled'])
        original=list(Review.objects.values()); row=Review.objects.get()
        self.assertFalse(service.release_decision(self.adult,now=row.expires_at)['review_valid'])
        self.assertEqual(service.release_decision(self.adult,now=row.expires_at)['effective_status'],'expired')
        with patch('recommendations.content_review.POLICY_VERSION','new-review-policy'):
            self.assertEqual(service.release_decision(self.adult)['effective_status'],'stale')
        for field,value in [('tags','科幻'),('synopsis','改变的合成资料'),('cover_url','https://new.invalid/cover')]:
            previous=getattr(self.adult,field)
            RecommendationAnime.objects.filter(pk=self.adult.pk).update(**{field:value})
            self.assertEqual(service.release_decision(self.adult)['effective_status'],'stale')
            RecommendationAnime.objects.filter(pk=self.adult.pk).update(**{field:previous})
        RecommendationAnime.objects.filter(pk=self.adult.pk).update(nsfw=None)
        self.assertEqual(service.release_decision(self.adult)['effective_status'],'unknown_rating')
        self.assertEqual(original,list(Review.objects.values()))

    def test_replay_never_resurrects_old_approval_or_accepts_uuid_drift(self):
        payload=self.payload(evidence_reference='internal',exclusions_confirmed=True)
        self.change('approve',payload=payload); self.change('revoke',version=1)
        result=self.change('approve',payload=payload)
        self.assertTrue(result['replayed']); self.assertEqual(result['review']['effective_status'],'revoked')
        self.assertEqual(State.objects.get().version,2)
        with self.assertRaises(service.ReviewConflict): self.change('approve',payload={**payload,'reason':'不同操作内容'})

    def test_audit_failure_rolls_back_review_and_epoch_together(self):
        with patch('recommendations.content_review.Event.objects.create',side_effect=IntegrityError('synthetic-audit-failure')):
            with self.assertRaises(IntegrityError): self.change()
        self.assertFalse(Review.objects.exists()); self.assertFalse(State.objects.exists())

    def test_database_constraints_and_version_overflow(self):
        self.change()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Review.objects.filter(anime=self.adult).update(status='approved')
        with self.assertRaises(IntegrityError), transaction.atomic(): State.objects.create(pk=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.create(**{key:value for key,value in Event.objects.values().get().items() if key!='id'})
        State.objects.filter(pk=1).update(version=2147483647)
        with self.assertRaises(service.ReviewConflict): self.change('revoke',version=1)
        self.assertEqual(Review.objects.get().status,'pending')

    def test_review_updates_invalidate_pages_cursors_evidence_and_old_write_header(self):
        first,browse=self.page(page_size=2),self.page(browse=True,page_size=2)
        row=first['results'][0]; scope=first['content_scope_context']
        self.approve()
        for path,query in [('/api/recommendations/',{'page':2,'page_size':2,'state_token':first['state_token']}),
                           ('/api/recommendations/',{'cursor':browse['next_page_cursor']}),
                           (f'/api/recommendations/{row["bangumi_id"]}/',{'evidence_token':row['evidence_token']})]:
            response=self.client.get(path,query); self.assertEqual(response.status_code,409,response.data)
        response=self.client.post('/api/recommendations/2/want/',{},format='json',HTTP_X_RECOMMENDATION_SCOPE=scope['scope_revision'])
        self.assertEqual(response.status_code,409)
        fresh=self.page(page_size=2)
        self.assertEqual([r['bangumi_id'] for r in first['results']],[r['bangumi_id'] for r in fresh['results']])
        self.assertEqual(fresh['evidence_context']['content_review_version'],1)
        self.assertEqual(fresh['evidence_context']['content_release_policy_version'],service.POLICY_VERSION)

    def test_approval_does_not_open_any_adult_route_or_affect_private_fields(self):
        before=list(UserAnimeRecord.objects.values()); prefs=list(RecommendationPreference.objects.values())
        self.approve()
        for identity in (90,91):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code,404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/',{},format='json').status_code,404)
            response=self.client.post('/api/recommendations/feedback/',{'bangumi_id':identity,
                'expected_feedback_version':0,'operation_id':str(uuid4())},format='json')
            self.assertEqual(response.status_code,404)
        self.assertEqual(self.client.get('/api/recommendations/?content_scope=include_adult').status_code,403)
        self.assertEqual(before,list(UserAnimeRecord.objects.values()))
        self.assertEqual(prefs,list(RecommendationPreference.objects.values())); self.assertFalse(Feedback.objects.exists())
        self.assertFalse({90,91}&{r['bangumi_id'] for r in self.page()['results']})

    def test_review_does_not_create_per_card_queries_or_query_private_text(self):
        self.page()
        with CaptureQueriesContext(connection) as small: self.page(page_size=1)
        with CaptureQueriesContext(connection) as large: self.page(page_size=24)
        self.assertEqual(len(small),len(large))
        self.assertFalse(any('"review"' in q['sql'].lower() for q in large))


class ReviewAdminTests(ReviewFixtures):
    def setUp(self):
        super().setUp(); self.browser=Client()
        self.url=reverse('admin:recommendations_content_review_control')

    def form(self,action='prepare'):
        response=self.browser.get(self.url,{'bangumi_id':90}); self.assertEqual(response.status_code,200)
        return {'ticket':response.context['form'].initial['ticket'],'action':action,
                'reason':'隔离后台人工核对','days':30,'confirmed':'on'}

    def test_special_permission_single_confirmation_and_readonly_admin(self):
        self.browser.force_login(self.owner); self.assertEqual(self.browser.get(self.url).status_code,302)
        self.other.is_staff=True; self.other.save(update_fields=['is_staff'])
        self.browser.force_login(self.other); self.assertEqual(self.browser.get(self.url).status_code,403)
        self.browser.force_login(self.reviewer); data=self.form()
        self.assertFalse(Review.objects.exists())
        self.assertContains(self.browser.post(self.url,{**data,'confirmed':''}),'必填')
        self.assertFalse(Review.objects.exists())
        self.assertEqual(self.browser.post(self.url,data).status_code,302)
        self.assertEqual(self.browser.post(self.url,data).status_code,302)
        self.assertEqual(Event.objects.count(),1)
        request=type('Request',(),{'user':self.reviewer})()
        for model in (Review,Event):
            controller=admin.site._registry[model]
            self.assertIsNone(controller.actions)
            self.assertFalse(controller.has_add_permission(request)); self.assertFalse(controller.has_change_permission(request))
            self.assertFalse(controller.has_delete_permission(request))

    def test_approval_requires_internal_reference_and_exclusions_no_cover_rendering(self):
        self.browser.force_login(self.reviewer)
        payload=self.form('approve')
        response=self.browser.post(self.url,payload)
        self.assertContains(response,'审核通过必须填写/明确确认')
        self.assertNotContains(response,'<img'); self.assertFalse(Review.objects.exists())
        response=self.browser.post(self.url,{**payload,'evidence_reference':'internal-test','exclusions_confirmed':'on'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(service.release_decision(self.adult)['review_valid'])
        self.assertFalse(service.release_decision(self.adult)['adult_access_allowed'])

    def test_signed_target_actor_policy_and_expiry_and_csrf(self):
        self.browser.force_login(self.reviewer); payload=self.form()
        body=signing.loads(payload['ticket'],salt=SALT)
        for changes in ({'actor':self.other.pk},{'work':92,'catalog':'0'*64},{'policy':'old-policy'}):
            result=self.browser.post(self.url,{**payload,'ticket':signing.dumps({**body,**changes},salt=SALT)})
            self.assertEqual(result.status_code,200); self.assertFalse(Review.objects.exists())
        with patch.object(signing.TimestampSigner,'timestamp',return_value='1'):
            expired=signing.dumps(body,salt=SALT)
        self.assertContains(self.browser.post(self.url,{**payload,'ticket':expired}),'无效/过期')
        strict=Client(enforce_csrf_checks=True); strict.force_login(self.reviewer)
        self.assertEqual(strict.post(self.url,payload).status_code,403)
        self.assertFalse(Review.objects.exists())


class ReviewConcurrencyTests(ReviewFixtures):
    def locked_catalog_writer(self, kind):
        scope=require_scope(self.owner)
        locked,proceed=Signal(),Signal()
        application='review-wait-test-'+uuid4().hex
        before=list(UserAnimeRecord.objects.values())
        def reviewer():
            close_old_connections()
            try:
                with transaction.atomic():
                    RecommendationAnime.objects.select_for_update().get(bangumi_id=1)
                    locked.set()
                    if not proceed.wait(10): raise AssertionError('review test release timed out')
                    self.change(work=1)
            finally: close_old_connections()
        def writer():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('application_name',%s,false)",[application])
                try:
                    if kind=='want': add_want(user=self.owner,bangumi_id=1,scope_context=scope)
                    else: mutate_feedback(user=self.owner,action='hide',scope_context=scope,
                        changes={'bangumi_id':1,'expected_feedback_version':0,'operation_id':uuid4()})
                except APIException as exc: return exc.status_code
                return 200
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            reviewing=pool.submit(reviewer)
            try:
                self.assertTrue(locked.wait(10))
                writing=pool.submit(writer)
                waiting=False; deadline=monotonic()+5
                while monotonic()<deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock')",[application])
                        waiting=cursor.fetchone()[0]
                    if waiting: break
                    sleep(.02)
                self.assertTrue(waiting,'writer did not reach catalog row lock contention')
                proceed.set(); reviewing.result(timeout=15)
                self.assertEqual(writing.result(timeout=15),409)
            finally: proceed.set()
        self.assertEqual(before,list(UserAnimeRecord.objects.values()))
        self.assertFalse(Feedback.objects.exists())

    def test_review_during_want_catalog_lock_wait_rejects_old_context(self):
        self.locked_catalog_writer('want')

    def test_review_during_feedback_catalog_lock_wait_rejects_old_context(self):
        self.locked_catalog_writer('feedback')

    def race(self,functions):
        barrier=Barrier(len(functions))
        def run(function):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try: return ('ok',function())
                except service.ReviewConflict: return ('conflict',None)
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool: return list(pool.map(run,functions))

    def test_duplicate_click_same_uuid_has_one_review_receipt_and_epoch(self):
        payload=self.payload(evidence_reference='internal',exclusions_confirmed=True)
        results=self.race([lambda:self.change('approve',payload=payload)]*2)
        self.assertEqual([kind for kind,_ in results],['ok','ok'])
        self.assertEqual(sum(r['replayed'] for _,r in results),1)
        self.assertEqual((Review.objects.count(),Event.objects.count(),State.objects.get().version),(1,1,1))

    def test_approve_and_revoke_same_version_only_one_can_commit(self):
        self.change()
        results=self.race([lambda:self.approve(version=1),lambda:self.change('revoke',version=1)])
        self.assertEqual(sorted(kind for kind,_ in results),['conflict','ok'])
        self.assertEqual((Event.objects.count(),State.objects.get().version),(2,2))

    def test_different_works_first_state_creation_preserves_both_global_updates(self):
        results=self.race([lambda:self.approve(work=90),lambda:self.approve(work=92)])
        self.assertEqual([kind for kind,_ in results],['ok','ok'])
        self.assertEqual((Review.objects.count(),Event.objects.count(),State.objects.get().version),(2,2,2))
