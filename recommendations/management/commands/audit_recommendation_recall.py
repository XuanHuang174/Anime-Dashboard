"""V2-A two-step frozen cohort and read-only full pipeline audit."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from rest_framework.exceptions import APIException

from recommendations.comparison import DEFAULT_LIMITS
from recommendations.feedback import feedback_snapshot
from recommendations.quality_audit import catalog_snapshot, audit_rows
from recommendations.quality_readonly import readonly_snapshot
from recommendations.resolution import resolved_records
from recommendations.services import preference_contract
from recommendations.version_control import release_descriptor, canonical_bytes
from recommendations.evidence import _token
from recommendations.diagnostics import prepare_sample, public_index
from recommendations.recall_diagnostics import (CONTRACT, COHORT_CONTRACT, load_config,
    freeze_cohort, verify_cohort, catalog_states, audit_sample, markdown_report)


class Command(BaseCommand):
    help = 'V2-A：先冻结无结果选择的评测集，再只读审计。只接受两个以内明确授权账号，不启用V2。'
    sample_auditor = staticmethod(audit_sample)
    report_renderer = staticmethod(markdown_report)
    report_metadata = staticmethod(lambda: {})

    def add_arguments(self, parser):
        parser.add_argument('--user-email', action='append', default=[])
        parser.add_argument('--confirm-authorized-samples', action='store_true')
        parser.add_argument('--config', type=Path, default=Path(settings.BASE_DIR) / 'evaluation/v2a-recall.v1.json')
        phase = parser.add_mutually_exclusive_group(required=True)
        phase.add_argument('--freeze-only', action='store_true')
        phase.add_argument('--cohort-lock', type=Path)
        parser.add_argument('--output-dir', type=Path)
        parser.add_argument('--execution-context', choices=('local_client', 'deployed_service', 'isolated_test'), default='local_client')

    def handle(self, *args, **options):
        emails = options['user_email']
        if (not options['confirm_authorized_samples'] or not 1 <= len(emails) <= 2
                or len(set(emails)) != len(emails) or any(not item or item != item.strip() for item in emails)):
            raise CommandError('Require 1–2 exact authorized accounts and confirmation; no user discovery.')
        if connection.vendor != 'postgresql' or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1':
            raise CommandError('PostgreSQL enforced read-only with unchanged default V1 required.')
        if bool(options['freeze_only']) == bool(options['cohort_lock']):
            raise CommandError('Choose freeze-only OR one previously frozen cohort lock.')
        private = (Path(settings.BASE_DIR) / '.tools').resolve()
        directory = options['output_dir'] or private / 'v2a-recall' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if not directory.resolve().is_relative_to(private) or directory.exists():
            raise CommandError('Use a new private .tools report directory; overwrite refused.')
        try:
            config, config_sha = load_config(options['config'])
            lock = None
            if options['cohort_lock']:
                path = options['cohort_lock'].resolve()
                if not path.is_relative_to(private):
                    raise ValueError('Cohort lock must stay under private .tools')
                raw = path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError('Cohort lock exceeds size bound')
                lock = json.loads(raw)
                body = {key: item for key, item in lock.items() if key != 'signature'}
                if not isinstance(lock.get('signature'), str) or not hmac.compare_digest(lock['signature'], _token(COHORT_CONTRACT, body)):
                    raise ValueError('Invalid cohort signature before database reads')
            release = release_descriptor()['fingerprint']
            started = perf_counter()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP permitted')):
                with readonly_snapshot():
                    with connection.cursor() as cursor:
                        cursor.execute('SHOW transaction_read_only'); readonly = cursor.fetchone()[0] == 'on'
                        cursor.execute('SHOW transaction_isolation'); isolation = cursor.fetchone()[0]
                    if not readonly or isolation != 'repeatable read':
                        raise ValueError('Database did not confirm a read-only consistent snapshot')
                    users = list(get_user_model().objects.filter(email__in=emails).only('pk', 'email', 'is_active'))
                    if len(users) != len(emails) or {user.email for user in users} != set(emails) or any(not user.is_active for user in users):
                        raise ValueError('Exact authorized account missing/inactive/ambiguous; scope not widened')
                    users.sort(key=lambda user: emails.index(user.email))
                    scope, rows = catalog_snapshot()
                    audit = audit_rows(rows, reference_year=datetime.now(timezone.utc).year, example_limit=0)
                    if any(audit['issue_counts'].get(name) for name in ('invalid_identity', 'duplicate_identity',
                            'score_invalid_type', 'score_non_finite', 'score_out_of_range')):
                        raise ValueError('Invalid catalog identity/rating; audit will not repair it')
                    catalog = {row['bangumi_id']: row for row in rows if type(row['bangumi_id']) is int and 0 < row['bangumi_id'] <= 2147483647}
                    states = catalog_states()
                    catalog_sha = hashlib.sha256(canonical_bytes([rows, sorted(states.items())])).hexdigest()
                    samples = [(user, *prepare_sample(resolved_records(user), preference_contract(user), user.pk, feedback_snapshot(user))) for user in users]
                    if options['freeze_only']:
                        cohort = freeze_cohort(samples, config, config_sha, catalog_sha, release)
                        report = None
                    else:
                        verify_cohort(lock, samples, config, config_sha, catalog_sha)
                        index, index_ms = public_index(list(catalog.values()))
                        reports = []
                        for number, (user, context, summary) in enumerate(samples, 1):
                            def progress(message):
                                self.stdout.write(f'sample_{number:03d}: {message}'); self.stdout.flush()
                            reports.append(self.sample_auditor(user, context, summary, index, catalog, states, config, progress))
                        report = {'contract_version': CONTRACT, 'created_at': datetime.now(timezone.utc).isoformat(),
                            'config_sha256': config_sha, 'configuration': config, 'catalog_sha256': catalog_sha,
                            'release_fingerprint': release, 'frozen_release_fingerprint': lock['frozen_release_fingerprint'],
                            'cohort_signature': lock['signature'], 'execution_context': options['execution_context'],
                            'transaction': {'read_only': readonly, 'isolation': isolation}, 'catalog_scope': scope,
                            'setup': {'index_build_ms': index_ms}, 'samples': reports,
                            'unchanged_release_limits': DEFAULT_LIMITS,
                            'online_default': 'content.v1', 'approval_issued': False, 'eligible_for_manual_switch': False,
                            'privacy': {'no_review_reads': True, 'no_mutations': True, 'no_external_http': True},
                            'display_semantics': 'Returned top10 slice, not actual frontend impressions; V2 unavailable in production browse.'}
                        report.update(self.report_metadata())
            if release != release_descriptor()['fingerprint'] or config_sha != load_config(options['config'])[1]:
                raise ValueError('Code/config changed during audit; no complete report issued')
            directory.mkdir(parents=True)
            if options['freeze_only']:
                contents = {'cohort.lock.json': json.dumps(cohort, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                            'freeze.md': '# V2-A cohort frozen\n\nNo ranking performed. Fixed configuration and anonymized HMAC target handles; no approval.\n'}
            else:
                report['audit_wall_ms'] = round((perf_counter() - started) * 1000, 3)
                contents = {'recall-audit.json': json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                            'report.md': self.report_renderer(report)}
                events = []
                for number, sample in enumerate(report['samples'], 1):
                    for fold in sample['folds']:
                        for algorithm, categories in fold['arms'].items():
                            for category, run in categories.items():
                                events.append(json.dumps({'sample_ordinal': number, 'target_ordinal': fold['target_ordinal'],
                                    'historical_member': fold['historical_member'], 'algorithm': algorithm, 'category': category,
                                    'logical_stages': fold['logical_census']['stages'], 'engine_stages': run['pipeline']['stages'],
                                    'destination': run['destination']}, ensure_ascii=False, allow_nan=False))
                contents['target-events.jsonl'] = '\n'.join(events) + ('\n' if events else '')
            for name, content in contents.items():
                with (directory / name).open('x', encoding='utf-8', newline='\n') as file:
                    file.write(content)
            with (directory / 'manifest.json').open('x', encoding='utf-8') as file:
                json.dump({'complete': True, 'approval_issued': False, 'freeze_only': options['freeze_only'],
                           'sha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in contents.items()}}, file, indent=2)
            self.stdout.write(f'Complete: {directory}; default V1 unchanged; no release approval.')
        except (ValueError, TypeError, KeyError, OSError, DatabaseError, APIException) as exc:
            # Exception types only for framework/DB failures, never SQL/params,
            # private source titles or model reprs in terminal output.
            raise CommandError(str(exc) if type(exc) is ValueError else f'Audit stopped safely ({type(exc).__name__}); no writable fallback.') from None
