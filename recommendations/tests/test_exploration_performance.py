"""11F instrumented synthetic 1k endpoint matrix; 50k bounds live in 11B/11D tests."""
import json
from pathlib import Path
import time
import tracemalloc
from unittest.mock import patch
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from recommendations.models import RecommendationAnime
from . import test_exploration_browsing as prior


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
                   RECOMMENDATION_EXPLORATION_EPOCH='11f-profile', RECOMMENDATION_ALGORITHM_VERSION='content.v1',
                   RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=True)
class ExplorationEndpointProfileTests(TransactionTestCase):
    setUp = prior.SingleWorkSnapshotTests.setUp
    catalog = prior.SingleWorkSnapshotTests.catalog

    def test_instrumented_cold_warm_endpoint_matrix_and_9_baseline(self):
        count = 1000 - RecommendationAnime.objects.count()
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'规模合成{i}', tags='音乐,日常',
            score=8, nsfw=False, season='2026-01', status_type='TV') for i in range(1000, 1000 + count)])
        self.assertEqual(RecommendationAnime.objects.count(), 1000)
        observations = []
        def measure(label, path, params, limit=36):
            cache.clear()
            result = None
            for phase in ('cold', 'warm'):
                lookups, hits = [], []
                original = cache.get
                def observe(key, *args, **kwargs):
                    value = original(key, *args, **kwargs)
                    if str(key).startswith('recommendations.browse.public'):
                        lookups.append(1)
                        if value is not None:
                            hits.append(1)
                    return value
                tracemalloc.start(1)
                started, cpu = time.perf_counter(), time.process_time()
                try:
                    with CaptureQueriesContext(connection) as queries, patch('recommendations.browsing.cache.get', side_effect=observe):
                        response = self.client.get(path, params)
                    wall, process_cpu = time.perf_counter() - started, time.process_time() - cpu
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertEqual(response.status_code, 200, response.data)
                self.assertLessEqual(len(queries), limit)
                self.assertLess(wall, 15)  # Generous local traced budget, never a production SLA.
                self.assertLess(peak, 128 * 1024 * 1024)
                result = response.data
                observations.append({'endpoint': label, 'phase': phase, 'wall_seconds': round(wall, 4),
                    'process_cpu_seconds': round(process_cpu, 4), 'python_traced_peak_bytes': peak,
                    'sql_queries': len(queries), 'public_cache_lookups': len(lookups), 'public_cache_hits': len(hits)})
            return result
        measure('catalog', '/api/recommendations/explore/catalog/', {'q': '规模'}, 12)
        measure('options', '/api/recommendations/explore/options/', {}, 20)
        similar = measure('similar_first', '/api/recommendations/explore/', {'intent': 'similar', 'seed_bangumi_id': 1})
        tags = measure('tags_first', '/api/recommendations/explore/', {'intent': 'tags', 'include_tags': '日常,音乐'})
        for intent, data in [('similar', similar), ('tags', tags)]:
            measure(intent + '_continuation', '/api/recommendations/explore/', {'cursor': data['next_page_cursor']})
            row = data['results'][0]
            measure(intent + '_detail', f'/api/recommendations/explore/{row["bangumi_id"]}/',
                {'detail_context': row['detail_context'], 'evidence_token': row['evidence_token']})
        measure('enhancement9_empty_journal', '/api/recommendations/', {'browse': 'true'})
        report = {'scope': 'local-isolated-postgresql-synthetic-1000', 'observations': observations,
            'budgets': {'wall_seconds_with_tracing': 15, 'sql_queries': 36, 'python_traced_peak_bytes': 128 * 1024 * 1024},
            'limitations': ['Single cold/warm samples, not p95 or load test.', 'CPU and traced allocation are Python client/process only, not PostgreSQL/server memory.',
                'Enhancement 9 comparison uses the SAME empty-journal account.', '50k SQL/wall bounds are measured separately without tracemalloc.']}
        folder = Path(settings.BASE_DIR) / '.tools'
        folder.mkdir(exist_ok=True)
        (folder / '11f-profile.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('11F instrumented profile:', json.dumps(report))
