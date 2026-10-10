"""Run with python -m recommendations.prepare_data; NEVER loads Django/.env."""

import argparse
import os
import sqlite3
import sys
import traceback
import zipfile
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpx

from .api_enrichment import enrich_file
from .source_pipeline import SourceError, convert_archive, download_archive


def resolve_proxy(explicit=None, system=False):
    """Opt-in transport proxy only; never change the official API URL.

Does not read .env or Django. urllib supports Windows manual Internet Settings
and HTTP(S)_PROXY; PAC/SOCKS are intentionally not enabled implicitly.
    """
    if explicit and system:
        raise SourceError('--proxy 与 --system-proxy 不可同时使用')
    proxy = explicit
    if system:
        proxies = getproxies()
        proxy = proxies.get('https') or proxies.get('all') or proxies.get('http')
        if not proxy:
            raise SourceError('未发现启用的系统 HTTP(S) 代理，请明确传 --proxy')
    if proxy:
        try:
            parsed = urlsplit(proxy)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in ('', '/') or not parsed.port):
                raise SourceError('代理必须为带端口的 HTTP(S) 地址，不能包含凭证或路径')
        except ValueError as exc:
            raise SourceError('代理地址格式无效（不会输出其值）') from exc
    return proxy


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bangumi 官方数据准备（独立本地脚本，不连接 Neon、不上传 R2）')
    commands = parser.add_subparsers(dest='command', required=True)
    download = commands.add_parser('download', help='获取官方最新 ZIP 并校验 SHA256')
    download.add_argument('--directory', default='data/recommendations/archive')
    convert = commands.add_parser('convert', help='仅保留动画 type=2，默认保留成人条目')
    convert.add_argument('--input', required=True)
    convert.add_argument('--output', default='data/recommendations/anime.jsonl')
    convert.add_argument('--sample', default='data/recommendations/anime_sample.jsonl')
    convert.add_argument('--report', default='data/recommendations/conversion_report.json')
    convert.add_argument('--sample-size', type=int, default=20)
    convert.add_argument('--limit', type=int, default=0, help='转换条数上限，0 为全部')
    convert.add_argument('--exclude-nsfw', action='store_true', help='仅明确指定时排除已标记成人条目')
    convert.add_argument('--overwrite', action='store_true')
    enrich = commands.add_parser('enrich', help='官方 API 补齐封面，默认最多尝试 20 个条目')
    enrich.add_argument('--input', required=True)
    enrich.add_argument('--output', default='data/recommendations/anime_enriched.jsonl')
    enrich.add_argument('--cache', default='data/recommendations/api_cache.sqlite3')
    enrich.add_argument('--report', default='data/recommendations/enrichment_report.json')
    enrich.add_argument('--limit', type=int, default=20, help='最多尝试多少个新条目（不含重试），0 明确表示全量；缓存命中不计数')
    enrich.add_argument('--rate-limit', type=float, default=1)
    enrich.add_argument('--retries', type=int, default=2)
    enrich.add_argument('--cache-ttl-hours', type=float, default=168)
    enrich.add_argument('--refresh-metadata', action='store_true', help='同时更新有效评分/标签等；缺失值不清空旧字段')
    enrich.add_argument('--refresh-cache', action='store_true')
    enrich.add_argument('--overwrite', action='store_true')
    transport = enrich.add_mutually_exclusive_group()
    transport.add_argument('--proxy', help='明确使用可信传输代理，如 http://127.0.0.1:7890；不允许凭证')
    transport.add_argument('--system-proxy', action='store_true', help='明确启用 Windows 手工系统代理/HTTP(S)_PROXY；不支持 PAC/SOCKS')
    enrich.add_argument('--progress', help='本地实时 JSON 进度文件（约每 20 秒更新）')
    enrich.add_argument('--stop-file', help='此标记文件出现后停止新请求，保留完整输出及已完成缓存')
    args = parser.parse_args(argv)
    log = lambda message: print(message, flush=True)
    try:
        if args.command == 'download':
            path, _ = download_archive(args.directory, log=log)
            log(f'归档路径：{path}')
        elif args.command == 'convert':
            convert_archive(args.input, args.output, args.sample, args.report,
                            sample_size=args.sample_size, limit=args.limit,
                            exclude_nsfw=args.exclude_nsfw, overwrite=args.overwrite, log=log)
        else:
            summary = enrich_file(args.input, args.output, args.cache, args.report,
                                  limit=args.limit, rate=args.rate_limit, retries=args.retries,
                                  ttl_hours=args.cache_ttl_hours,
                                  refresh_metadata=args.refresh_metadata, refresh_cache=args.refresh_cache,
                                  overwrite=args.overwrite,
                                  proxy=resolve_proxy(args.proxy, args.system_proxy),
                                  progress_path=args.progress, stop_file=args.stop_file,
                                  token=os.environ.get('BANGUMI_ACCESS_TOKEN', ''), log=log)
            if summary['counts'].get('failed') or summary['stopped_by_user']:
                log('有条目未补齐，请查看失败报告；所有条目已保留，可用成功缓存重新运行')
                return 2
        return 0
    except KeyboardInterrupt:
        print('任务已中断，成功缓存保留；重新运行可恢复。', file=sys.stderr, flush=True)
        return 130
    except (SourceError, OSError, sqlite3.Error, zipfile.BadZipFile, httpx.HTTPError, UnicodeError) as exc:
        # HTTP client exceptions can contain full URLs; never print raw
        # request objects, credentials or signed download redirect URLs.
        message = str(exc) if isinstance(exc, SourceError) else f'{type(exc).__name__}（网络或本地文件失败，请安全重试）'
        if isinstance(exc, UnicodeError):
            frames = traceback.extract_tb(exc.__traceback__)
            if frames:
                from pathlib import Path
                frame = frames[-1]
                message += f'；位置 {Path(frame.filename).name}:{frame.lineno}，编码 {getattr(exc, "encoding", "unknown")}'
        print(f'失败：{message}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
