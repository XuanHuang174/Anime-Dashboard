"""Public Bangumi API enrichment, with a LOCAL SQLite cache, never Django SQL.

Only the fixed official API origin receives the optional bearer token. No
redirects, mirrors, image downloads, production DB connections or R2 writes.
"""

import json
import math
import os
import random
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .normalization import clean_id, clean_nsfw, text
from .source_pipeline import (
    SourceError, RetryableSourceError, artifact_writer, iter_jsonlines,
    make_http_client, normalize_subject, read_json_response,
)


API_ORIGIN = 'https://api.bgm.tv'


class AuthorizationError(SourceError):
    pass


class RequestLimiter:
    def __init__(self, rate=1, clock=time.monotonic, sleep=time.sleep):
        if not 0 < rate <= 5:
            raise SourceError('请求频率必须大于 0 且不超过 5 次/秒；建议 1')
        self.interval, self.clock, self.sleep = 1 / rate, clock, sleep
        self.next_request = 0

    def wait(self):
        delay = self.next_request - self.clock()
        if delay > 0:
            self.sleep(delay)
        self.next_request = self.clock() + self.interval


class ApiCache:
    """Only store normalized public metadata; never credentials or response headers."""
    def __init__(self, path, ttl_hours=168):
        self.path = Path(path).resolve()
        if not math.isfinite(ttl_hours) or ttl_hours <= 0:
            raise SourceError('缓存有效期必须为大于 0 的有限数值')
        self.ttl_seconds = ttl_hours * 3600
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS subject_cache '
                        '(bangumi_id INTEGER PRIMARY KEY, fetched_at REAL NOT NULL, payload TEXT NOT NULL)')
        self.db.commit()

    def get(self, identity):
        record = self.db.execute('SELECT fetched_at, payload FROM subject_cache WHERE bangumi_id=?', (identity,)).fetchone()
        if not record or time.time() - record[0] >= self.ttl_seconds:
            return None
        try:
            result = json.loads(record[1])
            return result if isinstance(result, dict) and result.get('bangumi_id') == identity else None
        except (ValueError, TypeError):
            return None

    def put(self, identity, payload):
        self.db.execute('INSERT INTO subject_cache VALUES (?, ?, ?) '
                        'ON CONFLICT(bangumi_id) DO UPDATE SET fetched_at=excluded.fetched_at, payload=excluded.payload',
                        (identity, time.time(), json.dumps(payload, ensure_ascii=False)))
        self.db.commit()

    def close(self):
        self.db.close()


def authorization_header(token):
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    if not token:
        return {}
    if any(character.isspace() for character in token) or not token.isascii():
        raise SourceError('BANGUMI_ACCESS_TOKEN 格式无效（不会输出其值）')
    return {'Authorization': f'Bearer {token}'}


def fetch_subject(client, identity, limiter, headers, retries=2, sleep=time.sleep):
    for attempt in range(retries + 1):
        limiter.wait()
        try:
            # follow_redirects=False is explicit even for an injected client.
            with client.stream('GET', f'{API_ORIGIN}/v0/subjects/{identity}',
                               headers=headers, follow_redirects=False,
                               timeout=httpx.Timeout(10, connect=5, pool=5)) as response:
                code = response.status_code
                if code in (401, 403):
                    raise AuthorizationError(f'官方 API HTTP {code}，已停止新的请求；请检查授权')
                if code == 404:
                    raise SourceError('missing_or_restricted')
                if code == 429 or 500 <= code < 600:
                    raw = response.headers.get('Retry-After', '')
                    retry_after = min(float(raw), 60) if raw.isdigit() else 0
                    raise RetryableSourceError(f'官方 API HTTP {code}', retry_after)
                data = read_json_response(response)
                if data.get('id') != identity or data.get('type') != 2:
                    raise SourceError('官方 API 返回的 ID 或作品类型不匹配')
                # Allowlist metadata through normalization: raw headers/token
                # or unrelated user information can never enter the cache.
                normalized = normalize_subject(data)
                cover = normalized.get('cover_url', '')
                if cover:
                    parsed = urlsplit(cover)
                    if (len(cover) > 2000 or parsed.scheme != 'https' or not parsed.hostname
                            or parsed.username or parsed.password
                            or parsed.path.endswith('/no_icon_subject.png')):
                        normalized.pop('cover_url', None)
                return normalized
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError):
            failure = RetryableSourceError('官方 API 请求超时或网络中断')
        except RetryableSourceError as exc:
            failure = exc
        if attempt == retries:
            raise failure
        sleep(max(failure.retry_after, min(2 ** attempt + random.uniform(0, 0.25), 8)))
    raise SourceError('API 重试配置无效')


