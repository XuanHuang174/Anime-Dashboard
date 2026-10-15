"""Fail-closed core-7 acceptance runner. Local isolated PostgreSQL ONLY.

No production settings import, SQLite fallback, live accounts or external HTTP.
Creates/drops only Django's test_recommendation_isolated_tests on the dedicated
local instance. The operator must provision it explicitly before this command.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SUITES = ('recommendations.tests.test_want_api',
          'recommendations.tests.test_interaction_contract',
          'recommendations.tests.test_engine_api',
          'recommendations.tests.test_contracts_api',
          'anime.test_resource_search',
          'recommendations.tests.test_loop_7c')


def validate_target(url):
    try:
        value = urlsplit(url)
        safe = (value.scheme in ('postgres', 'postgresql')
                and value.hostname in ('127.0.0.1', 'localhost', '::1')
                and value.path == '/recommendation_isolated_tests'
                and not value.query and not value.fragment)
    except ValueError:
        safe = False
    if not safe:
        raise ValueError('Explicit loopback PostgreSQL recommendation_isolated_tests required; no fallback allowed.')


def main():
    report = {'scope': '7c-isolated-postgres-core-regression', 'database': 'local_dedicated_postgresql',
        'algorithm': 'content.v1', 'suites': list(SUITES), 'production_access': False,
        'external_http': False, 'limitations': [
            'Scoped core-7 pack, not all unrelated backend/algorithm benchmark suites.',
            'No production writes; browser external side effects are mocked separately.',
            'Functional coverage matrix is not a source-line coverage percentage.']}
    start = time.perf_counter()
    try:
        validate_target(os.environ.get('RECOMMENDATION_TEST_DATABASE_URL', '').strip())
        os.environ['DJANGO_SETTINGS_MODULE'] = 'config.recommendation_pg_test_settings'
        os.environ['RECOMMENDATION_ALGORITHM_VERSION'] = 'content.v1'
        import django
        django.setup()
        from django.conf import settings
        from django.test.runner import DiscoverRunner
        if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
            raise ValueError('PostgreSQL backend required.')

        class Result(unittest.TextTestResult):
            def addSuccess(self, test):
                super().addSuccess(test)
                report.setdefault('passed_cases', []).append(test.id())

        class TextRunner(unittest.TextTestRunner):
            resultclass = Result

        class Runner(DiscoverRunner):
            test_runner = TextRunner

            def run_suite(self, suite, **kwargs):
                result = super().run_suite(suite, **kwargs)
                report.update(tests_run=result.testsRun,
                    failed_cases=[case.id() for case, _ in result.failures],
                    error_cases=[case.id() for case, _ in result.errors],
                    skipped_cases=[case.id() for case, _ in result.skipped])
                return result

        # Requests mocks in tests are allowed; an accidentally unmocked upstream
        # request fails immediately instead of reaching real external services.
        with patch('requests.sessions.Session.request', side_effect=AssertionError('External HTTP forbidden in isolated acceptance')):
            failures = Runner(verbosity=2, interactive=False, keepdb=False).run_tests(SUITES)
        races = [name for name in report.get('passed_cases', []) if '.WantConcurrencyTests.' in name]
        report['row_lock_concurrency_passed'] = len(races)
        report['result'] = 'passed' if not failures and not report['skipped_cases'] and len(races) == 4 else 'failed'
    except Exception as exc:
        report.update(result='blocked_or_failed', failure_type=type(exc).__name__)
        # Never include exception bodies/DSNs/passwords in artifacts.
    report['seconds'] = round(time.perf_counter() - start, 3)
    output = ROOT / '.tools/acceptance-7c'
    output.mkdir(parents=True, exist_ok=True)
    filename = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with filename.open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps({'result': report['result'], 'report_path': str(filename)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
