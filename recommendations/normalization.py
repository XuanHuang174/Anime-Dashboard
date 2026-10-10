"""Shared pure cleaners; source preparation does not initialize Django."""

import math
import re


def text(value):
    return value.strip() if isinstance(value, str) else ''


def clean_id(value):
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


def clean_nsfw(value):
    # Unknown is never silently mislabeled as safe. Strings like "false" are
    # explicitly handled rather than bool("false") == True.
    if isinstance(value, bool) or value is None:
        return value
    if value in (0, '0', 'false', 'False'):
        return False
    if value in (1, '1', 'true', 'True'):
        return True
    return None
