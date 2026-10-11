"""Pure source normalization plus a transactional, idempotent catalog upsert."""

import html
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils.html import strip_tags

from .models import RecommendationAnime
from .normalization import clean_id, clean_nsfw, clean_score, clean_season, clean_tags, text


FIELD_ALIASES = {
    'title_zh': ('title_zh', 'name_cn', 'title_cn'),
    'title_jp': ('title_jp', 'name_jp', 'name'),
    'score': ('score', 'rating'),
    'tags': ('tags',),
    'cover_url': ('cover_url', 'image_url', 'cover', 'images'),
    'season': ('season', 'date', 'air_date'),
    'status_type': ('status_type', 'format'),
    'synopsis': ('synopsis', 'summary', 'description'),
    'nsfw': ('nsfw',),
}


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
        elif field == 'nsfw':
            values[field] = clean_nsfw(value)
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
    # An unknown incremental classification must not downgrade an existing
    # adult/non-adult decision. New records still get the model's None default.
    if values.get('nsfw', False) is None:
        values.pop('nsfw')
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
    # Adult records remain searchable as text-only catalog entries. Keeping
    # their public image field empty also prevents future UI from loading the
    # external cover directly. The internal source metadata is not published.
    if anime.nsfw is True and anime.cover_url:
        anime.cover_url = ''
        anime.save(update_fields=['cover_url', 'updated_at'])
    return created
