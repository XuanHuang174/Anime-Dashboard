from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from recommendations.quality_readonly import readonly_snapshot
from recommendations.tag_coverage import ARTIFACTS, analyze_public_tags, public_tag_snapshot, write_coverage_reports


class Command(BaseCommand):
    help = '5B-1：只读公共标签三分类及覆盖率试算；只生成待审核清单，不启用映射、不查询私人手账。'

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', type=Path)
        parser.add_argument('--example-limit', type=int, default=3, help='每个标签最多 0–5 个非成人公共作品样例')
        parser.add_argument('--statement-timeout-ms', type=int, default=30000)

    def handle(self, *args, **options):
        if not 0 <= options['example_limit'] <= 5:
            raise CommandError('example-limit must be 0–5')
        directory = options['output_dir'] or Path(settings.BASE_DIR) / '.tools' / 'recommendation-tags' / datetime.now(
            timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if any((directory / name).exists() for name in ARTIFACTS):
            raise CommandError('Output already exists; choose a new directory')
        try:
            with readonly_snapshot(timeout_ms=options['statement_timeout_ms']):
                scope, rows = public_tag_snapshot()
                self.stdout.write(f'Snapshot: {len(rows)} explicitly non-adult public rows; database read-only')
                report = analyze_public_tags(rows, scope, example_limit=options['example_limit'])
            paths = write_coverage_reports(report, directory)
        except (ValueError, OSError, DatabaseError) as exc:
            raise CommandError(str(exc) if isinstance(exc, ValueError) else
                f'Tag audit stopped safely ({type(exc).__name__}); no database writes performed') from None
        self.stdout.write(f"Unknown labels: {report['unknown_unique_tags']}; draft recovered works: "
                          f"{report['draft_impact']['recovered_zero_genre_works']}; NO mappings activated")
        for path in paths:
            self.stdout.write(str(path))
