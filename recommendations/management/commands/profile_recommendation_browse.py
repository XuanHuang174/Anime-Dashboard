"""Exact authorized natural data only. No tests/writes on the real database."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter, process_time
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import override_settings

from recommendations import browsing
from recommendations.comparison import QueryCounter
from recommendations.quality_readonly import readonly_snapshot
from recommendations.serializers import RecommendationQuerySerializer
from recommendations.services import profile_contract
from recommendations.version_control import release_descriptor


def measure(user, params):
    query = RecommendationQuerySerializer(data={'browse': True, **params})
    query.is_valid(raise_exception=True)
    metrics, counter = {}, QueryCounter()
    wall, cpu = perf_counter(), process_time()
    with connection.execute_wrapper(counter):
        page = browsing.browse_page(user, query.validated_data, query.initial_data, metrics=metrics)
    metrics.update(wall_ms=round((perf_counter() - wall) * 1000, 3), cpu_ms=round((process_time() - cpu) * 1000, 3),
                   sql_count=counter.count, sql_execute_ms=round(counter.seconds * 1000, 3))
    rows = page['results']
    combos = Counter(tuple(sorted(row['tags'])) for row in rows)
    metrics.update(result_count=page['count'], maximum_batches=(page['count'] + 23) // 24,
                   top_genre_combination_count=len(combos), largest_top_combination=max(combos.values(), default=0),
                   evidence_rows=sum(bool(row['evidence']) for row in rows))
    # Only a digest of personalized public results leaves this helper, never
    # source titles/IDs, personal scores, private tags or full response/cursors.
    digest = hashlib.sha256(json.dumps(page['results'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return page, metrics, digest


class Command(BaseCommand):
    help = '增强9：最多两个明确授权自然账号的强制只读性能抽查；不修改评分，不批准V2。'

    def add_arguments(self, parser):
        parser.add_argument('--user-email', action='append', default=[])
        parser.add_argument('--confirm-authorized-samples', action='store_true')
        parser.add_argument('--output-dir', type=Path)

    def handle(self, *args, **options):
        emails = options['user_email']
        if not options['confirm_authorized_samples'] or not 1 <= len(emails) <= 2 or len(set(emails)) != len(emails):
            raise CommandError('Require 1–2 exact authorized emails, explicit confirmation; no user discovery.')
        if connection.vendor != 'postgresql' or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1':
            raise CommandError('PostgreSQL enforced read-only and default V1 required; no SQLite fallback.')
        directory = options['output_dir'] or Path(settings.BASE_DIR) / '.tools/browse-performance' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if not directory.resolve().is_relative_to((Path(settings.BASE_DIR) / '.tools').resolve()) or directory.exists():
            raise CommandError('Use a new private .tools directory; overwrite refused.')
        with readonly_snapshot():
            users = list(get_user_model().objects.filter(email__in=emails, is_active=True).only('pk', 'email'))
            catalog = browsing.catalog_revision()
        if len(users) != len(emails) or {user.email for user in users} != set(emails):
            raise CommandError('Exact account missing/ambiguous/inactive; scope not widened.')
        report = {'contract_version': 'recommendations.browse.performance.v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                  'release_fingerprint': release_descriptor()['fingerprint'], 'catalog_revision': catalog,
                  'execution_context': 'local_client_to_configured_postgresql', 'online_default': 'content.v1',
                  'private_writes': False, 'external_http': False, 'samples': [],
                  'limitations': ['Not deployed-service SQL/CPU or HTTP latency; not a load test.',
                                 'SQL execute timing includes driver/network, not all fetch or server CPU.',
                                 'Warm PUBLIC feature cache only; no private result cache or all-user scans.']}
        users.sort(key=lambda user: emails.index(user.email))
        cases = [('uncached_balanced', 'all', 'balanced', False), ('cache_fill_balanced', 'all', 'balanced', True),
                 ('warm_balanced', 'all', 'balanced', True), ('warm_close', 'all', 'close', True),
                 ('warm_open', 'all', 'open', True), ('warm_familiar', 'familiar', 'balanced', True),
                 ('warm_explore', 'explore', 'balanced', True)]
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP permitted')):
            for number, user in enumerate(users, 1):
                with readonly_snapshot():
                    profile = profile_contract(user)
                before = hashlib.sha256(json.dumps(profile, sort_keys=True, default=str).encode()).hexdigest()
                sample = {'sample': number, 'usable_sample_count': profile['usable_sample_count'],
                          'cold_start': profile['cold_start'], 'cases': []}
                control = None
                for label, category, style, cached in cases:
                    self.stdout.write(f'sample_{number:03d}: {label} (read-only)'); self.stdout.flush()
                    with override_settings(RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=cached):
                        page, metrics, digest = measure(user, {'category': category, 'style': style})
                    if label == 'uncached_balanced':
                        control = digest
                    if label in ('cache_fill_balanced', 'warm_balanced') and digest != control:
                        raise CommandError('Natural inputs/results changed; comparison stopped without writes.')
                    sample['cases'].append({'label': label, **metrics})
                    if label == 'warm_balanced':
                        for kind in ('next_page_cursor', 'next_batch_cursor'):
                            if page[kind]:
                                _, continued, _ = measure(user, {'cursor': page[kind]})
                                sample['cases'].append({'label': kind, **continued})
                with readonly_snapshot():
                    after = hashlib.sha256(json.dumps(profile_contract(user), sort_keys=True, default=str).encode()).hexdigest()
                if before != after:
                    raise CommandError('Profile changed during diagnostic; no complete report issued.')
                report['samples'].append(sample)
        with readonly_snapshot():
            if catalog != browsing.catalog_revision() or report['release_fingerprint'] != release_descriptor()['fingerprint']:
                raise CommandError('Catalog/code changed during diagnostic; comparison must be repeated.')
        directory.mkdir(parents=True)
        with (directory / 'performance.json').open('x', encoding='utf-8') as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        lines = ['# 增强9 自然账号只读性能抽查', '', '执行位置：本机客户端连接配置的 PostgreSQL；不是线上服务端耗时。', '',
                 '| 样本 | 场景 | wall ms | CPU ms | SQL次数 | SQL execute ms | 公共缓存 | 本轮作品 | 最大批次 |',
                 '| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |']
        for sample in report['samples']:
            for case in sample['cases']:
                lines.append('| ' + ' | '.join(str(value) for value in (sample['sample'], case['label'], case['wall_ms'], case['cpu_ms'],
                    case['sql_count'], case['sql_execute_ms'], case['public_cache'], case['result_count'], case['maximum_batches'])) + ' |')
        (directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.stdout.write(f'Complete: {directory}; V1 unchanged; no user writes, no V2 approval.')
