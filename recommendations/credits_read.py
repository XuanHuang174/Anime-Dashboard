"""Scoped local-source reads. No upstream calls, profile reads, writes or shared cache.

Every public fact must belong to its exact current complete observation and its
own snapshot's entity endpoints. Private counts are computed after scope checks.
"""
from collections import defaultdict
from datetime import timedelta, datetime
import time
from django.core import signing
from django.db import connection
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from .credits_api_contract import (KINDS, TTL, PAGE_SIZE, SORT_VERSION, CreditsChanged,
    CreditsUnavailable, require_access, capabilities, rollout_revision)
from .credits_contract import parse_person_query, MAPPING_VERSION
from .credits_aliases import ALIAS_VERSION
from .credits_models import CreditsState, CreditsHead, CreditsSubject, CreditsPerson, CreditsAssertion
from .credits_source import digest, encoded
from .content_scope import catalog_for_scope
from .browsing import catalog_revision
from .browse_controls import season_year
from .score_filters import matches_score
from .evidence import _token
from .feedback import feedback_snapshot
from .resolution import resolved_records, LINK_FIELDS
from .serializers import RecommendationListAnimeSerializer
from .quality_readonly import readonly_snapshot

MAX_WORKS = 50000
MAX_FACTS = 10000
SUMMARY_LIMIT = 50
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SALT = 'recommendations.credits.cursor.v1'


def revision():
    return CreditsState.objects.filter(pk=1).values_list('revision', flat=True).first() or 0


def bounded(query, maximum):
    rows = list(query[:maximum + 1])
    if len(rows) > maximum:
        raise CreditsUnavailable('人物与制作资料超过本次读取上限。')
    return rows


def valid_facts(scope, kind):
    """Qualify before searching aliases/counting. Never join on a global character ID."""
    catalog = catalog_for_scope(scope).order_by()
    source = CreditsSubject.objects.filter(snapshot_id=OuterRef('observation__snapshot_id'),
                                           bangumi_id=OuterRef('observation__bangumi_id'))
    rating = catalog.filter(bangumi_id=OuterRef('observation__bangumi_id')).values('nsfw')[:1]
    head = CreditsHead.objects.filter(current_id=OuterRef('observation_id'), kind=kind,
                                      bangumi_id=OuterRef('observation__bangumi_id'))
    facts = CreditsAssertion.objects.filter(observation__kind=kind, source_eligible=True,
        observation__coverage='observed', observation__snapshot__source_at__gte=timezone.now() - timedelta(days=45),
        observation__snapshot__source_at__lte=timezone.now(),
        observation__snapshot__mapping_version=MAPPING_VERSION, observation__snapshot__alias_version=ALIAS_VERSION,
        observation__bangumi_id__in=Subquery(catalog.values('bangumi_id')),
        person__snapshot_id=F('observation__snapshot_id'), person__person_id=F('source_person_id'),
        person__person_type=2 if kind == 'studio' else 1, source_code=67 if kind == 'studio' else 0
    ).annotate(_current=Exists(head), _source_type=Subquery(source.values('subject_type')[:1]),
        _source_nsfw=Subquery(source.values('nsfw')[:1]), _catalog_nsfw=Subquery(rating)
    ).filter(_current=True, _source_type=2, _source_nsfw=F('_catalog_nsfw'))
    if kind == 'voice_actor':
        facts = facts.filter(character__snapshot_id=F('observation__snapshot_id'),
                              character__character_id=F('source_character_id'))
    return facts.order_by()


def entity_row(person, kind, count):
    return {'kind': kind, 'person_id': person.person_id, 'name': person.name,
            'work_count': count, 'alias_status': person.alias_status}


def coverage():
    return {'status': 'scoped_current_sources', 'complete_filmography': False,
            'detail': '仅展示当前内容范围、已收录目录及有效来源中确认的参与关系，不代表完整作品年表。'}


def base(version):
    return {'contract_version': 'recommendations.credits.' + version + '.v1',
            'credits_revision': revision(), 'coverage': coverage()}


