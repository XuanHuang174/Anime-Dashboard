import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from recommendations.audit_catalog import audit_catalog
from recommendations.source_pipeline import SourceError


class CatalogAuditTests(SimpleTestCase):
    def fixtures(self, directory):
        paths = [Path(directory) / name for name in ('source.jsonl', 'enriched.jsonl', 'enrichment.json', 'audit.json', 'pilot.jsonl')]
        rows = [{'bangumi_id': i, 'nsfw': False, 'score': 8.5, 'cover_url': 'https://lain.bgm.tv/a.jpg'} for i in range(1, 18)]
        rows += [{'bangumi_id': 18, 'nsfw': False, 'score': 0},
                 {'bangumi_id': 19, 'nsfw': True, 'score': 0, 'cover_url': 'https://lain.bgm.tv/b.jpg'},
                 {'bangumi_id': 20, 'nsfw': True, 'score': 0}]
        for path in paths[:2]:
            self.write_rows(path, rows)
        paths[2].write_text(json.dumps({'counts': {'rows': 20, 'nsfw': 2, 'missing_cover_url': 2}}), encoding='utf-8')
        return paths, rows

    def write_rows(self, path, rows):
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    def test_counts_hashes_and_boundary_pilot(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, rows = self.fixtures(directory)
            result = audit_catalog(*paths)
            self.assertEqual(result['counts']['rows'], 20)
            self.assertEqual(result['counts']['ordinary_cover'], 17)
            self.assertEqual(result['counts']['adult'], 2)
            self.assertEqual(len(result['enriched_sha256']), 64)
            self.assertEqual([json.loads(line) for line in paths[4].read_text().splitlines()], rows)

    def test_duplicate_or_missing_ids_publish_nothing(self):
        for mode in ('duplicate', 'missing'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                paths, rows = self.fixtures(directory)
                if mode == 'duplicate':
                    rows[-1]['bangumi_id'] = 19
                else:
                    rows.pop()
                self.write_rows(paths[1], rows)
                with self.assertRaises(SourceError):
                    audit_catalog(*paths)
                self.assertFalse(paths[3].exists())
                self.assertFalse(paths[4].exists())

    def test_adult_downgrade_unknown_and_invalid_score_fail(self):
        for field, value in (('nsfw', False), ('nsfw', None), ('score', float('nan'))):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                paths, rows = self.fixtures(directory)
                rows[-1][field] = value
                self.write_rows(paths[1], rows)
                with self.assertRaises(SourceError):
                    audit_catalog(*paths)
                self.assertFalse(paths[4].exists())

    def test_stopped_report_and_colliding_paths_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self.fixtures(directory)
            paths[2].write_text(json.dumps({'stopped_for_outage': True}), encoding='utf-8')
            with self.assertRaises(SourceError):
                audit_catalog(*paths)
            with self.assertRaises(SourceError):
                audit_catalog(*paths[:3], paths[0], paths[4])

    def test_existing_output_is_never_overwritten_implicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self.fixtures(directory)
            paths[4].write_text('previous artifact', encoding='utf-8')
            with self.assertRaises(SourceError):
                audit_catalog(*paths)
            self.assertEqual(paths[4].read_text(), 'previous artifact')

    def test_failure_log_counts_must_agree_with_report(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self.fixtures(directory)
            log = Path(directory) / 'enrich.log'
            log.write_text('条目 20 未补齐：missing_or_restricted；归档记录保留\n', encoding='utf-8')
            with self.assertRaises(SourceError):
                audit_catalog(*paths, log_path=log)
            paths[2].write_text(json.dumps({'counts': {'rows': 20, 'nsfw': 2, 'missing_cover_url': 2, 'failed': 1}}), encoding='utf-8')
            result = audit_catalog(*paths, log_path=log)
            self.assertEqual(result['api_failure_reasons'], {'missing_or_restricted': 1})
