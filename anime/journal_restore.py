"""Read-only restore impact preview and short-lived account/state-bound confirmation."""
import hashlib
import json

from django.core import signing
from rest_framework.exceptions import ValidationError

from .models import UserAnimeRecord, UserAnimeTag, UserProfile, WatchPlan, WatchPlanQueue


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def restore_binding(user, records, rating):
    return {'user': user.pk, 'file': fingerprint([records, rating]), 'state': fingerprint([
        list(UserAnimeRecord.objects.filter(user=user).order_by('pk').values()),
        list(UserAnimeTag.objects.filter(record__user=user).order_by('pk').values()),
        list(UserProfile.objects.filter(user=user).values('rating_preferences', 'rating_revision')),
        list(WatchPlan.objects.filter(record__user=user).order_by('record_id').values()),
        list(WatchPlanQueue.objects.filter(user=user).values('revision')),
    ])}


def restore_preview(user, records, rating):
    existing = list(UserAnimeRecord.objects.filter(user=user).select_related('anime').prefetch_related('watch_events', 'watch_rounds', 'watch_plan'))
    matched = set()
    summary = {'incoming_count': len(records), 'with_history': 0, 'events_in_file': 0,
               'rounds_in_file': 0, 'preserved_history': 0, 'new_baselines': 0,
               'replaced_history': 0, 'removed_count': 0, 'removed_history_events': 0}
    summary.update(plans_in_file=0, preserved_plans=0, cleared_plans=0, replaced_plans=0, removed_plans=0)
    for item in records:
        key = tuple(item['anime'][k] for k in ('chinese_title', 'japanese_title', 'airing_period'))
        private_matches = [row for row in existing if tuple(row.effective_anime_metadata()[k] for k in ('chinese_title', 'japanese_title', 'airing_period')) == key]
        if len(private_matches) > 1 and 'watch_history' not in item:
            raise ValidationError('旧备份中的作品对应多个私人记录，无法明确保留历程，请先使用完整新版备份。')
        if len(private_matches) > 1 and 'watch_plan' not in item:
            raise ValidationError('旧备份中的作品对应多个私人记录，无法明确保留观看计划。')
        candidates = private_matches or [row for row in existing if (row.anime.chinese_title, row.anime.japanese_title, row.anime.airing_period) == key]
        matched.update(row.pk for row in candidates)
        previous_plans = sum(getattr(row, 'watch_plan', None) is not None for row in candidates)
        if 'watch_plan' in item:
            if item['watch_plan'] is None: summary['cleared_plans'] += previous_plans
            else:
                summary['plans_in_file'] += 1
                summary['replaced_plans'] += previous_plans
        elif len(candidates) == 1:
            summary['preserved_plans'] += previous_plans
        if 'watch_history' in item:
            summary['with_history'] += 1
            summary['events_in_file'] += len(item['watch_history']['events'])
            summary['rounds_in_file'] += len(item['watch_history']['rounds'])
            summary['replaced_history'] += len(candidates)
        elif len(candidates) == 1:
            summary['preserved_history'] += 1
        else:
            summary['new_baselines'] += 1
    removed = [row for row in existing if row.pk not in matched]
    summary['removed_count'] = len(removed)
    summary['removed_history_events'] = sum(len(row.watch_events.all()) for row in removed)
    summary['removed_plans'] = sum(getattr(row, 'watch_plan', None) is not None for row in removed)
    summary['removed_titles'] = [row.effective_anime_metadata()['chinese_title'] for row in removed[:20]]
    summary['token'] = signing.dumps(restore_binding(user, records, rating), salt='journal-restore-a3', compress=True)
    return summary


def valid_restore_token(token, user, records, rating):
    try:
        return signing.loads(token, salt='journal-restore-a3', max_age=600) == restore_binding(user, records, rating)
    except signing.BadSignature:
        return False
