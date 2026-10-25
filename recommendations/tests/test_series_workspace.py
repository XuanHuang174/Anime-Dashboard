"""12C own state, identity, scoped details and lossless writes on isolated PostgreSQL."""
from unittest.mock import patch
import copy
import uuid
import json
import shutil
import subprocess
from pathlib import Path
from django.conf import settings

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations.models import RecommendationAnime, RecommendationRecordIdentity
from recommendations.binding import bind_record
from recommendations.series_models import SeriesNode
from recommendations.series_store import publish_archive
from recommendations.tests.test_series_backend import batch, resign


@override_settings(RECOMMENDATION_SERIES_ENABLED=True)
class SeriesWorkspaceTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user('workspace-owner')
        self.other = get_user_model().objects.create_user('workspace-other')
        self.admin = get_user_model().objects.create_superuser('workspace-admin', password='synthetic')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'目录{i}', title_jp=f'Original {i}',
            nsfw=False, tags='音乐', season='2026-01', status_type='TV') for i in (1, 2, 3, 4)])
        publish_archive(batch())

    def read(self, source=1, **params):
        return self.client.get(f'/api/recommendations/series/{source}/workspace/', params)

    def item(self, target=2, context=None, **params):
        return self.client.get(f'/api/recommendations/series/1/items/{target}/', {'context': context or self.read(**params).data['context'], **params})

    def want(self, target=2, context=None, body=None, **params):
        token = context or self.read(**params).data['context']
        return self.client.post(f'/api/recommendations/series/1/items/{target}/want/', body or {}, format='json', QUERY_STRING='&'.join(
            [f'context={token}'] + [f'{key}={value}' for key, value in params.items()]))

    def record(self, user=None, identity=2, **changes):
        anime = Anime.objects.create(bgm_id=identity, chinese_title=f'私有标题{identity}', japanese_title=f'Private{identity}', airing_period='2026-1')
        return UserAnimeRecord.objects.create(user=user or self.owner, anime=anime, **changes)

    def test_read_own_states_only_no_scores_reviews_or_writes(self):
        record = self.record(watch_status='watching', personal_score=9, review='PRIVATE_REVIEW_SENTINEL')
        self.record(self.other, 3, watch_status='completed')
        with CaptureQueriesContext(connection) as queries:
            response = self.read()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['contract_version'], 'recommendations.series.workspace.v1')
        self.assertEqual([row['watch_status'] for row in response.data['results']], ['watching', None])
        self.assertEqual(response.data['results'][1]['record_state'], 'no_confirmed_record')
        self.assertFalse(response.data['results'][0]['can_add_from_series'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn('PRIVATE_REVIEW_SENTINEL', str(response.data))
        sql = '\n'.join(row['sql'].lower() for row in queries)
        for forbidden in ('personal_score', '"review"', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)
        self.assertLessEqual(len(queries), 38)
        record.refresh_from_db()
        self.assertEqual(record.personal_score, 9)

    def test_alias_valid_and_stale_does_not_claim_unseen_or_duplicate(self):
        record = self.record(identity=None, watch_status='completed')
        bind_record(record_id=record.pk, user_id=self.owner.pk, target_id=2, source='user_confirmed')
        self.assertEqual(self.read().data['results'][0]['watch_status'], 'completed')
        record.private_chinese_title = 'changed fingerprint'
        record.metadata_initialized = True
        record.save(update_fields=['private_chinese_title', 'metadata_initialized'])
        row = self.read().data['results'][0]
        self.assertEqual(row['record_state'], 'needs_review')
        self.assertIsNone(row['watch_status'])
        self.assertFalse(row['can_add_from_series'])
        response = self.want()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.owner).count(), 1)

    def test_record_reference_exact_owner_no_title_inference(self):
        record = self.record()
        url = f'/api/recommendations/series/record/{record.pk}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['bangumi_id'], 2)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(self.owner)
        unknown = self.record(identity=None)
        response = self.client.get(f'/api/recommendations/series/record/{unknown.pk}/')
        self.assertIsNone(response.data['bangumi_id'])
        self.assertEqual(response.data['identity_state'], 'unlinked')

    def test_detail_membership_context_and_independent_public_contract(self):
        response = self.item()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['contract_version'], 'recommendations.series.detail.v1')
        self.assertEqual(response.data['item']['bangumi_id'], 2)
        self.assertNotIn('evidence', response.data['item'])
        self.assertNotIn('record_id', response.data['item'])
        self.assertNotIn('_node_revision', response.data)
        self.assertEqual(self.item(4).status_code, 404)
        self.assertEqual(self.item(1).status_code, 200)
        self.assertEqual(self.client.get('/api/recommendations/series/1/items/2/').status_code, 400)
        token = self.read().data['context']
        self.client.force_authenticate(self.other)
        self.assertEqual(self.item(context=token).status_code, 409)

    def test_actual_python_responses_pass_javascript_contract(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for 12C wire validation')
        page = self.read().data
        detail = self.item(context=page['context']).data
        result = subprocess.run([node, str(Path(settings.BASE_DIR) / 'frontend/tests/fixtures/verifySeriesWire.mjs')],
            input=json.dumps({'page': page, 'detail': detail}), encoding='utf-8', capture_output=True, cwd=settings.BASE_DIR, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '2')

    def test_conflicting_direct_and_alias_id_requires_review_even_for_forged_post(self):
        record = self.record(watch_status='watching')
        RecommendationRecordIdentity.objects.create(record=record, catalog_anime=RecommendationAnime.objects.get(bangumi_id=3),
            record_fingerprint='a' * 64, catalog_fingerprint='b' * 64, active=True, version=1)
        self.assertEqual(self.read().data['results'][0]['record_state'], 'needs_review')
        response = self.want()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'identity_confirmation_required')
        self.assertEqual(UserAnimeRecord.objects.count(), 1)

    def test_lossless_idempotent_want_and_current_status_refresh(self):
        response = self.want()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['watch_status'], 'plan')
        record = UserAnimeRecord.objects.get(pk=response.data['record_id'])
        record.watch_status = 'completed'
        record.personal_score = 9
        record.review = 'keep this review'
        record.save(update_fields=['watch_status', 'personal_score', 'review'])
        second = self.want()
        self.assertEqual(second.status_code, 200, second.data)
        self.assertFalse(second.data['created'])
        self.assertEqual(second.data['record_id'], record.pk)
        record.refresh_from_db()
        self.assertEqual((record.personal_score, record.review, record.watch_status), (9, 'keep this review', 'completed'))
        self.assertEqual(self.read().data['results'][0]['watch_status'], 'completed')
        self.assertEqual(self.want(4).status_code, 404)

    def test_changed_catalog_series_node_and_expired_tokens_block_all_actions(self):
        for change in ('catalog', 'node', 'series', 'signature'):
            with self.subTest(change=change):
                token = self.read().data['context']
                if change == 'catalog': RecommendationAnime.objects.filter(bangumi_id=2).update(title_jp='changed')
                elif change == 'node': SeriesNode.objects.filter(pk=2).update(release_date='2025')
                elif change == 'series': publish_archive(batch('dump-2026-10-21.000000Z', kinds=(6, 4)))
                else: token += 'x'
                for response in (self.read(context=token), self.item(context=token), self.want(context=token)):
                    self.assertEqual(response.status_code, 409, response.data)
                    self.assertEqual(response.data['code'], 'series_snapshot_changed')
        self.assertFalse(UserAnimeRecord.objects.exists())
        with patch('django.core.signing.time.time', return_value=0): token = self.read().data['context']
        self.assertEqual(self.want(context=token).status_code, 409)

    def test_private_args_duplicate_context_auth_and_gate_fail_closed(self):
        token = self.read().data['context']
        self.assertEqual(self.read(user_id=self.other.pk).status_code, 400)
        self.assertEqual(self.want(context=token, body={'user_id': self.other.pk}).status_code, 400)
        self.assertEqual(self.client.get('/api/recommendations/series/1/workspace/?context=x&context=y').status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.read().status_code, 401)
        self.client.force_authenticate(self.owner)
        with self.settings(RECOMMENDATION_SERIES_ENABLED=False):
            for response in (self.read(), self.item(context=token), self.want(context=token)):
                self.assertEqual(response.status_code, 503)
        self.assertFalse(UserAnimeRecord.objects.exists())

    def test_missing_empty_and_stale_sources_are_explicit_not_independent_claims(self):
        self.assertEqual(self.read(4).data['coverage'], 'not_loaded')
        publish_archive(batch('dump-2026-10-22.000000Z', kinds=()))
        response = self.read()
        self.assertEqual(response.data['coverage'], 'empty_observed')
        self.assertEqual(response.data['results'], [])

    def test_final_response_gate_and_safety_changes_discard_details(self):
        import recommendations.series_workspace as service
        real = service.detail
        def mutate(*args, **kwargs):
            data = real(*args, **kwargs)
            RecommendationAnime.objects.filter(bangumi_id=2).update(nsfw=True)
            return data
        with patch.object(service, 'detail', side_effect=mutate):
            self.assertEqual(self.item().status_code, 409)

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True,
                       RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
    def test_adult_scope_detail_want_and_revocation_never_expose_origin_cover(self):
        from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
        from recommendations.serving_control import change_control
        value = copy.deepcopy(batch('dump-2026-10-21.000000Z'))
        value['nodes'][1]['nsfw'] = True
        publish_archive(resign(value))
        RecommendationAnime.objects.filter(bangumi_id=2).update(nsfw=True, cover_url='https://unsafe.example/cover.jpg')
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [3])
        change_control(self.admin, {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid.uuid4()), 'reason': 'synthetic', 'confirmed': True})
        anime = RecommendationAnime.objects.get(bangumi_id=2)
        mutate_review(actor=self.admin, bangumi_id=2, action='approve', changes={ 'expected_version': 0,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION, 'operation_id': uuid.uuid4(),
            'reason': 'synthetic', 'evidence_reference': 'synthetic', 'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': 30})
        params = {'content_scope': 'include_adult'}
        response = self.item(**params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['item']['content_rating'], 'adult')
        self.assertEqual(response.data['item']['cover_url'], '')
        self.assertNotIn('unsafe.example', str(response.data))
        self.assertEqual(self.want(**params).status_code, 201)
        token = self.read(**params).data['context']
        mutate_review(actor=self.admin, bangumi_id=2, action='revoke', changes={'expected_version': 1,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION,
            'operation_id': uuid.uuid4(), 'reason': 'synthetic revoke'})
        self.assertGreaterEqual(self.item(context=token, **params).status_code, 400)
        self.assertGreaterEqual(self.want(context=token, **params).status_code, 400)
