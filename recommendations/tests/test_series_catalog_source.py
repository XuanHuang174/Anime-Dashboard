"""Synthetic ZIP-only tests for per-source isolation and cross-batch conflicts."""
import unittest
from unittest.mock import patch

from recommendations.series_catalog_source import prepare_catalog_archive
from recommendations.series_source import prepare_archive, digest
from recommendations.source_pipeline import SourceError
from . import test_series_source as archive_fixtures


class SeriesCatalogSourceTests(unittest.TestCase):
    setUp = archive_fixtures.SeriesArchiveSourceTests.setUp
    tearDown = archive_fixtures.SeriesArchiveSourceTests.tearDown
    archive = archive_fixtures.SeriesArchiveSourceTests.archive

    def prepare(self, checksum, seeds=(1, 2, 3)):
        return prepare_catalog_archive(self.path, checksum, seeds)

    def test_verified_empty_and_observed_sources_match_original_root_fingerprints(self):
        checksum = self.archive()
        result = self.prepare(checksum)
        self.assertEqual(result['report']['complete_sources'], 3)
        self.assertEqual(result['report']['quarantined_sources'], {})
        self.assertEqual(len(result['batches']), 1)
        batch = result['batches'][0]
        original = prepare_archive(self.path, checksum, [1, 2, 3])
        for field in ('nodes', 'assertions', 'payload_sha256', 'subject_payload_sha256', 'partial'):
            self.assertEqual(batch[field], original[field])
        self.assertEqual(self.prepare(checksum)['report']['plan_fingerprint'], result['report']['plan_fingerprint'])
        self.assertEqual(digest({k: v for k, v in batch.items() if k != 'fingerprint'}), batch['fingerprint'])

    def test_bad_target_unknown_and_duplicate_do_not_contaminate_other_sources(self):
        row = {'subject_id': 1, 'related_subject_id': 99, 'relation_type': 3, 'order': 0}
        for rows in ([row], [{**row, 'related_subject_id': 2, 'relation_type': 12345}],
                     [{**row, 'related_subject_id': 2}] * 2, [{**row, 'order': -1}],
                     [{**row, 'related_subject_id': 1}]):
            with self.subTest(rows=rows):
                result = self.prepare(self.archive(relations=rows))
                self.assertEqual(result['report']['complete_sources'], 2)
                self.assertEqual(result['report']['partial_sources'], 1)
                self.assertEqual(result['batches'][0]['seed_ids'], [2, 3])
                self.assertTrue(result['batches'][1]['partial'])
                self.assertEqual(result['batches'][1]['seed_ids'], [1])

    def test_invalid_source_metadata_is_skipped_and_affected_targets_are_quarantined(self):
        subjects = [{'id': i, 'type': 2, 'nsfw': False, 'date': '2026-02-30' if i == 2 else ''} for i in (1, 2, 3)]
        result = self.prepare(self.archive(subjects=subjects))
        self.assertEqual(result['report']['skipped_sources'], [2])
        self.assertEqual(result['report']['complete_sources'], 1)
        self.assertEqual(result['report']['partial_sources'], 1)
        self.assertEqual(result['report']['invalid_metadata_ids'], [2])
        self.assertTrue(all(node['bangumi_id'] != 2 for b in result['batches'] for node in b['nodes']))

    def test_cross_batch_cycles_and_reciprocal_mismatches_are_detected_before_split(self):
        for pairs in ([(1, 2, 3), (2, 1, 3)], [(1, 2, 3), (2, 1, 6)]):
            rows = [dict(subject_id=a, related_subject_id=b, relation_type=c, order=0) for a, b, c in pairs]
            with patch('recommendations.series_catalog_source.MAX_SEEDS', 1):
                result = self.prepare(self.archive(relations=rows))
            self.assertEqual(result['report']['complete_sources'], 1)
            self.assertEqual(result['report']['partial_sources'], 2)
            self.assertEqual(result['batches'][0]['seed_ids'], [3])
            self.assertTrue(all(batch['partial'] for batch in result['batches'][1:]))

    def test_missing_reciprocal_is_only_a_signal_and_cross_media_never_creates_cycles(self):
        subjects = [{'id': 1, 'type': 2, 'nsfw': False}, {'id': 2, 'type': 3}]
        rows = [dict(subject_id=1, related_subject_id=2, relation_type=3003, order=0)]
        result = self.prepare(self.archive(subjects=subjects, relations=rows), seeds=[1])
        self.assertFalse(result['batches'][0]['partial'])
        self.assertEqual(result['batches'][0]['assertions'][0]['kind'], 'cross_media')
        result = self.prepare(self.archive(relations=[dict(subject_id=1, related_subject_id=2, relation_type=3, order=0)]))
        self.assertEqual(result['report']['complete_sources'], 3)

    def test_checksum_bounds_and_duplicate_metadata_fail_closed(self):
        checksum = self.archive()
        with self.assertRaises(SourceError): self.prepare('0' * 64)
        with self.assertRaises(SourceError): self.prepare(checksum, [])
        with patch('recommendations.series_catalog_source.MAX_INDEX_EDGES', 0), self.assertRaises(SourceError):
            self.prepare(checksum)
        result = self.prepare(self.archive(subjects=[{'id': 1, 'type': 2}] * 2, relations=[]), [1])
        self.assertEqual(result['report']['skipped_sources'], [1])
        self.assertEqual(result['batches'], [])
