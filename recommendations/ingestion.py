"""Pure source normalization plus a transactional, idempotent catalog upsert."""

import html
import math
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils.html import strip_tags

from .models import RecommendationAnime


FIELD_ALIASES = {
    'title_zh': ('title_zh', 'name_cn', 'title_cn'),
    'title_jp': ('title_jp', 'name_jp', 'name'),
    'score': ('score', 'rating'),
    'tags': ('tags',),
    'cover_url': ('cover_url', 'image_url', 'cover', 'images'),
    'season': ('season', 'date', 'air_date'),
    'status_type': ('status_type', 'format'),
    'synopsis': ('synopsis', 'summary', 'description'),
}


def text(value):
    return value.strip() if isinstance(value, str) else ''


def clean_id(value):
    # Do not silently truncate floats, accept booleans, or upsert NULL IDs.
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value).strip()):
        raise ValueError('缺失或非法 Bangumi ID（必须是正整数）')
    result = int(value)
    if result > 2_147_483_647:
        raise ValueError('Bangumi ID 超出 IntegerField 范围')
    return result


def clean_score(value):
    if isinstance(value, dict):
        value = value.get('score', 0)
    try:
        result = float(value) if not isinstance(value, bool) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return result if math.isfinite(result) and 0 <= result <= 10 else 0.0


def clean_tags(value):
    if isinstance(value, str):
        value = re.split(r'[,，]', value)
    if not isinstance(value, list):
        return ''
    result = []
    for item in value:
        item = item.get('name', '') if isinstance(item, dict) else item
        # A list member containing commas is normalized just like a string.
        for name in re.split(r'[,，]', text(item)):
            name = name.strip()
            if name and name not in result:
                result.append(name)
    return ','.join(result)


def clean_season(value, row):
    value = text(value)
    months = {'WINTER': '01', 'SPRING': '04', 'SUMMER': '07', 'FALL': '10', 'AUTUMN': '10'}
    year = row.get('seasonYear')
    if value.upper() in months and str(year).isdigit() and 1900 <= int(year) <= 2200:
        return f'{year}-{months[value.upper()]}'
    match = re.fullmatch(r'(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?', value)
    if match and 1 <= int(match[2]) <= 12:
        quarter = ((int(match[2]) - 1) // 3) * 3 + 1
        return f'{match[1]}-{quarter:02d}'
    return value


def is_hosted_cover(url):
    domain = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', '').strip().lower()
    try:
        parsed = urlsplit(url)
        return bool(domain and parsed.scheme == 'https' and parsed.netloc.lower() == domain)
    except ValueError:
        return False


def normalize_row(row, id_field=None):
    if not isinstance(row, dict):
        raise ValueError('记录必须是 JSON 对象')
    if id_field:
        identity = row.get(id_field)
    else:
        # Generic `id` might be AniList/MAL ID: never guess its namespace.
        identity = next((row[key] for key in ('bangumi_id', 'bgm_id', 'subject_id') if key in row), None)
    bangumi_id = clean_id(identity)
    values = {}
    for field, aliases in FIELD_ALIASES.items():
        key = next((key for key in aliases if key in row), None)
        if key is None:
            continue  # Absent fields preserve existing data in incremental updates.
        value = row[key]
        if field == 'score':
            values[field] = clean_score(value)
        elif field == 'tags':
            values[field] = clean_tags(value)
        elif field == 'season':
            values[field] = clean_season(value, row)
        elif field == 'cover_url':
            if isinstance(value, dict):
                value = next((value[k] for k in ('large', 'common', 'medium', 'small') if value.get(k)), '')
            values[field] = text(value)
        elif field == 'synopsis':
            values[field] = html.unescape(strip_tags(text(value)))
            if len(values[field]) > 200_000:
                raise ValueError('简介超过 200000 字符')
        elif field == 'status_type':
            values[field] = {'MOVIE': '剧场版', 'TV_SHORT': 'TV短篇'}.get(text(value).upper(), text(value))
        else:
            values[field] = text(value)
    # Validate lengths/URLs before touching the database; never silently truncate.
    candidate = RecommendationAnime(bangumi_id=bangumi_id, **values)
    candidate.title_zh = candidate.title_zh or candidate.title_jp or f'未命名番剧 {bangumi_id}'
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    return bangumi_id, values


@transaction.atomic
def upsert_anime(bangumi_id, values):
    values = values.copy()
    source_cover = values.pop('cover_url', None)
    # update_or_create locks the existing row; this also serializes the final
    # R2 upload against imports, avoiding stale uploads to the fixed object key.
    create_values = {**values, 'title_zh': values.get('title_zh') or values.get('title_jp') or f'未命名番剧 {bangumi_id}'}
    if 'title_zh' in values and not values['title_zh']:
        values['title_zh'] = create_values['title_zh']
    anime, created = RecommendationAnime.objects.update_or_create(
        bangumi_id=bangumi_id, defaults=values, create_defaults=create_values,
    )
    if source_cover is not None:
        same_source = source_cover == (anime.source_cover_url or anime.cover_url)
        if not (same_source and is_hosted_cover(anime.cover_url)):
            anime.cover_url = source_cover
        anime.source_cover_url = source_cover
        anime.save(update_fields=['cover_url', 'source_cover_url', 'updated_at'])
    return created
