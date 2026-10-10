import asyncio
import math
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from recommendations.covers import sync_covers


class Command(BaseCommand):
    help = '后台异步转存推荐库外部封面到 R2，可重跑续传，不在 Web 请求中执行。'

    def add_arguments(self, parser):
        parser.add_argument('--concurrency', type=int, default=4)
        parser.add_argument('--rate-limit', type=float, default=2, help='本进程每秒最多 HTTP 请求数（含重定向、重试）')
        parser.add_argument('--retries', type=int, default=3, help='首次下载失败后的额外重试次数')
        parser.add_argument('--batch-size', type=int, default=500)
        parser.add_argument('--limit', type=int, default=0, help='最多处理多少张外部封面，0 表示不限制')
        parser.add_argument('--dry-run', action='store_true', help='只扫描并校验地址/DNS，不下载、不上传、不更新数据库')

    def handle(self, *args, **options):
        if not 1 <= options['concurrency'] <= 8:
            raise CommandError('concurrency 必须在 1–8 之间。')
        if not math.isfinite(options['rate_limit']) or not 0.1 <= options['rate_limit'] <= 20:
            raise CommandError('rate-limit 必须在 0.1–20 之间。')
        if not 0 <= options['retries'] <= 5 or not 1 <= options['batch_size'] <= 2000 or options['limit'] < 0:
            raise CommandError('retries 必须为 0–5、batch-size 必须为 1–2000、limit 不能为负。')
        if not options['dry_run'] and not settings.R2_STORAGE_ENABLED:
            raise CommandError('R2 未完整配置，请设置现有 AWS_* 环境变量。')
        domain = settings.AWS_S3_CUSTOM_DOMAIN
        if not options['dry_run']:
            parsed = urlsplit(f'https://{domain}')
            if (not domain or not parsed.hostname or parsed.netloc != parsed.hostname
                    or parsed.path or parsed.query or parsed.fragment):
                raise CommandError('需要有效的公开 R2 自定义域名（如 media.xh-anime.com），不能用签名 URL。')
        if connection.vendor == 'sqlite' and options['concurrency'] != 1:
            self.stdout.write(self.style.WARNING('SQLite 不支持行锁，自动降为单 worker；不要同时运行导入/转存进程。生产使用 PostgreSQL。'))
            options['concurrency'] = 1
        try:
            stats = asyncio.run(sync_covers(
                **{key: options[key] for key in ('concurrency', 'rate_limit', 'retries', 'batch_size', 'limit', 'dry_run')},
                log=self.stdout.write,
            ))
        except KeyboardInterrupt as exc:
            raise CommandError('已中断。成功转存的数据保留，重跑可继续处理外链。') from exc
        except Exception as exc:
            raise CommandError(f'后台转存异常：{type(exc).__name__}；可重跑续传。') from exc
        self.stdout.write(self.style.SUCCESS(f'封面任务结束：{dict(stats)}'))
        if stats['failed']:
            raise CommandError('部分封面失败：原外链已保留，请查看上述原因并重新运行。')