def merge_metadata(row, api, refresh=False):
    result = dict(row)
    for key in ('title_zh', 'title_jp', 'score', 'tags', 'season', 'status_type', 'synopsis', 'cover_url'):
        value = api.get(key)
        # Never clear archive data/R2 covers with a missing API field. A zero
        # score means unrated, not a replacement for an existing real rating.
        if value and (refresh or not result.get(key) or result.get(key) == '未知'):
            result[key] = value
    # Conservative safety: adult-marked in either source stays adult-marked.
    existing, fetched = clean_nsfw(row.get('nsfw')), clean_nsfw(api.get('nsfw'))
    result['nsfw'] = True if existing is True or fetched is True else existing if existing is not None else fetched
    return result


def enrich_file(source, output, cache_path, report, *, limit=20, rate=1, retries=2,
                token='', ttl_hours=168, refresh_metadata=False, refresh_cache=False,
                overwrite=False, log=print, client=None, limiter=None, sleep=time.sleep,
                proxy=None, progress_path=None, stop_file=None):
    """limit counts candidate attempts (cache hits do NOT consume API budget).

Zero limit explicitly means full enrichment. All unprocessed/failed records
remain in the output. Successful cache hits allow resuming without refetching.
    """
    if limit < 0 or not 0 <= retries <= 5:
        raise SourceError('limit 不得为负数，retries 必须为 0-5')
    paths = [Path(path).resolve() for path in (source, output, cache_path, report)]
    extra_paths = [Path(path).resolve() for path in (progress_path, stop_file) if path is not None]
    if len(set(paths + extra_paths)) != len(paths + extra_paths):
        raise SourceError('输入、输出、缓存、报告、进度及停止标记必须是不同文件')
    for path in (paths[1], paths[3]):
        if path.exists() and not overwrite:
            raise SourceError(f'输出已存在：{path.name}；请换名或明确使用 --overwrite')
    headers = authorization_header(token)
    limiter = limiter or RequestLimiter(rate)
    cache = ApiCache(paths[2], ttl_hours)
    own_client = client is None
    client = client or make_http_client(proxy=proxy)
    stats, failures, stopped, seen = Counter(), [], False, set()
    outage, consecutive_failures, user_stopped = False, 0, False
    started_at, last_progress = time.time(), 0

    def publish_progress(phase='running', force=False):
        nonlocal last_progress
        if progress_path is None or (not force and time.monotonic() - last_progress < 20):
            return
        snapshot = {'phase': phase, 'started_at': started_at, 'updated_at': time.time(),
                    'updated_at_utc': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
                    'source': str(paths[0]), 'output': str(paths[1]),
                    'counts': dict(stats), 'rate': rate, 'explicit_proxy': bool(proxy),
                    'note': '只补齐本地元数据/封面URL，不写Neon、不下载图片、不上传R2'}
        try:
            with artifact_writer(progress_path, overwrite=True) as destination:
                json.dump(snapshot, destination, ensure_ascii=False, indent=2)
            last_progress = time.monotonic()
        except OSError:
            # Windows readers can briefly lock the old status file. A display
            # failure must not destroy the successful API cache or data task.
            last_progress = time.monotonic()
            log('进度文件暂时无法更新；补齐和逐条缓存仍继续')

    try:
        publish_progress(force=True)
        with paths[0].open(encoding='utf-8-sig') as incoming, artifact_writer(paths[1], overwrite) as destination:
            for line_number, row in iter_jsonlines(incoming):
                if not isinstance(row, dict):
                    raise SourceError(f'标准输入第 {line_number} 行无效；未发布不完整输出')
                try:
                    identity = clean_id(row.get('bangumi_id'))
                except ValueError as exc:
                    raise SourceError(f'标准输入第 {line_number} 行 ID 无效') from exc
                if identity in seen:
                    raise SourceError(f'标准输入第 {line_number} 行 ID 重复')
                seen.add(identity)
                stats['rows'] += 1
                if stop_file is not None and Path(stop_file).exists():
                    stopped = user_stopped = True
                api = None
                needs_api = refresh_metadata or any(not row.get(key) for key in ('cover_url', 'tags', 'season', 'synopsis'))
                if needs_api:
                    api = None if refresh_cache else cache.get(identity)
                    if api:
                        stats['cache_hits'] += 1
                    elif not stopped and (not limit or stats['attempted'] < limit):
                        stats['attempted'] += 1
                        try:
                            api = fetch_subject(client, identity, limiter, headers, retries, sleep)
                            cache.put(identity, api)
                            stats['succeeded'] += 1
                            consecutive_failures = 0
                        except (SourceError, ValueError) as exc:
                            reason = str(exc) if isinstance(exc, SourceError) else 'invalid_api_metadata'
                            stats['failed'] += 1
                            if len(failures) < 100:
                                failures.append({'bangumi_id': identity, 'reason': reason})
                            if isinstance(exc, AuthorizationError):
                                stopped = True
                            elif reason != 'missing_or_restricted':
                                consecutive_failures += 1
                                if consecutive_failures >= 5:
                                    stopped = outage = True
                            else:
                                consecutive_failures = 0
                            log(f'条目 {identity} 未补齐：{reason}；归档记录保留')
                        if stats['attempted'] % 20 == 0:
                            log(f'API 已尝试 {stats["attempted"]}，成功 {stats["succeeded"]}，失败 {stats["failed"]}')
                    else:
                        stats['deferred'] += 1
                if api:
                    row = merge_metadata(row, api, refresh_metadata)
                if not row.get('cover_url'):
                    stats['missing_cover_url'] += 1
                if clean_nsfw(row.get('nsfw')) is True:
                    stats['nsfw'] += 1
                destination.write(json.dumps(row, ensure_ascii=False) + '\n')
                publish_progress()
        summary = {'source': str(paths[0]), 'api_origin': API_ORIGIN,
                   'policy': {'include_nsfw': True, 'limit': limit, 'rate': rate,
                              'refresh_metadata': refresh_metadata, 'authenticated': bool(headers),
                              'explicit_proxy': bool(proxy)},
                   'counts': dict(stats), 'stopped_for_authorization': stopped and not outage and not user_stopped,
                   'stopped_for_outage': outage,
                   'stopped_by_user': user_stopped,
                   'failures_first_100': failures}
        with artifact_writer(paths[3], overwrite) as destination:
            json.dump(summary, destination, ensure_ascii=False, indent=2)
        phase = ('stopped_by_user' if user_stopped else 'stopped_for_outage' if outage else
                 'stopped_for_authorization' if stopped else 'completed_partial' if stats['missing_cover_url'] else 'completed')
        publish_progress(phase=phase, force=True)
        log(f'补齐完成（失败或未处理条目均保留）：{dict(stats)}')
        return summary
    except (SourceError, OSError, sqlite3.Error, httpx.HTTPError, UnicodeError, KeyboardInterrupt):
        publish_progress(phase='interrupted_or_error', force=True)
        raise
    finally:
        cache.close()
        if own_client:
            client.close()
