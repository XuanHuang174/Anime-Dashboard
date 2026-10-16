"""8A synthetic contract tests; PostgreSQL only, never the production DSN."""
from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.feedback import feedback_snapshot, ensure_version, FeedbackConflict
from recommendations.models import (RecommendationAnime, RecommendationPreference,
    UserRecommendationFeedback as Feedback, UserRecommendationFeedbackState as State,
    RecommendationFeedbackOperation as Operation)
from .test_contracts_api import ContractFixtures

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('8A tests require the dedicated local PostgreSQL runner before test DB setup.')


class FeedbackAPITests(ContractFixtures, APITestCase):
    def setUp(self):
        # Fail instead of claiming a SQLite substitute validates locking.
        self.assertEqual(settings.SETTINGS_MODULE, 'config.recommendation_pg_test_settings')
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        super().setUp()
        cache.clear()
        self.client.force_authenticate(self.user)
        RecommendationPreference.objects.create(user=self.user, tags=['音乐'], version=7)
        for identity in (101, 102, 103):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'合成作品{identity}',
                nsfw=False, tags='音乐,日常', score=7, season='2026-04', status_type='TV')

    def body(self, identity=101, version=0, **extra):
        return {'bangumi_id': identity, 'expected_feedback_version': version,
                'operation_id': str(uuid4()), **extra}

    def hide(self, body=None):
        return self.client.post('/api/recommendations/feedback/', body or self.body(), format='json')

    def undo(self, version, target, identity=101, **extra):
        return self.client.post('/api/recommendations/feedback/undo/',
            self.body(identity, version, target_feedback_version=target, **extra), format='json')

    def hidden(self, **query):
        return self.client.get('/api/recommendations/feedback/hidden/', query)

    def test_authenticated_strict_contract_and_private_no_store(self):
        for path in ('feedback/', 'feedback/undo/', 'feedback/hidden/'):
            self.client.force_authenticate(None)
            response = self.client.get('/api/recommendations/' + path) if path.endswith('hidden/') else self.client.post('/api/recommendations/' + path, {}, format='json')
            self.assertEqual(response.status_code, 401)
        self.client.force_authenticate(self.user)
        for invalid in ({'user_id': self.other.pk}, {'anime_id': 101}, {'review': 'forged'},
                        {'bangumi_id': True}, {'bangumi_id': '101'}, {'bangumi_id': 0},
                        {'bangumi_id': 2147483648}, {'operation_id': 'invalid'},
                        {'expected_feedback_version': -1}, {'expected_feedback_version': None}):
            self.assertEqual(self.hide({**self.body(), **invalid}).status_code, 400)
        for key in ('operation_id', 'expected_feedback_version', 'bangumi_id'):
            body = self.body(); body.pop(key)
            self.assertEqual(self.hide(body).status_code, 400)
        self.assertEqual(self.client.post('/api/recommendations/feedback/?user_id=1', self.body(), format='json').status_code, 400)
        for query in ({'user_id': self.other.pk}, {'page': 2}, {'page_size': 25}):
            self.assertEqual(self.hidden(**query).status_code, 400)
        self.assertEqual(self.client.get('/api/recommendations/feedback/hidden/?page=1&page=2').status_code, 400)
        response = self.hidden()
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('Authorization', response['Vary'])
        self.assertEqual(Feedback.objects.count(), 0)

    def test_read_only_empty_list_does_not_create_state_or_receipts(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.hidden()
            feedback_snapshot(self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['feedback_version'], 0)
        self.assertEqual(response.data['results'], [])
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        self.assertEqual(State.objects.count(), 0)
        self.assertEqual(Operation.objects.count(), 0)

    def test_hide_undo_tombstone_and_explicit_typed_response(self):
        first = self.hide()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertIs(first.data['changed'], True)
        self.assertIs(first.data['replayed'], False)
        self.assertEqual(first.data['feedback_version'], 1)
        self.assertEqual(set(first.data['feedback']), {'bangumi_id', 'is_active', 'feedback_version', 'hidden_at', 'updated_at'})
        self.assertEqual(self.hidden().data['results'][0]['bangumi_id'], 101)
        response = self.undo(1, 1)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['feedback_version'], 2)
        self.assertIs(response.data['feedback']['is_active'], False)
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(self.hidden().data['count'], 0)
        self.assertEqual(RecommendationPreference.objects.get(user=self.user).version, 7)

    def test_duplicate_new_operation_is_noop_without_timestamp_or_revision_change(self):
        self.hide()
        before = Feedback.objects.values().get()
        state = State.objects.values().get()
        response = self.hide(self.body(version=1))
        self.assertFalse(response.data['changed'])
        self.assertFalse(response.data['replayed'])
        self.assertEqual(Feedback.objects.values().get(), before)
        self.assertEqual(State.objects.values().get(), state)
        self.assertEqual(Operation.objects.count(), 2)

    def test_uncertain_success_retry_after_undo_never_resurrects_hidden_work(self):
        body = self.body()
        self.hide(body)
        self.undo(1, 1)
        before = Feedback.objects.values().get()
        response = self.hide(body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['replayed'])
        self.assertFalse(response.data['changed'])
        self.assertFalse(response.data['feedback']['is_active'])
        self.assertEqual(response.data['operation_feedback_version'], 1)
        self.assertEqual(response.data['feedback_version'], 2)
        self.assertEqual(Feedback.objects.values().get(), before)
        self.assertEqual(Operation.objects.count(), 2)

    def test_old_undo_replay_after_rehide_reports_current_state_without_undoing_it(self):
        self.hide()
        body = self.body(version=1, target_feedback_version=1)
        self.client.post('/api/recommendations/feedback/undo/', body, format='json')
        self.hide(self.body(version=2))
        response = self.client.post('/api/recommendations/feedback/undo/', body, format='json')
        self.assertTrue(response.data['replayed'])
        self.assertTrue(response.data['feedback']['is_active'])
        self.assertEqual(response.data['feedback_version'], 3)

    def test_stale_global_and_target_versions_fail_without_writes(self):
        self.hide()
        for response in (self.hide(self.body(102, 0)), self.undo(0, 1)):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['code'], 'feedback_version_conflict')
            self.assertIs(response.data['reload_required'], True)
        self.undo(1, 1)
        self.hide(self.body(version=2))
        before = list(Operation.objects.values())
        response = self.undo(3, 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'feedback_target_conflict')
        self.assertTrue(Feedback.objects.get().is_active)
        self.assertEqual(list(Operation.objects.values()), before)

    def test_operation_uuid_is_immutable_and_scoped_to_user(self):
        body = self.body()
        self.hide(body)
        self.assertEqual(self.hide({**body, 'bangumi_id': 102}).data['code'], 'feedback_operation_conflict')
        self.assertEqual(self.hide({**body, 'expected_feedback_version': 1}).status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.hidden().data['feedback_version'], 0)
        self.assertEqual(self.hidden().data['count'], 0)
        self.assertEqual(self.hide(body).status_code, 200)
        self.undo(1, 1)
        self.assertTrue(Feedback.objects.get(user=self.user).is_active)
        self.assertFalse(Feedback.objects.get(user=self.other).is_active)

    def test_unsafe_missing_ids_and_post_hide_reclassification_are_not_exposed(self):
        for identity, rating in ((201, True), (202, None)):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh='不可泄露', nsfw=rating)
        for identity in (201, 202, 999):
            self.assertEqual(self.hide(self.body(identity)).status_code, 404)
            self.assertEqual(self.undo(0, 1, identity).status_code, 404)
        self.hide()
        RecommendationAnime.objects.filter(bangumi_id=101).update(nsfw=None)
        self.assertEqual(self.hidden().data['count'], 0)
        self.assertEqual(self.undo(1, 1).status_code, 404)
        self.assertEqual(self.client.get('/api/recommendations/101/').status_code, 404)
        self.assertTrue(Feedback.objects.get().is_active)

    def test_hidden_pagination_stable_order_and_revision_conflict(self):
        for version, identity in enumerate((101, 102, 103)):
            self.hide(self.body(identity, version))
        first = self.hidden(page_size=1).data
        self.assertEqual(first['results'][0]['bangumi_id'], 103)
        second = self.hidden(page=2, page_size=1, feedback_version=3).data
        self.assertEqual(second['results'][0]['bangumi_id'], 102)
        self.assertEqual(second['next_page'], 3)
        self.undo(3, 3, 103)
        self.assertEqual(self.hidden(page=2, page_size=1, feedback_version=3).status_code, 409)

    def test_journal_and_public_catalog_and_explicit_preferences_remain_bitwise_equal(self):
        record = self.seed(501, score='0.0', status='on_hold')
        UserAnimeRecord.objects.filter(pk=record.pk).update(review='完整私人长评\n' * 4000,
            metadata_initialized=True, private_base_tags=[], custom_tags=['二刷', '未知标签'])
        before = [list(model.objects.order_by('pk').values()) for model in
                  (UserAnimeRecord, Anime, RecommendationAnime, RecommendationPreference)]
        self.hide(self.body(501))
        self.undo(1, 1, 501)
        after = [list(model.objects.order_by('pk').values()) for model in
                 (UserAnimeRecord, Anime, RecommendationAnime, RecommendationPreference)]
        self.assertEqual(before, after)

    def test_atomic_receipt_failure_rolls_back_state_and_feedback(self):
        with patch('recommendations.feedback.Operation.objects.create', side_effect=DatabaseError('injected')):
            response = self.hide()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(State.objects.count(), 0)
        self.assertEqual(Feedback.objects.count(), 0)
        self.assertEqual(Operation.objects.count(), 0)

    def test_database_unique_and_check_constraints_are_real(self):
        self.hide()
        for values in ({'bangumi_id': 101, 'feedback_version': 2},
                       {'bangumi_id': -1, 'feedback_version': 2},
                       {'bangumi_id': 105, 'feedback_version': 0},
                       {'bangumi_id': 105, 'feedback_version': 2, 'is_active': True}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Feedback.objects.create(user=self.user, is_active=values.get('is_active', False),
                    **{key: value for key, value in values.items() if key != 'is_active'})
        old = Operation.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Operation.objects.create(user=self.user, operation_id=old.operation_id, bangumi_id=101,
                action='hide', request_fingerprint='x' * 64, applied_feedback_version=1)

    def test_revision_overflow_fails_closed_and_undo_missing_is_read_only(self):
        self.assertEqual(self.undo(0, 1).status_code, 404)
        State.objects.create(user=self.user, feedback_version=2147483647)
        self.assertEqual(self.hide(self.body(version=2147483647)).data['code'], 'feedback_version_exhausted')
        self.assertEqual(Feedback.objects.count(), 0)

    def test_backend_exclusion_exact_identity_not_genre_and_undo_restores_eligibility(self):
        before = self.client.get('/api/recommendations/').data
        self.assertEqual({r['bangumi_id'] for r in before['results']}, {101, 102, 103})
        self.hide()
        page = self.client.get('/api/recommendations/').data
        self.assertEqual({r['bangumi_id'] for r in page['results']}, {102, 103})
        self.client.force_authenticate(self.other)
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        self.assertIn(101, {r['bangumi_id'] for r in self.client.get('/api/recommendations/').data['results']})
        self.client.force_authenticate(self.user)
        self.undo(1, 1)
        self.assertIn(101, {r['bangumi_id'] for r in self.client.get('/api/recommendations/').data['results']})

    def test_feedback_invalidates_tokens_but_not_profile_or_preference_versions(self):
        page = self.client.get('/api/recommendations/', {'page_size': 1}).data
        row = page['results'][0]
        profile = self.client.get('/api/recommendations/profile/').data
        self.hide(self.body(row['bangumi_id']))
        self.assertEqual(self.client.get('/api/recommendations/', {'page': 2, 'page_size': 1,
            'state_token': page['state_token']}).status_code, 409)
        url = f'/api/recommendations/{row["bangumi_id"]}/'
        self.assertEqual(self.client.get(url, {'evidence_token': row['evidence_token']}).status_code, 409)
        fresh = self.client.get(url)
        self.assertEqual(fresh.status_code, 200)  # hidden is not a detail/journal ban
        self.assertEqual(fresh.data['evidence_context']['profile_version'], row['evidence_context']['profile_version'])
        self.assertEqual(fresh.data['evidence_context']['preference_version'], 7)
        self.assertEqual(fresh.data['evidence_context']['feedback_version'], 1)
        self.assertEqual(self.client.get('/api/recommendations/profile/').data, profile)

    def test_feedback_is_excluded_before_pool_cutoff_in_both_engines(self):
        # More than 400 equally related rows: exclusion must not starve the pool.
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'作品{i}',
            nsfw=False, score=7, tags='音乐,日常') for i in range(1000, 1410)])
        context = engine.user_context(self.user)
        context['feedback'] = {'version': 1, 'hidden_ids': frozenset(range(1000, 1400)) | {101, 102, 103}}
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': False, 'page_size': 12}
        index = engine.public_features()
        for version in ('content.v1', 'content.v2'):
            rows, _ = engine.rank_context(deepcopy(context), options, index, algorithm_version=version)
            self.assertEqual({r['anime'].bangumi_id for r in rows}, set(range(1400, 1410)))

    def test_mid_request_feedback_change_fails_closed(self):
        context = engine.user_context(self.user)
        self.hide()
        from recommendations.evidence import EvidencePresenter
        presenter = EvidencePresenter(self.user, context, {}, 'content.v1')
        with self.assertRaises(FeedbackConflict):
            presenter.ensure_safe([])
        with self.assertRaises(FeedbackConflict):
            ensure_version(self.user, 0)

    def test_hide_never_prevents_idempotent_existing_journal_ack(self):
        record = self.seed(501, score='9.1')
        before = UserAnimeRecord.objects.values().get(pk=record.pk)
        self.hide(self.body(501))
        result = self.client.post('/api/recommendations/501/want/', {}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.data['created'])
        self.assertEqual(UserAnimeRecord.objects.values().get(pk=record.pk), before)

    def test_bad_json_non_objects_form_and_oversize_never_write(self):
        for body in ([], 'invalid', None):
            self.assertEqual(self.client.post('/api/recommendations/feedback/', body, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/recommendations/feedback/', '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/api/recommendations/feedback/', self.body(), format='multipart').status_code, 415)
        self.assertEqual(self.client.post('/api/recommendations/feedback/', '{"x":"' + 'x' * 17000 + '"}',
            content_type='application/json').status_code, 400)
        self.assertEqual(Feedback.objects.count(), 0)
        self.assertEqual(State.objects.count(), 0)

    def test_real_jwt_and_no_external_identity_fallback(self):
        from rest_framework_simplejwt.tokens import AccessToken
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(self.other)))
        response = self.hide()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Feedback.objects.get().user_id, self.other.pk)
        # Catalog PK and exact Bangumi ID are different identity namespaces.
        pk = RecommendationAnime.objects.get(bangumi_id=101).pk
        if pk not in (101, 102, 103):
            self.assertEqual(self.hide(self.body(pk, 1)).status_code, 404)

    def test_no_feedback_does_not_change_v1_order_or_any_internal_score(self):
        context = engine.user_context(self.user)
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': 12}
        index = engine.public_features()
        original = deepcopy(context); original.pop('feedback')
        baseline, _ = engine.rank_context(original, options, index)
        current, _ = engine.rank_context(context, options, index)
        self.assertEqual([(row['anime'].bangumi_id, row['value']) for row in baseline],
                         [(row['anime'].bangumi_id, row['value']) for row in current])

    def test_undo_does_not_bypass_existing_record_hiding(self):
        record = self.seed(501, score=None, status='plan')
        RecommendationAnime.objects.filter(bangumi_id=501).update(tags='音乐,日常')
        before = UserAnimeRecord.objects.values().get(pk=record.pk)
        self.hide(self.body(501)); self.undo(1, 1, 501)
        page = self.client.get('/api/recommendations/').data
        self.assertNotIn(501, {row['bangumi_id'] for row in page['results']})
        page = self.client.get('/api/recommendations/', {'hide_recorded': False}).data
        self.assertIn(501, {row['bangumi_id'] for row in page['results']})
        self.assertEqual(UserAnimeRecord.objects.values().get(pk=record.pk), before)

    def test_feedback_read_race_rejects_mixed_version_snapshot(self):
        from recommendations.feedback import feedback_snapshot, hidden_feedback
        with patch('recommendations.feedback.current_version', side_effect=(0, 1)):
            with self.assertRaises(FeedbackConflict):
                feedback_snapshot(self.user)
        with patch('recommendations.feedback.current_version', side_effect=(0, 1)):
            with self.assertRaises(FeedbackConflict):
                hidden_feedback(self.user, {'page': 1, 'page_size': 12})

    def test_hiding_a_high_score_source_does_not_turn_its_genres_into_dislikes(self):
        for identity in (1, 2, 3):
            self.seed(identity, score='9.0', tags=['音乐', '日常'])
        before = self.client.get('/api/recommendations/profile/').data
        self.assertEqual(before['usable_sample_count'], 3)
        self.hide(self.body(1))
        self.assertEqual(self.client.get('/api/recommendations/profile/').data, before)
        page = self.client.get('/api/recommendations/').data
        self.assertEqual(page['mode'], 'personalized')
        self.assertTrue(page['results'])
        self.assertTrue(all(row['evidence']['kind'] == 'rated_work' for row in page['results']))

    def test_incremental_migration_only_creates_independent_feedback_tables(self):
        from importlib import import_module
        from django.db.migrations import CreateModel
        migration = import_module('recommendations.migrations.0005_recommendation_feedback').Migration
        self.assertEqual(len(migration.operations), 3)
        self.assertTrue(all(isinstance(operation, CreateModel) for operation in migration.operations))
        self.assertEqual({operation.name for operation in migration.operations},
                         {'UserRecommendationFeedback', 'UserRecommendationFeedbackState', 'RecommendationFeedbackOperation'})

    def test_8b_undo_invalidates_old_page_and_detail_without_changing_profile_or_rank_formula(self):
        before = self.client.get('/api/recommendations/profile/').data
        self.hide()
        page = self.client.get('/api/recommendations/', {'page_size': 1}).data
        row = page['results'][0]
        self.assertEqual(page['evidence_context']['feedback_version'], 1)
        self.assertEqual(self.undo(1, 1).status_code, 200)
        self.assertEqual(self.client.get('/api/recommendations/', {'page': 2, 'page_size': 1,
            'state_token': page['state_token']}).status_code, 409)
        self.assertEqual(self.client.get(f'/api/recommendations/{row["bangumi_id"]}/',
            {'evidence_token': row['evidence_token']}).status_code, 409)
        fresh = self.client.get('/api/recommendations/').data
        self.assertEqual(fresh['algorithm_version'], 'content.v1')
        self.assertEqual(fresh['evidence_context']['feedback_version'], 2)
        self.assertIn(101, {item['bangumi_id'] for item in fresh['results']})
        self.assertEqual(self.client.get('/api/recommendations/profile/').data, before)