def search(user, scope, kind, query):
    require_access(user, kind)
    try:
        parsed = parse_person_query(query)
    except ValueError:
        raise ValidationError({'q': '请输入人物姓名、已收录别名或完整 person 链接；不接受裸数字。'}) from None
    facts = valid_facts(scope, kind)
    people = CreditsPerson.objects.filter(pk__in=Subquery(facts.values('person_id')))
    if parsed['person_id'] is not None:
        people = people.filter(person_id=parsed['person_id'])
    else:
        people = people.filter(Q(aliases__search_key__contains=parsed['name_query']) | Q(name__icontains=query.strip()))
    people = list(people.order_by('person_id', '-snapshot__source_at', 'pk').distinct('person_id')[:16])
    selected = people[:15]
    counts = dict(facts.filter(source_person_id__in=[p.person_id for p in selected]).values('source_person_id')
        .annotate(n=Count('observation__bangumi_id', distinct=True)).values_list('source_person_id', 'n'))
    return {**base('search'), 'kind': kind, 'results': [entity_row(p, kind, counts[p.person_id]) for p in selected],
            'has_more': len(people) > 15, 'limit': 15}


def entity(scope, kind, person_id):
    facts = valid_facts(scope, kind).filter(source_person_id=person_id)
    person = CreditsPerson.objects.filter(pk__in=Subquery(facts.values('person_id'))).order_by('-snapshot__source_at', 'pk').first()
    if person is None:
        raise NotFound('当前范围没有可展示的人物或公司。')
    return entity_row(person, kind, facts.values('observation__bangumi_id').distinct().count()), facts


def entity_header(user, scope, kind, person_id):
    require_access(user, kind)
    row, _ = entity(scope, kind, person_id)
    return {**base('entity'), 'entity': row}


def evidence(facts, kind):
    """One source observation per work; multiple roles retained with bounded order."""
    ordered = sorted(facts, key=lambda f: (f.role_type if f.role_type in (1, 2, 3) else 4,
        f.source_order if f.source_order is not None else 2147483647, f.source_character_id, f.pk))
    first = ordered[0]
    return {'subject_id': first.observation.bangumi_id, 'person_id': first.source_person_id,
        'kind': kind, 'position_or_cast_type': first.source_code,
        'characters': [{'character_id': f.source_character_id, 'name': f.character.name,
            'role_type': f.role_type, 'source_order': f.source_order,
            'annotation_warnings': f.annotation_warnings, 'source_episode_note': f.source_note}
            for f in ordered if kind == 'voice_actor'],
        'source_episode_note': first.source_note, 'snapshot_version': first.observation.snapshot.snapshot_version,
        'source_at': first.observation.snapshot.source_at.isoformat()}


def subject_summary(user, scope, identity, *, expanded=False):
    if not catalog_for_scope(scope).filter(bangumi_id=identity).exists():
        raise NotFound('当前范围没有可展示的作品。')
    result = {**base('summary'), 'bangumi_id': identity, 'sections': {}}
    enabled = capabilities(user)
    limit = 5000 if expanded else SUMMARY_LIMIT
    for kind in KINDS:
        if not enabled[kind]:
            result['sections'][kind] = {'status': 'disabled', 'last_attempt_status': None,
                                       'results': [], 'count': 0, 'limit': 0, 'has_more': False}
            continue
        head = CreditsHead.objects.filter(bangumi_id=identity, kind=kind).select_related('current__snapshot', 'last_attempt').first()
        current = head.current if head else None
        status = current.coverage if current else (head.last_attempt.coverage if head and head.last_attempt else 'not_loaded')
        if current and (current.bangumi_id != identity or current.kind != kind or current.coverage not in ('observed', 'empty_observed')):
            raise CreditsUnavailable()
        if current:
            source = CreditsSubject.objects.filter(snapshot_id=current.snapshot_id, bangumi_id=identity).first()
            rating = catalog_for_scope(scope).filter(bangumi_id=identity).values_list('nsfw', flat=True).first()
            if (not source or source.subject_type != 2 or source.nsfw is not rating
                    or current.snapshot.mapping_version != MAPPING_VERSION or current.snapshot.alias_version != ALIAS_VERSION
                    or current.snapshot.source_at > timezone.now()):
                status = 'partial'
            elif current.snapshot.source_at < timezone.now() - timedelta(days=45):
                status = 'stale'
        facts = bounded(valid_facts(scope, kind).filter(observation__bangumi_id=identity)
            .select_related('person', 'character', 'observation__snapshot').order_by('source_person_id', 'pk'), 5000)
        if status in ('observed', 'empty_observed') and current.assertions.filter(source_eligible=True).count() != len(facts):
            status = 'partial'
        groups = defaultdict(list)
        for fact in facts:
            groups[fact.source_person_id].append(fact)
        # Preserve source role/order in both actor and character presentation.
        groups = sorted(groups.values(), key=lambda fs: (min((f.role_type if f.role_type in (1, 2, 3) else 4,
            f.source_order if f.source_order is not None else 2147483647) for f in fs), fs[0].source_person_id))
        result['sections'][kind] = {'status': status,
            'last_attempt_status': head.last_attempt.coverage if head and head.last_attempt else 'not_loaded',
            'count': len(groups), 'has_more': len(groups) > limit, 'limit': limit,
            'results': [{'person_id': fs[0].source_person_id, 'name': fs[0].person.name,
                         'kind': kind, 'evidence': evidence(fs, kind)} for fs in groups[:limit]]}
    return result


