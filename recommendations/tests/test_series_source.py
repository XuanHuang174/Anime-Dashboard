"""Pure synthetic ZIPs; no Django, database, third-party calls or bulk cleanup."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from contextlib import contextmanager

from recommendations.series_source import prepare_archive, seed_ids, _node
from recommendations.source_pipeline import SourceError


class SeriesArchiveSourceTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='series-12b-'))
        self.path = self.folder / 'dump-2026-10-20.000000Z.zip'

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        self.folder.rmdir()

    def archive(self, subjects=None, relations=None):
        subjects = subjects if subjects is not None else [
            {'id': i, 'type': 2, 'name': f'Original {i}', 'name_cn': f'合成作品{i}',
             'date': '2026-01-01', 'platform': 1, 'nsfw': False} for i in (1, 2, 3)]
        relations = relations if relations is not None else [
            {'subject_id': 1, 'related_subject_id': 2, 'relation_type': 3, 'order': 0},
            {'subject_id': 2, 'related_subject_id': 1, 'relation_type': 2, 'order': 0}]
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, rows in [('subject.jsonlines', subjects), ('subject-relations.jsonlines', relations)]:
                archive.writestr(name, ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode())
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_verified_zip_produces_stable_bounded_incremental_batch(self):
        checksum = self.archive()
        before = self.path.read_bytes()
        prepared = prepare_archive(self.path, checksum, [1, 2, 1])
        self.assertEqual(prepared['seed_ids'], [1, 2])
        self.assertFalse(prepared['partial'])
        self.assertEqual(len(prepared['assertions']), 2)
        self.assertEqual(prepared['fingerprint'], prepare_archive(self.path, checksum, [2, 1])['fingerprint'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_bad_digest_missing_members_unknown_source_and_source_schema_fail(self):
        checksum = self.archive()
        with self.assertRaises(SourceError): prepare_archive(self.path, '0' * 64, [1])
        with self.assertRaises(SourceError): prepare_archive(self.path, checksum, [99])
        with self.assertRaises(SourceError): prepare_archive(self.path, checksum, [1], timeout_seconds=0)
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('subject.jsonlines', '')
        with self.assertRaises(SourceError):
            prepare_archive(self.path, hashlib.sha256(self.path.read_bytes()).hexdigest(), [1])
        checksum = self.archive()
        real_zip = zipfile.ZipFile
        @contextmanager
        def replace_after_parse(*args):
            with real_zip(*args) as source:
                yield source
            self.path.write_bytes(b'replaced-during-read')
        with patch('recommendations.series_source.zipfile.ZipFile', side_effect=replace_after_parse), self.assertRaises(SourceError):
            prepare_archive(self.path, checksum, [1])

    def test_unknown_missing_target_duplicate_and_cycle_are_partial_not_published(self):
        base = {'subject_id': 1, 'related_subject_id': 2, 'relation_type': 12345, 'order': 0}
        checksum = self.archive(relations=[base])
        self.assertTrue(prepare_archive(self.path, checksum, [1])['partial'])
        base['related_subject_id'] = 99
        checksum = self.archive(relations=[base])
        batch = prepare_archive(self.path, checksum, [1])
        self.assertTrue(batch['partial'])
        self.assertEqual(batch['assertions'], [])
        base.update(related_subject_id=2, relation_type=3)
        checksum = self.archive(relations=[base, base])
        self.assertEqual(len(prepare_archive(self.path, checksum, [1])['assertions']), 1)
        self.assertTrue(prepare_archive(self.path, checksum, [1])['partial'])
        checksum = self.archive(relations=[base, {**base, 'subject_id': 2, 'related_subject_id': 1}])
        self.assertTrue(prepare_archive(self.path, checksum, [1, 2])['partial'])

    def test_cross_media_is_retained_without_interpreting_anime_codes_or_foreign_dates(self):
        checksum = self.archive(subjects=[{'id': 1, 'type': 2, 'nsfw': False},
            {'id': 2, 'type': 3, 'date': 'not-an-anime-date'}],
            relations=[{'subject_id': 1, 'related_subject_id': 2, 'relation_type': 3003, 'order': 0}])
        batch = prepare_archive(self.path, checksum, [1])
        self.assertFalse(batch['partial'])
        self.assertEqual(batch['assertions'][0]['kind'], 'cross_media')

    def test_date_precision_unknown_ratings_and_invalid_values_are_not_invented(self):
        for date in ('', '2026', '2026-02', '2024-02-29'):
            node = _node({'id': 1, 'type': 2, 'date': date})
            self.assertEqual(node['release_date'], date)
            self.assertIsNone(node['nsfw'])
        for changes in ({'date': '2026-02-29'}, {'nsfw': 0}, {'type': True}, {'platform': True}):
            with self.subTest(changes=changes), self.assertRaises((SourceError, ValueError)):
                _node({'id': 1, 'type': 2, **changes})

    def test_input_bounds_and_timeout_reject_before_sql_or_extract(self):
        for values in ([], range(1, 102), [True], [2147483648]):
            with self.assertRaises((SourceError, ValueError)): seed_ids(values)
        checksum = self.archive()
        with patch('recommendations.series_source.MAX_EDGES', 0), self.assertRaises(SourceError):
            prepare_archive(self.path, checksum, [1])
        with patch('recommendations.series_source.time.monotonic', side_effect=[0, 1000]), self.assertRaises(SourceError):
            prepare_archive(self.path, checksum, [1])


if __name__ == '__main__':
    unittest.main()
