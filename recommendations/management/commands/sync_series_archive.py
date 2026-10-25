"""Explicit local-archive sync; preview by default, never loads remote URLs."""
import json
from pathlib import Path
import zipfile

from django.core.management.base import BaseCommand, CommandError

from recommendations.series_source import prepare_archive, seed_ids
from recommendations.series_store import publish_archive, record_failed_attempt
from recommendations.source_pipeline import SourceError


class Command(BaseCommand):
    help = '预检官方本地归档的指定系列来源；仅 --apply 才写入独立系列表。'

    def add_arguments(self, parser):
        parser.add_argument('--input', required=True, type=Path)
        parser.add_argument('--expected-sha256', required=True)
        parser.add_argument('--ids', required=True, help='1–100 个明确的 Bangumi ID，以逗号分隔')
        parser.add_argument('--timeout-seconds', type=int, default=120)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            seeds = seed_ids(options['ids'].split(','))
        except (ValueError, SourceError) as exc:
            raise CommandError(str(exc)) from None
        try:
            prepared = prepare_archive(options['input'], options['expected_sha256'], seeds,
                                       timeout_seconds=options['timeout_seconds'])
        except (OSError, ValueError, KeyError, SourceError, zipfile.BadZipFile, RuntimeError) as exc:
            if options['apply']:
                record_failed_attempt(seeds)
            # No filesystem paths, response bodies, credentials or raw metadata.
            raise CommandError(f'系列归档预检失败（{type(exc).__name__}）；当前发布版本保持原样。') from None
        result = {'mode': 'apply' if options['apply'] else 'preview', 'snapshot_version': prepared['snapshot_version'],
            'seeds': len(seeds), 'nodes': len(prepared['nodes']), 'assertions': len(prepared['assertions']),
            'partial': prepared['partial'], 'fingerprint': prepared['fingerprint'], 'diagnostics': prepared['diagnostics']}
        if options['apply']:
            result['publication'] = publish_archive(prepared)
        self.stdout.write(json.dumps(result, ensure_ascii=False))
