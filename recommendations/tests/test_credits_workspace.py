"""13E source-bound reads/writes against isolated PostgreSQL only."""
from unittest.mock import patch
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from recommendations.models import RecommendationAnime, UserRecommendationFeedback, UserRecommendationFeedbackState
from recommendations.credits_models import CreditsState
from anime.models import UserAnimeRecord
from . import test_credits_http as http
ROOT = http.ROOT


# Reuse the archive fixture setup without inheriting/rerunning the 13C test suite.
@override_settings(RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED=True, RECOMMENDATION_CREDITS_STUDIO_ENABLED=True,
                   RECOMMENDATION_CREDITS_CANARY_USERS=[])
class CreditsWorkspaceTests(TransactionTestCase):
    setUp = http.CreditsHTTPTests.setUp
    tearDown = http.CreditsHTTPTests.tearDown
    publish = http.CreditsHTTPTests.publish
    get = http.CreditsHTTPTests.get
    works = http.CreditsHTTPTests.works
    record = http.CreditsHTTPTests.record

    def context(self, target=1):
        page = self.works(hide_recorded='false')
        self.assertEqual(page.status_code, 200, page.data)
        return next(row['action_context'] for row in page.data['results'] if row['bangumi_id'] == target)

    def action(self, token, target=1, kind='studio', person=201, post=False, **extra):
        path = ROOT + f'entities/{kind}/{person}/items/{target}/'
        if post:
            return self.client.post(path + 'want/?context=' + token, extra, format='json')
        return self.client.get(path, {'context': token, **extra})

    def test_read_only_details_and_no_private_profile_fields(self):
        record = self.record(identity=1, watch_status='completed', personal_score=9, review='PRIVATE_PROFILE')
        token = self.context()
        before = list(UserAnimeRecord.objects.values())
        response = self.action(token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['item']['watch_status'], 'completed')
        self.assertEqual(response.data['item']['bangumi_id'], 1)
        self.assertNotIn('PRIVATE_PROFILE', str(response.data))
        self.assertNotIn('record_id', response.data['item'])
        self.assertEqual(before, list(UserAnimeRecord.objects.values()))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('Authorization', response['Vary'])
        self.assertEqual(record.personal_score, 9)

    def test_lossless_idempotent_want_and_reconcile_completed_status(self):
        token = self.context()
        with patch('socket.socket', side_effect=AssertionError('No upstream requests')):
            first, repeated = self.action(token, post=True), self.action(token, post=True)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(first.data['record_id'], repeated.data['record_id'])
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.owner).count(), 1)
        row = UserAnimeRecord.objects.get(pk=first.data['record_id'])
        row.watch_status = 'completed'; row.personal_score = 9; row.review = 'KEEP_REVIEW'; row.watched_eps = 12
        row.save()
        third = self.action(token, post=True)
        self.assertEqual(third.status_code, 200, third.data)
        self.assertEqual(third.data['watch_status'], 'completed')
        row.refresh_from_db()
        self.assertEqual((row.review, row.personal_score, row.watched_eps), ('KEEP_REVIEW', 9, 12))
        completed = self.works(view='completed', hide_recorded='false')
        self.assertEqual([r['bangumi_id'] for r in completed.data['results']], [1])

    def test_token_binds_target_person_kind_account_session_and_rollout(self):
        token = self.context()
        for changes in ({'target': 2}, {'kind': 'voice_actor', 'person': 101}, {'person': 202}):
            self.assertEqual(self.action(token, **changes).status_code, 409)
            self.assertEqual(self.action(token, post=True, **changes).status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.action(token, post=True).status_code, 409)
        self.client.force_authenticate(self.owner, token='changed-session')
        self.assertEqual(self.action(token).status_code, 409)
        self.client.force_authenticate(self.owner)
        with override_settings(RECOMMENDATION_CREDITS_EPOCH='2'):
            self.assertEqual(self.action(token, post=True).status_code, 409)
        with override_settings(RECOMMENDATION_CREDITS_STUDIO_ENABLED=False):
            self.assertEqual(self.action(token, post=True).status_code, 503)
        self.assertFalse(UserAnimeRecord.objects.exists())

    def test_changed_sources_catalog_hidden_and_conflicting_identity_block_writes(self):
        token = self.context()
        CreditsState.objects.filter(pk=1).update(revision=100)
        self.assertEqual(self.action(token, post=True).status_code, 409)
        token = self.context()
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=True)
        self.assertEqual(self.action(token, post=True).status_code, 404)
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=False)
        token = self.context()
        UserRecommendationFeedbackState.objects.create(user=self.owner, feedback_version=1)
        UserRecommendationFeedback.objects.create(user=self.owner, bangumi_id=1, is_active=True, feedback_version=1, hidden_at=timezone.now())
        self.assertEqual(self.action(token, post=True).status_code, 409)
        self.assertFalse(UserAnimeRecord.objects.exists())

    def test_context_expiry_body_validation_and_cursors_are_not_action_tokens(self):
        token = self.context()
        self.assertEqual(self.action(token, post=True, personal_score=10).status_code, 400)
        self.assertEqual(self.action(token, extra='owner').status_code, 400)
        self.assertEqual(self.action(self.works().data['context']).status_code, 400)
        self.assertEqual(self.action(token + 'bad', post=True).status_code, 400)
        with patch('recommendations.credits_workspace.time.time', return_value=9999999999):
            self.assertEqual(self.action(token, post=True).status_code, 409)
        self.assertFalse(UserAnimeRecord.objects.exists())

    def test_final_read_rechecks_own_state_outside_snapshot(self):
        token = self.context()
        from recommendations.credits_read import read
        def concurrent(operation):
            result = read(operation)
            self.record(identity=1, watch_status='completed')
            return result
        with patch('recommendations.credits_views.reads.read', side_effect=concurrent):
            response = self.action(token)
        self.assertEqual(response.status_code, 409, response.data)

    def test_actual_detail_wire_passes_javascript_validator(self):
        import json, subprocess, shutil
        from django.conf import settings
        node = shutil.which('node')
        if not node: self.skipTest('Node unavailable')
        page = self.works().data
        row = page['results'][0]
        detail = self.action(row['action_context'], target=row['bangumi_id']).data
        result = subprocess.run([node, '--input-type=module', '-e',
            "import {validateCreditsDetail} from './frontend/src/utils/recommendationCredits.js';"
            "let s='';for await(const c of process.stdin)s+=c;const d=JSON.parse(s);"
            "validateCreditsDetail(d.detail,'studio',201,d.row,'safe');process.stdout.write('passed');"],
            input=json.dumps({'row': row, 'detail': detail}), text=True, capture_output=True, cwd=settings.BASE_DIR)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'passed')

    def test_conflicting_direct_and_alias_identity_blocks_old_membership_proof(self):
        from recommendations.models import RecommendationRecordIdentity
        token = self.context()
        record = self.record(identity=1, watch_status='completed')
        RecommendationRecordIdentity.objects.create(record=record, catalog_anime=RecommendationAnime.objects.get(bangumi_id=2),
            record_fingerprint='a' * 64, catalog_fingerprint='b' * 64, active=True, version=1)
        self.assertEqual(self.action(token).status_code, 409)
        self.assertEqual(self.action(token, post=True).status_code, 409)
        self.assertEqual(UserAnimeRecord.objects.count(), 1)
        self.assertNotIn(1, [row['bangumi_id'] for row in self.works(hide_recorded='false').data['results']])

    def test_concurrent_posts_share_owner_lock_and_create_one_record(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections
        from rest_framework.test import APIClient
        token = self.context()
        barrier = Barrier(2)
        def run(_):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(self.owner)
                barrier.wait(timeout=10)
                response = client.post(ROOT + 'entities/studio/201/items/1/want/?context=' + token, {}, format='json')
                return response.status_code, response.data
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(run, range(2)))
        self.assertEqual(sorted(code for code, _ in responses), [200, 201], responses)
        self.assertEqual(len({data['record_id'] for _, data in responses}), 1)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.owner).count(), 1)

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True)
    def test_reviewed_adult_detail_want_and_revocation_share_existing_scope_rules(self):
        import uuid
        from django.contrib.auth import get_user_model
        from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
        from recommendations.serving_control import change_control
        admin = get_user_model().objects.create_superuser('credits-admin', password='synthetic')
        change_control(admin, {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid.uuid4()),
                              'reason': 'synthetic', 'confirmed': True})
        anime = RecommendationAnime.objects.get(bangumi_id=26)
        mutate_review(actor=admin, bangumi_id=26, action='approve', changes={'expected_version': 0,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION,
            'operation_id': uuid.uuid4(), 'reason': 'synthetic', 'evidence_reference': 'synthetic',
            'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': 30})
        page = self.works(content_scope='include_adult').data
        while page['next_cursor']:
            page = self.works(cursor=page['next_cursor'], content_scope='include_adult').data
        adult = next(row for row in page['results'] if row['bangumi_id'] == 26)
        token = adult['action_context']
        path = ROOT + 'entities/studio/201/items/26/'
        response = self.client.get(path, {'context': token, 'content_scope': 'include_adult'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['item']['cover_url'], '')
        self.assertEqual(response.data['item']['content_rating'], 'adult')
        self.assertEqual(self.client.post(path + 'want/?context=' + token + '&content_scope=include_adult', {}, format='json').status_code, 201)
        self.assertEqual(self.action(token, target=26).status_code, 409)
        mutate_review(actor=admin, bangumi_id=26, action='revoke', changes={'expected_version': 1,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION,
            'operation_id': uuid.uuid4(), 'reason': 'synthetic revoke'})
        self.assertGreaterEqual(self.client.get(path, {'context': token, 'content_scope': 'include_adult'}).status_code, 400)
        self.assertGreaterEqual(self.client.post(path + 'want/?context=' + token + '&content_scope=include_adult', {}, format='json').status_code, 400)
