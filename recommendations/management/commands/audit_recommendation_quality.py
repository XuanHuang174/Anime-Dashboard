from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from recommendations.engine import build_feature_index
from recommendations.quality_audit import audit_rows, catalog_snapshot
from recommendations.quality_evaluation import evaluate_user, evaluate_probe, PREFERENCE_PROBES
from recommendations.quality_readonly import readonly_snapshot
from recommendations.quality_report import report_envelope, write_reports


class Command(BaseCommand):
    help = '5A：只读推荐库质量审计与 V1 评测；默认不读取私人手账，报告仅保存本地。'

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', type=Path)
        parser.add_argument('--user-id', type=int, action='append', default=[], help='明确选择已授权样本用户，可重复；不支持自动全站用户扫描')
        parser.add_argument('--top-k', type=int, default=10)
        parser.add_argument('--max-holdouts', type=int, default=5)
        parser.add_argument('--include-probes', action='store_true', help='增加固定合成题材探针，不读取其他私人用户，不冒充画像效果')
        parser.add_argument('--statement-timeout-ms', type=int, default=30000)

    def handle(self, *args, **options):
        ids = list(dict.fromkeys(options['user_id']))
        k, holdouts = options['top_k'], options['max_holdouts']
        if any(value < 1 for value in ids) or len(ids) > 10:
            raise CommandError('Only 1–10 explicitly selected positive user IDs are allowed')
        if not 1 <= k <= 30 or not 1 <= holdouts <= 10 or len(ids) * holdouts > 50:
            raise CommandError('top-k must be 1–30; holdouts 1–10; total fold budget at most 50')
        directory = options['output_dir'] or Path(settings.BASE_DIR) / '.tools' / 'recommendation-quality' / datetime.now(
            timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if any((directory / name).exists() for name in ('baseline.json', 'report.md', 'cases.json', 'manifest.json')):
            raise CommandError('Output already exists; choose a new directory')
        try:
            with readonly_snapshot(timeout_ms=options['statement_timeout_ms']):
                scope, rows = catalog_snapshot()
                self.stdout.write(f'Catalog snapshot: {len(rows)} safe rows; read-only transaction active')
                catalog = audit_rows(rows, reference_year=datetime.now(timezone.utc).year)
                catalog['scope'] = scope
                users = {user.pk: user for user in get_user_model().objects.filter(pk__in=ids).only('pk', 'is_active')} if ids else {}
                if set(users) != set(ids):
                    raise ValueError('Some explicitly selected sample users do not exist')
                invalid = any(catalog['issue_counts'].get(key) for key in (
                    'invalid_identity', 'duplicate_identity', 'score_invalid_type', 'score_non_finite', 'score_out_of_range'))
                if invalid and (ids or options['include_probes']):
                    raise ValueError('Invalid catalog identity/rating blocks V1 evaluation; run catalog-only audit first')
                ranking_rows = [row for row in rows if type(row['bangumi_id']) is int and 0 < row['bangumi_id'] <= 2_147_483_647]
                catalog['ranking_eligible_identity_rows'] = len(ranking_rows)
                index = build_feature_index(ranking_rows) if ids or options['include_probes'] else None
                evaluations = []
                for position, identity in enumerate(ids, 1):
                    self.stdout.write(f'Evaluating sample {position}/{len(ids)} (no identifiers or private contents logged)')
                    evaluations.append(evaluate_user(users[identity], index, top_k=k, max_holdouts=holdouts))
                if options['include_probes']:
                    self.stdout.write('Evaluating 3 fixed synthetic genre probes (NOT real user samples)')
                    evaluations.extend(evaluate_probe(index, name=name, tags=tags, top_k=k) for name, tags in PREFERENCE_PROBES)
                report = report_envelope(catalog, evaluations, top_k=k, max_holdouts=holdouts)
            paths = write_reports(report, directory)
        except (ValueError, OSError, DatabaseError) as exc:
            # DB exceptions may include credentials/SQL parameters; do not echo their content.
            raise CommandError(str(exc) if isinstance(exc, ValueError) else
                               f'Audit stopped safely ({type(exc).__name__}); no database writes performed') from None
        for path in paths:
            self.stdout.write(str(path))
