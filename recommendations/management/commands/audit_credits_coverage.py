import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from recommendations.credits_coverage import coverage_audit
from recommendations.source_pipeline import artifact_writer, SourceError


class Command(BaseCommand):
    help = '只读审计声优/制作公司源覆盖，分普通/成人/未知目录，不执行功能放行。'

    def add_arguments(self, parser):
        parser.add_argument('--output', type=Path, required=True)

    def handle(self, *args, **options):
        try:
            if options['output'].exists():
                raise SourceError('报告已存在，请换一个明确路径。')
            report = coverage_audit()
            with artifact_writer(options['output']) as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
            self.stdout.write(json.dumps({key: report.get(key) for key in ('schema_ready', 'status', 'catalog_count', 'integrity_issues')}, ensure_ascii=False))
            if not report['schema_ready'] or report.get('integrity_issues'):
                raise CommandError('参与关系结构尚未就绪或指针存在异常，请查看报告。')
        except (SourceError, ValueError, OSError) as error:
            raise CommandError(str(error)) from None
