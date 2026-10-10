import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from recommendations.api_enrichment import (
    ApiCache, AuthorizationError, RequestLimiter, authorization_header,
    enrich_file, fetch_subject, merge_metadata,
)
from recommendations.source_pipeline import (
    MANIFEST_URL, SourceError, artifact_writer, checked_url,
    convert_archive, download_archive, iter_jsonlines, normalize_subject,
)


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


class ArchiveTests(unittest.TestCase):
    def test_normalize_platform_score_season_tags_and_adult(self):
        data = normalize_subject({'id': 42, 'name': '原名', 'score': 'oops', 'platform': 3,
                                  'date': '2026-08-11', 'tags': [{'name': '百合'}, {'name': '日常'}],
                                  'meta_tags': ['百合'], 'nsfw': True})
        self.assertEqual((data['title_zh'], data['score'], data['season'], data['status_type']),
                         ('原名', 0, '2026-07', '剧场版'))
        self.assertEqual(data['tags'], '百合,日常')
        self.assertTrue(data['nsfw'])
        self.assertNotIn('cover_url', data)

    def test_atomic_failure_preserves_file_and_removes_only_own_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'output.jsonl'
            path.write_text('old')
            with self.assertRaises(SourceError), artifact_writer(path):
                pass
            with self.assertRaises(ValueError), artifact_writer(path, overwrite=True) as output:
                output.write('new')
                raise ValueError('stop')
            self.assertEqual(path.read_text(), 'old')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_zip_conversion_filters_only_type_and_retains_adult(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'archive.zip'
            rows = [{'id': 1, 'type': 2, 'name_cn': '普通', 'nsfw': False},
                    {'id': 2, 'type': 2, 'name': '成年标记测试', 'nsfw': True},
                    {'id': 3, 'type': 4}, {'id': 1, 'type': 2}, {'id': -1, 'type': 2}]
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('subject.jsonlines', '\n'.join(json.dumps(row) for row in rows) + '\ninvalid\n')
                archive.writestr('../irrelevant.txt', 'never extracted')
            result = convert_archive(source, root / 'out', root / 'sample', root / 'report', sample_size=1, log=lambda _: None)
            self.assertEqual(result['counts']['exported'], 2)
            self.assertEqual(result['counts']['nsfw'], 1)
            self.assertEqual(result['counts']['invalid'], 2)
            self.assertEqual(result['counts']['duplicate_ids'], 1)
            self.assertTrue(json.loads((root / 'sample').read_text(encoding='utf-8'))['nsfw'])
            filtered = convert_archive(source, root / 'out2', root / 'sample2', root / 'report2', exclude_nsfw=True, log=lambda _: None)
            self.assertEqual(filtered['counts']['exported'], 1)

    def test_source_and_output_cannot_overlap_or_sample_be_empty(self):
        with self.assertRaises(SourceError):
            convert_archive('x', 'x', 'z', 'r')
        with self.assertRaises(SourceError):
            convert_archive('x', 'y', 'z', 'r', sample_size=0)

    def test_oversized_synopsis_does_not_drop_subject_or_silently_truncate(self):
        row = normalize_subject({'id': 42, 'summary': 'x' * 200_001, 'nsfw': True})
        self.assertEqual(row['bangumi_id'], 42)
        self.assertTrue(row['nsfw'])
        self.assertNotIn('synopsis', row)
        self.assertIn('oversized_synopsis_omitted', row['_source_warnings'])

    def test_zip_requires_one_expected_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / 'x.zip', 'w') as archive:
                archive.writestr('other', '{}')
            with self.assertRaises(SourceError):
                convert_archive(root / 'x.zip', root / 'out', root / 'sample', root / 'report')

    def test_oversized_line_does_not_hide_next_record(self):
        with patch('recommendations.source_pipeline.MAX_LINE_CHARS', 10):
            rows = list(iter_jsonlines(io.StringIO('x' * 100 + '\n{}\n')))
        self.assertEqual(rows, [(1, None), (2, {})])

    def test_download_checks_hash_reuses_archive_and_denies_foreign_redirect(self):
        payload = b'example archive'
        manifest = {'name': 'dump-2026-10-06.zip', 'size': len(payload),
                    'digest': 'sha256:' + hashlib.sha256(payload).hexdigest(),
                    'browser_download_url': 'https://github.com/bangumi/Archive/releases/download/archive/dump-2026-10-06.zip'}
        def handler(request):
            if str(request.url) == MANIFEST_URL:
                return httpx.Response(200, json=manifest)
            return httpx.Response(200, content=payload)
        with tempfile.TemporaryDirectory() as directory:
            with patch('recommendations.source_pipeline.make_http_client', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handler))):
                path, _ = download_archive(directory, log=lambda _: None)
                self.assertEqual(path.read_bytes(), payload)
                reused, _ = download_archive(directory, log=lambda _: None)
                self.assertEqual(path, reused)
        def unsafe_handler(request):
            return httpx.Response(200, json=manifest) if str(request.url) == MANIFEST_URL else httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'})
        with tempfile.TemporaryDirectory() as directory:
            with patch('recommendations.source_pipeline.make_http_client', return_value=httpx.Client(transport=httpx.MockTransport(unsafe_handler))):
                with self.assertRaises(SourceError):
                    download_archive(directory, log=lambda _: None)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_corrupt_download_is_not_published(self):
        manifest = {'name': 'dump-test.zip', 'size': 3, 'digest': 'sha256:' + '0' * 64,
                    'browser_download_url': 'https://github.com/bangumi/Archive/releases/download/archive/dump-test.zip'}
        def handler(request):
            return httpx.Response(200, json=manifest) if str(request.url) == MANIFEST_URL else httpx.Response(200, content=b'bad')
        with tempfile.TemporaryDirectory() as directory:
            with patch('recommendations.source_pipeline.make_http_client', return_value=httpx.Client(transport=httpx.MockTransport(handler))):
                with self.assertRaises(SourceError):
                    download_archive(directory, log=lambda _: None)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_official_url_allowlist(self):
        for url in ('http://github.com/x', 'https://github.com.evil/x', 'https://user:pass@github.com/x', 'https://github.com:8443/x'):
            with self.assertRaises(SourceError):
                checked_url(url, {'github.com'})

    def test_cli_does_not_import_django_or_read_database_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / 'raw.jsonl', [{'id': 1, 'type': 2, 'name': 'test', 'nsfw': True}])
            environment = dict(os.environ, DATABASE_URL='postgresql://forbidden.invalid/live',
                               DJANGO_SETTINGS_MODULE='this.must.not.be.loaded', PYTHONIOENCODING='utf-8')
            command = [os.sys.executable, '-c',
                       'import sys; from recommendations.prepare_data import main; result=main(sys.argv[1:]); assert "django" not in sys.modules; raise SystemExit(result)',
                       'convert', '--input', str(root / 'raw.jsonl'), '--output', str(root / 'out'),
                       '--sample', str(root / 'sample'), '--report', str(root / 'report')]
            result = subprocess.run(command, env=environment, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.limiter = RequestLimiter(1, clock=lambda: 100, sleep=lambda _: None)

    def test_bearer_prefix_and_invalid_values(self):
        self.assertEqual(authorization_header('Bearer test-token'), {'Authorization': 'Bearer test-token'})
        self.assertEqual(authorization_header('  '), {})
        for token in ('line\nbreak', '中文', 'Bearer Bearer double'):
            with self.assertRaises(SourceError):
                authorization_header(token)

    def test_auth_goes_only_to_official_api_and_no_follow_redirect(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(302, headers={'Location': 'https://evil.example/token'})
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with self.assertRaises(SourceError):
                fetch_subject(client, 42, self.limiter, authorization_header('test'))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].url.host, 'api.bgm.tv')
        self.assertEqual(seen[0].headers['Authorization'], 'Bearer test')

    def test_retry_503_success_and_placeholder_omitted(self):
        seen, pauses = [], []
        def handler(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={'id': 42, 'type': 2, 'nsfw': True,
                                            'rating': {'score': 8.5}, 'images': {'large': 'https://lain.bgm.tv/img/no_icon_subject.png'}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = fetch_subject(client, 42, self.limiter, {}, sleep=pauses.append)
        self.assertEqual(result['score'], 8.5)
        self.assertNotIn('cover_url', result)
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(pauses), 1)

    def test_429_retry_after_is_bounded_and_exhausted(self):
        pauses = []
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={'Retry-After': '9999'}))) as client:
            with self.assertRaises(SourceError):
                fetch_subject(client, 42, self.limiter, {}, retries=1, sleep=pauses.append)
        self.assertEqual(pauses, [60])

    def test_wrong_identity_type_or_authorization_rejected(self):
        for response in (httpx.Response(200, json={'id': 43, 'type': 2}), httpx.Response(200, json={'id': 42, 'type': 4}), httpx.Response(401)):
            with httpx.Client(transport=httpx.MockTransport(lambda _, response=response: response)) as client:
                with self.assertRaises(SourceError):
                    fetch_subject(client, 42, self.limiter, {})

    def test_existing_fields_not_cleared_and_adult_never_lost(self):
        row = {'bangumi_id': 42, 'score': 9.0, 'tags': '日常', 'nsfw': True,
               'cover_url': 'https://media.xh-anime.com/covers/42.jpg'}
        result = merge_metadata(row, {'score': 0, 'tags': '', 'nsfw': False}, refresh=True)
        self.assertEqual(result, row)

    def test_limits_cache_resume_and_404_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            write_rows(source, [{'bangumi_id': number, 'title_zh': 'test', 'nsfw': number == 2} for number in (1, 2, 3)])
            requests = []
            def handler(request):
                identity = int(request.url.path.rsplit('/', 1)[1])
                requests.append(identity)
                if identity == 2:
                    return httpx.Response(404)
                return httpx.Response(200, json={'id': identity, 'type': 2, 'images': {'large': f'https://lain.bgm.tv/{identity}.jpg'}})
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                first = enrich_file(source, root / 'out', root / 'cache.sqlite3', root / 'report', limit=2,
                                    client=client, limiter=self.limiter, log=lambda _: None)
                self.assertEqual(first['counts']['rows'], 3)
                self.assertEqual(first['counts']['deferred'], 1)
                self.assertEqual(first['counts']['failed'], 1)
                exported = [json.loads(line) for line in (root / 'out').read_text(encoding='utf-8').splitlines()]
                self.assertTrue(exported[1]['nsfw'])
                self.assertNotIn('cover_url', exported[1])
                resumed = enrich_file(source, root / 'out2', root / 'cache.sqlite3', root / 'report2', limit=2,
                                      client=client, limiter=self.limiter, log=lambda _: None)
                self.assertEqual(resumed['counts']['cache_hits'], 1)
                self.assertEqual(requests, [1, 2, 2, 3])

    def test_403_stops_all_new_requests_but_keeps_all_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / 'source', [{'bangumi_id': number, 'nsfw': True} for number in (1, 2, 3)])
            with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client:
                result = enrich_file(root / 'source', root / 'out', root / 'cache', root / 'report',
                                     client=client, limiter=self.limiter, log=lambda _: None)
            self.assertTrue(result['stopped_for_authorization'])
            self.assertEqual(result['counts']['attempted'], 1)
            self.assertEqual(result['counts']['rows'], 3)

    def test_cache_expiration(self):
        with tempfile.TemporaryDirectory() as directory:
            for ttl in (0, -1, float('nan'), float('inf')):
                with self.assertRaises(SourceError):
                    ApiCache(Path(directory) / 'invalid-cache', ttl_hours=ttl)
            cache = ApiCache(Path(directory) / 'cache', ttl_hours=1)
            try:
                cache.put(42, {'bangumi_id': 42})
                self.assertIsNotNone(cache.get(42))
                with patch('recommendations.api_enrichment.time.time', return_value=10 ** 12):
                    self.assertIsNone(cache.get(42))
            finally:
                cache.close()

    def test_outage_circuit_breaker_keeps_unrequested_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / 'source', [{'bangumi_id': number, 'nsfw': True} for number in range(1, 11)])
            with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
                result = enrich_file(root / 'source', root / 'out', root / 'cache', root / 'report',
                                     retries=0, client=client, limiter=self.limiter, log=lambda _: None)
            self.assertTrue(result['stopped_for_outage'])
            self.assertEqual(result['counts']['attempted'], 5)
            self.assertEqual(result['counts']['rows'], 10)

    def test_rate_limiter_controls_start_spacing(self):
        now, sleeps = [0], []
        def sleep(duration):
            sleeps.append(duration)
            now[0] += duration
        limiter = RequestLimiter(1, clock=lambda: now[0], sleep=sleep)
        limiter.wait()
        limiter.wait()
        self.assertEqual(sleeps, [1])
        with self.assertRaises(SourceError):
            RequestLimiter(100)
