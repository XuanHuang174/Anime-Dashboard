"""N2-2 closed serving regression. ONLY synthetic local PostgreSQL data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event as Signal
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, close_old_connections, DatabaseError, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations import engine, browsing, deployment
from recommendations.age_assurance import AgeAssurance
from recommendations.content_scope import require_scope, ensure_scope
from recommendations.eligibility import mutate_eligibility
from recommendations.evidence import EvidencePresenter
from recommendations.feedback import mutate_feedback
from recommendations.models import (RecommendationAnime, RecommendationPreference,
    RecommendationContentEligibility as Eligibility, RecommendationEligibilityEvent as EligibilityEvent,
    UserRecommendationFeedback as Feedback)
from recommendations.serializers import RecommendationQuerySerializer
from recommendations.want import add_want


@override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
class ClosedScopeTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='n2-owner')
        self.other = get_user_model().objects.create_user(username='n2-other')
        self.manager = get_user_model().objects.create_superuser(username='n2-manager', password=None)
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'], version=2)
        for identity in range(1, 43):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'合成作品{identity}',
                score=7 + identity / 100, tags='音乐,成长' if identity % 2 else '音乐,日常',
                nsfw=False, season='2026-01', status_type='TV')
        for identity, rating in ((90, True), (91, None)):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh='RESTRICTED_SENTINEL',
                tags='音乐', nsfw=rating, score=10, cover_url='https://unsafe.example.test/COVER_SENTINEL')
        shared = Anime.objects.create(bgm_id=1, chinese_title='已有合成记录', base_tags=['音乐'])
        self.record = UserAnimeRecord.objects.create(user=self.owner, anime=shared,
            personal_score=0, review='PRIVATE_REVIEW_SENTINEL', custom_tags=['PRIVATE_TAG_SENTINEL'],
            watch_status='on_hold')

    def change(self, user=None, action='prepare', version=0):
        return mutate_eligibility(actor=self.manager, subject_id=(user or self.owner).pk, action=action,
            changes={'expected_version': version, 'operation_id': uuid4(), 'reason': '隔离合成测试'})

    def page(self, **query):
        response = self.client.get('/api/recommendations/', query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_explicit_safe_is_default_equivalent_and_does_not_change_v1_ranking(self):
        first, explicit = self.page(page_size=24), self.page(page_size=24, content_scope='safe')
        self.assertEqual(first, explicit)
        query = RecommendationQuerySerializer(data={'page_size':24}); self.assertTrue(query.is_valid())
        context, index = engine.user_context(self.owner), engine.public_features()
        for category in ('all', 'familiar', 'explore'):
            options = {**query.validated_data, 'category':category}
            with_scope = engine.rank_context(context, options, index)[0]
            old_context = {key:value for key,value in context.items() if key != '_content_scope'}
            without_scope = engine.rank_context(old_context, options, index)[0]
            self.assertEqual([(r['anime'].bangumi_id,r['value'],r['reason'],r['evidence']) for r in with_scope],
                             [(r['anime'].bangumi_id,r['value'],r['reason'],r['evidence']) for r in without_scope])

    def test_adult_scope_denied_before_any_database_lookup_on_all_scoped_routes(self):
        for user in (self.owner, self.manager):
            self.client.force_authenticate(user)
            for method,path in [('get',''),('get','2/'),('post','2/want/'),('post','feedback/'),
                                ('post','feedback/undo/'),('get','feedback/hidden/'),('get','context/')]:
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)('/api/recommendations/'+path+'?content_scope=include_adult',
                                                            {}, format='json')
                self.assertEqual(response.status_code, 403, response.data)
                self.assertEqual(response.data['code'], 'adult_scope_closed')
                self.assertEqual(len(queries), 0)
                self.assertNotIn('RESTRICTED_SENTINEL', str(response.data))

    def test_unknown_duplicate_and_client_authority_parameters_remain_400(self):
        for suffix in ('content_scope=all', 'content_scope=', 'content_scope=safe&content_scope=safe',
                       'content_scope=safe&nsfw=true', 'user_id=1', 'age=99', 'algorithm_version=content.v2'):
            self.assertEqual(self.client.get('/api/recommendations/?'+suffix).status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/recommendations/?content_scope=include_adult').status_code, 401)

    def test_qualification_reads_have_no_side_effects_or_private_proof_material(self):
        original = list(UserAnimeRecord.objects.values())
        with CaptureQueriesContext(connection) as queries:
            first = require_scope(self.owner)
            self.assertEqual(first, require_scope(self.owner))
        self.assertEqual(len(queries), 2)
        sql = '\n'.join(row['sql'].lower() for row in queries)
        for text in ('review', 'assurance_reference', 'password', 'insert ', 'update ', 'delete '):
            self.assertNotIn(text, sql)
        self.assertFalse(Eligibility.objects.exists()); self.assertFalse(EligibilityEvent.objects.exists())
        self.assertEqual(original, list(UserAnimeRecord.objects.values()))
        self.assertNotEqual(first, require_scope(self.other))

    def test_other_users_qualification_and_noop_do_not_invalidate_owner(self):
        original = self.page(page_size=2)
        self.change(user=self.other)
        self.assertEqual(self.page(page_size=2), original)
        self.change()
        current = require_scope(self.owner)
        self.change(version=1)
        self.assertEqual(current, require_scope(self.owner))

    def test_qualification_change_invalidates_legacy_token_and_evidence_not_results(self):
        first = self.page(page_size=2)
        row = first['results'][0]
        self.change()
        second = self.client.get('/api/recommendations/', {'page':2,'page_size':2,'state_token':first['state_token']})
        self.assertEqual(second.status_code, 409)
        detail = self.client.get(f'/api/recommendations/{row["bangumi_id"]}/', {'evidence_token':row['evidence_token']})
        self.assertEqual(detail.status_code, 409); self.assertNotIn('evidence', detail.data)
        fresh = self.page(page_size=2)
        self.assertEqual([r['bangumi_id'] for r in fresh['results']], [r['bangumi_id'] for r in first['results']])
        self.assertEqual(fresh['content_scope_context']['eligibility_version'], 1)

    def test_fixed_browse_cursor_retries_and_cross_user_scope_isolation(self):
        first = self.page(browse=True, page_size=2)
        cursor = first['next_page_cursor']; self.assertTrue(cursor)
        a, b = self.page(cursor=cursor), self.page(cursor=cursor)
        self.assertEqual(a,b)
        self.assertFalse({r['bangumi_id'] for r in first['results']} & {r['bangumi_id'] for r in a['results']})
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/recommendations/', {'cursor':cursor}).status_code, 409)
        self.client.force_authenticate(self.owner); self.change()
        self.assertEqual(self.client.get('/api/recommendations/', {'cursor':cursor}).status_code, 409)

    def test_scope_policy_and_release_policy_changes_invalidate_all_old_contexts(self):
        for constant in ('POLICY_VERSION','RELEASE_POLICY_VERSION'):
            first, browse = self.page(page_size=2), self.page(browse=True,page_size=2)
            row = first['results'][0]
            with patch('recommendations.content_scope.'+constant, 'closed-next'):
                self.assertEqual(self.client.get('/api/recommendations/', {'cursor':browse['next_page_cursor']}).status_code, 409)
                self.assertEqual(self.client.get('/api/recommendations/', {'page':2,'page_size':2,'state_token':first['state_token']}).status_code, 409)
                self.assertEqual(self.client.get(f'/api/recommendations/{row["bangumi_id"]}/', {'evidence_token':row['evidence_token']}).status_code, 409)

    def test_scope_change_during_read_drops_payload_after_readonly_transaction(self):
        original = deployment.browse_page
        def late_change(*args, **kwargs):
            page = original(*args, **kwargs)
            self.assertFalse(connection.in_atomic_block)
            self.change()
            return page
        with patch('recommendations.deployment.browse_page', side_effect=late_change):
            result = self.client.get('/api/recommendations/', {'browse':True})
        self.assertEqual(result.status_code, 409)
        self.assertNotIn('results', result.data); self.assertNotIn('evidence', result.data)
        self.assertEqual(result['Cache-Control'], 'private, no-store')

    def test_missing_qualification_storage_fails_closed_without_fake_empty_results(self):
        with patch('recommendations.views.require_scope', side_effect=DatabaseError('synthetic-missing-schema')):
            response = self.client.get('/api/recommendations/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('results', response.data)

    @override_settings(CORS_ALLOWED_ORIGINS=['https://authorized.example.test'])
    def test_optional_scope_header_is_usable_from_allowed_origin_only(self):
        for origin,allowed in [('https://authorized.example.test',True),('https://untrusted.example.test',False)]:
            response = self.client.options('/api/recommendations/2/want/',
                HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
                HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type,x-recommendation-scope')
            self.assertEqual(response.get('Access-Control-Allow-Origin') == origin,allowed)
            if allowed:
                self.assertIn('x-recommendation-scope',response['Access-Control-Allow-Headers'])

    def test_current_precondition_accepts_normal_want_without_changing_existing_record(self):
        before=list(UserAnimeRecord.objects.values())
        response=self.client.post('/api/recommendations/1/want/?content_scope=safe',{},format='json',
            HTTP_X_RECOMMENDATION_SCOPE=require_scope(self.owner)['scope_revision'])
        self.assertEqual(response.status_code,200)
        self.assertEqual(before,list(UserAnimeRecord.objects.values()))

    def test_old_predeployment_snapshot_without_scope_cannot_continue(self):
        from django.core import signing
        first=self.page(browse=True,page_size=2)
        body=signing.loads(first['next_page_cursor'],salt=browsing.CURSOR_SALT)
        body['revision']='0'*64  # valid signed legacy/other-context body, no matching scope revision
        old=signing.dumps(body,salt=browsing.CURSOR_SALT,compress=True)
        response=self.client.get('/api/recommendations/',{'cursor':old})
        self.assertEqual(response.status_code,409)

    def test_optional_precondition_and_locked_services_reject_stale_or_foreign_scope(self):
        scope = require_scope(self.owner)
        for revision,status in [('bad',400),(require_scope(self.other)['scope_revision'],409)]:
            response = self.client.post('/api/recommendations/2/want/', {}, format='json',
                                        HTTP_X_RECOMMENDATION_SCOPE=revision)
            self.assertEqual(response.status_code,status)
        self.change()
        for callback in [lambda:add_want(user=self.owner,bangumi_id=2,scope_context=scope),
                         lambda:mutate_feedback(user=self.owner,action='hide',scope_context=scope,
                             changes={'bangumi_id':2,'expected_feedback_version':0,'operation_id':uuid4()})]:
            with self.assertRaises(APIException) as error: callback()
            self.assertEqual(error.exception.status_code,409)
        self.assertEqual(UserAnimeRecord.objects.count(),1); self.assertFalse(Feedback.objects.exists())

    def test_even_valid_synthetic_qualification_cannot_open_adult_unknown_or_covers(self):
        now = timezone.now()
        proof = AgeAssurance(self.owner.pk, 'verified', 'isolated-only', 'test-ref', 1, now+timedelta(days=5))
        with patch('recommendations.age_assurance.current_age_assurance',return_value=proof):
            self.change(action='grant')
            with override_settings(RECOMMENDATION_ADULT_CONTENT_ENABLED=True):
                self.assertEqual(self.client.get('/api/recommendations/?content_scope=include_adult').status_code,403)
                for identity in (90,91):
                    self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code,404)
                    self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/',{},format='json').status_code,404)
                    result=self.client.post('/api/recommendations/feedback/',{'bangumi_id':identity,
                        'expected_feedback_version':0,'operation_id':str(uuid4())},format='json')
                    self.assertEqual(result.status_code,404)
        self.assertNotIn('COVER_SENTINEL',str(self.page()))

    def test_natural_expiry_invalidates_revision_but_never_grants_or_writes_on_read(self):
        now=timezone.now()
        proof=AgeAssurance(self.owner.pk,'verified','isolated-only','test-ref',1,now+timedelta(days=5))
        with patch('recommendations.age_assurance.current_age_assurance',return_value=proof): self.change(action='grant')
        scope=require_scope(self.owner); before=list(Eligibility.objects.values())
        with patch('recommendations.content_scope.timezone.now',return_value=now+timedelta(days=6)):
            self.assertNotEqual(scope,require_scope(self.owner))
        self.assertEqual(before,list(Eligibility.objects.values()))

    def test_existing_zero_score_and_private_fields_survive_feedback_undo_and_want(self):
        before=list(UserAnimeRecord.objects.values())
        first=self.client.post('/api/recommendations/feedback/',{'bangumi_id':1,
            'expected_feedback_version':0,'operation_id':str(uuid4())},format='json')
        self.assertEqual(first.status_code,200)
        undo=self.client.post('/api/recommendations/feedback/undo/',{'bangumi_id':1,
            'expected_feedback_version':1,'target_feedback_version':1,'operation_id':str(uuid4())},format='json')
        self.assertEqual(undo.status_code,200)
        for _ in range(2):
            response=self.client.post('/api/recommendations/1/want/',{},format='json')
            self.assertEqual(response.status_code,200); self.assertFalse(response.data['created'])
        self.assertEqual(before,list(UserAnimeRecord.objects.values()))

    def test_foreground_profile_revision_tracks_scope_without_new_frontend_controls(self):
        before=self.client.get('/api/recommendations/context/').data
        self.change()
        after=self.client.get('/api/recommendations/context/').data
        self.assertNotEqual(before['context']['profile_version'],after['context']['profile_version'])
        page=self.page()
        self.assertEqual(after['content_scope_context'], page['content_scope_context'])
        self.assertEqual(page['evidence_context']['scope_revision'],page['content_scope_context']['scope_revision'])
        self.assertEqual(page['results'][0]['evidence_context'],page['evidence_context'])

    def locked_writer(self, kind):
        self.change(); scope=require_scope(self.owner)
        changed,release=Signal(),Signal()
        application='n2-scope-test-'+uuid4().hex
        def revoke():
            close_old_connections()
            try:
                with transaction.atomic():
                    get_user_model().objects.select_for_update().get(pk=self.owner.pk)
                    self.change(action='revoke',version=1)
                    changed.set()
                    if not release.wait(10): raise AssertionError('test release timed out')
            finally: close_old_connections()
        def write():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('application_name', %s, false)",[application])
                try:
                    if kind == 'want':
                        add_want(user=self.owner,bangumi_id=2,scope_context=scope)
                    else:
                        mutate_feedback(user=self.owner,action='hide',scope_context=scope,
                            changes={'bangumi_id':2,'expected_feedback_version':0,'operation_id':uuid4()})
                except APIException as exc: return exc.status_code
                return 201
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(revoke)
            try:
                self.assertTrue(changed.wait(10))
                writer=pool.submit(write)
                # Prove the request actually reached a PostgreSQL lock wait;
                # merely launching two futures does not prove contention.
                waiting=False; deadline=monotonic()+5
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock')",[application])
                        waiting=cursor.fetchone()[0]
                    if waiting: break
                    sleep(.02)
                self.assertTrue(waiting,'writer never reached the PostgreSQL lock wait')
                release.set(); future.result(timeout=15)
                self.assertEqual(writer.result(timeout=15),409)
            finally: release.set()
        self.assertEqual(UserAnimeRecord.objects.count(),1)
        self.assertFalse(Feedback.objects.exists())

    def test_revoke_while_want_waits_rejects_stale_scope_under_user_lock(self):
        self.locked_writer('want')

    def test_revoke_while_feedback_waits_rejects_stale_scope_under_user_lock(self):
        self.locked_writer('feedback')
