from datetime import datetime, timezone
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError

from recommendations.quality_readonly import readonly_snapshot
from recommendations.tag_coverage import public_tag_snapshot
from recommendations.tag_expansion_review import build_expansion_review, expansion_markdown, load_proposal


class Command(BaseCommand):
    help = '公共标签扩充草案：PostgreSQL 只读试算，不启用规则、不读私人数据。'

    def add_arguments(self, parser):
        parser.add_argument('--proposal', type=Path, default=Path(settings.BASE_DIR) /
                            'evaluation/tag-expansion.20261020.proposal.json')
        parser.add_argument('--output-dir', type=Path)

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('This audit requires enforced read-only PostgreSQL; no SQLite fallback')
        directory = options['output_dir'] or Path(settings.BASE_DIR) / '.tools/tag-expansion' / datetime.now(
            timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        paths = [directory / name for name in ('review.json', 'review.md')]
        if any(path.exists() for path in paths):
            raise CommandError('Output already exists; choose a new directory')
        try:
            proposal = load_proposal(options['proposal'])
            with readonly_snapshot(timeout_ms=60000):
                scope, rows = public_tag_snapshot()
                report = build_expansion_review(rows, scope, proposal)
            contents = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                        expansion_markdown(report))
            directory.mkdir(parents=True, exist_ok=True)
            for path, content in zip(paths, contents):
                with path.open('x', encoding='utf-8', newline='\n') as output:
                    output.write(content)
        except (ValueError, OSError, DatabaseError) as exc:
            raise CommandError(str(exc) if isinstance(exc, ValueError) else
                               f'Audit stopped safely ({type(exc).__name__}); no database writes') from None
        self.stdout.write(json.dumps(report['simulation'], ensure_ascii=False))
        for path in paths:
            self.stdout.write(str(path))
