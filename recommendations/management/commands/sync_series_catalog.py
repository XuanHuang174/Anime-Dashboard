"""Preview first, then explicitly apply the identical public-catalog import plan."""
import json
from pathlib import Path
import re
import zipfile

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from recommendations.models import RecommendationAnime
from recommendations.quality_readonly import readonly_snapshot
from recommendations.series_catalog_source import prepare_catalog_archive, MAX_CATALOG
from recommendations.series_source import digest
from recommendations.series_store import publish_archive
from recommendations.source_pipeline import SourceError, artifact_writer


class SeriesWriteGuard:
    """Defense in depth: this command can only mutate independent series tables."""
    allowed = {f'recommendations_series{name}' for name in ('state', 'node', 'observation', 'head', 'assertion')}

    def __call__(self, execute, sql, params, many, context):
        text = sql.strip()
        if re.match(r'^(INSERT|UPDATE|DELETE)\b', text, re.I):
            match = re.match(r'^(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?([a-z_]+)"?', text, re.I)
            if not match or match[1].lower() not in self.allowed:
                raise SourceError('目录导入拒绝写入系列表之外的数据。')
        elif not re.match(r'^(SELECT|SET|SAVEPOINT|RELEASE|ROLLBACK|BEGIN|COMMIT)\b', text, re.I):
            raise SourceError('目录导入拒绝执行非预期 SQL。')
        return execute(sql, params, many, context)


def catalog_snapshot():
    with readonly_snapshot():
        rows = list(RecommendationAnime.objects.filter(bangumi_id__gt=0).order_by('bangumi_id')
                    .values_list('bangumi_id', 'nsfw')[:MAX_CATALOG + 1])
        if not rows or len(rows) > MAX_CATALOG:
            raise SourceError('真实目录为空或超过预检上限。')
        return rows


class Command(BaseCommand):
    help = '对真实公共目录一次扫描归档，隔离异常，按不超过100个来源分批导入独立系列表。'

    def add_arguments(self, parser):
        parser.add_argument('--input', required=True, type=Path)
        parser.add_argument('--expected-sha256', required=True)
        parser.add_argument('--timeout-seconds', type=int, default=300)
        parser.add_argument('--report', required=True, type=Path)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--expected-plan', help='必须与之前预检报告的 plan_fingerprint 一致')

    def handle(self, *args, **options):
        try:
            if options['report'].exists():
                raise SourceError('报告已存在，请使用新的明确路径。')
            catalog = catalog_snapshot()
            prepared = prepare_catalog_archive(options['input'], options['expected_sha256'],
                [row[0] for row in catalog], timeout_seconds=options['timeout_seconds'])
            report = prepared['report']
            report['catalog_identity_fingerprint'] = digest(catalog)
            report['plan_fingerprint'] = digest([report['plan_fingerprint'], digest(catalog)])
            report['mode'] = 'apply' if options['apply'] else 'preview'
            report['database_writes'] = False
            if options['apply']:
                if options['expected_plan'] != report['plan_fingerprint']:
                    raise SourceError('必须提供与真实目录和官方归档一致的预检 plan_fingerprint。')
                if catalog != catalog_snapshot():
                    raise SourceError('目录在预检期间发生变化，请重新预检。')
                publication = []
                with connection.execute_wrapper(SeriesWriteGuard()):
                    for index, batch in enumerate(prepared['batches'], 1):
                        result = publish_archive(batch)
                        publication.append(result)
                        self.stdout.write(json.dumps({'batch': index, 'total': len(prepared['batches']), **result}))
                        self.stdout.flush()
                report['publication'] = publication
                report['database_writes'] = any(row['changed'] for row in publication)
                report['catalog_identity_unchanged'] = catalog == catalog_snapshot()
            with artifact_writer(options['report']) as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
            self.stdout.write(json.dumps({key: report[key] for key in ('mode', 'catalog_count', 'complete_sources',
                'partial_sources', 'skipped_sources', 'batch_count', 'issue_counts', 'plan_fingerprint', 'database_writes')},
                ensure_ascii=False))
        except (SourceError, ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
            raise CommandError(f'系列目录导入停止（{type(exc).__name__}）；已提交批次可按原计划安全续跑。') from None
