"""Versioned presentation settings only; never rescales scores or rankings."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import math

from django.core.exceptions import ValidationError

COLORS = ('slate', 'violet', 'red', 'amber', 'teal', 'blue', 'pink', 'green',
          'ink', 'orange', 'gold', 'deeporange', 'classic')
CARDS = ('completed', 'average', 'movie', 'ova', 'short', 'highlight')


def legacy_rating_preferences():
    return {'version': 1, 'star_max': 5, 'show_stars': True, 'tiers': [
        {'min': low, 'label': label, 'stars': stars, 'color': color, 'star_color': star_color, 'effect': effect}
        for low, label, stars, color, star_color, effect in (
            (9.9, '殿堂', 5, 'violet', 'violet', 'gradient'),
            (9.5, '极高推荐', 5, 'red', 'amber', 'plain'),
            (9.0, '强烈推荐', 5, 'amber', 'amber', 'plain'),
            (8.0, '推荐', 4, 'slate', 'amber', 'plain'),
            (7.0, '值得一看', 3, 'slate', 'amber', 'plain'),
            (6.0, '一般', 2, 'slate', 'amber', 'plain'),
            (0.1, '其他评分', 1, 'slate', 'amber', 'plain'),
            (0.0, '零分', 0, 'slate', 'amber', 'plain'),
        )], 'statistics': {'label': '极高推荐', 'min': 9.5, 'max': 10.0, 'cards': list(CARDS)}}


def restore_classic_appearance(value):
    result = deepcopy(value)
    for tier in result['tiers']:
        low = tier['min']
        tier.update(color='classic' if low >= 9.9 else 'red' if low >= 9.5 else 'orange' if low >= 9 else 'ink',
                    star_color='classic' if low >= 9.9 else 'deeporange' if low >= 9.5 else 'gold',
                    effect='gradient' if low >= 9.9 else 'plain')
    return result


def default_rating_preferences():
    return restore_classic_appearance(legacy_rating_preferences())


def score_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10 or not math.isfinite(value):
        raise ValidationError('评分边界必须是 0–10 的有限数字，最多一位小数。')
    number = Decimal(str(value))
    if not 0 <= number <= 10 or number * 10 != (number * 10).to_integral_value():
        raise ValidationError('评分边界必须在 0–10 之间，最多一位小数。')
    return float(number)


def exact_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValidationError('评分配置字段缺失或包含不支持的字段。')


def label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 16 or any(ord(c) < 32 for c in value):
        raise ValidationError('名称需为 1–16 个可见字符。')


def validate_rating_preferences(value):
    exact_keys(value, ('version', 'star_max', 'show_stars', 'tiers', 'statistics'))
    if type(value['version']) is not int or value['version'] != 1:
        raise ValidationError('不支持的评分配置版本。')
    if type(value['star_max']) is not int or not 1 <= value['star_max'] <= 10 or type(value['show_stars']) is not bool:
        raise ValidationError('星级上限为 1–10，星级显示需为布尔值。')
    tiers = value['tiers']
    if not isinstance(tiers, list) or not 1 <= len(tiers) <= 10:
        raise ValidationError('评分档位需为 1–10 档。')
    previous = 11
    for tier in tiers:
        exact_keys(tier, ('min', 'label', 'stars', 'color', 'star_color', 'effect'))
        low = score_number(tier['min'])
        if low >= previous:
            raise ValidationError('档位起点必须从高到低排列且不重复，上限由相邻档位联动。')
        previous = low
        label(tier['label'])
        if type(tier['stars']) is not int or not 0 <= tier['stars'] <= value['star_max']:
            raise ValidationError('点亮星数不得超过星级上限。')
        if tier['color'] not in COLORS or tier['star_color'] not in COLORS or tier['effect'] not in ('plain', 'gradient', 'glow'):
            raise ValidationError('包含不支持的配色或效果。')
    if previous != 0:
        raise ValidationError('最低档必须从 0 分开始，以覆盖全部评分。')
    stats = value['statistics']
    exact_keys(stats, ('label', 'min', 'max', 'cards'))
    label(stats['label'])
    if score_number(stats['min']) > score_number(stats['max']):
        raise ValidationError('统计下限不能高于上限。')
    cards = stats['cards']
    if not isinstance(cards, list) or not 1 <= len(cards) <= len(CARDS) or any(not isinstance(c, str) or c not in CARDS for c in cards) or len(set(cards)) != len(cards):
        raise ValidationError('请至少选择一张统计卡片，且不能重复。')


def normalized_rating_preferences(value):
    try:
        validate_rating_preferences(value)
        old = legacy_rating_preferences()['tiers']
        if len(value['tiers']) == len(old) and all(
            all(row[key] == previous[key] for key in ('min', 'color', 'star_color', 'effect'))
            for row, previous in zip(value['tiers'], old)
        ):
            return restore_classic_appearance(value)
        return deepcopy(value)
    except (ValidationError, TypeError, ValueError, InvalidOperation):
        return default_rating_preferences()
