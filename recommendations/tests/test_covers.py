import asyncio
import io
import socket
from unittest.mock import AsyncMock, Mock, patch

import httpx
from botocore.exceptions import ClientError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from PIL import Image

from recommendations.covers import (
    CoverError, MAX_DOWNLOAD_BYTES, RateLimiter, RetryableCoverError,
    download_image, encode_jpeg, publish_cover, sync_covers, validate_download_url,
)
from recommendations.ingestion import is_hosted_cover, upsert_anime
from recommendations.models import RecommendationAnime


def png_bytes():
    output = io.BytesIO()
    Image.new('RGBA', (32, 48), (255, 0, 0, 120)).save(output, 'PNG')
    return output.getvalue()


class ImageTests(SimpleTestCase):
    def test_real_jpeg_conversion_with_alpha(self):
        result = encode_jpeg(png_bytes())
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual((image.format, image.mode, image.size), ('JPEG', 'RGB', (32, 48)))

    def test_reject_html_svg_corrupt_and_pixel_bombs(self):
        for value in (b'<html>error</html>', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', b''):
            with self.assertRaises(CoverError):
                encode_jpeg(value)
        with patch('recommendations.covers.MAX_PIXELS', 10), self.assertRaises(CoverError):
            encode_jpeg(png_bytes())

    @override_settings(AWS_S3_CUSTOM_DOMAIN='media.xh-anime.com')
    def test_hosted_host_match_is_exact(self):
        self.assertTrue(is_hosted_cover('https://media.xh-anime.com/covers/1.jpg?v=a'))
        self.assertFalse(is_hosted_cover('https://media.xh-anime.com.evil.test/1.jpg'))
        self.assertFalse(is_hosted_cover('https://media.xh-anime.com@evil.test/1.jpg'))


class DownloadTests(SimpleTestCase):
    def fetch(self, handler):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await download_image(client, 'https://images.example.com/a.png', {'images.example.com'}, RateLimiter(10000))
        with patch('recommendations.covers.validate_download_url', new_callable=AsyncMock):
            return asyncio.run(run())

    def test_success_stream(self):
        self.assertEqual(self.fetch(lambda req: httpx.Response(200, content=png_bytes(), headers={'content-type': 'image/png'})), png_bytes())

    def test_length_and_actual_body_limits(self):
        with self.assertRaises(CoverError):
            self.fetch(lambda req: httpx.Response(200, headers={'content-length': str(MAX_DOWNLOAD_BYTES + 1)}))
        with patch('recommendations.covers.MAX_DOWNLOAD_BYTES', 10), self.assertRaises(CoverError):
            # A deliberately false Content-Length cannot bypass streamed bounds.
            self.fetch(lambda req: httpx.Response(200, content=png_bytes(), headers={'content-length': '1'}))

    def test_status_and_http_compression(self):
        for status in (429, 503):
            with self.assertRaises(RetryableCoverError):
                self.fetch(lambda req: httpx.Response(status))
        with self.assertRaises(CoverError):
            self.fetch(lambda req: httpx.Response(404))
        with self.assertRaises(CoverError):
            self.fetch(lambda req: httpx.Response(200, headers={'content-encoding': 'gzip'}))

    def test_redirect_target_is_checked(self):
        async def validate(url, hosts):
            if '127.0.0.1' in url:
                raise CoverError('private destination')
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(302, headers={'location': 'https://127.0.0.1/secret'})
            )) as client:
                return await download_image(client, 'https://images.example.com/a', {'images.example.com'}, RateLimiter(10000))
        with patch('recommendations.covers.validate_download_url', side_effect=validate), self.assertRaises(CoverError):
            asyncio.run(run())

    def test_url_and_dns_security(self):
        async def run():
            for url in ('http://images.example.com/a', 'https://images.example.com:444/a',
                        'https://user:secret@images.example.com/a', 'https://evil.test/a', 'https://127.0.0.1/a'):
                with self.assertRaises(CoverError):
                    await validate_download_url(url, {'images.example.com', '127.0.0.1'})
            loop = asyncio.get_running_loop()
            with patch.object(loop, 'getaddrinfo', new=AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))])):
                with self.assertRaises(CoverError):
                    await validate_download_url('https://images.example.com/a', {'images.example.com'})
        asyncio.run(run())

    def test_shared_rate_limit(self):
        async def run():
            limiter = RateLimiter(100)
            started = asyncio.get_running_loop().time()
            await asyncio.gather(*(limiter.wait() for _ in range(3)))
            return asyncio.get_running_loop().time() - started
        self.assertGreaterEqual(asyncio.run(run()), 0.018)


