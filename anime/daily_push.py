import html
import logging
import re
import time
import unicodedata
from collections import defaultdict

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import UserAnimeRecord, UserProfile


logger = logging.getLogger(__name__)
BANGUMI_CALENDAR_URL = "https://api.bgm.tv/calendar"
RESEND_EMAILS_URL = "https://api.resend.com/emails"
NAME_NOISE_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]", flags=re.UNICODE)


def normalize_anime_name(text):
    """Normalize full-width forms and remove title marks, symbols and spaces."""

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return NAME_NOISE_PATTERN.sub("", normalized).lower()


def fetch_today_bangumi_items(*, request_get=requests.get):
    """Return only the calendar group for today's Shanghai weekday."""

    response = request_get(
        BANGUMI_CALENDAR_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "XHcode/AnimeDashboard/1.0 (daily-push)",
        },
        timeout=(3.05, 12),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Bangumi calendar returned an invalid payload.")

    today_weekday = timezone.localdate().isoweekday()
    for group in payload:
        weekday = group.get("weekday") if isinstance(group, dict) else None
        if isinstance(weekday, dict) and weekday.get("id") == today_weekday:
            items = group.get("items", [])
            return items if isinstance(items, list) else []
    return []


def _calendar_indexes(items):
    by_id = {}
    by_name = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        subject_id = item.get("id")
        if isinstance(subject_id, int):
            by_id[subject_id] = item
        for value in (item.get("name_cn"), item.get("name")):
            normalized = normalize_anime_name(value)
            if normalized:
                by_name.setdefault(normalized, item)
    return by_id, by_name


def _effective_record_names(record):
    if record.metadata_initialized:
        return (record.private_chinese_title, record.private_japanese_title)
    return (record.anime.chinese_title, record.anime.japanese_title)


def _display_title(record, calendar_item):
    for value in (*_effective_record_names(record), calendar_item.get("name_cn"), calendar_item.get("name")):
        if str(value or "").strip():
            return str(value).strip()
    return "未命名番剧"


def collect_daily_matches(calendar_items):
    """Group today's matching watching records by push-enabled profile."""

    by_id, by_name = _calendar_indexes(calendar_items)
    matches = defaultdict(list)
    records = (
        UserAnimeRecord.objects.filter(
            user__profile__is_push_enabled=True,
            user__is_active=True,
            watch_status=UserAnimeRecord.WatchStatus.WATCHING,
        )
        .exclude(user__email="")
        .select_related("user", "user__profile", "anime")
        .order_by("user_id", "pk")
    )

    for record in records.iterator(chunk_size=500):
        calendar_item = None
        if record.anime.bgm_id is not None:
            calendar_item = by_id.get(record.anime.bgm_id)
        else:
            for name in _effective_record_names(record):
                normalized = normalize_anime_name(name)
                if normalized and normalized in by_name:
                    calendar_item = by_name[normalized]
                    break
        if calendar_item is not None:
            matches[record.user.profile].append(
                _display_title(record, calendar_item)
            )
    return matches


def _claim_push(profile_id, push_date):
    with transaction.atomic():
        profile = UserProfile.objects.select_for_update().get(pk=profile_id)
        if not profile.is_push_enabled or profile.last_push_date == push_date:
            return None
        profile.last_push_date = push_date
        profile.save(update_fields=("last_push_date",))
        return profile


def _release_failed_claim(profile_id, push_date):
    UserProfile.objects.filter(
        pk=profile_id,
        last_push_date=push_date,
    ).update(last_push_date=None)


def _build_email(titles):
    unique_titles = list(dict.fromkeys(titles))
    decorated = [f"《{title.strip('《》')}》" for title in unique_titles]
    title_text = "、".join(decorated)
    plain_message = f"你关注的{title_text}今天已更新！\n\n登录 Anime Dashboard 查看你的私人手账。"
    list_items = "".join(
        f'<li style="margin:8px 0;font-weight:700">{html.escape(title)}</li>'
        for title in decorated
    )
    html_message = (
        '<div style="font-family:Arial,sans-serif;background:#fff5ee;padding:24px;color:#111">'
        '<div style="max-width:620px;margin:auto;border:4px solid #111;background:#fff;'
        'box-shadow:8px 8px 0 #4ecdc4;padding:24px">'
        '<p style="display:inline-block;border:2px solid #111;background:#ffe66d;'
        'padding:6px 10px;font-weight:900">DAILY ANIME PUSH</p>'
        '<h1 style="font-size:26px">今晚的新番更新到了！</h1>'
        '<p style="font-weight:700">你标记为“在看”的以下作品今天更新：</p>'
        f'<ul style="padding-left:24px">{list_items}</ul>'
        '<p style="margin-top:22px;font-size:13px">登录 Anime Dashboard 查看并更新你的追番记录。</p>'
        '</div></div>'
    )
    return plain_message, html_message


def send_daily_pushes(calendar_items):
    """Send at most one aggregated email to each matched user per day."""

    matches = collect_daily_matches(calendar_items)
    push_date = timezone.localdate()
    result = {"matched_users": len(matches), "sent": 0, "skipped": 0, "failed": 0}

    for profile, titles in matches.items():
        claimed_profile = _claim_push(profile.pk, push_date)
        if claimed_profile is None:
            result["skipped"] += 1
            continue

        plain_message, html_message = _build_email(titles)
        try:
            api_key = getattr(settings, "RESEND_API_KEY", "").strip()
            from_email = getattr(settings, "RESEND_FROM_EMAIL", "").strip()
            if not api_key or not from_email:
                raise RuntimeError(
                    "RESEND_API_KEY or RESEND_FROM_EMAIL is not configured."
                )

            payload = {
                "from": from_email,
                "to": [profile.user.email],
                "subject": f"今晚有 {len(set(titles))} 部新番更新啦！",
                "html": html_message,
                "text": plain_message,
            }
            response = requests.post(
                RESEND_EMAILS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=(
                    getattr(settings, "RESEND_CONNECT_TIMEOUT", 3.05),
                    getattr(settings, "RESEND_READ_TIMEOUT", 8.0),
                ),
            )
            if not response.ok or response.status_code not in (200, 201):
                logger.error(
                    "Resend API rejected daily push: status=%s user_id=%s response=%s",
                    response.status_code,
                    profile.user_id,
                    response.text,
                )
                raise RuntimeError(
                    f"Resend API returned HTTP {response.status_code}."
                )
            result["sent"] += 1
        except Exception:
            _release_failed_claim(profile.pk, push_date)
            result["failed"] += 1
            logger.exception("Daily anime push failed for user_id=%s", profile.user_id)
        finally:
            # Required provider protection: never burst emails across users.
            time.sleep(1)

    return result
