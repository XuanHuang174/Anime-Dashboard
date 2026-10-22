"""Synthetic isolated PostgreSQL ONLY. Never access live accounts/journal data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from time import sleep
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections, transaction, IntegrityError
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from anime.models import Anime, UserAnimeRecord
from recommendations.tests import test_content_scope_n2 as fixtures
from recommendations.eligibility import mutate_eligibility, own_eligibility, EligibilityConflict
from recommendations.serving_control import change_control, control_state
from recommendations.content_scope import require_scope, ensure_scope
from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
from recommendations.models import (RecommendationAnime, RecommendationContentReview,
    RecommendationContentEligibility, RecommendationServingControl as Control,
    RecommendationServingControlEvent as ControlEvent)
from recommendations.want import add_want
from recommendations.feedback import mutate_feedback


@override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True,
    RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
class ControlledServingTests(TransactionTestCase):
    setUp = fixtures.ClosedScopeTests.setUp
    change = fixtures.ClosedScopeTests.change

    def control(self, enabled=True, **overrides):
        return change_control(self.manager, {'enabled': enabled,
            'expected_version': control_state(admin=True)['version'], 'operation_id': str(uuid4()),
            'reason': 'Synthetic controlled test only', 'confirmed': True, **overrides})

    def review(self):
        anime = RecommendationAnime.objects.get(bangumi_id=90)
        return mutate_review(actor=self.manager, bangumi_id=90, action='approve', changes={
            'expected_version': 0, 'expected_catalog_revision': catalog_revision(anime),
            'expected_policy_version': POLICY_VERSION, 'operation_id': uuid4(),
            'reason': 'Synthetic content review', 'evidence_reference': 'synthetic-only',
            'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': 30})

    def enable(self):
        self.change(); self.change(action='approve', version=1); self.review(); self.control()
        return require_scope(self.owner, 'include_adult')

    def get(self, path='', **params):
        return self.client.get('/api/recommendations/' + path, {'content_scope': 'include_adult', **params})

    def test_four_gates_and_unknown_rating_never_allowed(self):
        self.assertFalse(Control.objects.exists())
        self.assertEqual(self.get().status_code, 403)
        self.control(); self.assertEqual(self.get().status_code, 403)
        self.change(); self.change(action='approve', version=1)
        self.assertTrue(own_eligibility(self.owner)['adult_access_allowed'])
        response = self.get(page_size=24)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(90, [r['bangumi_id'] for r in response.data['results']])
        self.assertEqual(self.get('90/').status_code, 404)
        self.review(); self.assertEqual(self.get('90/').status_code, 200)
        self.assertEqual(self.get('91/').status_code, 404)
        self.assertEqual(self.client.get('/api/recommendations/90/').status_code, 404)
        self.client.force_authenticate(self.other); self.assertEqual(self.get('90/').status_code, 403)

    def test_default_safe_order_identical_even_with_open_gates(self):
        before = self.client.get('/api/recommendations/', {'page_size': 24}).data
        self.enable()
        after = self.client.get('/api/recommendations/', {'page_size': 24}).data
        for key in ('bangumi_id', 'score', 'reason', 'tags', 'watch_status'):
            self.assertEqual([r[key] for r in before['results']], [r[key] for r in after['results']])
        self.assertEqual(before['count'], after['count'])
        self.assertTrue(all(r['content_rating'] == 'non_adult' for r in after['results']))

    def test_list_detail_evidence_and_neutral_media_no_private_sample(self):
        self.enable()
        page = self.get(page_size=24).data
        adult = next(r for r in page['results'] if r['bangumi_id'] == 90)
        self.assertEqual(adult['content_rating'], 'adult'); self.assertEqual(adult['cover_url'], '')
        detail = self.get('90/', evidence_token=adult['evidence_token'])
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['evidence'], adult['evidence'])
        self.assertNotIn('COVER_SENTINEL', str(detail.data)); self.assertNotIn('PRIVATE_REVIEW', str(page))
        UserAnimeRecord.objects.create(user=self.owner,
            anime=Anime.objects.create(bgm_id=90, chinese_title='Synthetic adult'), personal_score=10,
            watch_status='completed', custom_tags=['音乐'], review='DO_NOT_READ')
        from recommendations.engine import user_context
        context = user_context(self.owner, scope_context=require_scope(self.owner, 'include_adult'))
        self.assertNotIn(90, [s['bangumi_id'] for s in context['seeds']])
        self.assertEqual(context['owned'][90], 'completed')

    def test_pre400_scope_and_all_styles_real_association(self):
        self.enable()
        for style in ('balanced', 'close', 'open'):
            response = self.get(browse=True, style=style, page_size=24)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['algorithm_version'], 'content.v1')
            adult = next(r for r in response.data['results'] if r['bangumi_id'] == 90)
            self.assertEqual(adult['evidence']['kind'], 'explicit_preferences')
            self.assertEqual(adult['cover_url'], '')

    def test_want_idempotent_zero_overwrite_private_no_publication(self):
        scope = self.enable(); before = list(UserAnimeRecord.objects.values())
        # Even the showcase owner's default-public policy cannot publish adult rows.
        with patch('recommendations.want.imported_visibility_defaults', return_value={'is_active': True}):
            result = add_want(user=self.owner, bangumi_id=90, scope_context=scope)
        self.assertTrue(result['created']); self.assertEqual(result['content_rating'], 'adult')
        record = UserAnimeRecord.objects.get(pk=result['record_id'])
        self.assertFalse(record.anime.is_active); self.assertEqual(record.private_poster_url, '')
        record.personal_score = 0; record.review = 'PROTECTED'; record.custom_tags = ['PRIVATE']
        record.watch_status = 'on_hold'; record.save()
        saved = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        result = add_want(user=self.owner, bangumi_id=90, scope_context=scope)
        self.assertFalse(result['created'])
        self.assertEqual(saved, UserAnimeRecord.objects.filter(pk=record.pk).values().get())
        self.assertEqual(before, list(UserAnimeRecord.objects.filter(pk=self.record.pk).values()))

    def test_feedback_hidden_undo_scope_and_other_user_isolation(self):
        scope = self.enable(); before = list(UserAnimeRecord.objects.values())
        result = mutate_feedback(user=self.owner, action='hide', scope_context=scope, changes={
            'bangumi_id': 90, 'expected_feedback_version': 0, 'operation_id': uuid4()})
        self.assertEqual(self.get('feedback/hidden/').data['count'], 1)
        self.assertEqual(self.client.get('/api/recommendations/feedback/hidden/').data['count'], 0)
        mutate_feedback(user=self.owner, action='undo', scope_context=scope, changes={
            'bangumi_id': 90, 'expected_feedback_version': result['feedback_version'], 'operation_id': uuid4(),
            'target_feedback_version': result['feedback']['feedback_version']})
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))
        self.client.force_authenticate(self.other); self.assertEqual(self.get().status_code, 403)

    def test_revocation_global_review_expiry_and_catalog_invalidate(self):
        scope = self.enable(); first = self.get(browse=True, page_size=2).data
        cursor = first['next_page_cursor']; self.assertTrue(cursor)
        self.assertEqual(self.get(cursor=cursor).status_code, 200)
        self.assertEqual(self.client.get('/api/recommendations/', {'cursor': cursor}).status_code, 409)
        RecommendationAnime.objects.filter(bangumi_id=90).update(tags='音乐,科幻')
        self.assertEqual(self.get(cursor=cursor).status_code, 409)
        self.assertEqual(self.get('90/').status_code, 404)
        with self.assertRaises(APIException): ensure_scope(self.owner, scope)
        self.control(False)
        self.assertEqual(self.get().status_code, 403)
        self.assertEqual(self.client.get('/api/recommendations/').status_code, 200)
        self.control(True)
        self.change(action='revoke', version=2)
        self.assertEqual(self.get('90/').status_code, 403)

    def test_expiry_without_counter_change_invalidates_and_deadline_bound(self):
        self.enable()
        review = RecommendationContentReview.objects.get(anime__bangumi_id=90)
        review.expires_at = timezone.now() + timedelta(seconds=60); review.save()
        first = self.get(browse=True, page_size=2).data
        self.assertLessEqual(first['snapshot_expires_at'], int(review.expires_at.timestamp()))
        with patch('recommendations.content_scope.timezone.now', return_value=review.expires_at + timedelta(seconds=1)):
            self.assertEqual(self.get(cursor=first['next_page_cursor']).status_code, 409)
            self.assertEqual(self.get('90/').status_code, 404)

    def test_control_permissions_receipts_conflict_rollback_and_no_read_writes(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertFalse(control_state(admin=True)['enabled'])
        self.assertFalse(any(q['sql'].startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        payload = {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid4()), 'reason': 'Synthetic', 'confirmed': True}
        with self.assertRaises(PermissionDenied): change_control(self.other, payload)
        result = change_control(self.manager, payload)
        self.assertEqual(result['version'], 1); self.assertTrue(change_control(self.manager, payload)['replayed'])
        with self.assertRaises(EligibilityConflict): change_control(self.manager, {**payload, 'reason': 'different'})
        with self.assertRaises(EligibilityConflict): change_control(self.manager, {**payload, 'operation_id': str(uuid4())})
        with patch('recommendations.serving_control.Event.objects.create', side_effect=IntegrityError('synthetic')):
            with self.assertRaises(IntegrityError): self.control(False)
        self.assertTrue(control_state(admin=True)['enabled']); self.assertEqual(ControlEvent.objects.count(), 1)
        with override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=False):
            self.assertFalse(control_state()['effective_enabled'])
            with self.assertRaises(ValidationError): self.control(True)
        with override_settings(RECOMMENDATION_V2_BROWSE_ENABLED=True):
            self.assertEqual(self.get().status_code, 403)

    def test_module3_superuser_signed_approval_and_no_journal_exposure(self):
        self.change()
        self.assertEqual(self.client.get('/api/recommendations/admin/applications/').status_code, 403)
        self.client.force_authenticate(self.manager)
        queue = self.client.get('/api/recommendations/admin/applications/')
        self.assertEqual(queue.status_code, 200); self.assertNotIn('PRIVATE', str(queue.data))
        url = f'/api/recommendations/admin/applications/{self.owner.pk}/'
        ticket = self.client.get(url).data['ticket']
        self.assertEqual(self.client.post(url, {'ticket': ticket, 'action': 'approve', 'reason': 'Synthetic', 'confirmed': False}, format='json').status_code, 400)
        body = {'ticket': ticket, 'action': 'approve', 'reason': 'Synthetic', 'confirmed': True}
        self.assertEqual(self.client.post(url, body, format='json').status_code, 200)
        self.assertTrue(self.client.post(url, body, format='json').data['replayed'])
        self.assertEqual(self.client.post(f'/api/recommendations/admin/applications/{self.other.pk}/', body, format='json').status_code, 409)
        self.manager.is_superuser = False; self.manager.save()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_global_control_two_writers_one_version_wins(self):
        barrier = Barrier(2)
        def task():
            close_old_connections()
            try:
                barrier.wait()
                try: return self.control(expected_version=0)['version']
                except EligibilityConflict: return 'conflict'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: task(), range(2)))
        self.assertEqual(sorted(results, key=str), [1, 'conflict'])
        self.assertEqual(ControlEvent.objects.count(), 1)

    def test_waiting_want_cannot_bypass_global_close(self):
        scope = self.enable(); waiting = Event()
        def task():
            close_old_connections()
            try:
                waiting.set()
                try: return add_want(user=self.owner, bangumi_id=90, scope_context=scope)
                except APIException as exc: return exc.status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Control.objects.select_for_update().get(pk=1)
                future = pool.submit(task); self.assertTrue(waiting.wait(2)); sleep(.1)
                self.control(False)
            self.assertEqual(future.result(timeout=10), 409)
        self.assertFalse(UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=90).exists())

    def test_concurrent_adult_want_one_record_and_feedback_do_not_overwrite(self):
        scope = self.enable(); barrier = Barrier(3)
        before = UserAnimeRecord.objects.filter(pk=self.record.pk).values().get()
        def task(action):
            close_old_connections()
            try:
                barrier.wait()
                if action == 'want':
                    return add_want(user=self.owner, bangumi_id=90, scope_context=scope)['created']
                return mutate_feedback(user=self.owner, action='hide', scope_context=scope, changes={
                    'bangumi_id': 90, 'expected_feedback_version': 0, 'operation_id': uuid4()})['changed']
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(task, action) for action in ('want', 'want', 'feedback')]
            results = [f.result(timeout=15) for f in futures]
        self.assertEqual(sorted(results[:2]), [False, True]); self.assertTrue(results[2])
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=90).count(), 1)
        self.assertEqual(before, UserAnimeRecord.objects.filter(pk=self.record.pk).values().get())

    def test_manual_adult_identity_requires_scope_bound_confirmation(self):
        self.enable()
        record = UserAnimeRecord.objects.create(user=self.owner, anime=Anime.objects.create(
            chinese_title='Synthetic legacy identity', japanese_title='Different exact name'),
            personal_score=9, watch_status='completed', review='PROTECTED')
        response = self.get('identities/', scope='all', record_id=record.pk, keyword='RESTRICTED_SENTINEL')
        self.assertEqual(response.status_code, 200, response.data)
        candidate = response.data['results'][0]['candidates'][0]
        self.assertEqual(candidate['content_rating'], 'adult')
        body = {'record_id': record.pk, 'bangumi_id': 90, 'expected_version': 0,
            'confirmed': True, 'ticket': candidate['ticket']}
        saved = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        response = self.client.patch('/api/recommendations/identities/?content_scope=include_adult', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(saved, UserAnimeRecord.objects.filter(pk=record.pk).values().get())
        from recommendations.engine import user_context
        context = user_context(self.owner, requested_scope='include_adult')
        self.assertEqual(context['owned'][90], 'completed')
        self.assertNotIn(90, [s['bangumi_id'] for s in context['seeds']])

    def test_many_adult_cards_use_bulk_review_validation_and_separate_cache(self):
        self.enable()
        for identity in range(200, 212):
            anime = RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'Synthetic {identity}',
                tags='音乐', nsfw=True, score=9, status_type='TV')
            mutate_review(actor=self.manager, bangumi_id=identity, action='approve', changes={
                'expected_version': 0, 'expected_catalog_revision': catalog_revision(anime),
                'expected_policy_version': POLICY_VERSION, 'operation_id': uuid4(),
                'reason': 'Synthetic', 'evidence_reference': 'synthetic-only',
                'exclusions_confirmed': True, 'general_adult_confirmed': True})
        self.get(browse=True, page_size=1)  # warm the public-only scoped cache
        with CaptureQueriesContext(connection) as one:
            small = self.get(browse=True, page_size=1)
        with CaptureQueriesContext(connection) as many:
            large = self.get(browse=True, page_size=24)
        self.assertEqual(small.status_code, 200); self.assertEqual(large.status_code, 200)
        self.assertLessEqual(len(many), len(one) + 2)
        self.assertTrue(any(r['content_rating'] == 'adult' for r in large.data['results']))
        safe = self.client.get('/api/recommendations/', {'browse': True, 'page_size': 24})
        self.assertTrue(all(r['content_rating'] == 'non_adult' for r in safe.data['results']))
        sql = '\n'.join(q['sql'].lower() for q in many)
        self.assertNotIn('insert into', sql); self.assertNotIn('update "anime_useranimerecord"', sql)

    def test_final_get_rechecks_safety_after_repeatable_read_snapshot(self):
        self.enable()
        from recommendations import deployment
        original = deployment.browse_page
        for scope in ('safe', 'include_adult'):
            changed = []
            def after_snapshot(*args, **kwargs):
                data = original(*args, **kwargs)
                identity = data['results'][0]['bangumi_id']; changed.append(identity)
                RecommendationAnime.objects.filter(bangumi_id=identity).update(nsfw=None)
                return data
            with patch('recommendations.deployment.browse_page', side_effect=after_snapshot):
                response = self.client.get('/api/recommendations/', {'browse': True, 'content_scope': scope})
            self.assertEqual(response.status_code, 409, response.data)
            self.assertNotIn('results', response.data)
