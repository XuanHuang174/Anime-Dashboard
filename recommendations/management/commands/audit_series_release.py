import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from recommendations.series_release import audit
from recommendations.source_pipeline import artifact_writer, SourceError


class Command(BaseCommand):
    help = '只读审计当前数据库的系列覆盖及部署开关；不导入、不修改开关，也不代表线上验收通过。'

    def add_arguments(self, parser):
        parser.add_argument('--stage', choices=('closed', 'canary', 'full'), default='closed')
        parser.add_argument('--min-fresh-percent', type=float, default=90)
        parser.add_argument('--output', required=True, type=Path)

    def handle(self, *args, **options):
        try:
            result = audit(options['stage'], options['min_fresh_percent'])
            with artifact_writer(options['output']) as file:
                json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)
                file.write('\n')
        except (OSError, ValueError, DatabaseError, SourceError) as exc:
            raise CommandError(f'系列发布审计未完成（{type(exc).__name__}）；没有修改数据库或开关。') from None
        self.stdout.write(json.dumps({key: result[key] for key in ('status', 'issues', 'code_revision')}, ensure_ascii=False))
        if result['issues']:
            raise CommandError('系列发布条件不满足；已保留诊断报告，请先修复列出的事项。')