def own_records(user, scope, *, record_id=None):
    adult = catalog_for_scope(scope).filter(nsfw=True).values_list('bangumi_id', flat=True) if scope['content_scope'] == 'include_adult' else ()
    rows = resolved_records(user, allowed_adult_ids=set(adult), include_profile=False, max_rows=MAX_WORKS, record_id=record_id)
    if not rows.schema_available:
        raise CreditsUnavailable('手账身份服务尚未就绪。')
    return rows


def record_reference(user, scope, record_id):
    rows = own_records(user, scope, record_id=record_id)
    if not rows:
        raise NotFound('没有可读取的本人记录。')
    row = rows[0]
    return {**base('record'), 'record_id': row['pk'], 'identity_state': row['identity_state'],
            'bangumi_id': row['resolved_bangumi_id']}


def live_revision(user, scope, session):
    rows = own_records(user, scope)
    states, blocked = {}, set()
    priority = {'completed': 4, 'watching': 3, 'plan': 2, 'on_hold': 1}
    for row in rows:
        identity = row['resolved_bangumi_id']
        if identity:
            if priority.get(row['watch_status'], 0) > priority.get(states.get(identity), 0):
                states[identity] = row['watch_status']
        else:
            blocked.update(v for v in (row['anime__bgm_id'], row[LINK_FIELDS[0]]) if v)
    feedback = feedback_snapshot(user)
    # Includes identity fingerprints/metadata and status, but never scores/reviews/tags.
    owned_revision = digest(rows)
    value = _token(SALT + '.revision', [user.pk, session, user.get_session_auth_hash(), scope,
        revision(), catalog_revision(), owned_revision, feedback['version'], sorted(feedback['hidden_ids']),
        rollout_revision(user), SORT_VERSION])
    return value, states, blocked | set(feedback['hidden_ids'])


def period(row):
    """Catalog quarters/partial periods remain partial; never manufacture a day."""
    value = row.season.strip()
    year = season_year(value)
    if year is None:
        return None
    parts = value.split('-')
    return tuple(int(p) for p in parts)  # lexicographic; no padded fake date


def release_key(row, descending):
    value = period(row)
    if value is None:
        return (True, 0, True, 0, True, 0, row.bangumi_id)
    sign = -1 if descending else 1
    # Unknown month/day sort last within their known parent period. Sentinels
    # only order partial values and are never serialized as fabricated dates.
    return (False, sign * value[0], len(value) < 2, sign * value[1] if len(value) >= 2 else 0,
            len(value) < 3, sign * value[2] if len(value) >= 3 else 0, row.bangumi_id)


def load_cursor(token):
    if not isinstance(token, str) or not 1 <= len(token) <= 4096:
        raise ValidationError('分页上下文长度无效。')
    try:
        body = signing.loads(token, salt=SALT, max_age=TTL)
    except signing.SignatureExpired:
        raise CreditsChanged() from None
    except (signing.BadSignature, TypeError, ValueError):
        raise ValidationError('分页上下文无效。') from None
    if (not isinstance(body, dict) or set(body) != {'revision', 'kind', 'person_id', 'conditions', 'offset', 'expires', 'ids_hash'}
            or type(body['offset']) is not int or body['offset'] < 0 or body['offset'] % PAGE_SIZE
            or type(body['expires']) is not int or not int(time.time()) < body['expires'] <= int(time.time()) + TTL
            or not isinstance(body['conditions'], dict)
            or body['kind'] not in KINDS or type(body['person_id']) is not int
            or not 0 < body['person_id'] <= 2147483647
            or any(not isinstance(body[k], str) or len(body[k]) != 64 for k in ('revision', 'ids_hash'))):
        raise CreditsChanged()
    return body


