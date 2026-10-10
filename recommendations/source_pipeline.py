"""Official archive download/conversion: filesystem only, no Django or R2.

All generated files are under caller-selected local paths; no SQL is issued.
"""

import hashlib
import io
import json
import os
import re
import time
import uuid
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .normalization import clean_id, clean_nsfw, clean_score, clean_season, clean_tags, text


MANIFEST_URL = 'https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json'
USER_AGENT = 'XuanHuang174/AnimeDashboard-Recommendations/1.0 (+https://github.com/XuanHuang174/Anime-Dashboard)'
MAX_ARCHIVE_BYTES = 2 * 1024 ** 3
MAX_SUBJECT_BYTES = 4 * 1024 ** 3
MAX_LINE_CHARS = 2 * 1024 ** 2
# Based on bangumi/common/subject_platforms.yml (anime platforms only).
PLATFORMS = {0: '未知', 1: 'TV', 2: 'OVA', 3: '剧场版', 4: '短片', 5: 'WEB', 2006: '动态漫画'}


class SourceError(Exception):
    """Safe user-facing message; never contains bearer credentials."""


class RetryableSourceError(SourceError):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


@contextmanager
def artifact_writer(path, overwrite=False, binary=False):
    """Publish atomically; on failure preserve the previous completed artifact."""
    path = Path(path).resolve()
    if path.exists() and not overwrite:
        raise SourceError(f'输出文件已存在：{path.name}；请换名或明确使用 --overwrite')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('xb' if binary else 'x', **({} if binary else {'encoding': 'utf-8', 'newline': '\n'})) as output:
            yield output
            output.flush()
            os.fsync(output.fileno())
        # Hard-link publishes without overwriting a concurrent writer's result.
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        # Only remove this ONE explicitly created temporary file, never a tree.
        temporary.unlink(missing_ok=True)


def make_http_client():
    return httpx.Client(
        timeout=httpx.Timeout(15, connect=5, pool=5), trust_env=False,
        follow_redirects=False, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'},
    )


def checked_url(url, hosts, path_prefix=None):
    if not isinstance(url, str):
        raise SourceError('下载 URL 必须为字符串')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in hosts
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or (path_prefix and not parsed.path.startswith(path_prefix))):
            raise SourceError('拒绝非官方或不安全的下载地址')
    except ValueError as exc:
        raise SourceError('下载 URL 格式无效') from exc
    return url


def read_json_response(response, max_bytes=2 * 1024 ** 2):
    if response.status_code != 200:
        raise SourceError(f'数据源 HTTP {response.status_code}')
    body = bytearray()
    started = time.monotonic()
    for chunk in response.iter_bytes(4096):
        if time.monotonic() - started > 30 or len(body) + len(chunk) > max_bytes:
            raise SourceError('JSON 响应超过大小或时间限制')
        body.extend(chunk)
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise SourceError('数据源没有返回有效 JSON') from exc
    if not isinstance(result, dict):
        raise SourceError('数据源 JSON 必须为对象')
    return result


