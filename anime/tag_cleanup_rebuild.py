"""Server-held rebuilding plans. Raw personal labels are never guessed into genres."""
import re
import unicodedata

from recommendations.features import MANAGEMENT_TAGS
from recommendations.taxonomy import AVAILABLE_TAGS, canonical_tag, TAXONOMY_VERSION

# Attributes are independent of the recommendation genre vocabulary.
ATTRIBUTES = {'TV', 'OVA', 'OAD', '剧场版', '泡面番', 'WEB', '短片', '动态漫画',
              'NSFW', 'R18', 'R-18', '原创', '动画原创', '漫改', '漫画改', '轻改',
              '轻小说改', '小说改', '游戏改'}
DETAIL_MARKERS = {'真百', '真百合', '轻百', '轻百合'}


def tag_key(name):
    return unicodedata.normalize('NFKC', name).strip().casefold()


def retention_reasons(record, saved):
    reasons = {}
    def add(name, reason):
        reasons.setdefault(tag_key(name), [])
        if reason not in reasons[tag_key(name)]:
            reasons[tag_key(name)].append(reason)
    for name, labels in saved.items():
        for reason in labels:
            add(name, reason)
    for name in record.custom_tags:
        add(name, '本人自定义')
    for row in record.tag_preferences.all():
        add(row.name, '该作品已有配色')
    for name in record.effective_anime_metadata()['base_tags'] + record.custom_tags:
        normalized = unicodedata.normalize('NFKC', name).strip()
        if normalized in ATTRIBUTES:
            add(name, '作品类型、来源或分级')
        if normalized in MANAGEMENT_TAGS or re.fullmatch(r'[二三四五六七八九十\d]+刷', normalized):
            add(name, '个人管理标记')
        if normalized in DETAIL_MARKERS:
            add(name, '个人细分标记')
    return reasons


def rebuild_plan(record, saved, source, keep_personal, decisions):
    metadata = record.effective_anime_metadata()
    base, custom = metadata['base_tags'], list(record.custom_tags)
    colors = sorted((row.name, row.color) for row in record.tag_preferences.all())
    details = {'before_custom': custom, 'before_colors': colors, 'after_custom': custom,
               'after_colors': colors, 'source': source, 'taxonomy_version': TAXONOMY_VERSION,
               'decisions': decisions, 'candidates': [], 'genres': [], 'attributes': []}
    if source.get('state') != 'linked' or not source.get('tags'):
        return dict(before=base, after=base, protected=custom, uncertain=[], changes=[], details=details,
                    personal_removed=0, status='no_source',
                    source_note='请先核对标准作品身份' if source.get('state') != 'linked' else '当前没有可用的标准题材，未作重建')
    genres = [tag for tag in AVAILABLE_TAGS if tag in source['tags']]
    reasons = retention_reasons(record, saved)
    retained, after_custom, protected, review, choices, changes = [], [], [], [], [], []
    def append(values, name):
        if name not in values:
            values.append(name)
    for raw in dict.fromkeys(base + custom):
        name = unicodedata.normalize('NFKC', raw).strip()
        labels = reasons.get(tag_key(raw), [])
        canonical = canonical_tag(name)
        attribute = name in ATTRIBUTES
        standard = raw == canonical and canonical in genres
        personal = bool([label for label in labels if label != '作品类型、来源或分级'])
        fixed = attribute or standard
        action = 'keep' if fixed or (personal and keep_personal) else 'remove'
        action = decisions.get(raw, action) if not fixed else 'keep'
        reason = '独立保留的作品属性' if attribute else '当前标准题材' if standard else (
            '不在当前标准题材中' if canonical else '非标准题材标签')
        choices.append({'name': raw, 'reasons': labels or [reason], 'personal': personal, 'fixed': fixed,
                        'action': action, 'in_custom': raw in custom, 'reason': reason})
        if not fixed:
            append(review, raw)
        if action == 'personal':
            append(after_custom, raw)
            append(protected, raw)
            if raw in base:
                changes.append({'kind': 'move', 'from': raw, 'to': raw, 'reason': '保留为个人标记'})
        elif action == 'keep':
            if raw in custom:
                append(after_custom, raw)
            elif not standard:
                append(retained, raw)
            if personal or attribute:
                append(protected, raw)
    # A retained fine-grained/personal alias already supplies this genre. Avoid double badges.
    supplied = {canonical_tag(name) for name in retained + after_custom}
    after = [tag for tag in genres if tag not in supplied]
    for name in retained:
        append(after, name)
    moved = {change['from'] for change in changes}
    for raw in base:
        if raw not in after and raw not in moved:
            changes.append({'kind': 'remove', 'from': raw, 'field': 'base', 'reason': '按本次标准题材方案替换'})
    for raw in custom:
        if raw not in after_custom:
            changes.append({'kind': 'remove', 'from': raw, 'field': 'custom', 'reason': '本次明确舍弃个人标记'})
    for name in after:
        if name not in base:
            changes.append({'kind': 'add', 'to': name, 'reason': '已确认作品的本地标准题材'})
    used = set(after + after_custom)
    prior = set(base + custom)
    after_colors = [pair for pair in colors if pair[0] in used or pair[0] not in prior]
    removed = {row['name'] for row in choices if row['personal'] and row['name'] not in used}
    details.update(after_custom=after_custom, after_colors=after_colors, candidates=choices, genres=genres,
                   attributes=[name for name in after + after_custom if unicodedata.normalize('NFKC', name).strip() in ATTRIBUTES])
    return dict(before=base, after=after, protected=protected, uncertain=review, changes=changes,
                details=details, personal_removed=len(removed), source_note='已读取已确认作品的本地标准题材',
                status='proposed' if [after, after_custom, after_colors] != [base, custom, colors] else 'unchanged')