def works(user, scope, kind, person_id, query, session):
    require_access(user, kind)
    rev, states, excluded = live_revision(user, scope, session)
    body = load_cursor(query['cursor']) if 'cursor' in query else None
    from .credits_api_contract import conditions
    options = conditions(query)
    if body and (body['revision'] != rev or body['kind'] != kind or body['person_id'] != person_id or body['conditions'] != options):
        raise CreditsChanged()
    try:
        row, facts = entity(scope, kind, person_id)
    except NotFound:
        if body:
            raise CreditsChanged() from None
        raise
    ids = facts.values('observation__bangumi_id').distinct()
    catalog = bounded(catalog_for_scope(scope).filter(bangumi_id__in=Subquery(ids)).defer('synopsis', 'source_cover_url').order_by('bangumi_id'), MAX_WORKS)
    selected = []
    for item in catalog:
        identity, year = item.bangumi_id, season_year(item.season)
        if identity in excluded or options['hide_recorded'] and identity in states:
            continue
        if options['view'] == 'completed' and states.get(identity) != 'completed':
            continue
        if options['type'] != 'all' and item.status_type != options['type'] or not matches_score(item.score, options):
            continue
        if ((options['year_from'] is not None and (year is None or year < options['year_from']))
                or (options['year_to'] is not None and (year is None or year > options['year_to']))):
            continue
        selected.append(item)
    if options['sort'] == 'score_desc':
        selected.sort(key=lambda x: (-x.score, x.bangumi_id))
    else:
        selected.sort(key=lambda x: release_key(x, options['sort'] == 'release_desc'))
    ids_hash = digest([item.bangumi_id for item in selected])
    offset = body['offset'] if body else 0
    if body and (body['ids_hash'] != ids_hash or offset >= len(selected)):
        raise CreditsChanged()
    deadline = scope.get('scope_valid_until')
    expires = body['expires'] if body else min(int(time.time()) + TTL,
        int(datetime.fromisoformat(deadline).timestamp()) if deadline else int(time.time()) + TTL)
    if expires <= int(time.time()):
        raise CreditsChanged()
    token_body = {'revision': rev, 'kind': kind, 'person_id': person_id, 'conditions': options,
                  'offset': offset, 'expires': expires, 'ids_hash': ids_hash}
    page = selected[offset:offset + PAGE_SIZE]
    page_facts = bounded(facts.filter(observation__bangumi_id__in=[i.bangumi_id for i in page])
        .select_related('observation__snapshot', 'character').order_by('observation__bangumi_id', 'pk'), MAX_FACTS)
    grouped = defaultdict(list)
    for fact in page_facts:
        grouped[fact.observation.bangumi_id].append(fact)
    result = []
    from .credits_workspace import binding, action_context
    bound = binding(user, scope, session, kind, person_id)
    for item in page:
        payload = dict(RecommendationListAnimeSerializer(item, context={'content_scope': scope,
            'validated_adult_ids': {i.bangumi_id for i in page if i.nsfw is True}}).data)
        result.append({**payload, 'watch_status': states.get(item.bangumi_id),
            'action_context': action_context(bound, item.bangumi_id, expires),
            'content_rating': 'adult' if item.nsfw is True else 'non_adult',
            'release_period': item.season if period(item) else '',
            'release_precision': 'catalog_period' if period(item) else 'unknown',
            'evidence': evidence(grouped[item.bangumi_id], kind)})
    return {**base('works'), 'entity': row, 'conditions': options, 'count': len(selected), 'results': result,
        'ordering': SORT_VERSION, 'context': signing.dumps(token_body, salt=SALT, compress=True), 'expires_at': expires,
        'next_cursor': signing.dumps({**token_body, 'offset': offset + PAGE_SIZE}, salt=SALT, compress=True)
            if offset + PAGE_SIZE < len(selected) else None}


def read(operation):
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise CreditsUnavailable('人物与制作需要独立的 PostgreSQL 只读快照。')
    with readonly_snapshot(timeout_ms=10000):
        data = operation()
        if len(encoded(data).encode('utf-8')) > MAX_RESPONSE_BYTES:
            raise CreditsUnavailable('人物与制作响应超过安全大小限制，请缩小条件后重试。')
        return data
