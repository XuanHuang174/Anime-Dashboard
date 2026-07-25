import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from anime.models import Anime, UserAnimeRecord


STATUS_ALIASES = {
    "plan": UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
    "want": UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
    "想看": UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
    "watching": UserAnimeRecord.WatchStatus.WATCHING,
    "在看": UserAnimeRecord.WatchStatus.WATCHING,
    "completed": UserAnimeRecord.WatchStatus.COMPLETED,
    "watched": UserAnimeRecord.WatchStatus.COMPLETED,
    "看过": UserAnimeRecord.WatchStatus.COMPLETED,
    "on_hold": UserAnimeRecord.WatchStatus.ON_HOLD,
    "hold": UserAnimeRecord.WatchStatus.ON_HOLD,
    "搁置": UserAnimeRecord.WatchStatus.ON_HOLD,
}


def first_value(item, *keys, default=""):
    for key in keys:
        value = item.get(key)
        if value is not None:
            return value
    return default


def ensure_string_list(value, field_name, index):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
        raise CommandError(f"第 {index} 项的 {field_name} 必须是字符串数组。")
    return [tag.strip() for tag in value if tag.strip()]


class Command(BaseCommand):
    help = "从 JSON 番剧数组初始化公共 Anime 和指定用户的 UserAnimeRecord。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=Path,
            default=settings.BASE_DIR / "frontend" / "src" / "data" / "animeData.js",
            help="JSON 文件路径；默认兼容读取现有 frontend/src/data/animeData.js。",
        )
        parser.add_argument("--username", default="XHcode")
        parser.add_argument(
            "--password",
            default=None,
            help="仅在用户不存在时使用；省略则为新用户设置不可用密码。",
        )
        parser.add_argument(
            "--overwrite-records",
            action="store_true",
            help="覆盖已有私人记录；默认保留用户后来修改过的评分、状态、标签和评价。",
        )

    def handle(self, *args, **options):
        data_file = options["file"].resolve()
        items = self.load_items(data_file)
        user, user_created = self.get_or_create_user(
            options["username"],
            options["password"],
        )

        counters = {
            "anime_created": 0,
            "anime_updated": 0,
            "records_created": 0,
            "records_updated": 0,
            "records_preserved": 0,
        }

        with transaction.atomic():
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    raise CommandError(f"第 {index} 项必须是 JSON 对象。")

                anime = self.seed_anime(item, index, counters)
                self.seed_record(
                    user,
                    anime,
                    item,
                    index,
                    options["overwrite_records"],
                    counters,
                )

        user_message = "新建" if user_created else "使用已有"
        self.stdout.write(
            self.style.SUCCESS(
                f"导入完成：{user_message}用户 {user.username}；"
                f"公共番剧新增 {counters['anime_created']}、更新 {counters['anime_updated']}；"
                f"私人记录新增 {counters['records_created']}、更新 {counters['records_updated']}、"
                f"保留 {counters['records_preserved']}。"
            )
        )

    def load_items(self, data_file):
        if not data_file.is_file():
            raise CommandError(f"找不到数据文件：{data_file}")

        try:
            source = data_file.read_text(encoding="utf-8-sig").strip()
            if data_file.suffix.lower() == ".json":
                items = json.loads(source)
            else:
                # animeData.js 的数组内容本身是合法 JSON，只需去掉外层 JS 声明。
                items = json.loads(source[source.index("[") : source.rindex("]") + 1])
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise CommandError(f"无法解析数据文件：{error}") from error

        if not isinstance(items, list):
            raise CommandError("数据文件的顶层必须是 JSON 数组。")
        return items

    def get_or_create_user(self, username, password):
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=username)
        if created:
            if password:
                user.set_password(password)
            else:
                user.set_unusable_password()
                self.stdout.write(
                    self.style.WARNING(
                        f"用户 {username} 已创建，但未提供 --password，暂时不能用密码登录。"
                    )
                )
            user.save(update_fields=["password"])
        return user, created

    def seed_anime(self, item, index, counters):
        title = str(first_value(item, "chinese_title", "title")).strip()
        japanese_title = str(
            first_value(item, "japanese_title", "japaneseTitle")
        ).strip()
        airing_period = str(
            first_value(item, "airing_period", "period", default="未定档")
        ).strip() or "未定档"

        if not title:
            raise CommandError(f"第 {index} 项缺少 title/chinese_title。")

        base_tags = ensure_string_list(
            first_value(item, "base_tags", "tags", default=[]),
            "tags/base_tags",
            index,
        )
        anime, created = Anime.objects.update_or_create(
            chinese_title=title,
            japanese_title=japanese_title,
            airing_period=airing_period,
            defaults={
                "studio": str(first_value(item, "studio")).strip(),
                "episodes": str(first_value(item, "episodes")).strip(),
                "description": str(
                    first_value(item, "synopsis", "description")
                ).strip(),
                "poster_url": str(
                    first_value(item, "poster_url", "cover")
                ).strip(),
                "baike_url": str(
                    first_value(item, "baike_url", "baikeUrl")
                ).strip(),
                "base_tags": base_tags,
            },
        )
        counters["anime_created" if created else "anime_updated"] += 1
        return anime

    def seed_record(self, user, anime, item, index, overwrite, counters):
        raw_score = first_value(item, "personal_score", "score", default=None)
        try:
            score = Decimal(str(raw_score)) if raw_score not in (None, "") else None
        except InvalidOperation as error:
            raise CommandError(f"第 {index} 项的评分不是有效数字。") from error

        raw_status = str(
            first_value(item, "watch_status", "status", default="")
        ).strip()
        default_status = (
            UserAnimeRecord.WatchStatus.COMPLETED
            if score is not None
            else UserAnimeRecord.WatchStatus.PLAN_TO_WATCH
        )
        watch_status = STATUS_ALIASES.get(raw_status, default_status)
        custom_tags = ensure_string_list(
            first_value(item, "custom_tags", "customTags", default=[]),
            "custom_tags/customTags",
            index,
        )
        review = str(first_value(item, "review", "comment", default="")).strip()
        defaults = {
            "personal_score": score,
            "watch_status": watch_status,
            "custom_tags": custom_tags,
            "review": review,
        }

        record, created = UserAnimeRecord.objects.get_or_create(
            user=user,
            anime=anime,
            defaults=defaults,
        )
        if created:
            counters["records_created"] += 1
        elif overwrite:
            for field, value in defaults.items():
                setattr(record, field, value)
            record.save(update_fields=[*defaults, "updated_at"])
            counters["records_updated"] += 1
        else:
            counters["records_preserved"] += 1
