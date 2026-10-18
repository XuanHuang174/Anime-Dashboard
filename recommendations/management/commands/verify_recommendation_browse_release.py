"""D3 signed receipt from fixed PostgreSQL + frontend + isolated browser runs."""
import json
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from recommendations.deployment_evaluation import REQUIRED_CASES
from recommendations.version_control import release_descriptor, sign_artifact
from recommendations.management.commands.evaluate_recommendation_deployment import private_path


class Command(BaseCommand):
    help = 'D3 fixed local PostgreSQL/frontend/browser receipt; never SQLite, production test DB or handwritten pass flags.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True, type=Path)

    def handle(self, *args, **options):
        from scripts.verify_recommendation_7c import validate_target
        try:
            validate_target(os.environ.get('RECOMMENDATION_TEST_DATABASE_URL', ''))
            path = private_path(options['output'])
            if path.exists():
                raise ValueError('Choose a new attestation path')
            before = release_descriptor()['fingerprint']
            node = shutil.which('node')
            if not node:
                raise ValueError('Existing Node runtime required; no automatic installer')
            covered = importlib.util.find_spec('coverage') is not None
            child_env = dict(os.environ)
            child_env['COVERAGE_FILE'] = str(Path(settings.BASE_DIR) / '.tools/.coverage-v2d3')
            backend_command = [sys.executable, *(['-m', 'coverage', 'run', '--branch', '--source=recommendations'] if covered else []),
                               'scripts/verify_recommendation_v2d3.py']
            jobs = [('PostgreSQL', backend_command, settings.BASE_DIR),
                    ('frontend', [node, '--test', 'tests'], Path(settings.BASE_DIR) / 'frontend'),
                    ('build', [node, 'node_modules/vite/bin/vite.js', 'build'], Path(settings.BASE_DIR) / 'frontend'),
                    ('browser', [node, 'scripts/test-recommendation-v2-deployment.mjs'], Path(settings.BASE_DIR) / 'frontend')]
            outputs = {}
            for label, command, cwd in jobs:
                self.stdout.write('Running fixed isolated ' + label + ' checks'); self.stdout.flush()
                done = subprocess.run(command, cwd=cwd, env=child_env, capture_output=True, text=True, encoding='utf-8', timeout=600)
                if done.returncode != 0:
                    raise ValueError(label + ' checks failed; no receipt issued (inspect local test output separately)')
                outputs[label] = done.stdout
            last = json.loads(outputs['PostgreSQL'].strip().splitlines()[-1])
            backend = json.loads(private_path(Path(last['report_path'])).read_text(encoding='utf-8'))
            browser = json.loads((Path(settings.BASE_DIR) / '.tools/acceptance-v2d3-browser/report.json').read_text(encoding='utf-8'))
            if (backend.get('result') != 'passed' or backend.get('skipped_cases') or backend.get('production_access') is not False
                    or browser.get('result') != 'passed' or not all(c['passed'] for c in browser['checks'])
                    or not all(any(name.endswith('.' + required) for name in backend['passed_cases']) for required in REQUIRED_CASES)
                    or not re.search(r'# fail 0\b', outputs['frontend']) or not re.search(r'# skipped 0\b', outputs['frontend'])):
                raise ValueError('Incomplete test receipt; no approval')
            if before != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during test runs')
            payload = sign_artifact('browse-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
                'skipped': 0, 'backend_tests': backend['tests_run'], 'frontend_tests': int(re.search(r'# tests (\d+)', outputs['frontend'])[1]),
                'browser_checks': len(browser['checks']), 'build': 'pass', 'required_cases': list(REQUIRED_CASES),
                'coverage_collected': covered,
                'locks': [backend['feedback_concurrency_passed'], backend['want_concurrency_passed'], backend['additional_concurrency_passed']]})
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as file:
                json.dump(payload, file, indent=2)
            self.stdout.write(f'Complete: {path}; no V2 approval or switch issued.')
        except (ValueError, OSError, subprocess.TimeoutExpired, KeyError, TypeError) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Release checks stopped safely ({type(exc).__name__}).') from None
