"""Synthetic archives only; no Django or network required."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from recommendations.credits_aliases import parse_names
from recommendations.credits_source import ArchivePlan, MEMBERS
from recommendations.source_pipeline import SourceError


class CreditsArchiveFixture:
    def __init__(self):
        self.folder = Path(tempfile.mkdtemp(prefix='credits-13b-test-'))
        self.path = self.folder / 'dump-2026-10-20.000000Z.zip'
        self.rows = {
            'subject.jsonlines': [{'id': i, 'type': 2, 'name': f'合成作品{i}', 'nsfw': False} for i in (1, 2)],
            'person.jsonlines': [
                {'id': 101, 'name': '合成声优', 'type': 1, 'career': ['seiyu'], 'infobox': '{{Infobox Crt\n|别名= {\n[罗马字|Synthetic Actor]\n}\n}}'},
                {'id': 201, 'name': '合成公司', 'type': 2, 'career': ['producer'], 'infobox': '{{Infobox Crt\n|简体中文名= 合成动画\n}}'}],
            'character.jsonlines': [{'id': 301, 'name': '合成角色', 'role': 1}],
            'subject-persons.jsonlines': [{'subject_id': i, 'person_id': 201, 'position': 67, 'appear_eps': ''} for i in (1, 2)],
            'person-characters.jsonlines': [{'subject_id': 1, 'person_id': 101, 'character_id': 301, 'type': 0, 'summary': ''}],
            'subject-characters.jsonlines': [{'subject_id': 1, 'character_id': 301, 'type': 1, 'order': 0}]}

    def archive(self, version='dump-2026-10-20.000000Z'):
        target = self.folder / (version + '.zip')
        if target != self.path:
            self.path.unlink(missing_ok=True)  # One exact fixture archive only.
            self.path = target
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, rows in self.rows.items():
                archive.writestr(name, ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows).encode())
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def close(self):
        self.path.unlink(missing_ok=True)
        self.folder.rmdir()


class CreditsSourceTests(unittest.TestCase):
    def setUp(self): self.fixture = CreditsArchiveFixture()
    def tearDown(self): self.fixture.close()

    def test_names_parser_handles_explicit_lists_and_never_translates(self):
        box = '{{Infobox Crt\n|简体中文名= 合成名字\n|别名= {\n[罗马字|Example Name]\n[日文名|]\n}\n|生日= 不采集\n}}'
        result = parse_names('原名', box)
        self.assertEqual(result, {'names': ['原名', '合成名字', 'Example Name'], 'status': 'observed', 'issues': []})
        self.assertEqual(parse_names('原名', '')['names'], ['原名'])

    def test_bad_wiki_keeps_primary_name_and_marks_partial(self):
        for box in ('not a wiki', '{{Infobox Crt\n|别名= <b>name</b>\n}}',
                    '{{Infobox Crt\n|别名= {\n[昵称|不当别名合并]\n}}',
                    '{{Infobox Crt\n|别名= [[链接]]\n}}'):
            result = parse_names('原名', box)
            self.assertEqual(result['names'], ['原名']); self.assertEqual(result['status'], 'partial')

    def test_plan_is_stable_id_order_independent_and_removes_only_its_spool(self):
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [2, 1]) as plan:
            spool = plan.spool; self.assertTrue(spool.exists())
            report = plan.preview(); batch = plan.batch([1, 2])
            self.assertEqual(report['coverage']['voice_actor']['safe']['complete_observations'], 2)
            self.assertEqual([o['coverage'] for o in batch['observations']], ['observed', 'observed', 'observed', 'empty_observed'])
        self.assertFalse(spool.exists()); self.assertTrue(self.fixture.path.exists())
        with ArchivePlan(self.fixture.path, checksum, [1, 2]) as plan:
            self.assertEqual(report['plan_fingerprint'], plan.preview()['plan_fingerprint'])

    def test_missing_binding_or_person_quarantines_voice_without_blocking_company(self):
        self.fixture.rows['subject-characters.jsonlines'] = []
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [1]) as plan:
            studio, voice = plan.batch([1])['observations']
            self.assertEqual(studio['coverage'], 'observed')
            self.assertEqual(voice['coverage'], 'partial')
            self.assertEqual(voice['diagnostics']['issues']['missing_subject_character_binding'], 1)

    def test_duplicate_person_identity_never_uses_last_metadata(self):
        self.fixture.rows['person.jsonlines'].append(copy.deepcopy(self.fixture.rows['person.jsonlines'][0]))
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [1]) as plan:
            batch = plan.batch([1]); voice = batch['observations'][1]
            self.assertEqual(voice['coverage'], 'partial')
            self.assertNotIn(101, [p['person_id'] for p in batch['persons']])

    def test_original_dubbing_and_unknown_type_and_role_have_different_effects(self):
        raw = self.fixture.rows['person-characters.jsonlines'][0]
        self.fixture.rows['person-characters.jsonlines'].append({**raw, 'type': 3})
        self.fixture.rows['subject-characters.jsonlines'][0]['type'] = 999
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [1]) as plan:
            obs = plan.batch([1])['observations'][1]
            self.assertEqual(obs['coverage'], 'observed'); self.assertEqual(obs['diagnostics']['eligible_rows'], 1)
            self.assertEqual(obs['facts'][0]['annotation_warnings'], ['unmapped_character_role'])
        self.fixture.rows['person-characters.jsonlines'].append({**raw, 'type': 999})
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [1]) as plan:
            self.assertEqual(plan.batch([1])['observations'][1]['coverage'], 'partial')

    def test_bad_digest_missing_member_and_bad_json_stop_before_publication(self):
        checksum = self.fixture.archive()
        with self.assertRaises(SourceError):
            with ArchivePlan(self.fixture.path, '0'*64, [1]): pass
        self.fixture.rows.pop('character.jsonlines')
        checksum = self.fixture.archive()
        with self.assertRaises(SourceError):
            with ArchivePlan(self.fixture.path, checksum, [1]): pass

    def test_scope_counts_catalog_denominator_and_unknown_roots_are_explicit(self):
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [1, 2, 3]) as plan:
            report = plan.preview([(1, False), (2, True), (3, None)])
            self.assertEqual(report['coverage']['studio']['adult']['denominator'], 1)
            self.assertEqual(report['coverage']['studio']['unknown']['failed'], 1)
            self.assertEqual(report['scope'], 'catalog_source_preflight')

    def test_row_limit_never_becomes_a_complete_empty_observation(self):
        checksum = self.fixture.archive()
        with patch('recommendations.credits_source.MAX_ROOT_ROWS', 0):
            with ArchivePlan(self.fixture.path, checksum, [1]) as plan:
                self.assertEqual(plan.batch([1])['observations'][0]['coverage'], 'partial')

    def test_bad_alias_does_not_erase_confirmed_credits(self):
        self.fixture.rows['person.jsonlines'][0]['infobox'] = 'unsupported'
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [1]) as plan:
            batch = plan.batch([1]); person = next(p for p in batch['persons'] if p['person_id'] == 101)
            self.assertEqual(person['alias_status'], 'partial')
            self.assertEqual(batch['observations'][1]['coverage'], 'observed')

    def test_unattributable_source_id_never_becomes_a_successful_empty_result(self):
        self.fixture.rows['person-characters.jsonlines'][0]['subject_id'] = '1'
        checksum = self.fixture.archive()
        with self.assertRaises(SourceError):
            with ArchivePlan(self.fixture.path, checksum, [1]): pass

    def test_duplicate_credits_and_schema_drift_are_partial_not_silently_deduplicated(self):
        self.fixture.rows['subject-persons.jsonlines'].append(copy.deepcopy(self.fixture.rows['subject-persons.jsonlines'][0]))
        self.fixture.rows['person-characters.jsonlines'][0]['extra'] = 'unknown schema'
        checksum = self.fixture.archive()
        with ArchivePlan(self.fixture.path, checksum, [1]) as plan:
            self.assertEqual([o['coverage'] for o in plan.batch([1])['observations']], ['partial', 'partial'])


if __name__ == '__main__': unittest.main()