def download_archive(directory, log=print):
    """Resolve latest official metadata, verify exact size and SHA256, reuse cache."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with make_http_client() as client:
        with client.stream('GET', MANIFEST_URL) as response:
            manifest = read_json_response(response, max_bytes=64 * 1024)
        name = manifest.get('name', '')
        size = manifest.get('size')
        digest = manifest.get('digest', '')
        url = manifest.get('browser_download_url', '')
        if (not isinstance(name, str) or not isinstance(digest, str)
                or not re.fullmatch(r'dump-[A-Za-z0-9.\-]+\.zip', name)
                or isinstance(size, bool) or not isinstance(size, int)
                or not 0 < size <= MAX_ARCHIVE_BYTES
                or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest)):
            raise SourceError('官方归档元信息缺少有效名称、大小或 SHA256；拒绝未验证的下载')
        checked_url(url, {'github.com'}, '/bangumi/Archive/releases/download/archive/')
        destination = directory / name
        expected_hash = digest.split(':', 1)[1]
        if destination.exists():
            with destination.open('rb') as existing:
                actual_hash = hashlib.file_digest(existing, 'sha256').hexdigest()
            if destination.stat().st_size == size and actual_hash == expected_hash:
                log(f'复用已校验归档：{name}')
                return destination, manifest
            raise SourceError('本地同名归档校验失败，请换下载目录或手动移走该文件')
        log(f'下载官方归档：{name}，{size / 1024 ** 2:.1f} MiB')
        redirect_hosts = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
        for _ in range(5):
            checked_url(url, redirect_hosts)
            with client.stream('GET', url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                if response.status_code != 200:
                    raise SourceError(f'归档下载失败：HTTP {response.status_code}')
                received = 0
                hasher = hashlib.sha256()
                started = last_log = time.monotonic()
                with artifact_writer(destination, binary=True) as output:
                    for chunk in response.iter_bytes(64 * 1024):
                        received += len(chunk)
                        if received > size or time.monotonic() - started > 1800:
                            raise SourceError('归档超过声明大小或下载超过 30 分钟')
                        output.write(chunk)
                        hasher.update(chunk)
                        if time.monotonic() - last_log >= 10:
                            log(f'已下载 {received / 1024 ** 2:.1f}/{size / 1024 ** 2:.1f} MiB')
                            last_log = time.monotonic()
                    if received != size or hasher.hexdigest() != expected_hash:
                        raise SourceError('归档大小或 SHA256 不匹配，未发布不完整文件')
                with artifact_writer(destination.with_suffix('.manifest.json')) as output:
                    json.dump(manifest, output, ensure_ascii=False, indent=2)
                log('下载与 SHA256 校验完成')
                return destination, manifest
        raise SourceError('官方归档重定向次数过多')


def iter_jsonlines(source):
    """Bound a malformed huge line before allocating arbitrary memory."""
    line_number = 0
    while line := source.readline(MAX_LINE_CHARS + 1):
        line_number += 1
        if len(line) > MAX_LINE_CHARS:
            while line and not line.endswith('\n'):
                line = source.readline(64 * 1024)
            yield line_number, None
        elif line.strip():
            try:
                yield line_number, json.loads(line)
            except ValueError:
                yield line_number, None


@contextmanager
def subject_rows(path):
    """Read only the subject member; never extract archive paths to disk."""
    path = Path(path)
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            members = [member for member in archive.infolist() if member.filename == 'subject.jsonlines']
            if len(members) != 1 or members[0].file_size > MAX_SUBJECT_BYTES:
                raise SourceError('归档必须包含唯一 subject.jsonlines，且解压大小不得超过 4 GiB')
            with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig') as source:
                yield iter_jsonlines(source)
    else:
        with path.open('r', encoding='utf-8-sig') as source:
            yield iter_jsonlines(source)


def normalize_subject(row):
    identity = clean_id(row.get('id'))
    platform = row.get('platform')
    if isinstance(platform, int) and not isinstance(platform, bool):
        platform = PLATFORMS.get(platform, f'未知平台-{platform}')
    else:
        platform = {'Movie': '剧场版', 'MOVIE': '剧场版', 'TV短篇': 'TV短篇'}.get(text(platform), text(platform)) or '未知'
    raw_tags = row.get('tags', [])
    # Some archives store counts as a dictionary {tag_name: count}.
    if isinstance(raw_tags, dict):
        raw_tags = list(raw_tags)
    tags = clean_tags(raw_tags)
    meta_tags = clean_tags(row.get('meta_tags', []))
    title_jp = text(row.get('name'))
    result = {
        'bangumi_id': identity,
        'title_zh': text(row.get('name_cn')) or title_jp or f'未命名番剧 {identity}',
        'title_jp': title_jp,
        'score': clean_score(row.get('score', row.get('rating'))),
        'tags': clean_tags(','.join(filter(None, [tags, meta_tags]))),
        'season': clean_season(row.get('date', ''), row),
        'status_type': platform,
        'synopsis': text(row.get('summary')),
        'nsfw': clean_nsfw(row.get('nsfw')),
    }
    # Omit missing cover rather than clearing a previously hosted image on a
    # subsequent full archive import. API enrichment adds real image URLs.
    images = row.get('images') or {}
    cover = text(row.get('cover_url'))
    if isinstance(images, dict):
        cover = cover or next((text(images.get(key)) for key in ('large', 'common', 'medium') if text(images.get(key))), '')
    if cover:
        result['cover_url'] = cover
    if len(result['synopsis']) > 200_000:
        # Preserve the subject and all its other fields. Do not silently
        # truncate or erase an existing DB synopsis; raw text remains in ZIP.
        result.pop('synopsis')
        result['_source_warnings'] = ['oversized_synopsis_omitted']
    for key, maximum in {'title_zh': 500, 'title_jp': 500, 'tags': 4000, 'status_type': 32, 'season': 32}.items():
        if len(result[key]) > maximum:
            raise ValueError(f'{key} 超过字段长度限制')
    return result


def convert_archive(source, output, sample, report, *, sample_size=20, exclude_nsfw=False, limit=0, overwrite=False, log=print):
    if sample_size < 1 or limit < 0:
        raise SourceError('sample_size 必须大于 0，limit 不得为负数')
    paths = [Path(path).resolve() for path in (source, output, sample, report)]
    if len(set(paths)) != len(paths):
        raise SourceError('源文件、全量输出、样本和报告必须使用不同路径')
    for path in paths[1:]:
        if path.exists() and not overwrite:
            raise SourceError(f'输出已存在：{path.name}；请换名或明确使用 --overwrite')
    stats = Counter()
    platforms = Counter()
    seen = set()
    examples = []
    failures = []
    warnings = []
    with subject_rows(paths[0]) as rows, artifact_writer(paths[1], overwrite) as destination:
        for line_number, row in rows:
            stats['scanned'] += 1
            if not isinstance(row, dict):
                stats['invalid'] += 1
                if len(failures) < 20:
                    failures.append({'line': line_number, 'reason': 'invalid_json_object'})
                continue
            if row.get('type') != 2:
                stats['non_anime'] += 1
                continue
            try:
                normalized = normalize_subject(row)
            except ValueError as exc:
                stats['invalid'] += 1
                if len(failures) < 20:
                    failures.append({'line': line_number, 'reason': str(exc)})
                continue
            if normalized['bangumi_id'] in seen:
                stats['duplicate_ids'] += 1
                continue
            seen.add(normalized['bangumi_id'])
            if exclude_nsfw and normalized['nsfw'] is True:
                stats['excluded_nsfw'] += 1
                continue
            stats['exported'] += 1
            if normalized.get('_source_warnings'):
                stats['metadata_warnings'] += 1
                if len(warnings) < 100:
                    warnings.append({'bangumi_id': normalized['bangumi_id'], 'warnings': normalized['_source_warnings']})
            flag = normalized['nsfw']
            stats['nsfw' if flag is True else 'sfw' if flag is False else 'nsfw_unknown'] += 1
            for field in ('cover_url', 'season', 'tags', 'synopsis'):
                if not normalized.get(field):
                    stats[f'missing_{field}'] += 1
            if normalized['score'] == 0:
                stats['unrated'] += 1
            platforms[normalized['status_type']] += 1
            destination.write(json.dumps(normalized, ensure_ascii=False) + '\n')
            if len(examples) < sample_size:
                examples.append(normalized)
            elif flag is True and not any(item['nsfw'] is True for item in examples):
                # Sample includes one adult-marked record to exercise retention.
                examples[-1] = normalized
            if stats['exported'] % 5000 == 0:
                log(f'已转换 {stats["exported"]} 条动画')
            if limit and stats['exported'] >= limit:
                break
    with artifact_writer(paths[2], overwrite) as destination:
        for row in examples:
            destination.write(json.dumps(row, ensure_ascii=False) + '\n')
    summary = {
        'source': str(paths[0]), 'policy': {'include_nsfw': not exclude_nsfw, 'type': 2, 'limit': limit},
        'counts': dict(stats), 'platforms': dict(platforms), 'sample_count': len(examples),
        'bad_rows_first_20': failures,
        'warnings_first_100': warnings,
    }
    with artifact_writer(paths[3], overwrite) as destination:
        json.dump(summary, destination, ensure_ascii=False, indent=2)
    log(f'转换完成：{dict(stats)}')
    return summary
