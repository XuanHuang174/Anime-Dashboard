from django.db import transaction

from .models import TAG_COLOR_KEYS, UserAnimeTag


def get_record_tag_colors(record):
    """Return only colors owned by this private record."""

    cached_tags = getattr(record, "_prefetched_objects_cache", {}).get(
        "tag_preferences"
    )
    tags = cached_tags if cached_tags is not None else record.tag_preferences.all()
    return {tag.name: tag.color for tag in tags}


def normalized_record_tag_colors(record, colors=None):
    """Build a complete private map for tags currently visible on the record."""

    source = colors if colors is not None else record.anime.tag_colors
    metadata = record.effective_anime_metadata()
    visible_tags = list(
        dict.fromkeys(
            [
                *(metadata["base_tags"] or []),
                *(record.custom_tags or []),
            ]
        )
    )
    return {
        tag: source[tag]
        for tag in visible_tags
        if tag in source and source[tag] in TAG_COLOR_KEYS
    }


@transaction.atomic
def replace_record_tag_colors(*, record, user, colors):
    """Replace a record's colors after verifying its authenticated owner."""

    owned_tags = UserAnimeTag.objects.filter(
        record=record,
        record__user=user,
    )
    if record.user_id != user.pk:
        raise PermissionError("不能修改其他用户的标签配色。")

    normalized = {
        name.strip(): color
        for name, color in colors.items()
        if name.strip() and color in TAG_COLOR_KEYS
    }
    owned_tags.delete()
    UserAnimeTag.objects.bulk_create(
        [
            UserAnimeTag(record=record, name=name, color=color)
            for name, color in normalized.items()
        ]
    )
    if hasattr(record, "_prefetched_objects_cache"):
        record._prefetched_objects_cache.pop("tag_preferences", None)
    return normalized


def initialize_record_tag_colors(*, record, user, colors=None):
    """Copy defaults into private storage when a record is first created."""

    private_colors = normalized_record_tag_colors(record, colors)
    return replace_record_tag_colors(
        record=record,
        user=user,
        colors=private_colors,
    )
