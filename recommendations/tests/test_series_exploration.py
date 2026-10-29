"""12D synthetic isolated PostgreSQL: partition before cap, scoped facts and fixed batches."""
import copy
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import connection, DatabaseError
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from recommendations.models import RecommendationAnime
from recommendations.series_contract import normalize_archive_relation
from recommendations.series_models import SeriesNode, SeriesState
from recommendations.series_source import _node, digest
from recommendations.series_store import publish_archive, revise_relation
from recommendations.tests.test_series_backend import batch, resign
from recommendations.tests import test_exploration_browsing as prior


def archive(edges, version='dump-2026-10-20.000000Z', adult=()):
    ids = {1} | {identity for source, target, kind in edges for identity in (source, target)}
    value = batch(version)
    value['nodes'] = [_node({'id': identity, 'type': 2, 'name': f'Synthetic {identity}', 'name_cn': f'关系{identity}',
        'date': '2026-01-01', 'platform': 1, 'nsfw': identity in adult}) for identity in sorted(ids)]
    value['seed_ids'] = sorted({source for source, target, kind in edges} | {1})
    value['assertions'] = [normalize_archive_relation({'subject_id': source, 'related_subject_id': target, 'relation_type': kind, 'order': n},
        {identity: 2 for identity in ids}) for n, (source, target, kind) in enumerate(edges)]
    value['payload_sha256'] = digest(value['assertions'])
    value['subject_payload_sha256'] = digest(value['nodes'])
    return resign(value)


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_SERIES_ENABLED=True,
    RECOMMENDATION_SERIES_EXPLORATION_ENABLED=True, RECOMMENDATION_V2_BROWSE_ENABLED=False,
    RECOMMENDATION_ALGORITHM_VERSION='content.v1')
