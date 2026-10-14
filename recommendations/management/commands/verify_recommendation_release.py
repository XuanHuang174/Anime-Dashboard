"""Generate signed test attestation by running the fixed isolated safety suite."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from recommendations.version_control import CHECK_MODULES, sign_artifact, release_descriptor


class Command(BaseCommand):
    help = '5B-3：在隔离 SQLite 子进程运行固定验收测试，生成当前代码的签名凭据；不读取正式手账。'

    def add_arguments(self, parser):
        parser.add_argument('--output', type=Path)

    def handle(self, *args, **options):
        path = options['output'] or Path(settings.BASE_DIR) / '.tools' / 'recommendation-comparison' / datetime.now(
            timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') / 'test-attestation.json'
        if path.exists():
            raise CommandError('Choose a new attestation path; overwrite refused')
        command = [sys.executable, str(Path(settings.BASE_DIR) / 'manage.py'), 'test', *CHECK_MODULES,
                   '--settings=config.recommendation_test_settings', '--noinput', '--verbosity=1']
        self.stdout.write('Running fixed isolated safety/evidence/isolation/rollback tests; production DB is not used')
        before = release_descriptor()['fingerprint']
        try:
            completed = subprocess.run(command, cwd=settings.BASE_DIR, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            raise CommandError('Release checks did not finish; no attestation issued') from None
        # No arbitrary user-supplied test module, skip allowance or handwritten pass flag.
        output = completed.stdout + completed.stderr
        if completed.returncode != 0 or 'skipped=' in output or '\nOK\n' not in output:
            raise CommandError('Required release tests failed or were skipped; no attestation issued')
        if before != release_descriptor()['fingerprint']:
            raise CommandError('Release changed during tests; rerun checks before attestation')
        payload = sign_artifact('tests', {'modules': list(CHECK_MODULES), 'status': 'pass', 'skipped': 0,
                                         'isolated_settings': 'config.recommendation_test_settings'})
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        self.stdout.write(str(path))
