"""No production DB access. Generated fixtures stay in ignored .tools; no bulk cleanup."""
import json
import threading
import unittest
import uuid
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from unittest.mock import patch

from recommendations.progress_monitor import ProgressMonitor, handler_for, utcnow


class ProgressMonitorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2] / '.tools' / 'progress-monitor-tests' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.batch = str(uuid.uuid4())
        self.monitor = ProgressMonitor(self.root, self.batch)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def prepare(self):
        self.write('status.json', {'state': 'resuming', 'resume_attempt': 2,
                                  'batch_id': self.batch, 'progress_log': 'apply-resume-2.err'})
        self.write('preflight.json', {'counts': {'strong_unique': 2221, 'needs_confirmation': 373,
                                              'no_safe_candidate': 82}, 'entries': [{'private': 'DO_NOT_EXPOSE'}]})
        (self.root / 'apply-resume-2.err').write_text(
            f'已处理 1500/2676；batch-id={self.batch}\n已处理 1550/2676；batch-id={self.batch}\n', encoding='utf-8')

    def test_scan_and_committed_counts_are_independent(self):
        self.prepare()
        self.monitor.database.update(committed_links=1339)
        result = self.monitor.snapshot()
        self.assertEqual((result['scanned'], result['scan_total']), (1550, 2676))
        self.assertEqual(result['database']['committed_links'], 1339)
        self.assertEqual(result['attempt'], 2)
        self.assertEqual(result['expected_links'], 2221)
        self.assertNotIn('DO_NOT_EXPOSE', json.dumps(result))

    def test_missing_or_partial_files_never_claim_completion(self):
        (self.root / 'apply.json').write_text('{', encoding='utf-8')
        result = self.monitor.snapshot()
        self.assertNotEqual(result['phase'], 'complete')
        self.assertIsNone(result['database']['committed_links'])
        self.assertIsNone(result['scanned'])

    def test_error_log_overrides_stale_running_status(self):
        self.prepare()
        with (self.root / 'apply-resume-2.err').open('a', encoding='utf-8') as file:
            file.write('CommandError: OperationalError\n')
        self.assertEqual(self.monitor.snapshot()['phase'], 'attempt_interrupted')

    def test_apply_complete_is_not_acceptance_complete(self):
        self.prepare()
        self.write('apply.json', {'batch_id': self.batch, 'mode': 'apply', 'counts': {'linked': 2221},
                                 'resume_counts': {'existing_link': 1339, 'linked': 882,
                                                   'needs_confirmation': 373, 'no_safe_candidate': 82}})
        result = self.monitor.snapshot()
        self.assertEqual(result['phase'], 'apply_complete_waiting_acceptance')
        self.assertEqual(result['scanned'], 2676)
        self.write('status.json', {'state': 'complete', 'batch_id': self.batch})
        self.assertEqual(self.monitor.snapshot()['phase'], 'complete')

    def test_wrong_batch_report_does_not_complete_current_batch(self):
        self.prepare()
        self.write('apply.json', {'batch_id': str(uuid.uuid4()), 'mode': 'apply', 'counts': {'linked': 2221}})
        self.assertEqual(self.monitor.snapshot()['phase'], 'resuming')

    def test_status_cannot_select_arbitrary_private_file(self):
        self.write('status.json', {'state': 'resuming', 'progress_log': '../.env', 'secret': 'DO_NOT_EXPOSE'})
        result = self.monitor.snapshot()
        self.assertIsNone(result['scanned'])
        self.assertNotIn('DO_NOT_EXPOSE', json.dumps(result))

    def test_sampling_error_preserves_previous_committed_count(self):
        self.monitor.database.update(committed_links=1434, error_kind='OperationalError',
                                     error_at=utcnow().isoformat())
        result = self.monitor.snapshot()
        self.assertEqual(result['database']['committed_links'], 1434)
        self.assertEqual(result['database']['error_kind'], 'OperationalError')

    def test_quiet_batch_warns_without_claiming_stopped(self):
        self.prepare()
        with patch('recommendations.progress_monitor.utcnow', return_value=utcnow()+timedelta(minutes=6)):
            result = self.monitor.snapshot()
        self.assertTrue(result['no_recent_progress'])
        self.assertEqual(result['phase'], 'resuming')

    def test_http_rejects_foreign_host_and_origin(self):
        self.prepare()
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.monitor))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = build_opener(ProxyHandler({}))
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}):
                with self.assertRaises(HTTPError) as raised:
                    opener.open(Request(base+'/api/progress', headers=headers))
                self.assertEqual(raised.exception.code, 403)
            with opener.open(base+'/api/progress') as response:
                payload = json.loads(response.read())
                self.assertEqual(payload['scanned'], 1550)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
            with opener.open(base+'/') as response:
                html = response.read().decode('utf-8')
                self.assertNotIn('{{nonce}}', html)
                self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
            with self.assertRaises(HTTPError) as raised:
                opener.open(base+'/.env')
            self.assertEqual(raised.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
