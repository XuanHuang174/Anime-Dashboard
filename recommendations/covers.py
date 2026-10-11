"""Bounded async downloads and transactionally serialized R2 publication.

Only the background command calls this module. No downloads/uploads take place
in HTTP requests, imports, AppConfig.ready(), or database migrations.
"""

import asyncio
import hashlib
import io
import ipaddress
import random
import socket
from collections import Counter
from urllib.parse import urljoin, urlsplit

import boto3
import httpx
from asgiref.sync import sync_to_async
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from django.conf import settings
from django.db import DatabaseError, close_old_connections, transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from .ingestion import is_hosted_cover
from .models import RecommendationAnime


MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_OUTPUT_BYTES = 3_000_000


class CoverError(Exception):
    """Permanent, safe-to-log failure without the source URL or credentials."""


class RetryableCoverError(CoverError):
    pass


class RateLimiter:
    """Minimum interval between requests, shared by all workers in this run."""

    def __init__(self, requests_per_second):
        self.interval = 1 / requests_per_second
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def wait(self):
        async with self.lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, self.next_request - loop.time()))
            self.next_request = loop.time() + self.interval


async def validate_download_url(url, allowed_hosts):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != 'https' or not host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or host.lower() not in allowed_hosts):
            raise CoverError('封面地址不是白名单 HTTPS 地址')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise CoverError('不允许 IP 字面量地址')
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), 5,
        )
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            raise CoverError('封面域名解析到非公网地址')
    except (ValueError, UnicodeError) as exc:
        raise CoverError('封面 URL 格式错误') from exc
    except (OSError, asyncio.TimeoutError) as exc:
        raise RetryableCoverError('封面 DNS 解析失败或超时') from exc


async def download_image(client, url, allowed_hosts, limiter):
    for redirect_count in range(4):
        await validate_download_url(url, allowed_hosts)
        await limiter.wait()
        async with client.stream('GET', url) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('location')
                if not location or redirect_count == 3:
                    raise CoverError('重定向次数超限或缺少地址')
                url = urljoin(url, location)
                continue  # Every redirect target is independently validated.
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise RetryableCoverError(f'图片源暂不可用（HTTP {response.status_code}）')
            if response.status_code != 200:
                raise CoverError(f'图片源拒绝请求（HTTP {response.status_code}）')
            # Disallow compressed HTTP envelopes: the image itself is already
            # compressed, and unbounded decompression would bypass size checks.
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise CoverError('不接受压缩 HTTP 响应')
            mime = response.headers.get('content-type', '').split(';', 1)[0].lower()
            if mime and not (mime.startswith('image/') or mime == 'application/octet-stream'):
                raise CoverError('响应不是图片')
            length = response.headers.get('content-length')
            if length is not None:
                try:
                    if not 0 <= int(length) <= MAX_DOWNLOAD_BYTES:
                        raise CoverError('源图片超过 8 MiB 安全上限')
                except ValueError as exc:
                    raise CoverError('无效的 Content-Length') from exc
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(data) + len(chunk) > MAX_DOWNLOAD_BYTES:
                    raise CoverError('源图片超过 8 MiB 安全上限')
                data.extend(chunk)
            if not data:
                raise CoverError('图片响应为空')
            return bytes(data)
    raise CoverError('重定向次数超限')