@override_settings(AWS_S3_CUSTOM_DOMAIN='media.xh-anime.com', AWS_STORAGE_BUCKET_NAME='test-bucket')
class PublishTests(TransactionTestCase):
    def setUp(self):
        self.anime = RecommendationAnime.objects.create(
            bangumi_id=42, title_zh='测试', nsfw=False, cover_url='https://lain.bgm.tv/a.png', source_cover_url='https://lain.bgm.tv/a.png',
        )
        self.snapshot = {field: getattr(self.anime, field) for field in ('pk', 'bangumi_id', 'cover_url', 'source_cover_url')}

    def test_upload_key_jpeg_and_resume(self):
        client = Mock()
        self.assertEqual(publish_cover(self.snapshot, encode_jpeg(png_bytes()), client), 'uploaded')
        kwargs = client.put_object.call_args.kwargs
        self.assertEqual((kwargs['Key'], kwargs['ContentType']), ('covers/42.jpg', 'image/jpeg'))
        self.anime.refresh_from_db()
        self.assertTrue(is_hosted_cover(self.anime.cover_url))
        self.assertEqual(publish_cover(self.snapshot, b'old', client), 'stale')
        self.assertEqual(client.put_object.call_count, 1)

    def test_source_changed_or_deleted_never_uploads_stale_data(self):
        upsert_anime(42, {'cover_url': 'https://lain.bgm.tv/b.png'})
        client = Mock()
        self.assertEqual(publish_cover(self.snapshot, b'old', client), 'stale')
        self.anime.delete()
        self.assertEqual(publish_cover(self.snapshot, b'old', client), 'stale')
        client.put_object.assert_not_called()

    def test_classification_changed_after_snapshot_blocks_publication(self):
        for marker, expected in ((True, 'skipped_adult'), (None, 'skipped_unknown')):
            RecommendationAnime.objects.filter(pk=self.anime.pk).update(nsfw=marker)
            client = Mock()
            self.assertEqual(publish_cover(self.snapshot, png_bytes(), client), expected)
            client.put_object.assert_not_called()

    def test_adult_and_unknown_never_download_and_do_not_consume_limit(self):
        self.anime.nsfw = True
        self.anime.save(update_fields=['nsfw'])
        RecommendationAnime.objects.create(bangumi_id=43, title_zh='未知', cover_url='https://lain.bgm.tv/b.png')
        RecommendationAnime.objects.create(bangumi_id=44, title_zh='普通', nsfw=False, cover_url='https://lain.bgm.tv/c.png')
        client = Mock()
        download = AsyncMock(return_value=png_bytes())
        with patch('recommendations.covers.make_r2_client', return_value=client), \
                patch('recommendations.covers.download_image', download):
            stats = asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=0, batch_size=1, limit=1, dry_run=False, log=lambda msg: None))
        self.assertEqual((stats['skipped_adult'], stats['skipped_unknown'], stats['uploaded']), (1, 1, 1))
        self.assertEqual(stats['attempted'], 1)
        self.assertEqual(download.await_count, 1)
        self.assertEqual(client.put_object.call_args.kwargs['Key'], 'covers/44.jpg')

    def test_explicit_proxy_forwarded_without_implicit_environment_proxy(self):
        client = Mock()
        with patch('recommendations.covers.make_r2_client', return_value=client) as make, \
                patch('recommendations.covers.download_image', new=AsyncMock(return_value=png_bytes())), \
                patch('recommendations.covers.httpx.AsyncClient', wraps=httpx.AsyncClient) as transport:
            asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=0, batch_size=1, limit=1, dry_run=False,
                                   log=lambda msg: None, proxy='http://127.0.0.1:7890'))
        make.assert_called_once_with(1, proxy='http://127.0.0.1:7890')
        self.assertEqual(transport.call_args.kwargs['proxy'], 'http://127.0.0.1:7890')
        self.assertFalse(transport.call_args.kwargs['trust_env'])

    def test_dry_run_also_skips_adult_and_unknown_without_dns(self):
        RecommendationAnime.objects.filter(pk=self.anime.pk).update(nsfw=True)
        RecommendationAnime.objects.create(bangumi_id=43, title_zh='未知', cover_url='https://lain.bgm.tv/b.png')
        with patch('recommendations.covers.validate_download_url', new_callable=AsyncMock) as validate:
            stats = asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=0, batch_size=1, limit=20, dry_run=True, log=lambda msg: None))
        self.assertEqual((stats['skipped_adult'], stats['skipped_unknown'], stats['attempted']), (1, 1, 0))
        validate.assert_not_called()

    def test_cli_rejects_proxy_with_credentials_before_scan(self):
        with patch('recommendations.management.commands.sync_recommendation_covers.sync_covers') as sync:
            with self.assertRaises(CommandError):
                call_command('sync_recommendation_covers', proxy='http://secret:password@localhost:7890', dry_run=True, stdout=io.StringIO())
        sync.assert_not_called()

    def test_failed_upload_preserves_original_database(self):
        client = Mock()
        client.put_object.side_effect = RuntimeError('SDK failed')
        with self.assertRaises(RuntimeError):
            publish_cover(self.snapshot, b'data', client)
        self.anime.refresh_from_db()
        self.assertEqual(self.anime.cover_url, self.snapshot['cover_url'])

    def test_orchestrator_retries_and_closes_client(self):
        download = AsyncMock(side_effect=[RetryableCoverError('503'), png_bytes()])
        client = Mock()
        with patch('recommendations.covers.make_r2_client', return_value=client), \
                patch('recommendations.covers.download_image', download), \
                patch('recommendations.covers.asyncio.sleep', new_callable=AsyncMock):
            stats = asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=1, batch_size=1, limit=0, dry_run=False, log=lambda msg: None))
        self.assertEqual((stats['uploaded'], stats['retries']), (1, 1))
        client.close.assert_called_once()

    def test_limit_skips_hosted_and_only_attempts_one_external(self):
        RecommendationAnime.objects.create(bangumi_id=43, title_zh='已托管', nsfw=False, cover_url='https://media.xh-anime.com/covers/43.jpg')
        RecommendationAnime.objects.create(bangumi_id=44, title_zh='另一个', nsfw=False, cover_url='https://lain.bgm.tv/c.png')
        with patch('recommendations.covers.validate_download_url', new_callable=AsyncMock):
            stats = asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=1, batch_size=500, limit=1, dry_run=True, log=lambda msg: None))
        self.assertEqual((stats['attempted'], stats['already_hosted'], stats['eligible']), (1, 1, 1))

    def test_retry_exhaustion_preserves_source_and_never_uploads(self):
        client = Mock()
        with patch('recommendations.covers.make_r2_client', return_value=client), \
                patch('recommendations.covers.download_image', new=AsyncMock(side_effect=RetryableCoverError('503'))), \
                patch('recommendations.covers.asyncio.sleep', new_callable=AsyncMock):
            stats = asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=1, batch_size=500, limit=0, dry_run=False, log=lambda msg: None))
        self.assertEqual((stats['failed'], stats['retries']), (1, 1))
        client.put_object.assert_not_called()
        self.anime.refresh_from_db()
        self.assertEqual(self.anime.cover_url, self.snapshot['cover_url'])

    def test_invalid_r2_credentials_stop_remaining_work(self):
        RecommendationAnime.objects.create(bangumi_id=43, title_zh='第二个', nsfw=False, cover_url='https://lain.bgm.tv/b.png')
        client = Mock()
        client.put_object.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied'}, 'ResponseMetadata': {'HTTPStatusCode': 403}}, 'PutObject',
        )
        download = AsyncMock(return_value=png_bytes())
        with patch('recommendations.covers.make_r2_client', return_value=client), \
                patch('recommendations.covers.download_image', download):
            stats = asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=1, batch_size=500, limit=0, dry_run=False, log=lambda msg: None))
        self.assertEqual((stats['failed'], stats['blocked']), (1, 1))
        self.assertEqual(download.await_count, 1)
        self.assertEqual(client.put_object.call_count, 1)

    def test_dry_run_no_download_upload_or_database_mutation(self):
        with patch('recommendations.covers.validate_download_url', new_callable=AsyncMock), \
                patch('recommendations.covers.make_r2_client') as make, \
                patch('recommendations.covers.download_image', new_callable=AsyncMock) as download:
            stats = asyncio.run(sync_covers(concurrency=1, rate_limit=2, retries=1, batch_size=500, limit=1, dry_run=True, log=lambda msg: None))
        self.assertEqual(stats['eligible'], 1)
        make.assert_not_called()
        download.assert_not_called()
        self.anime.refresh_from_db()
        self.assertEqual(self.anime.cover_url, self.snapshot['cover_url'])

    @override_settings(R2_STORAGE_ENABLED=False)
    def test_unconfigured_r2_and_invalid_options_fail_before_upload(self):
        for kwargs in ({}, {'concurrency': 0}, {'rate_limit': float('nan')}, {'limit': -1}):
            with self.assertRaises(CommandError):
                call_command('sync_recommendation_covers', stdout=io.StringIO(), **kwargs)
