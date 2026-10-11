"""Explicit, guarded production job: import -> verify -> non-adult R2 sync.

Never invoked from a web request. Runtime files belong in ignored local data.
No migrations, deletes, private-journal writes, or credential logging.
"""

import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from recommendations.covers import make_r2_client, sync_covers
from recommendations.ingestion import is_hosted_cover, normalize_row
from recommendations.management.commands.import_anime import read_rows
from recommendations.models import RecommendationAnime
from recommendations.prepare_data import resolve_proxy
from recommendations.source_pipeline import artifact_writer


def load_verified_catalog(path, expected_sha256, expected_rows):
    with path.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != expected_sha256:
            raise CommandError('数据文件 SHA256 不匹配，拒绝写入。')
    catalog = {}
    for row in read_rows(path):
        identity, values = normalize_row(row)
        if identity in catalog or values.get('nsfw') not in (True, False) or values.get('nsfw') is None:
            raise CommandError('ID 重复或成人分类未知，拒绝写入。')
        catalog[identity] = values
    if len(catalog) != expected_rows or not catalog:
        raise CommandError('验收记录数不匹配，拒绝写入。')
    return catalog


def verify_database(catalog, *, complete):
    fields = ['bangumi_id', 'title_zh', 'title_jp', 'score', 'tags', 'season',
              'status_type', 'synopsis', 'nsfw', 'cover_url', 'source_cover_url']
    rows = list(RecommendationAnime.objects.values(*fields))
    identities = {row['bangumi_id'] for row in rows}
    if len(rows) != len(identities) or not identities <= catalog.keys():
        raise CommandError('推荐库存在意外 ID 或重复数据，停止任务。')
    if complete and identities != catalog.keys():
        raise CommandError('全量入库 ID 集合不一致，禁止转存。')
    hosted = {}
    for row in rows:
        identity = row['bangumi_id']
        expected = catalog[identity]
        if complete:
            for field, value in expected.items():
                if field == 'cover_url':
                    continue
                if field == 'title_zh' and not value:
                    value = expected.get('title_jp') or f'未命名番剧 {identity}'
                if row[field] != value:
                    raise CommandError(f'Bangumi {identity} 的 {field} 与验收文件不一致。')
            if row['source_cover_url'] != expected.get('cover_url', ''):
                raise CommandError(f'Bangumi {identity} 的图片来源不一致。')
        if row['nsfw'] is not False and row['cover_url']:
            raise CommandError('成人/未知作品存在公开封面，停止任务。')
        if is_hosted_cover(row['cover_url']):
            parsed = urlsplit(row['cover_url'])
            if parsed.path != f'/covers/{identity}.jpg' or expected.get('nsfw') is not False:
                raise CommandError('托管封面键或成人分类不一致，停止任务。')
            if row['source_cover_url'] != expected.get('cover_url'):
                raise CommandError('已托管封面的来源改变，需重新验收。')
            hosted[f'covers/{identity}.jpg'] = row['cover_url']
        elif complete:
            expected_cover = '' if expected['nsfw'] else expected.get('cover_url', '')
            if row['cover_url'] != expected_cover:
                raise CommandError('公开封面链接与验收数据不一致。')
    return {'rows': len(rows), 'adult': sum(row['nsfw'] is True for row in rows),
            'hosted': len(hosted), 'external': sum(bool(row['cover_url']) and not is_hosted_cover(row['cover_url']) for row in rows)}, hosted


class ProgressStream:
    def __init__(self, output, publish):
        self.output, self.publish = output, publish

    def write(self, message):
        if message.strip():
            payload = {'message': message.strip()}
            # The counters are display-only; stage gating uses database checks.
            if '：{' in message:
                try:
                    counters = ast.literal_eval(message[message.index('{'):].strip())
                    if isinstance(counters, dict):
                        payload['counters'] = counters
                except (ValueError, SyntaxError):
                    pass
            self.publish(**payload)
            self.output.write(message.rstrip('\n'))

    def flush(self):
        self.output.flush()


