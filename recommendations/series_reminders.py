"""12E: explicit one-hop follow-ups from completed own records, never a watch guide."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .browsing import catalog_revision
from .content_scope import catalog_for_scope, ensure_scope
from .engine import RecommendationUnavailable
from .exploration_contract import enabled as exploration_enabled
from .feedback import feedback_snapshot
from .quality_readonly import readonly_snapshot
from .resolution import resolved_records
from .series_exploration import graph, enabled as graph_enabled, fact_payload
from .series_models import SeriesReminderState, SeriesReminderDismissal, SeriesState
from .series_source import digest
from .series_store import SeriesConflict

VERSION = 'recommendations.series.reminders.v1'
POLICY = 'series-reminders.completed-direct.v1'
LIMIT = 6


def enabled(user=None):
    from .series_rollout import available
    return (graph_enabled() and available(user) and exploration_enabled(user)
            and getattr(settings, 'RECOMMENDATION_SERIES_REMINDERS_ENABLED', False) is True)


def require_enabled(user):
    if not enabled(user):
        raise RecommendationUnavailable('续作提醒尚未开放。')


def snapshot(user, scope):
    require_enabled(user)
    allowed = set(catalog_for_scope(scope).filter(nsfw=True).values_list('bangumi_id', flat=True)) if scope['content_scope'] == 'include_adult' else set()
    records = resolved_records(user, allowed_adult_ids=allowed, include_profile=False, max_rows=5000)
    if not records.schema_available:
        raise RecommendationUnavailable('手账身份服务尚未就绪。')
    completed = {row['resolved_bangumi_id'] for row in records if row['resolved_bangumi_id'] and row['watch_status'] == 'completed'}
    # Also exclude stale/conflicting aliases: absence of a confirmed link is not unseen.
    owned = {identity for row in records for identity in (row['anime__bgm_id'], row['recommendation_identity__catalog_anime__bangumi_id']) if identity}
    feedback = feedback_snapshot(user)
    hidden = set(feedback['hidden_ids'])
    data = graph(completed, scope)
    fresh = set()
    for identity, head in data['heads'].items():
        if head.current and head.current.source_at >= timezone.now() - timedelta(days=45):
            fresh.add(identity)
    continuations = {}
    for fact in data['facts']:
        if fact['source_id'] not in fresh:
            continue
        if fact['kind'] == 'sequel':
            previous, following = fact['source_id'], fact['target_id']
        elif fact['kind'] == 'prequel':
            previous, following = fact['target_id'], fact['source_id']
        else:
            continue
        continuations.setdefault((previous, following), []).append(fact)
    groups = {}
    for (previous, following), facts in sorted(continuations.items()):
        if ((following, previous) in continuations or previous not in completed
                or following in owned or previous in hidden or following in hidden):
            continue
        groups.setdefault(following, []).extend({'completed_id': previous, **fact_payload(fact, data['catalog'])} for fact in facts)
    # Reciprocal source assertions corroborate one completed work, not two reasons.
    for identity, facts in groups.items():
        unique = {}
        for fact in sorted(facts, key=lambda row: (row['completed_id'], row['kind'] != 'sequel', row['origin'] != 'manual')):
            unique.setdefault(fact['completed_id'], fact)
        groups[identity] = list(unique.values())
    dismissals = set(SeriesReminderDismissal.objects.filter(user=user, hidden=True).values_list('bangumi_id', flat=True))
    state = SeriesReminderState.objects.filter(user=user).first()
    ordered = sorted(groups, key=lambda identity: (data['nodes'][identity].release_date or '9999', identity))
    visible = [identity for identity in ordered if identity not in dismissals]
    results = []
    for identity in visible[:LIMIT]:
        node, row = data['nodes'][identity], data['catalog'][identity]
        results.append({'bangumi_id': identity, 'title_zh': row.title_zh, 'title_jp': row.title_jp,
            'release_date': node.release_date, 'release_format': node.release_format,
            'record_state': 'no_confirmed_record', 'facts': groups[identity][:2],
            'more_sources': len(groups[identity]) > 2})
    fingerprint = digest([POLICY, data['fingerprint'], catalog_revision(),
        sorted(completed), sorted(owned), sorted(hidden), sorted(dismissals), groups,
        state.version if state else 0, state.collapsed if state else False])
    return {'contract_version': VERSION, 'policy_version': POLICY, 'fingerprint': fingerprint,
        'version': state.version if state else 0, 'collapsed': state.collapsed if state else False,
        'limit': LIMIT, 'count': len(visible), 'hidden_count': len(set(ordered) & dismissals),
        'truncated': len(visible) > LIMIT, 'results': results,
        'record_note': '仅依据本人已看完的已确认记录；无已确认记录不代表没看过。发行日期不代表已播出或可观看。'}


def read(user, scope):
    with readonly_snapshot():
        data = snapshot(user, scope)
    data['context'] = signing.dumps({'owner': user.pk, 'scope': scope['scope_revision'], 'fingerprint': data['fingerprint']}, salt=VERSION, compress=True)
    data['expires_at'] = int(timezone.now().timestamp()) + 900
    return data


def verify(user, scope, data):
    # Recheck own records, feedback, node classification, source age and presentation choices.
    with readonly_snapshot():
        current = snapshot(user, scope)
    if data['fingerprint'] != current['fingerprint']:
        raise SeriesConflict('后续提醒依据已变化，请重新读取。')


@transaction.atomic
def mutate(user, scope, changes):
    require_enabled(user)
    if not get_user_model().objects.select_for_update().filter(pk=user.pk, is_active=True).exists():
        raise NotFound('账号不可用。')
    list(SeriesState.objects.select_for_update().filter(pk=1))
    ensure_scope(user, scope)
    current = snapshot(user, scope)
    try:
        binding = signing.loads(changes['context'], salt=VERSION, max_age=900)
    except signing.BadSignature:
        raise SeriesConflict('提醒上下文已过期，请重新读取。') from None
    if binding != {'owner': user.pk, 'scope': scope['scope_revision'], 'fingerprint': current['fingerprint']} or changes['expected_version'] != current['version']:
        raise SeriesConflict('提醒或本人记录已变化，请重新读取后操作。')
    action = changes['action']
    state, _ = SeriesReminderState.objects.get_or_create(user=user)
    if state.version >= 2147483647:
        raise RecommendationUnavailable('提醒版本已达到上限。')
    if action == 'collapse':
        state.collapsed = changes['collapsed']
    elif action == 'hide':
        if changes['bangumi_id'] not in {row['bangumi_id'] for row in current['results']}:
            raise SeriesConflict('作品已不在当前提醒中，请重新读取。')
        SeriesReminderDismissal.objects.update_or_create(user=user, bangumi_id=changes['bangumi_id'], defaults={'hidden': True})
    else:
        SeriesReminderDismissal.objects.filter(user=user, hidden=True).update(hidden=False)
    state.version += 1
    state.save(update_fields=['version', 'collapsed'])
    return {'contract_version': VERSION, 'action': action, 'version': state.version, 'saved': True}
