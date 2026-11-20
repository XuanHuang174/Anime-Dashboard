"""Canonical comma-separated selection; old all/single-type queries stay valid."""
from rest_framework import serializers

ANIME_TYPES = ('TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画')


def normalize_types(value):
    if value == 'all':
        return value
    if not isinstance(value, str):
        raise ValueError('作品类型必须是已支持类型的组合。')
    parts = value.split(',')
    if not parts or len(parts) > len(ANIME_TYPES) or len(set(parts)) != len(parts) or any(part not in ANIME_TYPES for part in parts):
        raise ValueError('作品类型组合包含未知、重复或空值。')
    return ','.join(sorted(parts))


def matches_type(kind, selection):
    return selection == 'all' or kind in selection.split(',')


def type_query(queryset, selection):
    return queryset if selection == 'all' else queryset.filter(status_type__in=selection.split(','))


class TypeSelectionField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=80, trim_whitespace=False, **kwargs)

    def to_internal_value(self, data):
        try:
            return normalize_types(super().to_internal_value(data))
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None