class Command(BaseCommand):
    help = '显式确认后运行生产推荐库全量入库、逐字段验收及非成人封面托管；可幂等续跑。'

    def add_arguments(self, parser):
        parser.add_argument('json_path', type=Path)
        parser.add_argument('--expected-sha256', required=True)
        parser.add_argument('--expected-rows', type=int, required=True)
        parser.add_argument('--run-dir', type=Path, required=True, help='必须是全新运行目录')
        parser.add_argument('--confirm-production', action='store_true')
        parser.add_argument('--system-proxy', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_production']:
            raise CommandError('必须明确传入 --confirm-production；本命令会写正式库和 R2。')
        database = urlsplit(settings.DATABASE_URL)
        endpoint = urlsplit(settings.AWS_S3_ENDPOINT_URL)
        if (connection.vendor != 'postgresql' or not (database.hostname or '').endswith('.neon.tech')
                or connection.settings_dict['HOST'] != database.hostname
                or not settings.R2_STORAGE_ENABLED
                or settings.AWS_STORAGE_BUCKET_NAME != 'xh-anime-media'
                or settings.AWS_S3_CUSTOM_DOMAIN != 'media.xh-anime.com'
                or endpoint.scheme != 'https' or not (endpoint.hostname or '').endswith('.r2.cloudflarestorage.com')
                or endpoint.username or endpoint.password or endpoint.query):
            raise CommandError('数据库或 R2 目标与已授权配置不一致，拒绝写入。')
        path = options['json_path'].resolve()
        run_dir = options['run_dir'].resolve()
        # Prevent artifacts from replacing source files or a prior job's report.
        if run_dir.exists() or run_dir == path or run_dir in path.parents:
            raise CommandError('运行目录必须全新且不能包含源文件。')
        run_dir.mkdir(parents=True)
        lock = run_dir.parent / 'production_pipeline.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise CommandError('存在生产任务锁；先确认旧任务结束，禁止重复启动。') from exc
        with os.fdopen(descriptor, 'w') as output:
            output.write(str(os.getpid()))
        stop_file = run_dir / 'STOP'
        state = {'pid': os.getpid(), 'started_at': timezone.now().isoformat(),
                 'expected_rows': options['expected_rows'], 'phase': 'preflight'}

        def publish(**updates):
            state.update(updates)
            state['updated_at'] = timezone.now().isoformat()
            with artifact_writer(run_dir / 'progress.json', overwrite=True) as output:
                json.dump(state, output, ensure_ascii=False, indent=2)

        stream = ProgressStream(self.stdout, publish)
        try:
            publish(message='校验数据指纹、目标与既有托管对象')
            catalog = load_verified_catalog(path, options['expected_sha256'], options['expected_rows'])
            before, hosted = verify_database(catalog, complete=False)
            proxy = resolve_proxy(None, options['system_proxy'])
            client = make_r2_client(2, proxy=proxy)
            try:
                keys = set()
                for page in client.get_paginator('list_objects_v2').paginate(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME, Prefix='covers/'):
                    keys.update(item['Key'] for item in page.get('Contents', []))
                if keys != hosted.keys():
                    raise CommandError('R2 covers/ 对象集合与已验收托管记录不一致，禁止覆盖。')
            finally:
                client.close()
            publish(phase='importing', before=before, message='开始分批幂等入库')
            call_command('import_anime', str(path), batch_size=100, stop_file=stop_file,
                         stdout=stream, stderr=stream)
            publish(phase='verifying_import', message='逐字段核验全量记录及成人封面隔离')
            after, verified_hosted = verify_database(catalog, complete=True)
            if any(verified_hosted.get(key) != url for key, url in hosted.items()):
                raise CommandError('原已托管封面未保持一致，停止转存。')
            publish(phase='transferring', import_verified=after, counters={},
                    message='仅转存明确非成人封面；2 worker，1 请求/秒')
            stats = asyncio.run(sync_covers(
                concurrency=2, rate_limit=1, retries=3, batch_size=20, limit=0,
                dry_run=False, log=stream.write, proxy=proxy,
                should_stop=stop_file.exists,
            ))
            after, _ = verify_database(catalog, complete=True)
            phase = ('stopped' if stats['stopped'] else 'failed' if stats['circuit_open'] or stats['aborted']
                     else 'completed_partial' if stats['failed'] or after['external'] else 'completed')
            publish(phase=phase, counters=dict(stats), final=after,
                    message='任务结束；成功批次与已托管封面保留')
            self.stdout.write(f'生产任务状态：{phase}；{after}')
            if phase in ('stopped', 'failed'):
                raise CommandError('任务安全停止，查看进度报告后再幂等续跑。')
        except Exception as exc:
            phase = 'stopped' if stop_file.exists() else 'failed'
            publish(phase=phase, error_type=type(exc).__name__, message='任务停止；未清理或删除业务数据')
            # SDK/connection errors can contain credentials: never emit raw text.
            if isinstance(exc, CommandError):
                raise
            raise CommandError(f'生产任务停止：{type(exc).__name__}，详见进度文件。') from None
        finally:
            lock.unlink(missing_ok=True)  # This one owned runtime lock only.
