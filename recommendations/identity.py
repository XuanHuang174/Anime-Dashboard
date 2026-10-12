"""Read-only identity contracts. Never guess IDs, merge records or mutate Anime."""
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from django.conf import settings
from rest_framework.exceptions import NotFound

from anime.models import Anime, UserAnimeRecord
from .models import RecommendationAnime
from .normalization import clean_id


def safe_catalog():
    return RecommendationAnime.objects.filter(nsfw=False, bangumi_id__isnull=False)


def require_safe_anime(anime):
    if anime.nsfw is not False or not anime.bangumi_id:
        raise NotFound('该作品不可用于推荐。')


def public_cover_url(anime):
    require_safe_anime(anime)
    domain = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', '').strip().lower()
    try:
        url = urlsplit(anime.cover_url)
        if (not domain or url.scheme != 'https' or url.netloc.lower() != domain
                or url.username or url.password or url.fragment
                or url.path != f'/covers/{anime.bangumi_id}.jpg'):
            return ''
        params = parse_qsl(url.query, keep_blank_values=True)
        if len(params) > 1 or any(key != 'v' or not re.fullmatch('[a-f0-9]{16}', value) for key, value in params):
            return ''
        return anime.cover_url
    except ValueError:
        return ''


def journal_field_conflicts(anime):
    """Report lossless compatibility, not silently truncating 500-char titles."""
    require_safe_anime(anime)
    title = anime.title_zh or anime.title_jp or f'未命名番剧 {anime.bangumi_id}'
    values = {'chinese_title': title, 'japanese_title': anime.title_jp,
              'airing_period': anime.season, 'poster_url': public_cover_url(anime)}
    return [f'{field}_too_long' for field, value in values.items()
            if len(value) > Anime._meta.get_field(field).max_length]


def normalized_title(value):
    value = unicodedata.normalize('NFKC', value or '').strip()
    return re.sub(r'\s+', '', value.removeprefix('《').removesuffix('》')).casefold()


def has_possible_legacy_record(*, user, anime):
    expected = {normalized_title(name) for name in (anime.title_zh, anime.title_jp) if name}
    rows = UserAnimeRecord.objects.filter(user=user, anime__bgm_id__isnull=True).order_by().values(
        'metadata_initialized', 'private_chinese_title', 'private_japanese_title',
        'anime__chinese_title', 'anime__japanese_title',
    )
    for row in rows:
        names = (row['private_chinese_title'], row['private_japanese_title']) if row['metadata_initialized'] else (
            row['anime__chinese_title'], row['anime__japanese_title'])
        if any(normalized_title(name) in expected for name in names if name):
            return True  # Confirmation only, NEVER a fuzzy automatic mapping.
    return False


def recommendation_identity(*, user, bangumi_id):
    """Authoritative IDs or validated private aliases align namespaces.

    A catalog ID is NOT an Anime PK or record PK. Legacy title matches can only
    signal a possible conflict; their metadata/IDs are never exposed or linked.
    """
    if not getattr(user, 'is_authenticated', False):
        raise NotFound('该作品不可用于推荐。')
    try:
        identity = clean_id(bangumi_id)
    except ValueError:
        raise NotFound('该作品不可用于推荐。') from None
    anime = safe_catalog().filter(bangumi_id=identity).first()
    if anime is None:
        raise NotFound('该作品不可用于推荐。')
    shared = Anime.objects.filter(bgm_id=identity).only('pk').first()
    from .resolution import resolved_records
    aligned = [row for row in resolved_records(user) if row['resolved_bangumi_id'] == identity]
    owned = aligned[0] if aligned else None
    # This deliberately conservative collision signal does not guess the
    # Japanese title/quarter or alter an old shared identity on a GET operation.
    possible_legacy = has_possible_legacy_record(user=user, anime=anime)
    # Protect the existing unique release identity without changing a row that
    # carries a different/absent authoritative Bangumi ID.
    quarter = re.fullmatch(r'(\d{4})-(0?[147]|10)', anime.season)
    seasons = {anime.season}
    if quarter:
        seasons.add(f'{quarter[1]}-{int(quarter[2])}')
    release_conflict = Anime.objects.filter(
        chinese_title=anime.title_zh, japanese_title=anime.title_jp,
        airing_period__in=seasons,
    ).exclude(bgm_id=identity).exists()
    conflicts = journal_field_conflicts(anime)
    return {
        'bangumi_id': identity,
        'anime_id': owned['anime_id'] if owned else shared.pk if shared else None,
        'record_id': owned['pk'] if owned else None,
        'watch_status': owned['watch_status'] if owned else None,
        'mapping_status': 'aligned_alias' if owned and owned['anime__bgm_id'] != identity else 'identity_conflict' if release_conflict else 'aligned' if shared else 'unlinked',
        'requires_identity_confirmation': False if owned else possible_legacy or release_conflict,
        'journal_compatible': not conflicts,
        'field_conflicts': conflicts,
    }
