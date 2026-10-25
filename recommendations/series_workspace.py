"""12C: bounded own states and public details, independent of recommendation evidence."""
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import NotFound

from .content_scope import catalog_for_scope, ensure_scope
from .engine import RecommendationUnavailable
from .quality_readonly import readonly_snapshot
from .resolution import resolved_records
from .serializers import RecommendationPublicAnimeSerializer
from .series_models import SeriesState, SeriesNode
from .series_read import read_relations, verify_response, require_enabled
from .want import add_want, WantConflict

WORKSPACE_VERSION = 'recommendations.series.workspace.v1'
DETAIL_VERSION = 'recommendations.series.detail.v1'
RECORD_VERSION = 'recommendations.series.record.v1'
PRIORITY = {'completed': 4, 'watching': 3, 'plan': 2, 'on_hold': 1}


def own_states(user, ids, scope):
    allowed = set(catalog_for_scope(scope).filter(bangumi_id__in=ids, nsfw=True).values_list('bangumi_id', flat=True))
    records = resolved_records(user, allowed_adult_ids=allowed, include_profile=False,
                               bangumi_ids=ids, max_rows=5000)
    if not records.schema_available:
        raise RecommendationUnavailable('作品身份服务尚未就绪。')
    result = {}
    for identity in ids:
        linked = [row for row in records if row['resolved_bangumi_id'] == identity]
        owned = max(linked, key=lambda row: (PRIORITY.get(row['watch_status'], 0), -row['pk']), default=None)
        ambiguous = any(row['resolved_bangumi_id'] is None and identity in (
            row['anime__bgm_id'], row['recommendation_identity__catalog_anime__bangumi_id']) for row in records)
        result[identity] = {'watch_status': owned['watch_status'] if owned else None,
                            'record_state': 'linked' if owned else 'needs_review' if ambiguous else 'no_confirmed_record'}
    return result


def workspace(user, source_id, scope, context=None):
    with readonly_snapshot():
        data = read_relations(user, source_id, scope, context, snapshot=False)
        return with_states(data, user, scope)


def with_states(data, user, scope):
    source_id = data['selected_seed']['bangumi_id']
    states = own_states(user, {source_id} | {row['bangumi_id'] for row in data['results']}, scope)
    data['contract_version'] = WORKSPACE_VERSION
    data['selected_seed'].update(states[source_id])
    for row in data['results']:
        row.update(states[row['bangumi_id']])
        row['can_add_from_series'] = row['record_state'] != 'needs_review' and row['watch_status'] is None
    data['record_note'] = '仅对应本人已确认身份的记录；无已确认记录不代表没看过。旧资料可能需要先核对身份。'
    return data


def member(data, target_id):
    if target_id != data['selected_seed']['bangumi_id'] and not any(row['bangumi_id'] == target_id for row in data['results']):
        raise NotFound('作品不在当前可见关联中。')


def detail(user, source_id, target_id, scope, context):
    with readonly_snapshot():
        data = with_states(read_relations(user, source_id, scope, context, snapshot=False), user, scope)
        member(data, target_id)
        anime = catalog_for_scope(scope).get(bangumi_id=target_id)
        item = dict(RecommendationPublicAnimeSerializer(anime, context={'content_scope': scope}).data)
        state = data['selected_seed'] if target_id == source_id else next(row for row in data['results'] if row['bangumi_id'] == target_id)
        item.update({key: state[key] for key in ('watch_status', 'record_state')})
        item['content_rating'] = 'adult' if anime.nsfw else 'non_adult'
        # Keep the complete checked set internal until the final revision check.
        data['contract_version'] = DETAIL_VERSION
        data['item'] = item
        return data


def record_reference(user, record_id, scope):
    allowed = catalog_for_scope(scope).filter(nsfw=True).values_list('bangumi_id', flat=True) if scope['content_scope'] == 'include_adult' else ()
    rows = resolved_records(user, allowed_adult_ids=allowed, include_profile=False, record_id=record_id)
    if not rows:
        raise NotFound('手账记录不存在。')
    if not rows.schema_available:
        raise RecommendationUnavailable('作品身份服务尚未就绪。')
    row = rows[0]
    return {'contract_version': RECORD_VERSION, 'record_id': record_id,
            'identity_state': row['identity_state'], 'bangumi_id': row['resolved_bangumi_id']}


@transaction.atomic
def want(user, source_id, target_id, scope, context):
    require_enabled()
    # Same ordering as journal writes/manual corrections: owner -> series -> catalog.
    if not get_user_model().objects.select_for_update().filter(pk=user.pk, is_active=True).exists():
        raise NotFound('账号不可用。')
    if not SeriesState.objects.select_for_update().filter(pk=1).exists():
        raise NotFound('系列资料尚未就绪。')
    data = read_relations(user, source_id, scope, context, snapshot=False)
    member(data, target_id)
    # Lock visible node metadata too; a direct classification update cannot race a write.
    ids = sorted({source_id} | {row['bangumi_id'] for row in data['results']})
    list(SeriesNode.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    from .content_scope import lock_scope
    locked_scope = lock_scope(user, scope)
    list(catalog_for_scope(locked_scope).select_for_update().filter(bangumi_id__in=ids).order_by('bangumi_id'))
    ensure_scope(user, scope)
    verify_response(data)
    if own_states(user, {target_id}, scope)[target_id]['record_state'] == 'needs_review':
        raise WantConflict('identity_confirmation_required', '记录身份存在冲突或已变化，请先核对旧记录；本次没有新增或合并。')
    require_enabled()
    return add_want(user=user, bangumi_id=target_id, scope_context=scope)
