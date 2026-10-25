"""12A pure tests: no Django setup, database, credentials or network access."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from recommendations.series_audit import audit_sample
from recommendations.series_contract import (KINDS, contract_descriptor, inspect_assertions,
    normalize_api_relation, normalize_archive_relation, validate_provenance)

FIXTURES = Path(__file__).parent / 'fixtures'


def archive_row(source=1, target=2, relation=3, order=0):
    return {'subject_id': source, 'related_subject_id': target, 'relation_type': relation, 'order': order}


def assertion(source=1, target=2, relation=3):
    return normalize_archive_relation(archive_row(source, target, relation), {source: 2, target: 2})


class SeriesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = json.loads((FIXTURES / 'series_source_sample.v1.json').read_text(encoding='utf-8'))

    def test_descriptor_is_explicitly_offline_and_locked(self):
        expected = json.loads((FIXTURES / 'series_contract.v1.json').read_text(encoding='utf-8'))
        self.assertEqual(contract_descriptor(), expected)
        for key in ('serving_enabled', 'database_writes', 'infers_series_membership',
                    'infers_watch_order', 'changes_recommendations'):
            self.assertIs(expected[key], False)

    def test_numeric_archive_and_text_api_agree_for_every_anime_kind(self):
        for kind in KINDS:
            with self.subTest(kind=kind.key):
                archive = assertion(relation=kind.code)
                api = normalize_api_relation(1, 2, {'id': 2, 'type': 2, 'relation': kind.label,
                    'name': 'Private-looking title ignored', 'images': {'large': 'https://example.invalid'}})
                for key in ('source_bangumi_id', 'target_bangumi_id', 'kind', 'section', 'anime_pair', 'requires_review'):
                    self.assertEqual(archive[key], api[key])
                self.assertNotIn('images', api)
                self.assertNotIn('name', api)

    def test_direction_summary_target_is_not_its_parent_and_order_is_source_only(self):
        row = normalize_archive_relation(archive_row(328609, 436738, 4, 19), {328609: 2, 436738: 2})
        self.assertEqual((row['source_bangumi_id'], row['target_bangumi_id'], row['kind']),
                         (328609, 436738, 'summary'))
        self.assertEqual(row['source_order'], 19)
        self.assertNotIn('watch_order', row)
        self.assertNotIn('release_format', row)

    def test_raw_archive_codes_from_other_media_never_become_anime_relations(self):
        for code in (1, 2, 3, 1004, 3003, 4014):
            for target_type in (1, 3, 4, 6):
                row = normalize_archive_relation(archive_row(relation=code), {1: 2, 2: target_type})
                self.assertEqual(row['kind'], 'cross_media')
                self.assertFalse(row['anime_pair'])
                self.assertFalse(row['requires_review'])
                self.assertEqual(row['source_relation'], code)

    def test_unknown_codes_labels_and_missing_metadata_never_fallback_to_sequel(self):
        rows = [normalize_archive_relation(archive_row(relation=12345), {1: 2, 2: 2}),
                normalize_archive_relation(archive_row(relation=3), {1: 2}),
                normalize_api_relation(1, 2, {'id': 2, 'type': 2, 'relation': '新关系'}),
                normalize_api_relation(1, 2, {'id': 2, 'type': 2, 'relation': 'Sequel'})]
        self.assertTrue(all(row['kind'] == 'unknown' and row['requires_review'] for row in rows))
        self.assertEqual(rows[1]['target_subject_type'], None)

    def test_invalid_identity_self_edges_unknown_types_and_source_schema_rejected(self):
        for invalid in (True, 0, -1, 1.5, '1.0', 2147483648):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_archive_relation(archive_row(target=invalid), {1: 2})
        cases = [archive_row(target=1), archive_row(relation=True), archive_row(relation=0),
                 archive_row(order=-1), archive_row(order='0'), {**archive_row(), 'owner': 42}, {}]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(ValueError):
                normalize_archive_relation(row, {1: 2, 2: 2})
        for types in ({}, {1: 1, 2: 2}, {1: 2, 2: 5}, {1: 2, 2: True}):
            with self.subTest(types=types), self.assertRaises(ValueError):
                normalize_archive_relation(archive_row(), types)

    def test_api_does_not_accept_missing_types_bad_labels_or_non_anime_source(self):
        cases = [{'id': 2, 'type': None, 'relation': '续集'}, {'id': 2, 'relation': '续集'},
                 {'id': 2, 'type': 2, 'relation': '续集\n'}, {'id': 2, 'type': 2, 'relation': '续\u200b集'},
                 {'id': 2, 'type': 2, 'relation': ''},
                 {'id': 2, 'type': 2, 'relation': 3}, {'id': 1, 'type': 2, 'relation': '续集'}]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(ValueError):
                normalize_api_relation(1, 2, row)
        with self.assertRaises(ValueError):
            normalize_api_relation(1, 1, {'id': 2, 'type': 2, 'relation': '续集'})

    def test_observation_provenance_requires_official_source_hash_and_timezone(self):
        valid = self.sample['provenance']
        self.assertEqual(validate_provenance(valid), valid)
        mutations = [{'source_reference': 'https://example.com/data.zip'},
            {'source_reference': valid['source_reference'] + '?token=secret'},
            {'source_kind': 'manual'}, {'payload_sha256': 'not-a-digest'},
            {'observed_at': '2026-10-25'}, {'snapshot_version': 'latest'}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_provenance({**valid, **mutation})
        with self.assertRaises(ValueError):
            validate_provenance({**valid, 'token': 'secret'})
        api = {**valid, 'source_kind': 'bangumi_api', 'snapshot_version': 'local-observation-1',
               'source_reference': 'https://api.bgm.tv/v0/subjects/328609/subjects'}
        self.assertEqual(validate_provenance(api), api)
        with self.assertRaises(ValueError):
            validate_provenance({**api, 'snapshot_version': 'observation\n1'})

    def test_reciprocals_audited_without_generating_or_erasing_edges(self):
        one = assertion()
        self.assertEqual(len(inspect_assertions([one])['missing_reciprocals']), 1)
        self.assertFalse(inspect_assertions([one])['chronology_cycle'])
        reverse = assertion(2, 1, 2)
        self.assertEqual(inspect_assertions([one, reverse])['missing_reciprocals'], [])
        self.assertFalse(inspect_assertions([one, reverse])['chronology_cycle'])
        self.assertEqual(inspect_assertions([one, one])['duplicate_count'], 1)
        self.assertEqual(one['kind'], 'sequel')

    def test_cycles_branches_and_disagreeing_relations_are_flagged_not_linearized(self):
        rows = [assertion(1, 2), assertion(2, 3), assertion(3, 1)]
        self.assertTrue(inspect_assertions(rows)['chronology_cycle'])
        self.assertFalse(inspect_assertions([assertion(1, 2), assertion(1, 3)])['chronology_cycle'])
        conflicting = inspect_assertions([assertion(), assertion(2, 1)])
        self.assertEqual(len(conflicting['reciprocal_mismatches']), 2)
        multiple = inspect_assertions([assertion(), assertion(relation=4)])
        self.assertEqual(multiple['multiple_relation_pairs'], [[1, 2, ['sequel', 'summary']]])

    def test_weak_links_never_generate_chronology_or_inverse_claims(self):
        rows = [assertion(1, 2, 8), assertion(2, 3, 7), assertion(3, 1, 14)]
        report = inspect_assertions(rows)
        self.assertFalse(report['chronology_cycle'])
        self.assertEqual(report['missing_reciprocals'], [])
        self.assertEqual(len(rows), 3)

    def test_real_archive_sample_has_separate_anime_and_cross_media_rows(self):
        report = audit_sample(self.sample)
        self.assertEqual((report['input_rows'], report['normalized_rows'], report['anime_pair_rows']), (415, 415, 64))
        self.assertEqual(report['kind_counts']['cross_media'], 351)
        self.assertEqual(report['row_errors'], [])
        self.assertEqual(report['diagnostics']['unknown_count'], 0)
        self.assertFalse(report['diagnostics']['chronology_cycle'])

    def test_real_recap_chain_is_also_a_sequel_chain_not_a_global_work_type(self):
        report = audit_sample(self.sample)
        edges = {(row['source_bangumi_id'], row['target_bangumi_id']): row['kind']
                 for row in report['anime_assertions']}
        self.assertEqual(edges[328609, 436738], 'summary')
        self.assertEqual(edges[436738, 328609], 'full_story')
        self.assertEqual(edges[436738, 459642], 'sequel')
        self.assertEqual(edges[328609, 459642], 'summary')
        self.assertEqual(edges[265, 772], 'alternative_version')
        self.assertEqual(edges[3774, 12426], 'side_story')
        subjects = {row['id']: row for row in self.sample['subjects']}
        self.assertEqual(edges[10639, 290], 'sequel')
        self.assertGreater(subjects[10639]['date'], subjects[290]['date'])

    def test_sample_empty_missing_metadata_and_bad_rows_are_explicit(self):
        for invalid in (None, {}, {**self.sample, 'relations': [None] * 5001},
                        {**self.sample, 'seed_ids': [1424, 1424]},
                        {**self.sample, 'subjects': [None]}):
            with self.subTest(invalid_type=type(invalid)), self.assertRaises(ValueError):
                audit_sample(invalid)
        sample = copy.deepcopy(self.sample)
        sample['relations'] = []
        self.assertEqual(audit_sample(sample)['seed_ids_without_normalized_assertions'], sorted(sample['seed_ids']))
        sample['relations'] = [archive_row(1424, 9999999), archive_row(1424, 1424), None]
        report = audit_sample(sample)
        self.assertEqual(report['kind_counts'], {'unknown': 1})
        self.assertEqual(len(report['row_errors']), 2)
        self.assertEqual(report['normalized_rows'], 1)
        sample['subjects'].append(sample['subjects'][0])
        with self.assertRaises(ValueError):
            audit_sample(sample)

    def test_audit_cli_leaves_source_unchanged_and_refuses_overwrite(self):
        source = FIXTURES / 'series_source_sample.v1.json'
        previous = source.read_bytes()
        folder = Path(tempfile.mkdtemp(prefix='series-12a-'))
        output = folder / 'report.json'
        try:
            cmd = [sys.executable, '-m', 'recommendations.series_audit', '--input', str(source), '--output', str(output)]
            first = subprocess.run(cmd, capture_output=True, encoding='utf-8')
            self.assertEqual(first.returncode, 0, first.stderr)
            content = output.read_bytes()
            second = subprocess.run(cmd, capture_output=True, encoding='utf-8')
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(output.read_bytes(), content)
        finally:
            output.unlink(missing_ok=True)  # This single test-owned report only.
            folder.rmdir()  # Empty directory; never recursively delete.
        self.assertEqual(source.read_bytes(), previous)

    def test_audit_cannot_connect_to_network_or_database(self):
        with patch('socket.socket', side_effect=AssertionError('No network')):
            report = audit_sample(self.sample)
        self.assertFalse(report['production_writes'])
        # Pure module entrypoint runs without loading .env or Django.
        result = subprocess.run([sys.executable, '-c',
            "import sys; import recommendations.series_audit; assert 'django' not in sys.modules"],
            capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
