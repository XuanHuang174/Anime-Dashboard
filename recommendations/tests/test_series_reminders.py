"""12E synthetic LOCAL PostgreSQL: own completed facts and presentation-only writes."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from anime.models import UserAnimeRecord
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.binding import bind_record
from recommendations.series_models import SeriesReminderState, SeriesReminderDismissal, SeriesObservation, SeriesNode
from recommendations.series_store import publish_archive, revise_relation
from recommendations.tests import test_series_workspace as prior
from recommendations.tests.test_series_exploration import archive
from recommendations.feedback import mutate_feedback, current_version


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
    RECOMMENDATION_SERIES_ENABLED=True, RECOMMENDATION_SERIES_EXPLORATION_ENABLED=True,
    RECOMMENDATION_SERIES_REMINDERS_ENABLED=True)
class SeriesReminderTests(TransactionTestCase):
    setUp = prior.SeriesWorkspaceTests.setUp
    record = prior.SeriesWorkspaceTests.record
    url = '/api/recommendations/series/reminders/'

    def prepare(self, edges=None):
        publish_archive(archive(edges or [(1, 2, 3), (1, 3, 4), (4, 1, 2)], version='dump-2026-10-21.000000Z'))
        self.source = self.record(identity=1, watch_status='completed', personal_score=10, review='PRIVATE_SENTINEL')

    def read(self, **params):
        return self.client.get(self.url, params)

    def change(self, action, data=None, **fields):
        data = data or self.read().data
        return self.client.patch(self.url, {'action': action, 'context': data['context'], 'expected_version': data['version'], **fields}, format='json')

    def test_completed_only_direction_no_editions_transitive_or_other_users(self):
        self.prepare()
        self.record(self.other, identity=2, watch_status='completed')
        response = self.read()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual([row['bangumi_id'] for row in data['results']], [2, 4])
        self.assertEqual(data['results'][1]['facts'][0]['kind'], 'prequel')
        self.assertEqual(data['results'][1]['facts'][0]['completed_id'], 1)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn('PRIVATE_SENTINEL', str(data))
        self.source.watch_status = 'watching'; self.source.save(update_fields=['watch_status'])
        self.assertEqual(self.read().data['results'], [])
        publish_archive(archive([(1, 2, 3), (2, 3, 3)], version='dump-2026-10-22.000000Z'))
        self.source.watch_status = 'completed'; self.source.save(update_fields=['watch_status'])
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [2, 4])

    def test_any_own_status_feedback_and_ambiguous_links_excluded(self):
        self.prepare()
        target = self.record(identity=2, watch_status='on_hold')
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [4])
        target.watch_status = 'plan'; target.save(update_fields=['watch_status'])
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [4])
        mutate_feedback(user=self.owner, action='hide', changes={'bangumi_id': 4, 'expected_feedback_version': current_version(self.owner), 'operation_id': str(uuid4())})
        self.assertEqual(self.read().data['count'], 0)

    def test_stale_identity_not_a_source_and_not_a_claim_of_unseen(self):
        self.prepare()
        alias = self.record(identity=None, watch_status='plan')
        bind_record(record_id=alias.pk, user_id=self.owner.pk, target_id=2, source='user_confirmed')
        alias.metadata_initialized = True; alias.private_chinese_title = 'changed alias'; alias.save()
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [4])
        self.source.anime.bgm_id = None; self.source.anime.save(update_fields=['bgm_id'])
        self.assertEqual(self.read().data['results'], [])

    def test_hide_restore_collapse_persist_and_do_not_change_other_domains(self):
        self.prepare()
        before = list(UserAnimeRecord.objects.values())
        preferences = list(RecommendationPreference.objects.values())
        first = self.read().data
        self.assertFalse(SeriesReminderState.objects.exists())  # GET does not create state.
        self.assertEqual(self.change('hide', first, bangumi_id=2).status_code, 200)
        hidden = self.read().data
        self.assertEqual((hidden['count'], hidden['hidden_count'], hidden['version']), (1, 1, 1))
        self.assertEqual(self.change('hide', first, bangumi_id=2).status_code, 409)  # no stale replay
        self.assertEqual(self.change('collapse', hidden, collapsed=True).status_code, 200)
        self.assertTrue(self.read().data['collapsed'])
        self.assertEqual(self.change('restore').status_code, 200)
        restored = self.read().data
        self.assertEqual((restored['count'], restored['hidden_count']), (2, 0))
        self.assertTrue(restored['collapsed'])
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertEqual(list(RecommendationPreference.objects.values()), preferences)
        self.assertEqual(current_version(self.owner), 0)
        self.client.force_authenticate(self.other)
        self.assertFalse(self.read().data['collapsed'])
        self.assertEqual(self.change('collapse', restored, collapsed=False).status_code, 409)
        self.assertFalse(SeriesReminderState.objects.filter(user=self.other).exists())

    def test_completed_or_relation_change_expires_mutations_and_final_read(self):
        self.prepare()
        old = self.read().data
        self.record(identity=2, watch_status='plan')
        response = self.change('hide', old, bangumi_id=2)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'series_snapshot_changed')
        self.assertFalse(SeriesReminderDismissal.objects.exists())
        old = self.read().data
        SeriesNode.objects.filter(pk=4).update(nsfw=None)
        self.assertEqual(self.change('collapse', old, collapsed=True).status_code, 409)
        from recommendations import series_reminders
        real = series_reminders.read
        def race(*args):
            result = real(*args)
            self.source.watch_status = 'plan'; self.source.save(update_fields=['watch_status'])
            return result
        with patch.object(series_reminders, 'read', side_effect=race):
            self.assertEqual(self.read().status_code, 409)

    def test_stale_source_manual_suppression_and_conflicting_pairs(self):
        self.prepare()
        SeriesObservation.objects.update(source_at=timezone.now() - timedelta(days=46))
        self.assertEqual(self.read().data['count'], 0)
        SeriesObservation.objects.update(source_at=timezone.now())
        revise_relation(actor=self.admin, source_id=1, target_id=2, action='suppress', kind='', reason='synthetic',
            evidence_reference='audit:12e', operation_id=uuid4(), expected_revision=self.read_series_revision())
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [4])
        publish_archive(archive([(1, 2, 3), (2, 1, 3)], version='dump-2026-10-22.000000Z'))
        self.assertEqual([row['bangumi_id'] for row in self.read().data['results']], [4])

    def read_series_revision(self):
        from recommendations.series_read import revision
        return revision()

    def test_bounded_deduplicated_stable_six_and_readonly_sql(self):
        self.prepare([(1, i, 3) for i in range(10, 24)] + [(2, 10, 3)])
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'目录{i}', nsfw=False) for i in range(10, 24)])
        self.record(identity=2, watch_status='completed')
        with CaptureQueriesContext(connection) as sql:
            data = self.read().data
        self.assertEqual((data['count'], len(data['results']), data['truncated']), (14, 6, True))
        self.assertEqual([row['bangumi_id'] for row in data['results']], list(range(10, 16)))
        self.assertEqual(len(data['results'][0]['facts']), 2)
        query_text = '\n'.join(row['sql'].lower() for row in sql)
        for forbidden in ('personal_score', '"review"', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, query_text)
        self.assertLessEqual(len(sql), 50)

    def test_reciprocal_facts_are_one_reason_and_real_frontend_contract(self):
        self.prepare([(1, 2, 3), (2, 1, 2)])
        data = self.read().data
        self.assertEqual(len(data['results'][0]['facts']), 1)
        import json
        import shutil
        import subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        node = shutil.which('node') or str(root / '.tools/node/node.exe')
        result = subprocess.run([node, '--input-type=module', '-e',
            "import {readFileSync} from 'node:fs'; import {validateReminders} from './src/utils/seriesReminders.js'; validateReminders(JSON.parse(readFileSync(0,'utf8')));"],
            cwd=root / 'frontend', input=json.dumps(data), text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scope_flags_strict_parameters_auth_and_reenable(self):
        self.prepare()
        for setting in ('RECOMMENDATION_SERIES_ENABLED', 'RECOMMENDATION_SERIES_EXPLORATION_ENABLED', 'RECOMMENDATION_SERIES_REMINDERS_ENABLED', 'RECOMMENDATION_EXPLORATION_ENABLED'):
            with override_settings(**{setting: False}):
                self.assertEqual(self.read().status_code, 503)
                self.assertNotIn('series_reminders', self.client.get('/api/recommendations/capabilities/').data['exploration'])
        with override_settings(RECOMMENDATION_EXPLORATION_CANARY_USERS=['other-user']):
            self.assertEqual(self.read().status_code, 503)
        self.assertTrue(self.client.get('/api/recommendations/capabilities/').data['exploration']['series_reminders']['enabled'])
        self.assertEqual(self.read(owner=self.other.pk).status_code, 400)
        for fields in ({'bangumi_id': True}, {'bangumi_id': -1}, {'bangumi_id': 99}, {'bangumi_id': 2, 'user_id': self.other.pk}):
            self.assertGreaterEqual(self.change('hide', **fields).status_code, 400)
        self.assertEqual(self.change('collapse', collapsed='true').status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.read().status_code, (401, 403))

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True,
        RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_ALGORITHM_VERSION='content.v1')
    def test_nsfw_current_review_scope_and_unknown_no_titles_or_count(self):
        self.prepare()
        RecommendationAnime.objects.filter(bangumi_id=2).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=4).update(nsfw=None)
        publish_archive(archive([(1, 2, 3), (1, 4, 3)], version='dump-2026-10-22.000000Z', adult=(2,)))
        self.assertEqual(self.read().data['count'], 0)
        from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
        from recommendations.serving_control import change_control
        change_control(self.admin, {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid4()), 'reason': 'synthetic', 'confirmed': True})
        anime = RecommendationAnime.objects.get(bangumi_id=2)
        mutate_review(actor=self.admin, bangumi_id=2, action='approve', changes={'expected_version': 0,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION, 'operation_id': uuid4(),
            'reason': 'synthetic', 'evidence_reference': 'audit:12e', 'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': 30})
        response = self.read(content_scope='include_adult')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['bangumi_id'] for row in response.data['results']], [2])
        mutate_review(actor=self.admin, bangumi_id=2, action='revoke', changes={'expected_version': 1,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION, 'operation_id': uuid4(), 'reason': 'synthetic'})
        self.assertGreaterEqual(self.read(content_scope='include_adult').status_code, 400)
