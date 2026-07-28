import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from anime.models import Anime, UserAnimeRecord


class Command(BaseCommand):
    help = "从前端原始数据恢复指定用户的私人番剧资料，可选同步修复公共库。"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument(
            "--title",
            action="append",
            dest="titles",
            required=True,
            help="需要恢复的番剧中文名；可重复传入。",
        )
        parser.add_argument(
            "--file",
            type=Path,
            default=settings.BASE_DIR / "frontend" / "src" / "data" / "animeData.js",
        )
        parser.add_argument(
            "--restore-public",
            action="store_true",
            help="同时将对应的公共 Anime 行恢复为原始数据。",
        )

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(
            username=options["username"],
        ).first()
        if user is None:
            raise CommandError(f"用户不存在：{options['username']}")

        source_path = options["file"].resolve()
        try:
            source = source_path.read_text(encoding="utf-8-sig").strip()
            items = json.loads(source[source.index("[") : source.rindex("]") + 1])
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise CommandError(f"无法解析数据文件：{error}") from error

        wanted_titles = set(options["titles"])
        source_by_title = {
            str(item.get("title") or item.get("chinese_title") or "").strip(): item
            for item in items
            if isinstance(item, dict)
        }
        missing_titles = wanted_titles - source_by_title.keys()
        if missing_titles:
            raise CommandError(
                f"原始数据中找不到：{', '.join(sorted(missing_titles))}"
            )

        restored_records = 0
        restored_public = 0
        with transaction.atomic():
            for title in wanted_titles:
                item = source_by_title[title]
                records = UserAnimeRecord.objects.select_related("anime").filter(
                    user=user,
                    anime__chinese_title=title,
                )
                for record in records:
                    self._apply_to_record(record, item)
                    restored_records += 1

                    if options["restore_public"]:
                        self._apply_to_anime(record.anime, item)
                        restored_public += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"恢复完成：私人记录 {restored_records} 条，"
                f"公共资料 {restored_public} 条。"
            )
        )

    @staticmethod
    def _metadata(item):
        return {
            "chinese_title": str(
                item.get("title") or item.get("chinese_title") or ""
            ).strip(),
            "japanese_title": str(
                item.get("japaneseTitle") or item.get("japanese_title") or ""
            ).strip(),
            "airing_period": str(
                item.get("period") or item.get("airing_period") or "未定档"
            ).strip(),
            "studio": str(item.get("studio") or "").strip(),
            "episodes": str(item.get("episodes") or "").strip(),
            "description": str(
                item.get("description") or item.get("synopsis") or ""
            ).strip(),
            "poster_url": str(
                item.get("cover") or item.get("poster_url") or ""
            ).strip(),
            "baike_url": str(
                item.get("baikeUrl") or item.get("baike_url") or ""
            ).strip(),
            "base_tags": list(
                dict.fromkeys(
                    str(tag).strip()
                    for tag in (item.get("tags") or item.get("base_tags") or [])
                    if str(tag).strip()
                )
            ),
        }

    def _apply_to_record(self, record, item):
        metadata = self._metadata(item)
        for field, value in metadata.items():
            setattr(record, f"private_{field}", value)
        record.private_poster_image = None
        record.metadata_initialized = True
        record.save(
            update_fields=(
                "private_chinese_title",
                "private_japanese_title",
                "private_airing_period",
                "private_studio",
                "private_episodes",
                "private_description",
                "private_poster_url",
                "private_poster_image",
                "private_baike_url",
                "private_base_tags",
                "metadata_initialized",
                "updated_at",
            )
        )

    def _apply_to_anime(self, anime, item):
        metadata = self._metadata(item)
        for field, value in metadata.items():
            setattr(anime, field, value)
        anime.poster_image = None
        anime.save(
            update_fields=(
                "chinese_title",
                "japanese_title",
                "airing_period",
                "studio",
                "episodes",
                "description",
                "poster_url",
                "poster_image",
                "baike_url",
                "base_tags",
                "updated_at",
            )
        )
