"""12F local synthetic rollout, coverage diagnostics, outage and rollback."""
import io
import json
from pathlib import Path
from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection, DatabaseError
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from recommendations import series_release, series_rollout
from recommendations.models import RecommendationAnime
from recommendations.series_models import SeriesNode, SeriesObservation, SeriesReminderState
from recommendations.series_store import publish_archive, record_failed_attempt
from recommendations.tests import test_series_workspace as prior
from recommendations.tests.test_series_exploration import archive
from recommendations.tests.test_series_backend import batch


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
    RECOMMENDATION_SERIES_ENABLED=True, RECOMMENDATION_SERIES_CANARY_USERS=[],
    RECOMMENDATION_SERIES_EXPLORATION_ENABLED=True, RECOMMENDATION_SERIES_REMINDERS_ENABLED=True)
class SeriesReleaseTests(TransactionTestCase):
    setUp = prior.SeriesWorkspaceTests.setUp
    record = prior.SeriesWorkspaceTests.record

    def prepare(self):
        publish_archive(archive([(1, 2, 3), (2, 1, 2)], version='dump-2026-10-21.000000Z'))
        self.record(identity=1, watch_status='completed')

    def caps(self):
        return self.client.get('/api/recommendations/capabilities/').data['exploration']

    def test_independent_canary_preserves_existing_all_three_modes(self):
        self.prepare()
        with override_settings(RECOMMENDATION_SERIES_CANARY_USERS=[self.owner.username]):
            self.assertTrue(self.caps()['series_exploration']['enabled'])
            self.assertEqual(self.client.get('/api/recommendations/series/1/workspace/').status_code, 200)
            self.client.force_authenticate(self.other)
            self.assertNotIn('series_exploration', self.caps())
            self.assertNotIn('series_reminders', self.caps())
            self.assertTrue(all(row['available'] for row in self.caps()['navigation']))
            for path, params in [('/api/recommendations/explore/', {'intent': 'similar', 'seed_bangumi_id': 1}),
                    ('/api/recommendations/explore/', {'intent': 'tags', 'include_tags': '音乐'}),
                    ('/api/recommendations/', {})]:
                self.assertEqual(self.client.get(path, params).status_code, 200)
            for path in ('series/1/', 'series/1/workspace/', 'series/annotations/?ids=1,2', 'series/reminders/',
                         'explore/series/?intent=similar&seed_bangumi_id=1'):
                self.assertEqual(self.client.get('/api/recommendations/' + path).status_code, 503)

    def test_invalid_config_never_full_release(self):
        for value in ('owner', [None], [''], [' owner '], {}):
            with override_settings(RECOMMENDATION_SERIES_CANARY_USERS=value):
                self.assertIsNone(series_rollout.canary_users())
                self.assertFalse(series_rollout.available(self.owner))
                self.assertNotIn('series_exploration', self.caps())
        self.owner.is_active = False
        self.assertFalse(series_rollout.available(self.owner))

    def test_rollback_rejects_old_read_and_write_contexts_without_data_loss(self):
        self.prepare()
        data = self.client.get('/api/recommendations/series/reminders/').data
        context = self.client.get('/api/recommendations/series/1/workspace/').data['context']
        with override_settings(RECOMMENDATION_SERIES_CANARY_USERS=[self.other.username]):
            response = self.client.patch('/api/recommendations/series/reminders/', {'action': 'collapse',
                'collapsed': True, 'context': data['context'], 'expected_version': data['version']}, format='json')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(SeriesReminderState.objects.exists())
            self.assertEqual(self.client.post('/api/recommendations/series/1/items/2/want/?context=' + context, {}, format='json').status_code, 503)
        with override_settings(RECOMMENDATION_SERIES_ENABLED=False):
            self.assertEqual(self.client.get('/api/recommendations/series/1/workspace/').status_code, 503)
        self.assertEqual(self.client.get('/api/recommendations/series/1/workspace/').status_code, 200)

    def test_canary_removed_during_read_does_not_send_old_payload(self):
        from recommendations import series_views
        real = series_views.read_relations
        def race(*args, **kwargs):
            data = real(*args, **kwargs)
            settings.RECOMMENDATION_SERIES_CANARY_USERS = [self.other.username]
            return data
        with override_settings(RECOMMENDATION_SERIES_CANARY_USERS=[]), patch.object(series_views, 'read_relations', side_effect=race):
            self.assertEqual(self.client.get('/api/recommendations/series/1/').status_code, 503)

    def test_series_database_outage_does_not_block_legacy_reads(self):
        self.prepare()
        with patch('recommendations.series_exploration.graph', side_effect=DatabaseError('synthetic')):
            self.assertEqual(self.client.get('/api/recommendations/series/annotations/?ids=1,2').status_code, 503)
            self.assertEqual(self.client.get('/api/recommendations/explore/', {'intent': 'similar', 'seed_bangumi_id': 1}).status_code, 200)
            self.assertEqual(self.client.get('/api/recommendations/explore/', {'intent': 'tags', 'include_tags': '音乐'}).status_code, 200)
            self.assertEqual(self.client.get('/api/recommendations/').status_code, 200)
        with patch('recommendations.series_reminders.graph', side_effect=DatabaseError('synthetic')):
            self.assertEqual(self.client.get('/api/recommendations/series/reminders/').status_code, 503)

    def test_coverage_uses_explicit_denominators_age_empty_and_failures_no_private_reads(self):
        self.prepare()
        record_failed_attempt([1])
        with CaptureQueriesContext(connection) as queries:
            data = series_release.coverage()
        self.assertEqual(data['catalog_count'], 4)
        safe = data['segments']['safe']
        self.assertEqual(safe['fresh_complete_count'], 2)
        self.assertEqual(safe['fresh_complete_percent'], 50)
        self.assertEqual(safe['last_attempt_states']['failed'], 1)
        self.assertEqual(data['next_safe_seed_ids'], [3, 4])
        self.assertFalse(data['adult_review_or_access_verified'])
        sql = '\n'.join(row['sql'].lower() for row in queries)
        for forbidden in ('anime_useranimerecord', 'auth_user', 'personal_score', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)
        SeriesObservation.objects.filter(source_id=2).update(source_at=timezone.now() - timedelta(days=46))
        SeriesNode.objects.filter(pk=1).update(nsfw=None)
        updated = series_release.coverage()['segments']['safe']
        self.assertEqual(updated['fresh_complete_count'], 0)
        self.assertEqual(updated['coverage_states']['stale'], 1)

    def test_missing_schema_insufficient_coverage_and_topology_block(self):
        self.prepare()
        with override_settings(RECOMMENDATION_SERIES_CANARY_USERS=[self.owner.username]):
            data = series_release.audit('canary')
            self.assertIn('safe_fresh_coverage_below_threshold', data['issues'])
            self.assertEqual(data['production_acceptance'], 'not_performed')
            self.assertEqual(series_release.audit('canary', 50)['status'], 'ready_for_operator_verification')
        self.assertIn('series_canary_accounts_required', series_release.audit('canary', 50)['issues'])
        with override_settings(RECOMMENDATION_EXPLORATION_CANARY_USERS=[self.owner.username]):
            self.assertIn('existing_exploration_not_full', series_release.audit('full', 50)['issues'])
        with patch.object(series_release, 'REQUIRED_MIGRATIONS', ('missing_migration',)):
            self.assertIn('required_migrations_missing', series_release.audit()['issues'])
        for value in (-1, 101, float('nan')):
            with self.assertRaises(ValueError): series_release.audit(min_fresh_percent=value)
        original = series_release.coverage()
        changed = {**original, 'fingerprint': 'changed'}
        with patch.object(series_release, 'coverage', side_effect=[original, changed]):
            self.assertIn('series_data_changed_during_audit', series_release.audit('closed')['issues'])

    def test_empty_observation_is_coverage_but_not_proof_of_no_series(self):
        self.prepare()
        publish_archive(batch(version='dump-2026-10-22.000000Z', kinds=(), seeds=(3,)))
        data = series_release.coverage()
        self.assertEqual(data['segments']['safe']['coverage_states']['empty_observed'], 1)
        self.assertEqual(data['segments']['safe']['fresh_complete_count'], 3)
        self.assertEqual(data['next_safe_seed_ids'], [4])
        with patch.object(series_release, 'MAX_CATALOG', 2):
            with self.assertRaises(ValueError): series_release.coverage()

    def test_cli_preserves_report_without_claiming_release_or_overwriting(self):
        self.prepare()
        path = Path(settings.BASE_DIR) / '.tools/12f' / ('audit-' + uuid4().hex + '.json')
        with self.assertRaises(CommandError):
            call_command('audit_series_release', output=path, stage='canary', stdout=io.StringIO(), stderr=io.StringIO())
        raw = path.read_bytes()
        data = json.loads(raw)
        self.assertEqual(data['status'], 'blocked')
        self.assertFalse(data['switches_changed'])
        with self.assertRaises(CommandError):
            call_command('audit_series_release', output=path, stage='canary', stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(path.read_bytes(), raw)
