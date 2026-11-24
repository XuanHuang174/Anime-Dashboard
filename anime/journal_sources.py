"""Private source references, distinct from arbitrary user-entered URLs."""
import re
from urllib.parse import urlsplit

from django.core import signing
from django.db import DatabaseError, transaction
from rest_framework.exceptions import ValidationError

SOURCE_SALT = 'journal.bangumi-autofill.v1'


def bangumi_url(identity):
    return f'https://bgm.tv/subject/{identity}' if identity else ''


def normalize_bangumi_url(value):
    if not value:
        return ''
    try:
        parsed = urlsplit(value.strip())
        match = re.fullmatch(r'/subject/([1-9]\d*)/?', parsed.path)
        if (parsed.scheme not in ('https', 'http') or parsed.netloc.lower() not in ('bgm.tv', 'bangumi.tv')
                or not match or int(match[1]) > 2147483647):
            raise ValueError()
        return bangumi_url(int(match[1]))
    except ValueError:
        raise ValidationError('请填写有效的 Bangumi 作品条目链接，例如 https://bgm.tv/subject/123。') from None


def source_ticket(user, identity):
    return signing.dumps({'owner': user.pk, 'subject': identity}, salt=SOURCE_SALT)


def verify_source_ticket(user, ticket):
    try:
        data = signing.loads(ticket, salt=SOURCE_SALT, max_age=3600)
        identity = data['subject']
        if data['owner'] != user.pk or type(identity) is not int or not 1 <= identity <= 2147483647:
            raise ValueError()
        return identity
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise ValidationError({'autofill_ticket': '录入来源凭据无效或已过期，请重新选择 Bangumi 条目，或清除来源后手动创建。'}) from None


def confirmed_source_id(record, *, private=False):
    direct = record.verified_bangumi_id or record.anime.bgm_id
    if direct:
        return direct
    # Do not open a transaction or issue a query for each item in a prefetched
    # list. Only an individual write without the relation cached needs a lookup.
    cache = record._state.fields_cache
    if 'recommendation_identity' in cache:
        link = cache['recommendation_identity']
    else:
        if not private:
            return None  # Public lists must preload aliases, never query per work.
        try:
            from recommendations.models import RecommendationRecordIdentity
            with transaction.atomic():
                link = RecommendationRecordIdentity.objects.select_related('catalog_anime').filter(record_id=record.pk).first()
            cache['recommendation_identity'] = link
        except DatabaseError:
            return None  # Optional alias metadata must not break a valid write.
    if link is None or not link.active:
        return None
    from recommendations.matching import record_fingerprint, catalog_fingerprint
    metadata = record.effective_anime_metadata()
    row = {'anime_id': record.anime_id, 'metadata_initialized': True,
           'private_chinese_title': metadata['chinese_title'], 'private_japanese_title': metadata['japanese_title'],
           'private_airing_period': metadata['airing_period'], 'private_base_tags': []}
    if link.record_fingerprint != record_fingerprint(row) or link.catalog_fingerprint != catalog_fingerprint(link.catalog_anime):
        return None
    return link.catalog_anime.bangumi_id


def validate_record_source(record, url):
    normalized = normalize_bangumi_url(url)
    identity = confirmed_source_id(record, private=True)
    if normalized and identity and normalized != bangumi_url(identity):
        raise ValidationError({'bangumi_url': '链接与已确认的作品身份不一致，请先核对标准作品，不会通过编辑链接覆盖身份。'})
    return normalized
