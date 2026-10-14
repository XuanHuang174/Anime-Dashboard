from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from rest_framework.exceptions import APIException

from recommendations.comparison import build_comparison_report, evaluate_snapshot
from recommendations.comparison_report import write_comparison, FILES
from recommendations.quality_audit import audit_rows, catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.resolution import resolved_records
from recommendations.services import preference_contract
from recommendations.version_control import CHECK_MODULES, read_artifact


class Command(BaseCommand):
    help = '5B-3：只读两轮配对对照；只接受显式授权自然样本，不扫描全站或改变线上默认。'

    def add_arguments(self, parser):
        parser.add_argument('--catalog-only', action='store_true')
        parser.add_argument('--user-id', type=int, action='append', default=[])
        parser.add_argument('--user-email', action='append', default=[])
        parser.add_argument('--confirm-authorized-samples', action='store_true')
        parser.add_argument('--test-attestation', type=Path)
        parser.add_argument('--output-dir', type=Path)
        parser.add_argument('--top-k', type=int, default=10)
        parser.add_argument('--max-holdouts', type=int, default=4)
        parser.add_argument('--repeats', type=int, default=2)
        parser.add_argument('--statement-timeout-ms', type=int, default=30000)

    def handle(self, *args, **options):
        ids, emails = list(dict.fromkeys(options['user_id'])), list(dict.fromkeys(options['user_email']))
        count = len(ids) + len(emails)
        if ((ids and emails) or count > 5 or any(identity < 1 for identity in ids)
                or (options['catalog_only'] and count)
                or (not options['catalog_only'] and (not count or not options['confirm_authorized_samples']))):
            raise CommandError('Select 1–5 explicitly authorized accounts by IDs OR exact emails, or use catalog-only')
        if not 3 <= options['top_k'] <= 30 or not 1 <= options['max_holdouts'] <= 5 or not 2 <= options['repeats'] <= 3 or count * options['max_holdouts'] > 20:
            raise CommandError('Bounds: top-k 3–30, holdouts 1–5, repeats 2–3, total targets <=20')
        directory = options['output_dir'] or Path(settings.BASE_DIR) / '.tools' / 'recommendation-comparison' / datetime.now(
            timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if not directory.resolve().is_relative_to((Path(settings.BASE_DIR) / '.tools').resolve()):
            raise CommandError('Reports must stay under private ignored .tools; never use a public/static directory')
        if any((directory / name).exists() for name in FILES):
            raise CommandError('Output exists; choose a new directory')
        try:
            attestation = read_artifact(options['test_attestation'], 'tests') if options['test_attestation'] else None
            if attestation and (attestation.get('modules') != list(CHECK_MODULES) or attestation.get('status') != 'pass'
                                or attestation.get('skipped') != 0):
                raise ValueError('Required isolated test attestation is incomplete')
            with readonly_snapshot(timeout_ms=options['statement_timeout_ms']):
                scope, rows = catalog_snapshot()
                audit = audit_rows(rows, reference_year=datetime.now(timezone.utc).year, example_limit=0)
                if count and any(audit['issue_counts'].get(key) for key in (
                        'invalid_identity', 'duplicate_identity', 'score_invalid_type', 'score_non_finite', 'score_out_of_range')):
                    raise ValueError('Invalid public identities or ratings block evaluation; no automatic repair')
                catalog = [row for row in rows if type(row['bangumi_id']) is int and 0 < row['bangumi_id'] <= 2_147_483_647]
                users = list(get_user_model().objects.filter(pk__in=ids).only('pk', 'is_active')) if ids else (
                    list(get_user_model().objects.filter(email__in=emails).only('pk', 'is_active', 'email')) if emails else [])
                if len(users) != count or any(not user.is_active for user in users) or (emails and {user.email for user in users} != set(emails)):
                    raise ValueError('Explicit account selection is missing, inactive or ambiguous; no widening of scope')
                samples = []
                for position, user in enumerate(sorted(users, key=lambda user: user.pk), 1):
                    self.stdout.write(f'Evaluating authorized sample {position}/{count}; same snapshot, no private content logged')
                    samples.append(evaluate_snapshot(resolved_records(user), preference_contract(user), user.pk, catalog,
                        top_k=options['top_k'], max_holdouts=options['max_holdouts'], repeats=options['repeats']))
                report = build_comparison_report(scope, catalog, samples, attestation,
                    top_k=options['top_k'], max_holdouts=options['max_holdouts'], repeats=options['repeats'])
            if attestation:
                read_artifact(options['test_attestation'], 'tests')  # Detect code changes during a long evaluation.
            for path in write_comparison(report, directory):
                self.stdout.write(str(path))
            self.stdout.write('Gate status: ' + ', '.join(f"{key}={value['status']}" for key, value in report['gates'].items()))
            self.stdout.write('Online default unchanged; approval issued only if ALL six gates pass')
        except (ValueError, OSError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if isinstance(exc, ValueError) else
                               f'Comparison stopped safely ({type(exc).__name__}); no writes or fallback') from None
