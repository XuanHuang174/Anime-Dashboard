"""13A offline tests: no Django setup, production data, credentials or HTTP."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from recommendations.credits_contract import (contract_descriptor, normalize_person, normalize_alias,
    alias_index, normalize_subject_character, normalize_archive_staff, normalize_archive_cast,
    validate_provenance, parse_person_query)
from recommendations.credits_audit import audit_sample

FIXTURES = Path(__file__).parent / 'fixtures'


def metadata():
    subjects = {1: {'id': 1, 'type': 2}, 2: {'id': 2, 'type': 2}}
    persons = {1: normalize_person({'id': 1, 'name': '合成声优', 'type': 1, 'career': ['seiyu']}),
               2: normalize_person({'id': 2, 'name': '合成公司', 'type': 2, 'career': ['producer']})}
    characters = {1: {'id': 1, 'name': '合成角色', 'role': 1}}
    bindings = {(1, 1): normalize_subject_character({'subject_id': 1, 'character_id': 1, 'type': 1, 'order': 0})}
    return subjects, persons, characters, bindings


def cast(**changes):
    return {'subject_id': 1, 'person_id': 1, 'character_id': 1, 'type': 0, 'summary': '', **changes}


def staff(**changes):
    return {'subject_id': 1, 'person_id': 2, 'position': 67, 'appear_eps': '', **changes}


class CreditsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = json.loads((FIXTURES / 'credits_source_sample.v1.json').read_text(encoding='utf-8'))

    def test_frozen_descriptor_is_offline_and_does_not_enable_features(self):
        expected = json.loads((FIXTURES / 'credits_contract.v1.json').read_text(encoding='utf-8'))
        self.assertEqual(contract_descriptor(), expected)
        for key in ('serving_enabled', 'database_writes', 'changes_recommendations', 'infers_preferences', 'merges_alias_identities'):
            self.assertIs(expected[key], False)

    def test_only_exact_animation_studio_position_on_company_is_initially_eligible(self):
        subjects, persons, _, _ = metadata()
        fact = normalize_archive_staff(staff(appear_eps='1-3、12'), subjects, persons)
        self.assertTrue(fact['source_eligible']); self.assertEqual(fact['source_episode_note'], '1-3、12')
        self.assertNotIn('episode_numbers', fact)
        for position in (42, 63, 20, 2004, 99999):
            with self.subTest(position=position):
                fact = normalize_archive_staff(staff(position=position), subjects, persons)
                self.assertFalse(fact['source_eligible']); self.assertEqual(fact['source_position'], position)
        self.assertEqual(normalize_archive_staff(staff(position=42), subjects, persons)['position_label'], '製作')
        for kind in (1, 3):
            persons[2]['person_type'] = kind
            self.assertIn('not_company', normalize_archive_staff(staff(), subjects, persons)['review_reasons'])

    def test_cast_joins_subject_character_person_not_global_character(self):
        args = metadata()
        fact = normalize_archive_cast(cast(), *args)
        self.assertTrue(fact['source_eligible'])
        other = normalize_archive_cast(cast(subject_id=2), *args)
        self.assertFalse(other['source_eligible'])
        self.assertIn('missing_subject_character_binding', other['review_reasons'])
        # Namespaces overlap numerically without a self-edge or ID collision.
        self.assertEqual((fact['subject_id'], fact['character_id'], fact['person_id']), (1, 1, 1))

    def test_dubbing_actor_and_unknown_cast_types_never_become_original_cv(self):
        for kind in (1, 2, 3, 4, 5, 6, 999):
            with self.subTest(kind=kind):
                fact = normalize_archive_cast(cast(type=kind), *metadata())
                self.assertFalse(fact['source_eligible']); self.assertEqual(fact['source_cast_type'], kind)
        self.assertEqual(normalize_archive_cast(cast(), *metadata())['cast_label'], 'CV')

    def test_missing_endpoints_and_wrong_person_type_are_not_inferred_from_career(self):
        args = metadata()
        for mapping, reason in ((0, 'missing_subject'), (1, 'missing_person'), (2, 'missing_character'), (3, 'missing_subject_character_binding')):
            changed = copy.deepcopy(args); changed[mapping].clear()
            self.assertIn(reason, normalize_archive_cast(cast(), *changed)['review_reasons'])
        args[1][1]['person_type'] = 2
        self.assertIn('not_individual', normalize_archive_cast(cast(), *args)['review_reasons'])

    def test_unknown_role_is_annotation_only_with_exact_cast_binding(self):
        args = metadata(); args[3][1, 1]['role_type'] = 999
        fact = normalize_archive_cast(cast(), *args)
        self.assertTrue(fact['source_eligible'])
        self.assertEqual(fact['annotation_warnings'], ['unmapped_character_role'])
        self.assertEqual(fact['role_type'], 999)
        self.assertNotIn('role_label', fact)

    def test_cross_media_and_missing_classification_are_not_serving_grants(self):
        for kind in (1, 3, 4, 6):
            args = metadata(); args[0][1]['type'] = kind
            self.assertIn('non_anime_subject', normalize_archive_cast(cast(), *args)['review_reasons'])
            self.assertFalse(normalize_archive_staff(staff(), *args[:2])['source_eligible'])
        args = metadata(); args[0][1]['nsfw'] = None
        fact = normalize_archive_cast(cast(), *args)
        self.assertTrue(fact['source_eligible'])  # source only; NOT safe/approved.
        self.assertNotIn('content_rating', fact)

    def test_strict_archive_schema_ids_and_metadata_drift(self):
        for value in (True, 0, -1, '1', 1.5, 2147483648):
            for normalizer, row, key, args in ((normalize_archive_staff, staff(), 'position', metadata()[:2]),
                                               (normalize_archive_cast, cast(), 'person_id', metadata())):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    normalizer({**row, key: value}, *args)
        for row in ({**staff(), 'owner_id': 1}, {'subject_id': 1}, {**staff(), 'appear_eps': 'a\x00b'}):
            with self.assertRaises(ValueError): normalize_archive_staff(row, *metadata()[:2])
        args = metadata(); args[1][1]['person_type'] = True
        with self.assertRaises(ValueError): normalize_archive_cast(cast(), *args)
        args = metadata(); args[2][1]['id'] = 2
        with self.assertRaises(ValueError): normalize_archive_cast(cast(), *args)

    def test_alias_collision_never_merges_identity_and_no_romanization_is_invented(self):
        aliases = [{'person_id': 1, 'name': 'ＡＢＣ'}, {'person_id': 2, 'name': 'abc'}, {'person_id': 3, 'name': '同名'}]
        self.assertEqual(alias_index(aliases)['abc'], [1, 2])
        self.assertEqual(normalize_alias(1, '青山吉能')['search_key'], '青山吉能')
        with self.assertRaises(ValueError): normalize_alias(1, '名\u200b称')
        for kind in (True, 0, 4):
            with self.assertRaises(ValueError): normalize_person({'id': 1, 'name': 'x', 'type': kind, 'career': []})

    def test_search_ids_are_person_namespaced_and_external_urls_not_followed(self):
        for query in ('https://bgm.tv/person/2481', 'http://bangumi.tv/person/2481/', 'person:2481'):
            self.assertEqual(parse_person_query(query), {'person_id': 2481, 'name_query': None})
        self.assertEqual(parse_person_query('ＣｌｏｖｅｒＷｏｒｋｓ')['name_query'], 'cloverworks')
        for value in ('2481', 'https://bgm.tv/subject/2481', 'https://example.org/person/1', 'person:1?token=x', 'x\n', 'a'*121):
            with self.subTest(value=value), self.assertRaises(ValueError): parse_person_query(value)

    def test_provenance_requires_all_members_hashes_fixed_archive_and_timezone(self):
        source = {key: self.sample[key] for key in ('snapshot_version', 'source_reference', 'archive_sha256', 'observed_at', 'members')}
        self.assertEqual(validate_provenance(source), source)
        for change in ({'observed_at': '2026-10-27'}, {'archive_sha256': 'x'}, {'members': {}},
                       {'snapshot_version': 'latest'}, {'source_reference': source['source_reference']+'?token=x'}):
            with self.assertRaises(ValueError): validate_provenance({**source, **change})

    def test_real_sample_joint_studio_original_cast_and_inconsistent_metadata(self):
        report = audit_sample(self.sample)
        self.assertEqual((report['input_rows'], report['normalized_rows']), (2033, 2033))
        self.assertEqual(report['eligible_kind_counts'], {'studio': 9, 'cast': 245})
        self.assertEqual(report['row_errors'], [])
        self.assertEqual(report['cast_type_counts'], {'0': 246, '1': 1, '3': 80, '5': 9})
        studio = [fact['person_id'] for fact in report['eligible_source_assertions'] if fact['subject_id'] == 265 and fact['kind'] == 'studio']
        self.assertEqual(studio, [93, 696])
        self.assertEqual(report['review_reason_counts']['not_individual'], 1)
        self.assertEqual(report['duplicate_rows'], 0)
        self.assertFalse(report['production_writes'])

    def test_duplicate_conflict_and_missing_binding_diagnostics_never_repair_rows(self):
        sample = copy.deepcopy(self.sample); sample['staff'].append(copy.deepcopy(sample['staff'][0]))
        report = audit_sample(sample)
        self.assertEqual(report['duplicate_rows'], 1)
        sample['staff'][-1]['appear_eps'] = 'source disagreement'
        self.assertTrue(audit_sample(sample)['conflicting_credit_keys'])
        sample = copy.deepcopy(self.sample); sample['subject_characters'] = []
        report = audit_sample(sample)
        self.assertNotIn('cast', report['eligible_kind_counts'])
        self.assertEqual(report['review_reason_counts']['missing_subject_character_binding'], 336)

    def test_bounded_audit_rejects_duplicate_entities_and_outside_sources(self):
        for key in ('subjects', 'persons', 'characters', 'subject_characters'):
            sample = copy.deepcopy(self.sample); sample[key].append(copy.deepcopy(sample[key][0]))
            with self.subTest(key=key), self.assertRaises(ValueError): audit_sample(sample)
        sample = copy.deepcopy(self.sample); sample['cast'][0]['subject_id'] = 1
        self.assertEqual(len(audit_sample(sample)['row_errors']), 1)
        sample['aliases'] = [sample['aliases'][0]] * 5001
        with self.assertRaises(ValueError): audit_sample(sample)

    def test_offline_audit_no_network_and_cli_never_overwrites(self):
        with patch('socket.socket', side_effect=AssertionError('No HTTP allowed')):
            self.assertEqual(audit_sample(self.sample)['row_errors'], [])
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            command = [sys.executable, '-m', 'recommendations.credits_audit', '--input', str(FIXTURES / 'credits_source_sample.v1.json'), '--output', str(output)]
            result = subprocess.run(command, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            first = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=20).returncode, 0)
            self.assertEqual(output.read_bytes(), first)


if __name__ == '__main__':
    unittest.main()
