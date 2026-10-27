import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections, IntegrityError
from datetime import timedelta
from django.utils import timezone
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.credits_models import (CreditsState, CreditsSnapshot, CreditsSubject, CreditsPerson,
    CreditsCharacter, CreditsAlias, CreditsObservation, CreditsHead, CreditsAssertion)
from recommendations.credits_source import ArchivePlan, digest
from recommendations.credits_store import publish_batch, CreditsConflict
from recommendations.credits_coverage import coverage_audit
from .test_credits_source import CreditsArchiveFixture


class CreditsBackendTests(TransactionTestCase):
    def setUp(self):
        self.fixture = CreditsArchiveFixture()
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'目录{i}', nsfw=rating)
            for i, rating in ((1, False), (2, True), (3, None))])

    def tearDown(self): self.fixture.close()

    def batch(self, version='dump-2026-10-20.000000Z', seeds=(1, 2)):
        checksum = self.fixture.archive(version)
        with ArchivePlan(self.fixture.path, checksum, list(seeds)) as plan:
            return plan.batch(list(seeds))

    def test_publication_is_atomic_idempotent_and_does_not_change_business_data(self):
        before = list(RecommendationAnime.objects.values())
        value = self.batch()
        with CaptureQueriesContext(connection) as captured:
            result = publish_batch(value)
        self.assertEqual(result['published'], 4)
        self.assertLessEqual(len(captured), 40)
        self.assertFalse(publish_batch(value)['changed'])
        self.assertEqual(CreditsObservation.objects.count(), 4)
        self.assertEqual(before, list(RecommendationAnime.objects.values()))
        self.assertFalse(RecommendationPreference.objects.exists())
        self.assertEqual(CreditsAlias.objects.filter(search_key='synthetic actor').count(), 1)

    def test_partial_voice_keeps_previous_version_while_complete_studio_advances(self):
        publish_batch(self.batch()); old = CreditsHead.objects.get(bangumi_id=1, kind='voice_actor').current_id
        self.fixture.rows['subject-characters.jsonlines'] = []
        self.fixture.rows['person.jsonlines'][0]['name'] = '新版姓名'
        result = publish_batch(self.batch('dump-2026-10-21.000000Z'))
        head = CreditsHead.objects.get(bangumi_id=1, kind='voice_actor')
        self.assertEqual(head.current_id, old); self.assertNotEqual(head.last_attempt_id, old)
        self.assertEqual(head.last_attempt.coverage, 'partial')
        old_person = CreditsPerson.objects.get(snapshot=head.current.snapshot, person_id=101)
        self.assertEqual(old_person.name, '合成声优')
        self.assertEqual(result['quarantined'], 1)

    def test_empty_failed_and_not_loaded_are_distinct(self):
        publish_batch(self.batch(seeds=(1, 3)))
        self.assertIsNone(CreditsHead.objects.get(bangumi_id=3, kind='studio').current_id)
        self.fixture.rows['person-characters.jsonlines'] = []
        publish_batch(self.batch('dump-2026-10-21.000000Z', seeds=(1,)))
        self.assertEqual(CreditsHead.objects.get(bangumi_id=1, kind='voice_actor').current.coverage, 'empty_observed')
        self.assertFalse(CreditsHead.objects.filter(bangumi_id=2).exists())

    def test_failed_assertion_insert_rolls_back_snapshot_alias_and_pointers(self):
        publish_batch(self.batch()); previous = list(CreditsHead.objects.values())
        new = self.batch('dump-2026-10-21.000000Z')
        with patch.object(CreditsAssertion.objects, 'bulk_create', side_effect=IntegrityError('synthetic failure')):
            with self.assertRaises(IntegrityError): publish_batch(new)
        self.assertEqual(previous, list(CreditsHead.objects.values()))
        self.assertEqual(CreditsSnapshot.objects.count(), 1)
        self.assertEqual(CreditsState.objects.get(pk=1).revision, 1)

    def test_old_archive_rejected_for_existing_root_but_disjoint_backfill_is_versioned(self):
        publish_batch(self.batch('dump-2026-10-21.000000Z', seeds=(1,)))
        with self.assertRaises(CreditsConflict): publish_batch(self.batch(seeds=(1,)))
        self.assertEqual(publish_batch(self.batch(seeds=(2,)))['published'], 2)
        self.assertEqual(CreditsSnapshot.objects.count(), 2)

    def test_tampered_plan_or_same_snapshot_different_bytes_rejected(self):
        value = self.batch(); publish_batch(value)
        altered = copy.deepcopy(value); altered['observations'][0]['coverage'] = 'partial'
        with self.assertRaises(CreditsConflict): publish_batch(altered)
        self.fixture.rows['person.jsonlines'][0]['name'] = 'changed source'
        with self.assertRaises(CreditsConflict): publish_batch(self.batch())

    def test_operator_command_preview_apply_matching_plan_and_resume(self):
        checksum = self.fixture.archive()
        preview, applied, replayed = (self.fixture.folder / name for name in ('preview.json', 'apply.json', 'replay.json'))
        arguments = dict(input=self.fixture.path, expected_sha256=checksum, catalog=True, stdout=io.StringIO())
        try:
            call_command('sync_credits_archive', report=preview, **arguments)
            self.assertFalse(CreditsSnapshot.objects.exists())
            plan = json.loads(preview.read_text())['plan_fingerprint']
            with self.assertRaises(CommandError): call_command('sync_credits_archive', report=applied, apply=True, expected_plan='wrong', **arguments)
            applied.unlink()  # One explicit failed-operation report.
            with CaptureQueriesContext(connection) as captured:
                call_command('sync_credits_archive', report=applied, apply=True, expected_plan=plan, **arguments)
            writes = [q['sql'] for q in captured if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
            self.assertTrue(writes); self.assertTrue(all('recommendations_credits' in sql for sql in writes))
            call_command('sync_credits_archive', report=replayed, apply=True, expected_plan=plan, **arguments)
            self.assertFalse(json.loads(replayed.read_text())['database_writes'])
            with self.assertRaises(CommandError): call_command('sync_credits_archive', report=preview, **arguments)
        finally:
            preview.unlink(missing_ok=True)
            applied.unlink(missing_ok=True)
            replayed.unlink(missing_ok=True)

    def test_coverage_is_readonly_separate_denominators_and_does_not_read_private_records(self):
        publish_batch(self.batch())
        with CaptureQueriesContext(connection) as captured: result = coverage_audit()
        self.assertEqual(result['catalog_count'], 3)
        self.assertEqual(result['segments']['studio']['safe']['fresh_complete_count'], 1)
        self.assertEqual(result['segments']['studio']['adult']['catalog_count'], 1)
        self.assertEqual(result['segments']['voice_actor']['unknown']['coverage_states'], {'not_loaded': 1})
        self.assertEqual(result['integrity_issues'], {})
        sql = '\n'.join(q['sql'].lower() for q in captured)
        for forbidden in ('useranimerecord', 'personal_score', 'recommendationpreference', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)

    def test_concurrent_replay_serializes_one_revision(self):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL row locking only')
        value = self.batch()
        def publish():
            try: return publish_batch(copy.deepcopy(value))
            finally: connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: publish(), range(2)))
        self.assertEqual(sum(row['changed'] for row in results), 1)
        self.assertEqual(CreditsState.objects.get(pk=1).revision, 1)
        self.assertEqual(CreditsObservation.objects.count(), 4)

    def test_command_failure_reports_committed_batches_and_can_resume(self):
        checksum = self.fixture.archive()
        preview = self.fixture.folder / 'before.json'
        failed = self.fixture.folder / 'failure.json'
        resumed = self.fixture.folder / 'resumed.json'
        arguments = dict(input=self.fixture.path, expected_sha256=checksum, ids='1,2', batch_size=1, stdout=io.StringIO())
        calls = []
        def fail_second(batch):
            calls.append(1)
            if len(calls) == 2: raise IntegrityError('synthetic second-batch failure')
            return publish_batch(batch)
        try:
            call_command('sync_credits_archive', report=preview, **arguments)
            plan = json.loads(preview.read_text())['plan_fingerprint']
            with patch('recommendations.management.commands.sync_credits_archive.publish_batch', side_effect=fail_second):
                with self.assertRaises(CommandError):
                    call_command('sync_credits_archive', report=failed, apply=True, expected_plan=plan, **arguments)
            report = json.loads(failed.read_text())
            self.assertTrue(report['database_writes']); self.assertEqual(len(report['publication']), 1)
            self.assertEqual(CreditsObservation.objects.count(), 2)
            call_command('sync_credits_archive', report=resumed, apply=True, expected_plan=plan, **arguments)
            self.assertEqual(CreditsObservation.objects.count(), 4)
        finally:
            preview.unlink(missing_ok=True)
            failed.unlink(missing_ok=True)
            resumed.unlink(missing_ok=True)

    def test_audit_marks_old_sources_stale_and_reports_missing_endpoint_rate(self):
        publish_batch(self.batch())
        self.fixture.rows['subject-characters.jsonlines'] = []
        publish_batch(self.batch('dump-2026-10-21.000000Z'))
        report = coverage_audit()
        self.assertEqual(report['segments']['voice_actor']['safe']['endpoint_issue_rates']['missing_subject_character_binding'], 1.0)
        self.assertEqual(report['next_safe_source_ids']['voice_actor'], [1])
        with patch('recommendations.credits_coverage.timezone.now', return_value=timezone.now() + timedelta(days=90)):
            report = coverage_audit()
        self.assertEqual(report['segments']['studio']['safe']['coverage_states'], {'stale': 1})
        self.assertEqual(report['segments']['studio']['safe']['fresh_complete_count'], 0)
