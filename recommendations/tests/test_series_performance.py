"""12F measured local synthetic baseline; never a production/p95/load claim."""
import json
import statistics
import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from recommendations.models import RecommendationAnime
from recommendations.series_store import publish_archive
from recommendations.tests import test_exploration_browsing as prior
from recommendations.tests.test_series_exploration import archive
from recommendations.series_release import coverage


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
    RECOMMENDATION_SERIES_ENABLED=True, RECOMMENDATION_SERIES_CANARY_USERS=[],
    RECOMMENDATION_SERIES_EXPLORATION_ENABLED=True, RECOMMENDATION_SERIES_REMINDERS_ENABLED=True,
    RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_ALGORITHM_VERSION='content.v1')
class SeriesPerformanceTests(TransactionTestCase):
    setUp = prior.SingleWorkSnapshotTests.setUp
    catalog = prior.SingleWorkSnapshotTests.catalog
    record = prior.SingleWorkSnapshotTests.record

    def test_measured_50000_catalog_1000_relations_and_100_own_records(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertIn(connection.settings_dict['HOST'], ('localhost', '127.0.0.1', '::1'))
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        existing = set(RecommendationAnime.objects.values_list('bangumi_id', flat=True))
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'合成性能{i}',
            tags='音乐,日常', score=8, nsfw=False, season='2026-01', status_type='TV') for i in range(1, 50001) if i not in existing], batch_size=1000)
        publish_archive(archive([(1, i, 3) for i in range(300, 1300)]))
        for identity in range(1, 101): self.record(identity, watch_status='completed')
        jobs = [
            ('workspace', '/api/recommendations/series/1/workspace/', {}),
            ('annotations24', '/api/recommendations/series/annotations/', {'ids': ','.join(str(i) for i in range(300, 324))}),
            ('partition', '/api/recommendations/explore/series/', {'intent': 'similar', 'seed_bangumi_id': 1}),
            ('reminders', '/api/recommendations/series/reminders/', {}),
            ('legacy_similar', '/api/recommendations/explore/', {'intent': 'similar', 'seed_bangumi_id': 1}),
            ('legacy_tags', '/api/recommendations/explore/', {'intent': 'tags', 'include_tags': '音乐'}),
            ('personal', '/api/recommendations/', {}),
        ]
        samples = []
        for name, path, params in jobs:
            for attempt in range(4):
                tracemalloc.start(); started, cpu = time.perf_counter(), time.process_time()
                try:
                    with CaptureQueriesContext(connection) as queries:
                        response = self.client.get(path, params)
                    elapsed, used = time.perf_counter() - started, time.process_time() - cpu
                    peak = tracemalloc.get_traced_memory()[1]
                finally: tracemalloc.stop()
                self.assertEqual(response.status_code, 200, response.data)
                self.assertLess(elapsed, 15)
                self.assertLessEqual(len(queries), 65)
                self.assertLess(peak, 128 * 1024 * 1024)
                self.assertIn('no-store', response['Cache-Control'])
                samples.append({'endpoint': name, 'phase': 'cold' if not attempt else 'warm',
                    'wall_seconds': round(elapsed, 4), 'process_cpu_seconds': round(used, 4),
                    'sql_queries': len(queries), 'python_traced_peak_bytes': peak})
        started = time.perf_counter()
        with CaptureQueriesContext(connection) as queries: report = coverage()
        audit_time = time.perf_counter() - started
        self.assertEqual(report['catalog_count'], 50000)
        self.assertEqual(report['current_touching_assertions'], 1000)
        self.assertLess(audit_time, 15); self.assertLessEqual(len(queries), 20)
        summaries = []
        for name, *_ in jobs:
            rows = [row for row in samples if row['endpoint'] == name]
            summaries.append({'endpoint': name, 'cold_seconds': rows[0]['wall_seconds'],
                'warm_median_seconds': statistics.median(row['wall_seconds'] for row in rows[1:]),
                'warm_max_seconds': max(row['wall_seconds'] for row in rows[1:]),
                'maximum_queries': max(row['sql_queries'] for row in rows),
                'peak_python_bytes': max(row['python_traced_peak_bytes'] for row in rows)})
        artifact = {'contract_version': 'recommendations.series.performance.v1',
            'scope': 'local-isolated-postgresql-synthetic-50000-catalog-1000-relations-100-own',
            'production_access': False, 'concurrent_load_test': False, 'samples_per_endpoint': 4,
            'summaries': summaries, 'samples': samples, 'coverage_audit_seconds': round(audit_time, 4),
            'coverage_audit_queries': len(queries), 'budgets': {'instrumented_wall_seconds': 15, 'queries': 65, 'python_traced_bytes': 134217728},
            'limitations': ['Cold plus three warm local samples; no production p95 claim.',
                'Python CPU/allocations exclude PostgreSQL/server memory.', 'Synthetic coverage is not production coverage.']}
        path = Path(settings.BASE_DIR) / '.tools/12f' / ('performance-' + uuid4().hex + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as file: json.dump(artifact, file, indent=2)
        print('12F measured synthetic profile:', json.dumps({'artifact': str(path), 'summaries': summaries, 'audit_seconds': round(audit_time, 4)}))
