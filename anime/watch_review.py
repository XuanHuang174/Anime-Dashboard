"""Private, live viewing reviews. Actual date evidence never comes from write timestamps."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import DatabaseError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException

from .models import UserAnimeRecord, WatchRound, WatchEvent
from .watch_history import date_bounds
from recommendations.quality_readonly import readonly_snapshot

VERSION = 'journal.watch-review.v1'
POLICY = 'actual-date-contained.v1'
SALT = 'journal.watch-review.snapshot.v1'
SECTIONS = ('works', 'started', 'completed', 'rewatch', 'activity', 'notes', 'uncertain', 'undated', 'month_unknown')
LIMITS = {'records': 10000, 'rounds': 50000, 'events': 100000}


class ReviewChanged(APIException):
    status_code = 409
    default_detail = {'code': 'watch_review_changed', 'detail': '回顾资料已变化或读取凭据已过期，请重新读取回顾。'}


class ReviewUnavailable(APIException):
    status_code = 503
    default_detail = {'code': 'watch_review_unavailable', 'detail': '回顾暂时无法完整读取，请稍后重试。'}


class ReviewQuery(serializers.Serializer):
    year = serializers.RegexField(r'^[1-9]\d{3}$', required=False)
    month = serializers.RegexField(r'^[1-9]\d{3}-\d{2}$', required=False)
    start = serializers.RegexField(r'^[1-9]\d{3}-\d{2}-\d{2}$', required=False)
    end = serializers.RegexField(r'^[1-9]\d{3}-\d{2}-\d{2}$', required=False)
    section = serializers.ChoiceField(choices=SECTIONS, default='works')
    page = serializers.IntegerField(min_value=1, max_value=1000000, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=20)
    snapshot = serializers.CharField(max_length=2048, required=False)

    def to_internal_value(self, data):
        if (set(data) - set(self.fields) or hasattr(data, 'getlist') and any(len(data.getlist(k)) != 1 for k in data)):
            raise serializers.ValidationError({'non_field_errors': ['查询参数未知或重复；回顾不继承手账筛选。']})
        if sum(('year' in data, 'month' in data, 'start' in data or 'end' in data)) > 1:
            raise serializers.ValidationError({'non_field_errors': ['年份、月份和自选区间不能同时填写。']})
        # DRF treats an optional empty HTML query value as omitted. An explicit
        # year must not silently fall back to the current monthly review.
        if data.get('year') == '':
            raise serializers.ValidationError({'year': ['请填写有效的四位年份。']})
        return super().to_internal_value(data)

    def validate(self, data):
        if ('start' in data) != ('end' in data):
            raise serializers.ValidationError('自选区间需要同时填写起止日期。')
        if 'year' in data:
            start, end = date_bounds(data['year'])
            mode = 'year'
        elif 'start' in data:
            start, end = date_bounds(data['start'])[0], date_bounds(data['end'])[0]
            if start > end:
                raise serializers.ValidationError('结束日期不能早于开始日期。')
            if (end - start).days >= 366:
                raise serializers.ValidationError('单次回顾最多选择 366 天。')
            mode = 'custom'
        else:
            month = data.get('month', timezone.localdate(timezone=ZoneInfo('Asia/Shanghai')).strftime('%Y-%m'))
            start, end = date_bounds(month)
            data['month'] = month
            mode = 'month'
        if data['section'] == 'month_unknown' and mode != 'year':
            raise serializers.ValidationError('月份待补充明细仅适用于年度回顾。')
        if data['page'] > 1 and not data.get('snapshot'):
            raise serializers.ValidationError('翻页需要沿用首次读取的回顾凭据。')
        data['period'] = {'mode': mode, 'start': start.isoformat(), 'end': end.isoformat(),
                          'timezone': 'Asia/Shanghai', 'end_inclusive': True}
        return data


def bounded(query, kind):
    result = list(query[:LIMITS[kind] + 1])
    if len(result) > LIMITS[kind]:
        raise ReviewUnavailable('本账号的回顾资料超出单次完整读取上限，未返回不完整统计。')
    return result


def attribution(value, first, last):
    if not value:
        return 'undated', None
    lower, upper = date_bounds(value)
    bounds = {'start': lower.isoformat(), 'end': upper.isoformat()}
    if first <= lower and upper <= last:
        return 'definite', bounds
    if lower <= last and upper >= first:
        return 'uncertain', bounds
    return 'outside', bounds


def precision(value):
    return {4: 'year', 7: 'month', 10: 'day'}.get(len(value), 'unknown')


def read_source(user):
    records = bounded(UserAnimeRecord.objects.filter(user=user).select_related('anime').only(
        'pk', 'anime_id', 'metadata_initialized', 'personal_score', 'watch_status', 'watch_revision', 'watch_history_base',
        'private_chinese_title', 'private_japanese_title', 'private_airing_period', 'private_poster_url', 'private_poster_image',
        'anime__chinese_title', 'anime__japanese_title', 'anime__airing_period', 'anime__poster_url', 'anime__poster_image')
        .order_by('pk'), 'records')
    rounds = bounded(WatchRound.objects.filter(record__user=user).order_by('record_id', 'number').values(
        'record_id', 'number', 'is_current', 'voided', 'origin', 'status', 'watched_eps', 'total_eps',
        'started_on', 'completed_on', 'rating', 'rating_origin', 'note'), 'rounds')
    # Read only the event state needed for attribution, not entire private snapshots.
    events = bounded(WatchEvent.objects.filter(record__user=user).order_by('record_id', 'revision').values(
        'record_id', 'revision', 'kind', 'source', 'occurred_on', 'note', 'recorded_at',
        'before__record__watch_status', 'after__record__watch_status',
        'before__record__watched_eps', 'after__record__watched_eps',
        'before__round__number', 'after__round__number'), 'events')
    return records, rounds, events


def work_payload(record, request):
    source, prefix = (record, 'private_') if record.metadata_initialized else (record.anime, '')
    image = getattr(source, prefix + 'poster_image')
    cover = image.url if image else getattr(source, prefix + 'poster_url')
    if image and request:
        cover = request.build_absolute_uri(cover)
    return {'record_id': record.pk, 'title': getattr(source, prefix + 'chinese_title'),
        'original_title': getattr(source, prefix + 'japanese_title'),
        'airing_period': getattr(source, prefix + 'airing_period'), 'poster_url': cover,
        'current_score': str(record.personal_score) if record.personal_score is not None else None,
        'current_watch_status': record.watch_status, 'metadata_basis': 'current_journal'}


def build_facts(records, rounds, events, period):
    first, last = date.fromisoformat(period['start']), date.fromisoformat(period['end'])
    grouped = defaultdict(list)
    for row in rounds:
        grouped[row['record_id']].append(row)
    facts, valid_rounds = [], {}

    def add(record_id, kind, actual_date, number, key, **extra):
        attribution_state, bounds = attribution(actual_date, first, last)
        facts.append({'id': key, 'record_id': record_id, 'kind': kind, 'round_number': number,
            'actual_date': actual_date, 'date_precision': precision(actual_date), 'date_bounds': bounds,
            'attribution': attribution_state, **extra})

    for record in records:
        rows = grouped[record.pk]
        if not rows:
            # Match history's virtual first round, without materializing it on GET.
            rows = [{'number': 1, 'voided': False, 'status': record.watch_status,
                'started_on': '', 'completed_on': '', 'rating': None, 'rating_origin': '', 'note': ''}]
        valid = [row for row in rows if not row['voided']]
        for index, row in enumerate(valid):
            number = row['number']
            valid_rounds[(record.pk, number)] = index > 0
            common = {'is_rewatch': index > 0, 'note': row['note'], 'date_source': 'effective_round'}
            if row['started_on'] or row['status'] in ('watching', 'on_hold', 'completed'):
                add(record.pk, 'started', row['started_on'], number, f'{record.pk}:round:{number}:started', **common)
            # An old completed_on left on a corrected unfinished round is not a completion.
            if row['status'] == 'completed':
                add(record.pk, 'completed', row['completed_on'], number, f'{record.pk}:round:{number}:completed',
                    rating=str(row['rating']) if row['rating'] is not None else None,
                    rating_origin=row['rating_origin'], **common)

    undone = {(e['record_id'], e['revision'] - 1) for e in events if e['kind'] == 'undo'}
    for event in events:
        rid, revision, kind = event['record_id'], event['revision'], event['kind']
        number = event['after__round__number']
        if kind in ('undo', 'update-round', 'start-rewatch') or (rid, revision) in undone or (rid, number) not in valid_rounds:
            continue
        if kind in ('note', 'memory'):
            fact_kind = kind
        elif kind in ('transition', 'change'):
            if event['note'].strip():
                # A dated thought accompanying a state edit remains a thought;
                # it cannot independently prove that viewing happened then.
                add(rid, 'note', event['occurred_on'], number, f'{rid}:event:{revision}:note',
                    is_rewatch=valid_rounds[(rid, number)], note=event['note'], date_source='explicit_event',
                    event_revision=revision, source=event['source'], source_kind=kind,
                    recorded_at=event['recorded_at'].isoformat())
            before, after = event['before__record__watch_status'], event['after__record__watch_status']
            # Milestones use the effective round dates above, including later corrections.
            # Do not replay old milestone snapshots as a second viewing occurrence.
            if before == after == 'watching' and event['after__record__watched_eps'] > event['before__record__watched_eps']:
                fact_kind = 'progress'
            elif {before, after} == {'watching', 'on_hold'}:
                fact_kind = 'resume' if after == 'watching' else 'pause'
            else:
                continue
        else:
            continue
        add(rid, fact_kind, event['occurred_on'], number, f'{rid}:event:{revision}',
            is_rewatch=valid_rounds[(rid, number)], note=event['note'], date_source='explicit_event',
            event_revision=revision, source=event['source'], recorded_at=event['recorded_at'].isoformat())
    return facts


def aggregate(facts):
    definite = [f for f in facts if f['attribution'] == 'definite']
    started = [f for f in definite if f['kind'] == 'started']
    completed = [f for f in definite if f['kind'] == 'completed']
    notes = [f for f in definite if f['kind'] in ('note', 'memory')]
    activity = [f for f in definite if f['kind'] not in ('note', 'memory')]
    ratings = [Decimal(f['rating']) for f in completed if f['rating'] is not None]
    summary = {'started_works': len({f['record_id'] for f in started}), 'started_rounds': len(started),
        'completed_works': len({f['record_id'] for f in completed}), 'completed_rounds': len(completed),
        'first_completed_rounds': sum(not f['is_rewatch'] for f in completed),
        'rewatch_started_rounds': sum(f['is_rewatch'] for f in started),
        'rewatch_completed_rounds': sum(f['is_rewatch'] for f in completed),
        'notes': len(notes), 'memories': sum(f['kind'] == 'memory' for f in notes),
        'viewed_works': len({f['record_id'] for f in activity}),
        'related_works': len({f['record_id'] for f in definite}), 'activity_facts': len(activity),
        'completed_round_rating': {'count': len(ratings),
            'average': str((sum(ratings) / len(ratings)).quantize(Decimal('0.01'))) if ratings else None,
            'basis': 'effective_completed_round', 'missing_count': len(completed) - len(ratings)}}
    coverage = {}
    for state in ('uncertain', 'undated'):
        subset = [f for f in facts if f['attribution'] == state]
        coverage[state] = {'facts': len(subset), 'works': len({f['record_id'] for f in subset})}
    coverage.update(undated_scope='owner_all_history', uncertain_scope='overlaps_selected_period',
        includes_undated_in_summary=False, includes_uncertain_in_summary=False)
    return summary, coverage


def annual_distribution(facts, year):
    """Partition definite annual facts once; year precision never invents a month.

    Annual work counts and rating averages still come from aggregate(facts),
    not sums/means of the monthly summaries. All buckets share that source read.
    """
    months = defaultdict(list)
    unknown = []
    for fact in facts:
        if fact['attribution'] != 'definite':
            continue
        if fact['date_precision'] == 'year':
            unknown.append(fact)
        else:
            months[fact['actual_date'][:7]].append(fact)
    unknown_summary, _ = aggregate(unknown)
    return {
        'year': year,
        'basis': 'effective_facts_by_actual_month',
        'months': [
            {'month': month, 'facts': len(months[month]), 'summary': aggregate(months[month])[0]}
            for month in (f'{year}-{number:02d}' for number in range(1, 13))
        ],
        'month_unknown': {'facts': len(unknown), 'works': len({f['record_id'] for f in unknown}),
                          'summary': unknown_summary},
    }


def select_entries(facts, works, section):
    if section == 'works':
        grouped = defaultdict(list)
        for fact in facts:
            if fact['attribution'] == 'definite': grouped[fact['record_id']].append(fact)
        return [{'entry_type': 'work', 'work': works[rid], 'matched_fact_count': len(rows),
            'matched_kinds': dict(Counter(f['kind'] for f in rows)),
            'latest_actual_date': max(rows, key=fact_sort)['actual_date']} for rid, rows in grouped.items()]
    if section == 'month_unknown':
        selected = [f for f in facts if f['attribution'] == 'definite' and f['date_precision'] == 'year']
    elif section in ('uncertain', 'undated'):
        selected = [f for f in facts if f['attribution'] == section]
    else:
        selected = [f for f in facts if f['attribution'] == 'definite' and (
            f['kind'] == section or section == 'rewatch' and f['kind'] == 'completed' and f['is_rewatch']
            or section == 'activity' and f['kind'] not in ('note', 'memory')
            or section == 'notes' and f['kind'] in ('note', 'memory'))]
    return [{'entry_type': 'fact', **f, 'work': works[f['record_id']]} for f in selected]


def fact_sort(fact):
    bounds = fact.get('date_bounds') or {}
    return (bounds.get('end', ''), bounds.get('start', ''), fact['record_id'], fact['id'])


def fingerprint(records, rounds, events):
    # Covers notes, metadata, clear, restore, direct model changes and round corrections.
    rows = []
    for record in records:
        source, prefix = (record, 'private_') if record.metadata_initialized else (record.anime, '')
        rows.append([record.pk, record.watch_revision, record.watch_history_base, record.personal_score, record.watch_status,
            *[str(getattr(source, prefix + key)) for key in ('chinese_title', 'japanese_title', 'airing_period', 'poster_url', 'poster_image')]])
    # File names, not expiring storage URLs, determine snapshot identity.
    source = {'records': rows, 'rounds': rounds, 'events': events}
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def verify_snapshot(user, token, period, revision):
    try:
        proof = signing.loads(token, salt=SALT, max_age=900)
    except signing.BadSignature:
        raise ReviewChanged() from None
    if proof != {'owner': user.pk, 'period': period, 'revision': revision, 'policy': POLICY}:
        raise ReviewChanged()


def read_review(user, params, request=None, *, entries=False):
    query = ReviewQuery(data=params)
    query.is_valid(raise_exception=True)
    options = query.validated_data
    if not entries and any(key in params for key in ('section', 'page', 'page_size')):
        raise serializers.ValidationError('摘要不接受分页或分区参数，请使用明细接口。')
    try:
        with readonly_snapshot():
            records, rounds, events = read_source(user)
            revision = fingerprint(records, rounds, events)
            if options.get('snapshot'):
                verify_snapshot(user, options['snapshot'], options['period'], revision)
            token = options.get('snapshot') or signing.dumps(
                {'owner': user.pk, 'period': options['period'], 'revision': revision, 'policy': POLICY}, salt=SALT)
            facts = build_facts(records, rounds, events, options['period'])
            summary, coverage = aggregate(facts)
            payload = {'contract_version': VERSION, 'policy_version': POLICY, 'period': options['period'],
                'generated_at': timezone.now().isoformat(), 'snapshot': token, 'summary': summary, 'coverage': coverage,
                'counts_scope': 'owner_all_records_selected_period', 'live': True, 'metadata_basis': 'current_journal'}
            if options['period']['mode'] == 'year':
                payload['annual'] = annual_distribution(facts, options['year'])
            if entries:
                works = {r.pk: {'record_id': r.pk} for r in records}
                selected = select_entries(facts, works, options['section'])
                if options['section'] == 'works':
                    selected.sort(key=lambda r: (date_bounds(r['latest_actual_date'])[1], r['work']['record_id']), reverse=True)
                else:
                    selected.sort(key=fact_sort, reverse=True)
                count, page, size = len(selected), options['page'], options['page_size']
                start = (page - 1) * size
                selected_page = selected[start:start + size]
                record_map = {r.pk: r for r in records}
                page_works = {rid: work_payload(record_map[rid], request)
                    for rid in {entry['work']['record_id'] for entry in selected_page}}
                payload.update(section=options['section'], count=count, page=page, page_size=size,
                    count_unit='works' if options['section'] == 'works' else 'facts',
                    next_page=page + 1 if start + size < count else None,
                    results=[{**entry, 'work': page_works[entry['work']['record_id']]} for entry in selected_page])
            return payload
    except DatabaseError:
        raise ReviewUnavailable() from None