class SeriesExplorationTests(TransactionTestCase):
    setUp = prior.SingleWorkSnapshotTests.setUp
    catalog = prior.SingleWorkSnapshotTests.catalog
    record = prior.SingleWorkSnapshotTests.record
    hide = prior.SingleWorkSnapshotTests.hide

    def publish(self, edges=None, **kwargs):
        publish_archive(archive(edges or [(1, 10, 3), (1, 11, 4), (1, 12, 8), (1, 200, 6), (13, 1, 2)], **kwargs))

    def start(self, **params):
        return self.client.get('/api/recommendations/explore/series/', {'intent': 'similar', 'seed_bangumi_id': 1, **params})

    def item(self, row, strict=True, **params):
        return self.client.get(f'/api/recommendations/explore/series/{row["bangumi_id"]}/', {'detail_context': row['detail_context'],
            **({'evidence_token': row['evidence_token']} if strict else {}), **params})

    def annotations(self, ids='10,11,12'):
        return self.client.get('/api/recommendations/series/annotations/', {'ids': ids})

    def all_ids(self, data):
        ids = []
        while True:
            ids += [row['bangumi_id'] for row in data['results']]
            token = data['next_page_cursor'] or data['next_batch_cursor']
            if not token: return ids
            response = self.client.get('/api/recommendations/explore/series/', {'cursor': token})
            self.assertEqual(response.status_code, 200, response.data)
            data = response.data

    def test_disjoint_partition_direct_reverse_facts_and_weak_world_links(self):
        self.publish()
        result = self.start()
        self.assertEqual(result.status_code, 200, result.data)
        data = result.data
        self.assertEqual(data['contract_version'], 'recommendations.explore.series.v1')
        self.assertEqual(data['series_context']['policy_version'], 'series-explore.direct.v2')
        self.assertEqual({row['bangumi_id'] for row in data['series_context']['items']}, {10, 11, 13, 200})
        self.assertEqual(data['eligible_count'], 140)
        ids, page = [], data
        while True:
            ids += [row['bangumi_id'] for row in page['results']]
            token = page['next_page_cursor'] or page['next_batch_cursor']
            if not token: break
            response = self.client.get('/api/recommendations/explore/series/', {'cursor': token})
            self.assertEqual(response.status_code, 200, response.data)
            page = response.data
            self.assertEqual(page['series_context'], data['series_context'])
        self.assertEqual(len(ids), 120)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertFalse(set(ids) & {10, 11, 13, 200})
        self.assertIn(12, ids)  # same setting stays in genre similarity, not a continuous story.
        reverse = next(row for row in data['series_context']['items'] if row['bangumi_id'] == 13)['facts'][0]
        self.assertEqual((reverse['source_id'], reverse['target_id'], reverse['kind']), (13, 1, 'prequel'))
        self.assertNotIn('_series_subjects', data)
        self.assertIn('no-store', result['Cache-Control'])

    def test_exclude_more_than_200_relations_before_120_cap_not_just_first_12_or_200(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'扩展{i}', tags='音乐,日常', score=10,
            nsfw=False, season='2026-01', status_type='TV') for i in range(300, 651)])
        self.publish([(1, i, 3) for i in range(300, 550)])
        data = self.start().data
        self.assertEqual(data['returned_count'], 120)
        self.assertEqual(data['eligible_count'], 244)
        self.assertEqual(data['series_context']['filtered_count'], 250)
        self.assertEqual(len(data['series_context']['items']), 12)
        self.assertTrue(data['series_context']['truncated'])
        self.assertTrue(all(row['bangumi_id'] >= 550 for row in data['results']))

    def test_related_reference_ignores_recommendation_filters_but_honors_hidden_and_scope(self):
        self.publish()
        self.record(10, watch_status='completed', personal_score=9)
        self.hide(11)
        data = self.start().data
        self.assertEqual({row['bangumi_id'] for row in data['series_context']['items']}, {10, 13, 200})
        self.assertEqual(next(row for row in data['series_context']['items'] if row['bangumi_id'] == 10)['watch_status'], 'completed')
        data = self.start(hide_recorded=False).data
        self.assertIn(10, [row['bangumi_id'] for row in data['series_context']['items']])
        expected = data['series_context']['items']
        for filters in ({'type': 'OVA'}, {'year_from': 2027}, {'min_score': 9},
                        {'min_score': 6.3, 'max_score': 7.5}, {'hide_recorded': True}):
            response = self.start(**filters)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['series_context']['items'], expected)
            self.assertNotIn(10, [row['bangumi_id'] for row in response.data['results']])
        # No profile or review reads even when a valid record has both.
        with CaptureQueriesContext(connection) as queries: response = self.start()
        self.assertEqual(response.status_code, 200, response.data)
        sql = '\n'.join(row['sql'].lower() for row in queries)
        for forbidden in ('personal_score', '"review"', 'recommendationpreference', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)
        self.assertLessEqual(len(queries), 48)

    def test_annotations_preserve_direction_never_force_global_or_transitive_type(self):
        self.publish([(1, 10, 4), (10, 11, 3), (1, 12, 8)])
        data = self.annotations().data
        self.assertEqual(data['contract_version'], 'recommendations.series.annotations.v1')
        row = next(row for row in data['results'] if row['bangumi_id'] == 10)
        self.assertEqual({fact['kind'] for fact in row['facts']}, {'summary', 'sequel'})
        self.assertNotIn('classification', row)
        # 11 is indirectly linked through 10, not falsely merged into seed 1's direct partition.
        ids = self.all_ids(self.start().data)
        self.assertIn(11, ids)
        self.hide(1)
        row = next(row for row in self.annotations().data['results'] if row['bangumi_id'] == 10)
        self.assertTrue(all(fact['source_id'] != 1 for fact in row['facts']))

    def test_unknown_unscoped_or_unreviewed_endpoints_do_not_leak_names_or_counts(self):
        self.publish([(1, 90, 3), (1, 91, 3), (1, 9999, 3)], adult=(90,))
        RecommendationAnime.objects.filter(bangumi_id=90).update(title_zh='HIDDEN_SENTINEL')
        data = self.start().data
        self.assertEqual(data['series_context']['filtered_count'], 0)
        self.assertNotIn('HIDDEN_SENTINEL', str(data))
        self.assertEqual(self.annotations('90,91,9999').data['results'], [])
        SeriesNode.objects.filter(pk=1).update(nsfw=None)
        self.assertEqual(self.start().status_code, 404)

    def test_relation_changes_expire_cursor_detail_and_foreground_context_including_loose_detail(self):
        self.publish()
        data = self.start().data
        SeriesNode.objects.filter(pk=10).update(release_date='2025')
        responses = [self.client.get('/api/recommendations/explore/series/', {'cursor': data['next_page_cursor']}),
            self.item(data['results'][0]), self.item(data['results'][0], strict=False),
            self.client.get('/api/recommendations/explore/series/context/', {'snapshot_context': data['snapshot_context']})]
        for response in responses:
            self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(response.data['code'], 'exploration_snapshot_changed')

    def test_old_and_series_contracts_cannot_mix_and_closed_strategy_keeps_old_api(self):
        self.publish()
        self.assertTrue(self.client.get('/api/recommendations/capabilities/').data['exploration']['series_exploration']['enabled'])
        new = self.start().data
        old = self.client.get('/api/recommendations/explore/', {'intent': 'similar', 'seed_bangumi_id': 1}).data
        self.assertEqual(old['contract_version'], 'recommendations.explore.v1')
        self.assertNotIn('series_context', old)
        for endpoint, token in [('/api/recommendations/explore/', new['next_page_cursor']), ('/api/recommendations/explore/series/', old['next_page_cursor'])]:
            self.assertEqual(self.client.get(endpoint, {'cursor': token}).status_code, 409)
        with self.settings(RECOMMENDATION_SERIES_EXPLORATION_ENABLED=False):
            self.assertNotIn('series_exploration', self.client.get('/api/recommendations/capabilities/').data['exploration'])
            self.assertEqual(self.start().status_code, 503)
            self.assertEqual(self.annotations().status_code, 503)
            self.assertEqual(self.client.get('/api/recommendations/explore/', {'cursor': old['next_page_cursor']}).status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/recommendations/explore/series/', {'cursor': new['next_page_cursor']}).status_code, 409)

    def test_no_write_duplicate_parameters_and_bounded_graph_fail_closed(self):
        self.publish()
        for ids in ('1,1', '0', '2147483648', ','.join(str(i) for i in range(1, 26))):
            self.assertEqual(self.annotations(ids).status_code, 400)
        self.assertEqual(self.client.post('/api/recommendations/series/annotations/', {}, format='json').status_code, 405)
        with patch('recommendations.series_exploration.LIMIT', 1): self.assertEqual(self.start().status_code, 503)
        with patch('recommendations.series_exploration.graph', side_effect=DatabaseError('synthetic')):
            self.assertEqual(self.start().status_code, 503)

    def test_metadata_final_conflict_has_series_code_not_authorization_loss(self):
        self.publish()
        import recommendations.series_exploration as service
        real = service.annotations
        def change(*args, **kwargs):
            data = real(*args, **kwargs)
            SeriesNode.objects.filter(pk=10).update(release_date='2025')
            return data
        with patch.object(service, 'annotations', side_effect=change):
            response = self.annotations()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'series_snapshot_changed')

    def test_missing_empty_and_manual_suppression_and_update_are_explicit(self):
        self.assertEqual(self.start().data['series_context']['coverage'], 'not_loaded')
        self.publish()
        self.owner.is_superuser = True; self.owner.is_staff = True; self.owner.save()
        revise_relation(actor=self.owner, source_id=1, target_id=10, action='suppress', kind='', reason='synthetic', evidence_reference='audit:12d',
            operation_id=uuid4(), expected_revision=SeriesState.objects.get(pk=1).revision)
        self.assertNotIn(10, [row['bangumi_id'] for row in self.start().data['series_context']['items']])
        self.assertIn(10, self.all_ids(self.start().data))

    def test_confirmed_own_write_can_refresh_details_without_renewing_expiry(self):
        self.publish()
        data = self.start().data; row = data['results'][0]
        self.assertEqual(self.client.post(f'/api/recommendations/{row["bangumi_id"]}/want/', {}, format='json').status_code, 201)
        response = self.item(row, strict=False)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['watch_status'], 'plan')
        self.assertEqual(response.data['snapshot_expires_at'], data['snapshot_expires_at'])

    def test_actual_python_contract_matches_javascript_pages_details_and_annotations(self):
        self.publish()
        data = self.start().data
        next_page = self.client.get('/api/recommendations/explore/series/', {'cursor': data['next_page_cursor']}).data
        detail = self.item(data['results'][0]).data
        node = shutil.which('node')
        self.assertIsNotNone(node)
        result = subprocess.run([node, str(Path(settings.BASE_DIR) / 'frontend/tests/fixtures/verifySeriesExplorationWire.mjs')],
            input=json.dumps({'page': data, 'next': next_page, 'detail': detail, 'annotations': self.annotations().data}),
            capture_output=True, encoding='utf-8', cwd=settings.BASE_DIR, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '12')

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True)
    def test_adult_scope_requires_current_review_and_preserves_safe_legacy_separation(self):
        from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
        from recommendations.serving_control import change_control
        self.owner.is_superuser = True; self.owner.is_staff = True; self.owner.save()
        self.publish([(1, 90, 3), (1, 91, 3)], adult=(90,))
        self.assertEqual(self.start().data['series_context']['items'], [])
        change_control(self.owner, {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid4()), 'reason': 'synthetic', 'confirmed': True})
        anime = RecommendationAnime.objects.get(bangumi_id=90)
        mutate_review(actor=self.owner, bangumi_id=90, action='approve', changes={'expected_version': 0,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION, 'operation_id': uuid4(),
            'reason': 'synthetic', 'evidence_reference': 'audit:12d', 'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': 30})
        response = self.start(content_scope='include_adult')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['bangumi_id'] for row in response.data['series_context']['items']], [90])
        self.assertNotIn('COVER_SENTINEL', str(response.data))
        token = response.data['next_page_cursor']
        mutate_review(actor=self.owner, bangumi_id=90, action='revoke', changes={'expected_version': 1,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION,
            'operation_id': uuid4(), 'reason': 'synthetic revoke'})
        self.assertGreaterEqual(self.client.get('/api/recommendations/explore/series/', {'cursor': token, 'content_scope': 'include_adult'}).status_code, 400)
