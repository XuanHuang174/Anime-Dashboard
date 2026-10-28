import copy
import io
import json
from pathlib import Path
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from datetime import timedelta
from recommendations import credits_release as release
from recommendations.credits_coverage import coverage_audit
from recommendations.credits_models import CreditsSnapshot, CreditsAssertion, CreditsHead
from recommendations.credits_performance import profile
from recommendations.models import RecommendationAnime
from . import test_credits_http as prior


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
    RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED=False, RECOMMENDATION_CREDITS_STUDIO_ENABLED=False,
    RECOMMENDATION_CREDITS_CANARY_USERS=[])
class CreditsReleaseTests(TransactionTestCase):
    setUp = prior.CreditsHTTPTests.setUp
    def tearDown(self):
        (self.fixture.folder / 'release.json').unlink(missing_ok=True)
        self.fixture.close()
    publish = prior.CreditsHTTPTests.publish
    works = prior.CreditsHTTPTests.works
    get = prior.CreditsHTTPTests.get

    def prepare(self):
        self.fixture.rows['person-characters.jsonlines'] = [
            {'subject_id': i, 'person_id': 101, 'character_id': 301, 'type': 0, 'summary': ''} for i in range(1, 28)]
        self.fixture.rows['subject-characters.jsonlines'] = [
            {'subject_id': i, 'character_id': 301, 'type': 1, 'order': 0} for i in range(1, 28)]
        self.publish('dump-2026-10-21.000000Z')

    def test_fixed_policy_separates_fresh_complete_and_actual_relations(self):
        self.prepare()
        data = coverage_audit()
        self.assertEqual(release.evaluate(data, ('studio', 'voice_actor'))[0], [])
        empty = copy.deepcopy(data)
        empty['segments']['voice_actor']['safe']['works_with_initial_source_relations'] = 0
        issues, decisions = release.evaluate(empty, ('voice_actor',))
        self.assertIn('voice_actor:safe_associated_below_policy', issues)
        self.assertFalse(decisions['voice_actor']['ready'])
        boundary = copy.deepcopy(data)
        boundary['segments']['studio']['safe'].update(catalog_count=1000, fresh_complete_count=899, works_with_initial_source_relations=400)
        self.assertIn('studio:safe_fresh_complete_below_policy', release.evaluate(boundary, ('studio',))[0])
        self.assertEqual(release.POLICY['studio']['min_fresh_complete_percent'], 90)
        self.assertEqual(release.POLICY['voice_actor']['min_fresh_complete_percent'], 98)

    def test_kind_independence_canary_and_full_do_not_restrict_old_modes(self):
        with override_settings(RECOMMENDATION_CREDITS_STUDIO_ENABLED=True, RECOMMENDATION_CREDITS_CANARY_USERS=['XHcode']):
            self.assertEqual(release.rollout('canary', ('studio',))['issues'], [])
            self.assertIn('voice_actor_gate_mismatch', release.rollout('canary', ('studio', 'voice_actor'))['issues'])
            self.assertIn('credits_canary_not_cleared', release.rollout('full', ('studio',))['issues'])
            self.assertIn('credits_gates_not_closed', release.rollout('closed', ('studio',))['issues'])
        with override_settings(RECOMMENDATION_CREDITS_STUDIO_ENABLED=True):
            self.assertEqual(release.rollout('full', ('studio',))['issues'], [])
        for value in ('XHcode', [None], [''], [' XHcode '], ['XHcode', 'XHcode']):
            with override_settings(RECOMMENDATION_CREDITS_CANARY_USERS=value):
                self.assertIn('invalid_credits_canary_config', release.rollout('full', ('studio',))['issues'])
        with override_settings(RECOMMENDATION_EXPLORATION_CANARY_USERS=['XHcode']):
            self.assertIn('existing_exploration_not_full', release.rollout('closed', ('studio',))['issues'])

    def test_coverage_parity_rejects_future_incompatible_snapshots_and_bad_source_codes(self):
        self.prepare()
        before = coverage_audit()
        CreditsSnapshot.objects.update(source_at=timezone.now() + timedelta(days=1))
        future = coverage_audit()
        self.assertEqual(future['segments']['studio']['safe']['fresh_complete_count'], 0)
        CreditsSnapshot.objects.update(source_at=timezone.now(), mapping_version='unmapped')
        self.assertEqual(coverage_audit()['segments']['voice_actor']['safe']['fresh_complete_count'], 0)
        from recommendations.credits_contract import MAPPING_VERSION
        CreditsSnapshot.objects.update(mapping_version=MAPPING_VERSION)
        current = CreditsHead.objects.get(bangumi_id=1, kind='studio').current_id
        CreditsAssertion.objects.filter(observation_id=current, source_eligible=True).update(source_code=42)
        bad = coverage_audit()
        self.assertGreater(bad['integrity_issues']['eligible_fact_wrong_source_code'], 0)
        self.assertEqual(bad['segments']['studio']['safe']['fresh_complete_count'], 24)
        self.assertNotEqual(before['fingerprint'], bad['fingerprint'])
        self.assertEqual(release.evaluate(bad, ('voice_actor',))[0], [])
        self.assertIn('studio:integrity_issues', release.evaluate(bad, ('studio',))[0])

    def test_top_page_capacity_and_changes_during_audit_block_release(self):
        self.prepare()
        data = coverage_audit()
        self.assertEqual(data['read_capacity']['voice_actor']['maximum_entity_works'], 26)
        self.assertEqual(data['read_capacity']['voice_actor']['maximum_possible_page_facts'], 12)
        altered = copy.deepcopy(data)
        altered['read_capacity']['studio']['maximum_possible_page_facts'] = 10001
        self.assertIn('studio:read_capacity_exceeded', release.evaluate(altered, ('studio',))[0])
        with patch.object(release, 'coverage_audit', side_effect=[data, {**data, 'fingerprint': 'changed'}]):
            self.assertIn('credits_or_catalog_changed_during_audit', release.audit()['issues'])

    def test_audit_is_readonly_and_not_a_render_or_browser_acceptance_claim(self):
        self.prepare()
        with CaptureQueriesContext(connection) as queries:
            data = release.audit()
        self.assertEqual(data['status'], 'ready_for_operator_verification', data['issues'])
        self.assertFalse(data['production_writes']); self.assertFalse(data['switches_changed'])
        self.assertEqual(data['production_acceptance'], 'not_performed')
        self.assertEqual(data['rollout']['environment_origin'], 'calling_django_process_not_render_confirmation')
        for forbidden in ('anime_useranimerecord', 'auth_user', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, '\n'.join(q['sql'].lower() for q in queries))

    def test_cli_retains_blocked_report_and_refuses_overwrite(self):
        path = self.fixture.folder / 'release.json'
        with self.assertRaises(CommandError): call_command('audit_credits_release', output=path, stdout=io.StringIO())
        original = path.read_bytes()
        self.assertEqual(json.loads(original)['status'], 'blocked')
        with self.assertRaises(CommandError): call_command('audit_credits_release', output=path, stdout=io.StringIO())
        self.assertEqual(path.read_bytes(), original)
        with patch.object(release, 'coverage_audit', return_value={'schema_ready': False}):
            self.assertIn('required_migration_missing', release.audit()['issues'])

    def test_public_source_profile_has_explicit_scope_and_never_reads_journals(self):
        self.prepare()
        with CaptureQueriesContext(connection) as queries:
            data = profile(samples=1)
        self.assertEqual(data['status'], 'passed_source_query_profile', data['issues'])
        self.assertEqual(len(data['measurements']), 4)
        self.assertEqual(data['safe_catalog_count'], 25)
        self.assertFalse(data['production_writes']); self.assertIsNone(data['server_sql_count'])
        sql = '\n'.join(row['sql'].lower() for row in queries)
        for forbidden in ('anime_useranimerecord', 'auth_user', 'personal_score', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)
        for args in ({'samples': 3}, {'kinds': ('invalid',)}):
            with self.assertRaises(ValueError): profile(**args)

    def test_rollback_rejects_old_operations_and_preserves_public_and_private_data(self):
        from anime.models import UserAnimeRecord
        self.prepare()
        with override_settings(RECOMMENDATION_CREDITS_STUDIO_ENABLED=True):
            token = self.works().data['results'][0]['action_context']
        count = CreditsAssertion.objects.count()
        path = '/api/recommendations/credits/entities/studio/201/items/1/'
        self.assertEqual(self.client.get(path, {'context': token}).status_code, 503)
        self.assertEqual(self.client.post(path + 'want/?context=' + token, {}, format='json').status_code, 503)
        self.assertEqual(CreditsAssertion.objects.count(), count)
        self.assertFalse(UserAnimeRecord.objects.exists())
        self.assertEqual(self.client.get('/api/recommendations/').status_code, 200)
