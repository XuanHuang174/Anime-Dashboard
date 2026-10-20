"""Owner-authorized all-active natural cohort; freeze BEFORE any ranking."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from rest_framework.exceptions import APIException

from recommendations import browsing, engine
from recommendations.deployment_evaluation import configuration, cohort_entry, evaluate_sample, deployment_gates
from recommendations.execution import recipe_descriptor
from recommendations.quality_readonly import readonly_snapshot
from recommendations.version_control import (sign_artifact, verify_artifact, read_artifact, canonical_bytes,
                                             release_descriptor)


def private_path(path):
    root = (Path(settings.BASE_DIR) / '.tools').resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError('Reports/locks must stay under private ignored .tools')
    return path


def historical_receipt(path, fingerprint):
    private_path(path)
    raw = path.read_bytes()
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('Historical report too large')
    data = json.loads(raw)
    manifest = json.loads((path.parent / 'manifest.json').read_text(encoding='utf-8'))
    from recommendations.recall_diagnostics import load_config
    _, fixed_sha = load_config(Path(settings.BASE_DIR) / 'evaluation/v2a-recall.v1.json')
    if (manifest.get('complete') is not True or manifest['sha256'].get('recall-audit.json') != hashlib.sha256(raw).hexdigest()
            or data.get('release_fingerprint') != fingerprint or data.get('config_sha256') != fixed_sha
            or data.get('contract_version') != 'recommendations.execution-audit.v1'
            or not data.get('cohort_signature') or not data.get('samples')
            or any(s.get('d1_balanced_equivalence') != 'pass' for s in data['samples'])):
        raise ValueError('Current-code historical fixed-cohort D1 receipt required; do not replace historical targets')
    return hashlib.sha256(raw).hexdigest()


class Command(BaseCommand):
    help = 'D3: explicit site-owner read-only authorization, preregistered all-active cohort; no scores changed or V2 enabled.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        phase = parser.add_mutually_exclusive_group(required=True)
        phase.add_argument('--freeze-only', action='store_true')
        phase.add_argument('--cohort-lock', type=Path)
        parser.add_argument('--historical-report', type=Path)
        parser.add_argument('--test-attestation', type=Path)
        parser.add_argument('--output-dir', required=True, type=Path)

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Require explicit sitewide read-only authorization, PostgreSQL and unchanged V1/off.')
        try:
            directory = private_path(options['output_dir'])
            if directory.exists():
                raise ValueError('Use a new directory; overwrite refused')
            config, fingerprint = configuration(), release_descriptor()['fingerprint']
            lock = None
            if options['cohort_lock']:
                path = private_path(options['cohort_lock'])
                lock = read_artifact(path, 'browse-cohort')
                if not options['historical_report'] or not options['test_attestation']:
                    raise ValueError('Evaluation requires historical receipt and isolated test attestation')
                tests = read_artifact(private_path(options['test_attestation']), 'browse-tests')
                historical_sha = historical_receipt(options['historical_report'], fingerprint)
            started = perf_counter()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:config['maximum_accounts'] + 1])
                    if len(users) > config['maximum_accounts']:
                        raise ValueError('Account bound exceeded; no favorable truncation or partial report')
                    selected, entries = [], []
                    for user in users:
                        context = engine.user_context(user, evaluation_baseline=True)
                        if context['mode'] == 'personalized' and len(context['seeds']) >= max(4, settings.RECOMMENDATION_PROFILE_MIN_SAMPLES + 1):
                            selected.append((user, context)); entries.append(cohort_entry(user, context))
                        if perf_counter() - started > config['maximum_wall_seconds']:
                            raise ValueError('Read-only evaluation budget exceeded')
                    if len(selected) > config['maximum_eligible_accounts']:
                        raise ValueError('Eligible account bound exceeded; no partial selection')
                    cohort = {'configuration': config, 'catalog_revision': browsing.catalog_revision(),
                              'active_account_count': len(users), 'entries': entries, 'recipe': recipe_descriptor()}
                    if options['freeze_only']:
                        report = sign_artifact('browse-cohort', cohort)
                        name = 'cohort.lock.json'
                    else:
                        for key, value in cohort.items():
                            if lock.get(key) != value:
                                raise ValueError('Frozen cohort/catalog/input drift; stopped without reselecting targets')
                        _, index = browsing.public_browse_index(cohort['catalog_revision'], {}, recipe=recipe_descriptor())
                        samples = []
                        for ordinal, (user, context) in enumerate(selected, 1):
                            def progress(message):
                                self.stdout.write(f'sample_{ordinal:03d}/{len(selected)}: {message}'); self.stdout.flush()
                                if perf_counter() - started > config['maximum_wall_seconds']:
                                    raise ValueError('Read-only evaluation budget exceeded; no partial success')
                            progress('baseline and fixed targets, read-only')
                            samples.append(evaluate_sample(user, context, index, progress=progress))
                        body = {'configuration': config, 'recipe': recipe_descriptor(), 'samples': samples,
                            'cohort_sha256': hashlib.sha256(canonical_bytes(lock)).hexdigest(),
                            'frozen_before_ranking': True, 'historical_baseline_checked': True,
                            'historical_report_sha256': historical_sha, 'read_only': True,
                            'no_review_reads': True, 'no_external_http': True, 'online_default': 'content.v1',
                            'execution_context': 'local_or_cli_engine_process_not_deployment_performance',
                            'active_account_count': len(users), 'seconds': round(perf_counter() - started, 3)}
                        report = sign_artifact('browse-evaluation', body)
                        gates = deployment_gates(report, tests)
                        name = 'evaluation.json'
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during audit; no complete report issued')
            directory.mkdir(parents=True)
            with (directory / name).open('x', encoding='utf-8') as file:
                json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
            if lock:
                with (directory / 'gates.json').open('x', encoding='utf-8') as file:
                    json.dump(gates, file, ensure_ascii=False, indent=2)
                self.stdout.write('Gate status: ' + ', '.join(f'{k}={v["status"]}' for k, v in gates.items()))
            self.stdout.write(f'Complete: {directory}; eligible_accounts={len(selected)}; V1 unchanged; no approval issued.')
        except (ValueError, OSError, DatabaseError, APIException, KeyError) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'D3 evaluation stopped safely ({type(exc).__name__}).') from None
