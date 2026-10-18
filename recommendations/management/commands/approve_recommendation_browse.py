"""Manual reviewed release receipt, only after every preregistered gate passes."""
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from recommendations.deployment_evaluation import deployment_gates
from recommendations.execution import recipe_descriptor
from recommendations.version_control import read_artifact, sign_artifact, canonical_bytes
from recommendations.management.commands.evaluate_recommendation_deployment import private_path


class Command(BaseCommand):
    help = 'D3: validate signed independent receipts; never changes any environment setting or enables V2.'

    def add_arguments(self, parser):
        for name in ('evaluation', 'tests', 'performance', 'output-dir'):
            parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--confirm-reviewed', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_reviewed']:
            raise CommandError('Manual review confirmation required; no approval issued')
        try:
            directory = private_path(options['output_dir'])
            if directory.exists():
                raise ValueError('Use a new directory; overwrite refused')
            paths = {name: private_path(options[name]) for name in ('evaluation', 'tests', 'performance')}
            receipts = {name: read_artifact(path, 'browse-' + ('evaluation' if name == 'evaluation' else name)) for name, path in paths.items()}
            gates = deployment_gates(receipts['evaluation'], receipts['tests'], receipts['performance'])
            if any(value['status'] != 'pass' for value in gates.values()):
                raise ValueError('Not all six gates pass; no approval issued: ' + ', '.join(f'{k}={v["status"]}' for k, v in gates.items()))
            approval = sign_artifact('browse-approval', {'recipe': recipe_descriptor(serving_enabled=True),
                'gates': {key: value['status'] for key, value in gates.items()},
                'evaluation_sha256': hashlib.sha256(paths['evaluation'].read_bytes()).hexdigest(),
                'tests_sha256': hashlib.sha256(canonical_bytes(receipts['tests'])).hexdigest(),
                'performance_sha256': hashlib.sha256(canonical_bytes(receipts['performance'])).hexdigest()})
            directory.mkdir(parents=True)
            for name, path in paths.items():
                with (directory / (name + '.json')).open('xb') as file:
                    file.write(path.read_bytes())
            with (directory / 'approval.json').open('x', encoding='utf-8') as file:
                json.dump(approval, file, ensure_ascii=False, indent=2)
            self.stdout.write(f'Approval bundle: {directory}; V1 unchanged. Operator switch is separate.')
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Approval stopped safely ({type(exc).__name__}).') from None
