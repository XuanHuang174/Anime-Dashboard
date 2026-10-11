import json
from collections import Counter
from itertools import islice
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, transaction

from recommendations.ingestion import normalize_row, upsert_anime


def read_rows(path):
    """JSONL streams in constant memory; ordinary JSON is size-bounded."""
    with path.open('r', encoding='utf-8-sig') as source:
        if path.suffix.lower() in ('.jsonl', '.ndjson'):
            line_number = 0
            while line := source.readline(2 * 1024 * 1024 + 1):
                line_number += 1
                if len(line) > 2 * 1024 * 1024:
                    while not line.endswith('\n'):
                        line = source.readline(64 * 1024)
                        if not line:
                            break
                    yield ValueError(f'JSONL 第 {line_number} 行超过 2 Mi 字符限制')
                    continue
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        # A malformed JSONL row must not discard the entire file.
                        yield ValueError(f'JSONL 第 {line_number} 行不是有效 JSON')
            return
        if path.stat().st_size > 64 * 1024 * 1024:
            raise CommandError('JSON 超过 64 MiB，请转换成逐行 JSONL 后导入。')
        payload = json.load(source)
        if isinstance(payload, dict):
            payload = next((payload[key] for key in ('data', 'items', 'results', 'anime') if isinstance(payload.get(key), list)), None)
        if not isinstance(payload, list):
            raise CommandError('需要 JSON 数组、包含 data/items/results/anime 数组的对象，或 JSONL 文件。')
        yield from payload


class Command(BaseCommand):
    help = '将本地番剧 JSON/JSONL 幂等导入独立推荐库（不会删除旧数据或修改私人手账）。'

    def add_arguments(self, parser):
        parser.add_argument('json_path', type=Path)
        parser.add_argument('--batch-size', type=int, default=500)
        parser.add_argument('--id-field', help='仅在确定该字段确实是 Bangumi ID 时指定，例如 --id-field id')
        parser.add_argument('--dry-run', action='store_true', help='只清洗和校验，不写入数据库')
        parser.add_argument('--skip-invalid', action='store_true', help='有跳过项时仍返回退出码 0；默认非零提醒')
        parser.add_argument('--stop-file', type=Path, help='检测到该文件时在下一批事务开始前安全停止')

    def handle(self, *args, **options):
        path = options['json_path'].resolve()
        size = options['batch_size']
        if not 1 <= size <= 2000:
            raise CommandError('batch-size 必须在 1–2000 之间。')
        if not path.is_file():
            raise CommandError('找不到指定的 JSON 文件。')
        stats = Counter()
        seen = set()
        try:
            rows = iter(read_rows(path))
            batch_number = 0
            while batch := list(islice(rows, size)):
                if options.get('stop_file') and options['stop_file'].exists():
                    raise CommandError('检测到停止文件；已提交批次保留，未开始下一批，可幂等重跑。')
                batch_number += 1
                # A savepoint per row inside each bounded transaction permits
                # bad records to fail without rolling back their valid peers.
                with transaction.atomic():
                    for row in batch:
                        stats['processed'] += 1
                        try:
                            if isinstance(row, ValueError):
                                raise row
                            identity, values = normalize_row(row, options['id_field'])
                            if identity in seen:
                                stats['duplicate_ids'] += 1
                            seen.add(identity)
                            if options['dry_run']:
                                stats['valid'] += 1
                            else:
                                created = upsert_anime(identity, values)
                                stats['created' if created else 'updated'] += 1
                        except (ValueError, ValidationError) as exc:
                            stats['skipped'] += 1
                            if stats['skipped'] <= 20:
                                # Validation messages may contain image URLs;
                                # do not emit source URLs or query credentials.
                                reason = str(exc) if isinstance(exc, ValueError) else '字段长度或 URL 校验失败'
                                self.stderr.write(f'第 {stats["processed"]} 条跳过：{reason}')
                self.stdout.write(f'批次 {batch_number} 完成：{dict(stats)}')
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CommandError(f'读取数据文件失败：{type(exc).__name__}；已提交的批次可以安全重跑。') from exc
        except DatabaseError as exc:
            # Infrastructure errors are not data errors; stop rather than
            # skipping thousands of rows when the database is unavailable.
            raise CommandError(f'数据库异常：{type(exc).__name__}；当前批次回滚，之前批次保留。') from exc
        self.stdout.write(self.style.SUCCESS(f'{"校验" if options["dry_run"] else "导入"}结束：{dict(stats)}'))
        if stats['skipped'] and not options['skip_invalid']:
            raise CommandError('部分记录被跳过，请修正后重跑；有效记录已保留。')
