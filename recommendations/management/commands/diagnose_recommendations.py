"""Explicit-authorized, PostgreSQL-enforced read-only V1/V2 diagnostics."""
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

from recommendations.comparison import QueryCounter, DEFAULT_LIMITS
from recommendations.diagnostics import prepare_sample, public_index, diagnose_sample, markdown_diagnostic
from recommendations.feedback import feedback_snapshot
from recommendations.quality_audit import catalog_snapshot, audit_rows
from recommendations.quality_readonly import readonly_snapshot
from recommendations.resolution import resolved_records
from recommendations.services import preference_contract
from recommendations.version_control import release_descriptor, canonical_bytes


class Command(BaseCommand):
    help = '当前 V1/V2 自然基线与耗时诊断；最多两个显式账号，永不批准上线或改分。'

    def add_arguments(self, parser):
        parser.add_argument('--user-email', action='append', default=[])
        parser.add_argument('--confirm-authorized-samples', action='store_true')
        parser.add_argument('--output-dir', type=Path)
        parser.add_argument('--top-k', type=int, default=10)
        parser.add_argument('--repeats', type=int, default=2)
        parser.add_argument('--max-holdouts', type=int, default=4)
        parser.add_argument('--statement-timeout-ms', type=int, default=30000)
        parser.add_argument('--execution-context', choices=('local_client', 'deployed_service', 'isolated_test'), default='local_client')

    def handle(self, *args, **options):
        emails = list(dict.fromkeys(options['user_email']))
        if not options['confirm_authorized_samples'] or not 1 <= len(emails) <= 2 or any(not value.strip() for value in emails):
            raise CommandError('Require 1–2 exact authorized emails and explicit confirmation; no account discovery.')
        if not (3 <= options['top_k'] <= 30 and 2 <= options['repeats'] <= 3
                and 1 <= options['max_holdouts'] <= 4 and 1000 <= options['statement_timeout_ms'] <= 120000):
            raise CommandError('Invalid diagnostic bounds.')
        if connection.vendor != 'postgresql':
            raise CommandError('PostgreSQL only; no SQLite or writable fallback.')
        if settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1':
            raise CommandError('Keep default V1; this diagnostic does not change configuration.')
        directory = options['output_dir'] or Path(settings.BASE_DIR) / '.tools/v2-diagnostics' / datetime.now(
            timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if not directory.resolve().is_relative_to((Path(settings.BASE_DIR) / '.tools').resolve()):
            raise CommandError('Diagnostic artifacts must stay under private .tools.')
        names = ('diagnostic.json', 'report.md', 'manifest.json')
        if any((directory / name).exists() for name in names):
            raise CommandError('Choose a new report directory; overwrite refused.')
        try:
            fingerprint = release_descriptor()['fingerprint']
            def diagnostic_sources():
                return {name: hashlib.sha256((Path(settings.BASE_DIR) / name).read_text(
                    encoding='utf-8').replace('\r\n', '\n').encode()).hexdigest() for name in (
                        'recommendations/diagnostics.py', 'recommendations/management/commands/diagnose_recommendations.py',
                        'recommendations/tests/test_diagnostics.py')}
            sources = diagnostic_sources()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('External HTTP forbidden')):
                with readonly_snapshot(timeout_ms=options['statement_timeout_ms']):
                    with connection.cursor() as cursor:
                        cursor.execute('SHOW transaction_read_only'); readonly = cursor.fetchone()[0] == 'on'
                        cursor.execute('SHOW transaction_isolation'); isolation = cursor.fetchone()[0]
                    if not readonly or isolation != 'repeatable read':
                        raise ValueError('Database did not confirm enforced read-only repeatable-read snapshot.')
                    users = list(get_user_model().objects.filter(email__in=emails).only('pk', 'email', 'is_active'))
                    if (len(users) != len(emails) or {user.email for user in users} != set(emails)
                            or any(not user.is_active for user in users)):
                        raise ValueError('Exact authorized account missing, inactive or ambiguous; no widening.')
                    # Preserve explicit selection order; only sample ordinals leave the process.
                    users = sorted(users, key=lambda user: emails.index(user.email))
                    started = perf_counter()
                    scope, rows = catalog_snapshot()
                    catalog_ms = round((perf_counter() - started) * 1000, 3)
                    audit = audit_rows(rows, reference_year=datetime.now(timezone.utc).year, example_limit=0)
                    if any(audit['issue_counts'].get(key) for key in (
                            'invalid_identity', 'duplicate_identity', 'score_invalid_type', 'score_non_finite', 'score_out_of_range')):
                        raise ValueError('Invalid public identities or ratings; diagnostic cannot repair data.')
                    catalog = [row for row in rows if type(row['bangumi_id']) is int and 0 < row['bangumi_id'] <= 2147483647]
                    index, index_ms = public_index(catalog)
                    samples = []
                    for number, user in enumerate(users, 1):
                        self.stdout.write(f'Preparing authorized sample {number}/{len(users)}; no private content logged', ending='\n')
                        self.stdout.flush()
                        started, counter = perf_counter(), QueryCounter()
                        with connection.execute_wrapper(counter):
                            context, summary = prepare_sample(resolved_records(user), preference_contract(user), user.pk, feedback_snapshot(user))
                        summary['prepare_ms'] = round((perf_counter() - started) * 1000, 3)
                        summary['prepare_sql_queries'] = counter.count
                        summary['prepare_sql_execute_ms'] = round(counter.seconds * 1000, 3)
                        def progress(message):
                            self.stdout.write(f'sample_{number:03d}: {message}'); self.stdout.flush()
                        samples.append(diagnose_sample(context, summary, index, top_k=options['top_k'],
                            repeats=options['repeats'], max_holdouts=options['max_holdouts'], progress=progress))
            if fingerprint != release_descriptor()['fingerprint'] or sources != diagnostic_sources():
                raise ValueError('Code changed during diagnostics; no complete report issued.')
            report = {'contract_version': 'recommendations.diagnostic.v1',
                'created_at': datetime.now(timezone.utc).isoformat(), 'release_fingerprint': fingerprint,
                'diagnostic_sources': sources,
                'execution_context': options['execution_context'], 'database': 'postgresql',
                'transaction': {'read_only': readonly, 'isolation': isolation},
                'setup': {'catalog_read_ms': catalog_ms, 'index_build_ms': index_ms}, 'catalog_scope': scope,
                'catalog_fingerprint': hashlib.sha256(canonical_bytes(catalog)).hexdigest(),
                'samples': samples, 'configuration': {key: options[key] for key in ('top_k', 'repeats', 'max_holdouts')},
                'unchanged_release_limits': DEFAULT_LIMITS, 'online_default': 'content.v1',
                'eligible_for_manual_switch': False, 'approval_issued': False,
                'privacy': {'no_review_reads': True, 'no_mutations': True, 'no_external_calls': True,
                            'natural_samples_only': True, 'explicit_account_selection_only': True}}
            directory.mkdir(parents=True, exist_ok=True)
            contents = {'diagnostic.json': json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                        'report.md': markdown_diagnostic(report)}
            for name, content in contents.items():
                with (directory / name).open('x', encoding='utf-8', newline='\n') as file:
                    file.write(content)
            with (directory / 'manifest.json').open('x', encoding='utf-8') as file:
                json.dump({'complete': True, 'approval_issued': False,
                    'sha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in contents.items()}}, file, indent=2)
            self.stdout.write(f'Diagnostic complete: {directory}; NOT a release approval; online V1 unchanged.')
        except (ValueError, OSError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if isinstance(exc, ValueError) else
                               f'Diagnostic stopped safely ({type(exc).__name__}); no writes or fallback.') from None
