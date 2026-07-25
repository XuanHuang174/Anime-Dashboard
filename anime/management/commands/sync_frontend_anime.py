import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from anime.models import Anime, UserAnimeRecord


class Command(BaseCommand):
    help = "将 React animeData.js 同步到公共番剧库和指定用户的私人手账。"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="XHcode")
        parser.add_argument(
            "--data-file",
            type=Path,
            default=settings.BASE_DIR / "frontend" / "src" / "data" / "animeData.js",
        )

    def handle(self, *args, **options):
        username = options["username"]
        data_file = options["data_file"].resolve()
        user_model = get_user_model()

        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist as error:
            raise CommandError(f"用户 {username!r} 不存在。") from error

        if not data_file.is_file():
            raise CommandError(f"找不到前端数据文件：{data_file}")

        try:
            source = data_file.read_text(encoding="utf-8")
            array_source = source[source.index("[") : source.rindex("]") + 1]
            items = json.loads(array_source)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise CommandError(f"无法解析前端番剧数据：{error}") from error

        anime_created = 0
        anime_updated = 0
        records_created = 0
        records_preserved = 0

        with transaction.atomic():
            for item in items:
                title = str(item.get("title", "")).strip()
                period = str(item.get("period", "")).strip() or "未定档"
                if not title:
                    self.stderr.write("跳过缺少中文名的数据项。")
                    continue

                anime, created = Anime.objects.update_or_create(
                    chinese_title=title,
                    japanese_title=str(item.get("japaneseTitle", "")).strip(),
                    airing_period=period,
                    defaults={
                        "studio": str(item.get("studio", "")).strip(),
                        "episodes": str(item.get("episodes", "")).strip(),
                        "description": str(item.get("description", "")).strip(),
                        "poster_url": str(item.get("cover", "")).strip(),
                        "baike_url": str(item.get("baikeUrl", "")).strip(),
                        "base_tags": list(item.get("tags") or []),
                    },
                )

                if created:
                    anime_created += 1
                else:
                    anime_updated += 1

                raw_score = item.get("score")
                score = Decimal(str(raw_score)) if raw_score is not None else None
                _, record_created = UserAnimeRecord.objects.get_or_create(
                    user=user,
                    anime=anime,
                    defaults={
                        "personal_score": score,
                        "watch_status": (
                            UserAnimeRecord.WatchStatus.COMPLETED
                            if score is not None
                            else UserAnimeRecord.WatchStatus.PLAN_TO_WATCH
                        ),
                    },
                )

                if record_created:
                    records_created += 1
                else:
                    records_preserved += 1

        self.stdout.write(
            self.style.SUCCESS(
                "同步完成："
                f"公共番剧新增 {anime_created}、更新 {anime_updated}；"
                f"{username} 手账新增 {records_created}、保留已有 {records_preserved}。"
            )
        )