def encode_jpeg(data):
    """Decode safely, orient, downsize, flatten alpha, and really encode JPEG."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'JPEG', 'PNG', 'WEBP', 'GIF'}:
                raise CoverError('仅接受 JPEG/PNG/WebP/GIF，不接受 SVG 等活动内容')
            if image.width * image.height > MAX_PIXELS:
                raise CoverError('源图片像素超过 1200 万安全上限')
            image.seek(0)  # Animated covers use their first frame.
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            if image.mode in ('RGBA', 'LA', 'P'):
                rgba = image.convert('RGBA')
                canvas = Image.new('RGB', image.size, 'white')
                canvas.paste(rgba, mask=rgba.getchannel('A'))
                image = canvas
            else:
                image = image.convert('RGB')
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=85, optimize=True)
            result = output.getvalue()
            if len(result) > MAX_OUTPUT_BYTES:
                raise CoverError('转码后的图片超过 3 MB 安全上限')
            return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CoverError('图片损坏或解码失败') from exc


def make_r2_client(concurrency, proxy=None):
    return boto3.client(
        's3', endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name='auto',
        config=Config(
            signature_version='s3v4', connect_timeout=5, read_timeout=10,
            retries={'mode': 'standard', 'total_max_attempts': 3},
            max_pool_connections=concurrency,
            proxies={'http': proxy, 'https': proxy} if proxy else {},
        ),
    )


def publish_cover(snapshot, data, client):
    """Row lock covers BOTH the fixed-key upload and its DB update.

    A CAS performed only after uploading is insufficient: an older worker could
    overwrite covers/ID.jpg even when its later database update is rejected.
    PostgreSQL row locks serialize this short final stage with imports/workers.
    SQLite runs one worker and must not run concurrent importer/cover processes.
    """
    close_old_connections()
    try:
        with transaction.atomic():
            anime = RecommendationAnime.objects.select_for_update().filter(pk=snapshot['pk']).first()
            # Recheck under the row lock: classification may have changed
            # since a worker took its snapshot or started downloading.
            if anime is not None and anime.nsfw is not False:
                return 'skipped_adult' if anime.nsfw is True else 'skipped_unknown'
            if (anime is None or anime.bangumi_id != snapshot['bangumi_id']
                    or anime.cover_url != snapshot['cover_url']
                    or anime.source_cover_url != snapshot['source_cover_url']
                    or is_hosted_cover(anime.cover_url)):
                return 'stale'
            key = f'covers/{anime.bangumi_id}.jpg'
            client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=key, Body=data,
                ContentType='image/jpeg', CacheControl='public, max-age=86400',
                Metadata={'sha256': hashlib.sha256(data).hexdigest()},
            )
            # Versioned query prevents stale browser/CDN caches after a source
            # change, while the R2 object key remains exactly covers/ID.jpg.
            version = hashlib.sha256(data).hexdigest()[:16]
            anime.source_cover_url = anime.source_cover_url or snapshot['cover_url']
            anime.cover_url = f'https://{settings.AWS_S3_CUSTOM_DOMAIN}/{key}?v={version}'
            anime.save(update_fields=['cover_url', 'source_cover_url', 'updated_at'])
            return 'uploaded'
    finally:
        close_old_connections()


def get_batch(after_pk, size):
    close_old_connections()
    try:
        return list(RecommendationAnime.objects.filter(
            pk__gt=after_pk, bangumi_id__isnull=False,
        ).exclude(cover_url='').order_by('pk').values(
            'pk', 'bangumi_id', 'cover_url', 'source_cover_url', 'nsfw',
        )[:size])
    finally:
        close_old_connections()


async def sync_covers(*, concurrency, rate_limit, retries, batch_size, limit, dry_run, log, proxy=None):
    semaphore = asyncio.Semaphore(concurrency)
    limiter = RateLimiter(rate_limit)
    hosts = {host.strip().lower() for host in settings.RECOMMENDATION_COVER_ALLOWED_HOSTS}
    stats = Counter()
    aborted = asyncio.Event()
    r2 = None if dry_run else make_r2_client(concurrency, proxy=proxy)
    after_pk = 0
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=5, pool=5),
            limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
            follow_redirects=False, trust_env=False,
            proxy=proxy,
            headers={'User-Agent': 'AnimeDashboard-CoverSync/1.0', 'Accept-Encoding': 'identity'},
        ) as client:
            async def process(snapshot):
                async with semaphore:
                    if aborted.is_set():
                        return 'blocked'
                    try:
                        if dry_run:
                            await validate_download_url(snapshot['cover_url'], hosts)
                            return 'eligible'
                        for attempt in range(retries + 1):
                            try:
                                data = await asyncio.wait_for(download_image(
                                    client, snapshot['cover_url'], hosts, limiter,
                                ), timeout=30)
                                break
                            except (RetryableCoverError, httpx.TransportError, asyncio.TimeoutError):
                                if attempt == retries:
                                    raise CoverError('下载重试耗尽')
                                stats['retries'] += 1
                                await asyncio.sleep(min(2 ** attempt, 8) + random.uniform(0, 0.5))
                        jpeg = await asyncio.to_thread(encode_jpeg, data)
                        if aborted.is_set():
                            return 'blocked'
                        # SDK upload and synchronous transactions never block
                        # the event loop. Each thread has its own DB connection.
                        return await sync_to_async(publish_cover, thread_sensitive=False)(snapshot, jpeg, r2)
                    except CoverError as exc:
                        log(f'Bangumi {snapshot["bangumi_id"]}：{exc}')
                        return 'failed'
                    except (DatabaseError, NoCredentialsError, EndpointConnectionError) as exc:
                        aborted.set()
                        log(f'基础设施故障，停止新增任务：{type(exc).__name__}')
                        return 'failed'
                    except ClientError as exc:
                        error_code = exc.response.get('Error', {}).get('Code', '')
                        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
                        if error_code in {'AccessDenied', 'InvalidAccessKeyId', 'SignatureDoesNotMatch', 'NoSuchBucket', 'InvalidToken'} or status in (401, 403):
                            aborted.set()
                            log('R2 鉴权/存储桶配置错误，停止新增任务')
                        else:
                            log(f'Bangumi {snapshot["bangumi_id"]}：R2 上传失败')
                        return 'failed'
                    except Exception as exc:
                        # No raw SDK/HTTP exception text (may contain secrets).
                        log(f'Bangumi {snapshot["bangumi_id"]}：{type(exc).__name__}')
                        return 'failed'

            while True:
                batch = await sync_to_async(get_batch, thread_sensitive=False)(after_pk, batch_size)
                if not batch:
                    break
                after_pk = batch[-1]['pk']
                pending = []
                for snapshot in batch:
                    # Fail closed for missing classifications too; neither
                    # excluded category consumes the pilot's image limit.
                    if snapshot['nsfw'] is not False:
                        stats['skipped_adult' if snapshot['nsfw'] is True else 'skipped_unknown'] += 1
                    elif is_hosted_cover(snapshot['cover_url']):
                        stats['already_hosted'] += 1
                    elif not limit or stats['attempted'] < limit:
                        stats['attempted'] += 1
                        pending.append(snapshot)
                # At most batch_size tasks exist; at most concurrency images are
                # downloaded/decoded in memory, not 13000 simultaneous tasks.
                for result in await asyncio.gather(*(process(row) for row in pending)):
                    stats[result] += 1
                log(f'封面进度（扫描至 PK {after_pk}）：{dict(stats)}')
                if aborted.is_set() or (limit and stats['attempted'] >= limit):
                    break
    finally:
        if r2 is not None:
            r2.close()
    return stats
