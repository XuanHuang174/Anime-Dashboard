"""12B synthetic LOCAL PostgreSQL only: transactions, permissions, scopes, races."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as dt_timezone
import io
import threading
import uuid
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, close_old_connections, IntegrityError
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.series_contract import normalize_archive_relation, MAPPING_VERSION
from recommendations.series_models import (SeriesState, SeriesNode, SeriesObservation, SeriesHead, SeriesAssertion, SeriesOverrideEvent)
from recommendations.series_read import node_revision
from recommendations.series_source import _node, digest
from recommendations.series_store import publish_archive, revise_relation, record_failed_attempt, SeriesConflict

if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
    raise RuntimeError('12B requires isolated LOCAL PostgreSQL settings.')


def batch(version='dump-2026-10-20.000000Z', kinds=(3, 4), seeds=(1,)):
    nodes = [_node({'id': i, 'type': 2, 'name': f'Original {i}', 'name_cn': f'合成{i}',
                   'date': '2026-01-01', 'platform': 1, 'nsfw': False}) for i in (1, 2, 3)]
    rows = [normalize_archive_relation({'subject_id': 1, 'related_subject_id': i, 'relation_type': kind,
                                       'order': i}, {1: 2, 2: 2, 3: 2}) for i, kind in zip((2, 3), kinds)]
    body = {'snapshot_version': version, 'source_at': datetime.strptime(version, 'dump-%Y-%m-%d.%H%M%SZ').replace(tzinfo=dt_timezone.utc).isoformat(),
        'source_reference': f'https://github.com/bangumi/Archive/releases/download/archive/{version}.zip',
        'archive_sha256': digest(version), 'payload_sha256': digest(rows), 'subject_payload_sha256': digest(nodes),
        'mapping_version': MAPPING_VERSION, 'seed_ids': list(seeds), 'nodes': nodes, 'assertions': rows,
        'diagnostics': {}, 'partial': False}
    return {**body, 'fingerprint': digest(body)}


def resign(value):
    value['fingerprint'] = digest({key: row for key, row in value.items() if key != 'fingerprint'})
    return value


@override_settings(RECOMMENDATION_SERIES_ENABLED=True)
class SeriesBackendTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user('series-owner', password='local-only')
        self.other = get_user_model().objects.create_user('series-other')
        self.admin = get_user_model().objects.create_superuser('series-reviewer', password='local-only')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'目录{i}',
            title_jp=f'Original {i}', nsfw=False, tags='音乐', season='2026-01', status_type='TV') for i in (1, 2, 3)])

    def read(self, source=1, **query):
        return self.client.get(f'/api/recommendations/series/{source}/', query)

    def revise(self, **changes):
        values = dict(actor=self.admin, source_id=1, target_id=2, action='replace', kind='side_story',
            reason='已核对两端关系', evidence_reference='local-audit:12b', operation_id=uuid.uuid4(),
            expected_revision=SeriesState.objects.get(pk=1).revision)
        values.update(changes)
        return revise_relation(**values)

    def test_incremental_publication_is_atomic_replayable_and_does_not_touch_catalog(self):
        before = list(RecommendationAnime.objects.values())
        value = batch()
        result = publish_archive(value)
        self.assertTrue(result['changed'])
        self.assertEqual(result['published'], 1)
        self.assertFalse(publish_archive(value)['changed'])
        self.assertEqual(SeriesObservation.objects.count(), 1)
        self.assertEqual(SeriesAssertion.objects.count(), 2)
        self.assertEqual(before, list(RecommendationAnime.objects.values()))
        self.assertFalse(RecommendationPreference.objects.exists())
        # A distinct source in the same archive adds only that source's observation.
        second = resign({**copy.deepcopy(value), 'seed_ids': [2], 'assertions': []})
        self.assertEqual(publish_archive(second)['published'], 1)
        self.assertEqual(SeriesObservation.objects.count(), 2)

    def test_hundred_source_publication_has_bounded_queries_and_correct_pointers(self):
        value = batch(kinds=(), seeds=range(1, 101))
        value['nodes'] = [_node({'id': i, 'type': 2, 'nsfw': False}) for i in range(1, 101)]
        value = resign(value)
        with CaptureQueriesContext(connection) as queries:
            result = publish_archive(value)
        self.assertEqual(result['published'], 100)
        self.assertLessEqual(len(queries), 18)
        self.assertEqual(SeriesObservation.objects.filter(coverage='empty_observed').count(), 100)
        self.assertEqual(SeriesHead.objects.exclude(current_id=None).count(), 100)
        for head in SeriesHead.objects.select_related('current', 'last_attempt'):
            self.assertEqual(head.source_id, head.current.source_id)
            self.assertEqual(head.current_id, head.last_attempt_id)
        replay = publish_archive(value)
        self.assertEqual(replay['replayed'], 100)
        self.assertFalse(replay['changed'])

    def test_bulk_pointer_failure_rolls_back_all_sources_and_command_write_guard(self):
        from recommendations.management.commands.sync_series_catalog import SeriesWriteGuard
        from recommendations.source_pipeline import SourceError
        guard = SeriesWriteGuard()
        for sql in ('UPDATE "recommendations_recommendationanime" SET nsfw=false',
                    'DELETE FROM "anime_useranimerecord"', 'TRUNCATE recommendations_seriesnode'):
            with self.assertRaises(SourceError):
                guard(lambda *args: None, sql, [], False, {})
        value = resign({**batch(kinds=(), seeds=(1, 2, 3))})
        with patch.object(SeriesHead.objects, 'bulk_update', side_effect=IntegrityError('synthetic')), self.assertRaises(IntegrityError):
            publish_archive(value)
        self.assertEqual(SeriesNode.objects.count(), 0)
        self.assertEqual(SeriesObservation.objects.count(), 0)
        self.assertEqual(SeriesHead.objects.count(), 0)

    def test_catalog_command_requires_identical_preview_and_replays_without_business_writes(self):
        import json
        from pathlib import Path
        import tempfile
        from django.core.management.base import CommandError
        from .test_series_source import SeriesArchiveSourceTests
        fixture = SeriesArchiveSourceTests()
        fixture.setUp()
        preview = fixture.folder / 'preview.json'
        applied = fixture.folder / 'applied.json'
        replayed = fixture.folder / 'replayed.json'
        try:
            checksum = fixture.archive()
            arguments = dict(input=fixture.path, expected_sha256=checksum, stdout=io.StringIO())
            call_command('sync_series_catalog', report=preview, **arguments)
            report = json.loads(preview.read_text(encoding='utf-8'))
            self.assertEqual(SeriesObservation.objects.count(), 0)
            with self.assertRaises(CommandError):
                call_command('sync_series_catalog', report=applied, apply=True, expected_plan='wrong', **arguments)
            before = list(RecommendationAnime.objects.values())
            with CaptureQueriesContext(connection) as queries:
                call_command('sync_series_catalog', report=applied, apply=True,
                             expected_plan=report['plan_fingerprint'], **arguments)
            writes = [row['sql'] for row in queries if row['sql'].lstrip().startswith(('INSERT', 'UPDATE', 'DELETE'))]
            self.assertTrue(writes)
            self.assertTrue(all('recommendations_series' in sql for sql in writes))
            self.assertEqual(SeriesObservation.objects.count(), 3)
            self.assertEqual(before, list(RecommendationAnime.objects.values()))
            call_command('sync_series_catalog', report=replayed, apply=True,
                         expected_plan=report['plan_fingerprint'], **arguments)
            self.assertFalse(json.loads(replayed.read_text(encoding='utf-8'))['database_writes'])
        finally:
            preview.unlink(missing_ok=True)
            applied.unlink(missing_ok=True)
            replayed.unlink(missing_ok=True)
            fixture.tearDown()

    def test_same_version_mutation_old_archive_and_tampered_precheck_rejected(self):
        value = batch()
        publish_archive(value)
        bad = copy.deepcopy(value)
        bad['assertions'][0]['kind'] = 'side_story'
        with self.assertRaises(ValidationError): publish_archive(bad)
        with self.assertRaises(SeriesConflict): publish_archive(resign(bad))
        # Wider catalog preflight may discover conflicts across old batch edges.
        # The same snapshot must not silently replay a different quality status.
        with self.assertRaises(SeriesConflict): publish_archive(resign({**value, 'partial': True}))
        with self.assertRaises(SeriesConflict): publish_archive(batch('dump-2026-09-20.000000Z'))
        self.assertEqual(SeriesObservation.objects.count(), 1)

    def test_database_error_rolls_back_whole_batch_and_metadata(self):
        publish_archive(batch())
        old = SeriesHead.objects.get(pk=1).current_id
        value = batch('dump-2026-10-21.000000Z')
        value['nodes'][0]['title'] = 'must roll back'
        with patch.object(SeriesAssertion.objects, 'bulk_create', side_effect=IntegrityError('synthetic')), self.assertRaises(IntegrityError):
            publish_archive(resign(value))
        self.assertEqual(SeriesObservation.objects.count(), 1)
        self.assertEqual(SeriesHead.objects.get(pk=1).current_id, old)
        self.assertNotEqual(SeriesNode.objects.get(pk=1).title, 'must roll back')

    def test_partial_failed_and_successful_empty_are_distinct_and_keep_old_versions(self):
        publish_archive(batch())
        old = SeriesHead.objects.get(pk=1).current_id
        value = batch('dump-2026-10-21.000000Z')
        value['partial'] = True
        publish_archive(resign(value))
        self.assertEqual(SeriesHead.objects.get(pk=1).current_id, old)
        self.assertEqual(self.read().data['last_attempt_status'], 'partial')
        record_failed_attempt([1, 99])
        self.assertEqual(SeriesHead.objects.get(pk=1).current_id, old)
        self.assertFalse(SeriesNode.objects.filter(pk=99).exists())
        self.assertEqual(self.read().data['last_attempt_status'], 'failed')
        empty = batch('dump-2026-10-22.000000Z', kinds=())
        publish_archive(empty)
        response = self.read()
        self.assertEqual(response.data['coverage'], 'empty_observed')
        self.assertEqual(response.data['results'], [])
        self.assertEqual(SeriesObservation.objects.count(), 4)

    def test_read_is_scoped_depth_one_and_never_reads_or_writes_private_fields(self):
        publish_archive(batch())
        with CaptureQueriesContext(connection) as queries:
            response = self.read()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['bangumi_id'] for row in response.data['results']], [2, 3])
        self.assertEqual(response.data['depth'], 1)
        self.assertTrue(all(row['can_add_from_series'] is False for row in response.data['results']))
        self.assertNotIn('_node_revision', response.data)
        self.assertIn('no-store', response['Cache-Control'])
        sql = '\n'.join(row['sql'].lower() for row in queries)
        for forbidden in ('useranimerecord', 'personal_score', 'recommendationpreference', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)
        self.assertLessEqual(len(queries), 28)
        with patch('recommendations.series_read.timezone.now', return_value=datetime(2090, 1, 1, tzinfo=dt_timezone.utc)):
            self.assertEqual(self.read().data['coverage'], 'stale')

    def test_context_binds_owner_source_scope_revision_and_catalog(self):
        publish_archive(batch())
        context = self.read().data['context']
        self.assertEqual(self.read(context=context).status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.read(context=context).status_code, 409)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.read(2, context=context).status_code, 409)
        self.assertEqual(self.read(context=context+'x').status_code, 409)
        RecommendationAnime.objects.filter(bangumi_id=2).update(title_zh='更新名称')
        self.assertEqual(self.read(context=context).status_code, 409)
        context = self.read().data['context']
        self.revise()
        self.assertEqual(self.read(context=context).status_code, 409)

    def test_live_content_and_archive_disagreement_never_leaks_hidden_nodes(self):
        publish_archive(batch())
        RecommendationAnime.objects.filter(bangumi_id=2).update(nsfw=True, title_zh='HIDDEN_SENTINEL')
        SeriesNode.objects.filter(pk=3).update(nsfw=None)
        response = self.read()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['results'], [])
        self.assertNotIn('HIDDEN_SENTINEL', str(response.data))
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=None)
        self.assertEqual(self.read().status_code, 404)

    def test_missing_catalog_unknown_target_and_weak_relations_never_auto_expand(self):
        value = batch(kinds=(8, 7))
        publish_archive(value)
        RecommendationAnime.objects.filter(bangumi_id=3).update(bangumi_id=30)
        response = self.read()
        self.assertEqual([row['kind'] for row in response.data['results']], ['same_setting'])
        self.assertEqual(response.data['depth'], 1)
        self.assertEqual(self.read(30).data['coverage'], 'not_loaded')

    def test_unknown_empty_partial_stale_root_and_direct_node_change_context(self):
        self.assertEqual(self.read().data['coverage'], 'not_loaded')
        value = batch()
        value['partial'] = True
        publish_archive(resign(value))
        self.assertEqual(self.read().data['coverage'], 'partial')
        self.assertEqual(self.read().data['results'], [])
        # Explicit human evidence may correct one pair even for an unpublished partial observation.
        self.revise()
        self.assertEqual(self.read().data['results'][0]['origin'], 'manual')
        context = self.read().data['context']
        SeriesNode.objects.filter(pk=2).update(release_date='2026-02')
        self.assertEqual(self.read(context=context).status_code, 409)

    def test_manual_revision_is_permissioned_idempotent_and_append_only_with_clear(self):
        publish_archive(batch())
        with self.assertRaises(PermissionDenied): self.revise(actor=self.owner)
        op = uuid.uuid4()
        rev = SeriesState.objects.get(pk=1).revision
        event = self.revise(operation_id=op, expected_revision=rev)
        replay = self.revise(operation_id=op, expected_revision=rev)
        self.assertEqual(event.pk, replay.pk)
        with self.assertRaises(SeriesConflict): self.revise(operation_id=op, expected_revision=rev, reason='different')
        self.assertEqual(self.read().data['results'][0]['kind'], 'side_story')
        self.revise(action='suppress', kind='')
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [3])
        self.revise(action='clear', kind='')
        self.assertEqual(self.read().data['results'][0]['kind'], 'sequel')

        # Clearing a correction is durable revocation, not a hidden future veto.
        publish_archive(batch('dump-2026-10-22.000000Z'))
        self.assertEqual(self.read().data['results'][0]['kind'], 'sequel')
        self.assertEqual(SeriesOverrideEvent.objects.count(), 3)
        self.assertEqual(list(SeriesAssertion.objects.filter(target_id=2).values_list('kind', flat=True)), ['sequel', 'sequel'])

    def test_new_snapshot_does_not_silently_reactivate_manually_corrected_pair(self):
        publish_archive(batch())
        self.revise()
        publish_archive(batch('dump-2026-10-21.000000Z'))
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [3])
        self.revise(action='clear', kind='')
        self.assertEqual(self.read().data['results'][0]['kind'], 'sequel')

    def test_wrong_version_invalid_target_and_empty_evidence_rejected(self):
        publish_archive(batch())
        for change in ({'target_id': 1}, {'target_id': 99}, {'action': 'delete'}, {'kind': 'invented'},
                       {'reason': ''}, {'evidence_reference': ''}):
            with self.subTest(change=change), self.assertRaises(ValidationError): self.revise(**change)
        with self.assertRaises(SeriesConflict): self.revise(expected_revision=0)
        self.admin.is_active = False
        self.admin.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied): self.revise()

    def test_closed_gate_authentication_forged_queries_and_schema_failure(self):
        publish_archive(batch())
        with self.settings(RECOMMENDATION_SERIES_ENABLED=False): self.assertEqual(self.read().status_code, 503)
        self.client.force_authenticate(None)
        self.assertEqual(self.read().status_code, 401)
        self.client.force_authenticate(self.owner)
        for query in ({'user_id': self.other.pk}, {'depth': 2}, {'context': ''}, {'content_scope': 'unknown'}):
            self.assertEqual(self.read(**query).status_code, 400)
        self.assertEqual(self.client.get('/api/recommendations/series/1/?context=a&context=b').status_code, 400)
        with patch('recommendations.series_read.revision', side_effect=IntegrityError('synthetic')):
            self.assertEqual(self.read().status_code, 503)

    def test_final_response_rechecks_series_catalog_nodes_and_runtime_gate(self):
        publish_archive(batch())
        import recommendations.series_views as views
        real = views.read_relations
        def mutate(*args, **kwargs):
            data = real(*args, **kwargs)
            SeriesNode.objects.filter(pk=2).update(nsfw=True)
            return data
        with patch.object(views, 'read_relations', side_effect=mutate):
            self.assertEqual(self.read().status_code, 409)

    def test_concurrent_same_snapshot_serializes_without_duplicates(self):
        value = batch()
        barrier = threading.Barrier(2)
        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return publish_archive(value)['changed']
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sorted(outputs), [False, True])
        self.assertEqual(SeriesObservation.objects.count(), 1)
        self.assertEqual(SeriesState.objects.get(pk=1).revision, 1)

    def test_concurrent_manual_decisions_reject_stale_form(self):
        publish_archive(batch())
        barrier = threading.Barrier(2)
        rev = SeriesState.objects.get(pk=1).revision
        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try:
                    self.revise(expected_revision=rev)
                    return 'changed'
                except SeriesConflict: return 'conflict'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sorted(outputs), ['changed', 'conflict'])
        self.assertEqual(SeriesOverrideEvent.objects.count(), 1)

    def test_django_admin_single_pair_form_and_read_only_models(self):
        publish_archive(batch())
        self.client.force_login(self.admin)
        url = '/admin/recommendations/seriesnode/1/revise/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        initial = response.context_data['form'].initial
        response = self.client.post(url, {**initial, 'target_id': 2, 'action': 'suppress', 'kind': '',
            'reason': '单条核验', 'evidence_reference': 'audit:synthetic', 'confirmed': True})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SeriesOverrideEvent.objects.count(), 1)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 302)

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True,
                       RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
    def test_adult_relations_require_live_review_scope_and_both_gates(self):
        from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
        from recommendations.serving_control import change_control, control_state
        value = batch()
        value['nodes'][1]['nsfw'] = True
        publish_archive(resign(value))
        RecommendationAnime.objects.filter(bangumi_id=2).update(nsfw=True)
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [3])
        self.assertEqual(self.read(content_scope='include_adult').status_code, 403)
        change_control(self.admin, {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid.uuid4()),
            'reason': 'synthetic test', 'confirmed': True})
        anime = RecommendationAnime.objects.get(bangumi_id=2)
        # Merely opening the gate still does not approve a relation target.
        self.assertEqual(self.read(content_scope='include_adult').status_code, 403)
        mutate_review(actor=self.admin, bangumi_id=2, action='approve', changes={
            'expected_version': 0, 'expected_catalog_revision': catalog_revision(anime),
            'expected_policy_version': POLICY_VERSION, 'operation_id': uuid.uuid4(),
            'reason': 'Synthetic only', 'evidence_reference': 'synthetic-review',
            'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': 30})
        response = self.read(content_scope='include_adult')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['bangumi_id'] for row in response.data['results']], [2, 3])
        context = response.data['context']
        self.assertEqual(self.read(context=context).status_code, 409)
        change_control(self.admin, {'enabled': False, 'expected_version': control_state()['version'],
            'operation_id': str(uuid.uuid4()), 'reason': 'synthetic off', 'confirmed': True})
        self.assertEqual(self.read(content_scope='include_adult', context=context).status_code, 403)

    def test_delegated_series_permission_does_not_grant_content_review_or_catalog_edits(self):
        publish_archive(batch())
        self.other.is_staff = True
        self.other.save(update_fields=['is_staff'])
        self.other.user_permissions.add(Permission.objects.get(codename='review_series_relations'))
        self.revise(actor=self.other)
        self.assertFalse(self.other.has_perm('recommendations.manage_content_review'))
        self.client.force_login(self.other)
        self.assertEqual(self.client.post('/admin/recommendations/seriesnode/1/change/', {'title': 'forged'}).status_code, 403)
        self.assertEqual(self.client.post('/admin/recommendations/seriesoverrideevent/1/delete/').status_code, 403)
        self.assertEqual(SeriesOverrideEvent.objects.count(), 1)

    def test_read_limit_qualifies_before_truncation_and_queries_do_not_grow_per_target(self):
        value = batch()
        value['assertions'] = []
        value['nodes'] = value['nodes'][:1]
        for identity in range(10, 240):
            node = _node({'id': identity, 'type': 2, 'date': '2026', 'nsfw': False, 'name': str(identity)})
            value['nodes'].append(node)
            value['assertions'].append(normalize_archive_relation({'subject_id': 1, 'related_subject_id': identity,
                'relation_type': 3, 'order': identity}, {1: 2, identity: 2}))
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'目录{i}', nsfw=False)
                                                 for i in range(10, 240)])
        publish_archive(resign(value))
        with CaptureQueriesContext(connection) as queries:
            response = self.read()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 200)
        self.assertTrue(response.data['truncated'])
        self.assertLessEqual(len(queries), 28)

    def test_command_preview_and_apply_failures_never_replace_current_snapshot(self):
        publish_archive(batch())
        old = SeriesHead.objects.get(pk=1).current_id
        output = io.StringIO()
        with patch('recommendations.management.commands.sync_series_archive.prepare_archive', return_value=batch()):
            call_command('sync_series_archive', input='unused.zip', expected_sha256='0'*64, ids='1', stdout=output)
        self.assertIn('preview', output.getvalue())
        self.assertEqual(SeriesObservation.objects.count(), 1)
        from django.core.management import CommandError
        with patch('recommendations.management.commands.sync_series_archive.prepare_archive', side_effect=ValueError('PRIVATE_ERROR')), self.assertRaises(CommandError) as error:
            call_command('sync_series_archive', input='unused.zip', expected_sha256='0'*64, ids='1', apply=True)
        self.assertNotIn('PRIVATE_ERROR', str(error.exception))
        self.assertEqual(SeriesHead.objects.get(pk=1).current_id, old)
        self.assertEqual(SeriesHead.objects.get(pk=1).last_attempt.coverage, 'failed')

        # Actual byte-verified ZIP -> command -> publication -> scoped HTTP read.
        from .test_series_source import SeriesArchiveSourceTests
        fixture = SeriesArchiveSourceTests()
        fixture.setUp()
        try:
            checksum = fixture.archive(subjects=[{'id': i, 'type': 2, 'date': '2026-01', 'nsfw': False}
                                                for i in (4, 5)], relations=[{
                'subject_id': 4, 'related_subject_id': 5, 'relation_type': 3, 'order': 0}])
            call_command('sync_series_archive', input=fixture.path, expected_sha256=checksum, ids='4',
                         stdout=io.StringIO())
            self.assertFalse(SeriesHead.objects.filter(pk=4).exists())
            call_command('sync_series_archive', input=fixture.path, expected_sha256=checksum, ids='4',
                         apply=True, stdout=io.StringIO())
            RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'合成{i}', nsfw=False)
                                                     for i in (4, 5)])
            result = self.read(4)
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['results'][0]['bangumi_id'], 5)
            self.assertEqual(result.data['results'][0]['release_precision'], 'month')
        finally:
            fixture.tearDown()
