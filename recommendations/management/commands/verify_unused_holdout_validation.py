"""Sign actual fixed-suite results with normal app key, never a handwritten pass."""
import json
import os
from pathlib import Path
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from recommendations.unused_validation import private_path
from recommendations.version_control import release_descriptor, sign_artifact


class Command(BaseCommand):
    help = 'Fixed local PostgreSQL regression and concurrency attestation for unused-pair validation.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True, type=Path)

    def handle(self, *args, **options):
        from scripts.verify_recommendation_7c import validate_target
        try:
            validate_target(os.environ.get('RECOMMENDATION_TEST_DATABASE_URL', ''))
            path = private_path(options['output'])
            if path.exists():
                raise ValueError('New test attestation path required')
            fingerprint = release_descriptor()['fingerprint']
            self.stdout.write('Running fixed isolated PostgreSQL regression and 16 lock cases'); self.stdout.flush()
            child_env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
            # Binary capture avoids Windows reader-thread decode failures and
            # never turns undecodable output into a fabricated passing receipt.
            done = subprocess.run([sys.executable, 'scripts/verify_recommendation_v2d3.py'],
                cwd=settings.BASE_DIR, env=child_env, capture_output=True, timeout=600)
            if done.returncode:
                raise ValueError('Fixed regression failed; no test attestation issued')
            output = json.loads(done.stdout.decode('utf-8').strip().splitlines()[-1])
            report = json.loads(private_path(output['report_path']).read_text(encoding='utf-8'))
            cases = [name for name in report.get('passed_cases', []) if '.test_unused_validation.' in name]
            discovery_cases = [name for name in report.get('passed_cases', []) if '.test_discovery_candidate.' in name]
            dual_cases = [name for name in report.get('passed_cases', []) if '.test_dual_discovery.' in name]
            manifest_cases = [name for name in report.get('passed_cases', []) if '.test_public_manifest.' in name]
            cold_cases = [name for name in report.get('passed_cases', []) if '.test_cold_public.' in name]
            if (report.get('result') != 'passed' or report.get('production_access') is not False
                    or report.get('failed_cases') or report.get('error_cases') or report.get('skipped_cases')
                    or [report.get(k) for k in ('feedback_concurrency_passed', 'want_concurrency_passed',
                                               'additional_concurrency_passed')] != [6, 4, 6] or len(cases) < 8
                    or len(discovery_cases) < 8 or len(dual_cases) < 8 or len(manifest_cases) < 8 or len(cold_cases) < 8):
                raise ValueError('Incomplete isolated validation suite')
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during tests')
            data = sign_artifact('unused-holdout-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
                'backend_tests': report['tests_run'], 'unused_validation_tests': len(cases), 'skipped': 0,
                'discovery_development_tests': len(discovery_cases), 'dual_discovery_tests': len(dual_cases),
                'public_manifest_tests': len(manifest_cases), 'cold_public_tests': len(cold_cases), 'locks': [6, 4, 6]})
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as file:
                json.dump(data, file, indent=2)
            self.stdout.write(f'Complete: {path}; actual_tests={report["tests_run"]}; no serving approval.')
        except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Isolated checks stopped ({type(exc).__name__})') from None
