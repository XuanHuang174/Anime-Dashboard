"""Read-only operator audit, never a recommendation/content-access grant."""
from collections import Counter, defaultdict
from datetime import timedelta
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from .models import RecommendationAnime
from .credits_models import CreditsHead, CreditsSubject, CreditsState, CreditsAssertion
from .credits_source import MAX_CATALOG, digest
from .credits_contract import MAPPING_VERSION
from .credits_aliases import ALIAS_VERSION
from .quality_readonly import readonly_snapshot
from .source_pipeline import SourceError

MAX_ASSERTIONS = 1000000
REQUIRED_MIGRATION = '0016_credits_foundation'


def coverage_audit():
    with readonly_snapshot():
        if not MigrationRecorder.Migration.objects.filter(app='recommendations', name=REQUIRED_MIGRATION).exists():
            return {'schema_ready': False, 'missing_migration': REQUIRED_MIGRATION, 'production_writes': False}
        catalog = list(RecommendationAnime.objects.filter(bangumi_id__gt=0).order_by('bangumi_id')
                       .values_list('bangumi_id', 'nsfw')[:MAX_CATALOG + 1])
        if len(catalog) > MAX_CATALOG:
            raise SourceError('目录超过覆盖审计上限。')
        ids = {row[0] for row in catalog}
        source = CreditsSubject.objects.filter(snapshot_id=OuterRef('current__snapshot_id'), bangumi_id=OuterRef('bangumi_id'))
        heads = list(CreditsHead.objects.filter(bangumi_id__in=ids).select_related('current__snapshot', 'last_attempt')
            .only('bangumi_id', 'kind', 'current_id', 'last_attempt_id', 'current__coverage', 'current__kind', 'current__bangumi_id',
                  'current__snapshot__source_at', 'current__snapshot__mapping_version', 'current__snapshot__alias_version',
                  'current__snapshot__snapshot_version', 'last_attempt__coverage', 'last_attempt__diagnostics')
            .annotate(source_type=Subquery(source.values('subject_type')[:1]), source_nsfw=Subquery(source.values('nsfw')[:1]))[:2 * MAX_CATALOG + 1])
        if len(heads) > 2 * MAX_CATALOG:
            raise SourceError('来源指针超过覆盖审计上限。')
        head_map = {(head.bangumi_id, head.kind): head for head in heads}
        now = timezone.now()
        cutoff = now - timedelta(days=45)
        segments, integrity, next_safe = {}, Counter(), {'studio': [], 'voice_actor': []}
        integrity_by_kind = {'studio': Counter(), 'voice_actor': Counter()}
        def issue(kind, reason):
            integrity[reason] += 1
            if kind in integrity_by_kind: integrity_by_kind[kind][reason] += 1
        relations, invalid_observations = Counter(), set()
        ratings = dict(catalog)
        qualified = {head.current_id for head in heads if head.current and head.current.bangumi_id == head.bangumi_id
            and head.current.kind == head.kind and head.current.coverage in ('observed', 'empty_observed')
            and cutoff <= head.current.snapshot.source_at <= now
            and head.current.snapshot.mapping_version == MAPPING_VERSION and head.current.snapshot.alias_version == ALIAS_VERSION
            and (head.source_type, head.source_nsfw) == (2, ratings[head.bangumi_id])}
        entity_facts = {kind: defaultdict(Counter) for kind in ('studio', 'voice_actor')}
        import hashlib
        fact_hash = hashlib.sha256()
        current_heads = CreditsHead.objects.filter(bangumi_id__in=ids).values('current_id')
        all_facts = CreditsAssertion.objects.filter(observation_id__in=current_heads)
        raw_assertions = all_facts.count()
        assertion_count, missing, aliases, seen_people = 0, Counter(), Counter(), set()
        for fact in all_facts.filter(source_eligible=True).select_related('observation', 'person', 'character').only(
            'observation_id', 'person_id', 'character_id', 'source_person_id', 'source_character_id', 'source_eligible',
            'source_code', 'observation__kind', 'observation__snapshot_id', 'observation__bangumi_id',
            'person__snapshot_id', 'person__person_id', 'person__person_type', 'person__name',
            'person__alias_status', 'character__snapshot_id', 'character__character_id', 'character__name').order_by('pk').iterator(chunk_size=2000):
            assertion_count += 1
            if assertion_count > MAX_ASSERTIONS:
                raise SourceError('断言超过审计上限，不出具部分成功报告。')
            if fact.source_eligible:
                valid = True
                if (not fact.person_id or fact.person.snapshot_id != fact.observation.snapshot_id
                        or fact.person.person_id != fact.source_person_id
                        or fact.person.person_type != (2 if fact.observation.kind == 'studio' else 1)):
                    issue(fact.observation.kind, 'eligible_fact_missing_person')
                    valid = False
                if fact.observation.kind == 'voice_actor' and (not fact.character_id
                        or fact.character.snapshot_id != fact.observation.snapshot_id or fact.character.character_id != fact.source_character_id):
                    issue(fact.observation.kind, 'eligible_fact_missing_character')
                    valid = False
                if fact.observation.kind not in ('studio', 'voice_actor') or fact.source_code != (67 if fact.observation.kind == 'studio' else 0):
                    issue(fact.observation.kind, 'eligible_fact_wrong_source_code')
                    valid = False
                if not valid:
                    invalid_observations.add(fact.observation_id)
                else:
                    relations[fact.observation_id] += 1
                    if fact.observation_id in qualified and ratings[fact.observation.bangumi_id] is not None:
                        entity_facts[fact.observation.kind][fact.source_person_id][fact.observation.bangumi_id] += 1
                fact_hash.update(digest([fact.pk, fact.observation_id, fact.source_person_id, fact.source_character_id,
                    fact.source_code, fact.person_id, fact.character_id, valid,
                    fact.person.name if fact.person_id else None, fact.character.name if fact.character_id else None]).encode())
                if fact.person_id and fact.person_id not in seen_people:
                    seen_people.add(fact.person_id)
                    if len(seen_people) > 400000:
                        raise SourceError('当前参与实体版本超过覆盖审计上限。')
                    aliases[fact.person.alias_status] += 1
        for kind in ('studio', 'voice_actor'):
            segments[kind] = {}
            for group, rating in (('safe', False), ('adult', True), ('unknown', None)):
                selected = [identity for identity, nsfw in catalog if nsfw is rating]
                states, attempts, diagnostics, fresh, associated, initial_rows = Counter(), Counter(), Counter(), 0, 0, 0
                for identity in selected:
                    head = head_map.get((identity, kind))
                    current = head.current if head else None
                    status = current.coverage if current else 'not_loaded'
                    if current and (current.bangumi_id != identity or current.kind != kind or current.coverage not in ('observed', 'empty_observed')):
                        issue(kind, 'invalid_current_pointer')
                    if current and current.snapshot.source_at < cutoff:
                        status = 'stale'
                    elif current and current.pk not in qualified:
                        status = 'partial'
                    if current and current.pk in invalid_observations:
                        status = 'partial'
                    states[status] += 1
                    attempt = head.last_attempt if head else None
                    attempts[attempt.coverage if attempt else 'not_loaded'] += 1
                    if group == 'safe' and len(next_safe[kind]) < 100 and (status not in ('observed', 'empty_observed')
                            or attempt and attempt.coverage in ('partial', 'failed')):
                        next_safe[kind].append(identity)
                    if attempt:
                        diagnostics.update(attempt.diagnostics.get('issues', {}))
                        initial_rows += attempt.diagnostics.get('initial_scope_rows', 0)
                    if current and status in ('observed', 'empty_observed'):
                        if (head.source_type, head.source_nsfw) == (2, rating):
                            if (current.coverage == 'observed') != bool(relations[current.pk]):
                                issue(kind, 'observation_relation_semantics_mismatch')
                            else:
                                fresh += 1
                                associated += relations[current.pk] > 0
                segments[kind][group] = {'catalog_count': len(selected), 'coverage_states': dict(states),
                    'last_attempt_states': dict(attempts), 'latest_attempt_issue_counts': dict(diagnostics),
                    'fresh_complete_count': fresh, 'fresh_complete_percent': round(100 * fresh / len(selected), 3) if selected else None,
                    'works_with_initial_source_relations': associated, 'latest_attempt_initial_scope_rows': initial_rows,
                    'endpoint_issue_rates': {reason: round(diagnostics[reason] / initial_rows, 6) if initial_rows else None
                        for reason in ('missing_person', 'missing_character', 'missing_subject_character_binding')}}
                if kind == 'voice_actor':
                    for reason in ('missing_person', 'missing_character', 'missing_subject_character_binding'):
                        missing[reason] += diagnostics[reason]
        revision = CreditsState.objects.filter(pk=1).values_list('revision', flat=True).first() or 0
        capacity = {}
        for kind, people in entity_facts.items():
            capacity[kind] = {'entity_count': len(people),
                'maximum_entity_works': max((len(rows) for rows in people.values()), default=0),
                'maximum_possible_page_facts': max((sum(sorted(rows.values(), reverse=True)[:12]) for rows in people.values()), default=0),
                'largest_entities': [{'person_id': person, 'work_count': len(rows)} for person, rows in
                    sorted(people.items(), key=lambda row: (-len(row[1]), row[0]))[:5]]}
        return {'contract_version': 'recommendations.credits.coverage.v1', 'schema_ready': True,
            'status': 'foundation_audit_not_release_acceptance', 'catalog_count': len(catalog), 'credits_revision': revision,
            'segments': segments, 'integrity_issues': dict(integrity), 'latest_voice_endpoint_issues': dict(missing),
            'integrity_by_kind': {kind: dict(value) for kind, value in integrity_by_kind.items()},
            'current_assertions': raw_assertions, 'current_initial_eligible_assertions': assertion_count,
            'current_eligible_person_alias_statuses': dict(aliases),
            'read_capacity': capacity,
            'source_versions': sorted({head.current.snapshot.snapshot_version for head in heads if head.current}),
            'next_safe_source_ids': next_safe,
            'fingerprint': digest([revision, catalog, [(h.bangumi_id, h.kind, h.current_id, h.last_attempt_id,
                h.current.snapshot.mapping_version if h.current else None, h.current.snapshot.alias_version if h.current else None,
                h.current.snapshot.source_at.isoformat() if h.current else None,
                h.current.snapshot.snapshot_version if h.current else None)
                for h in sorted(heads, key=lambda h: (h.bangumi_id, h.kind))], segments, fact_hash.hexdigest(), capacity, dict(integrity)]),
            'semantics': 'scoped-source-observation-coverage-not-full-filmography-or-approved-candidates',
            'adult_review_or_access_verified': False, 'private_records_read': False, 'production_writes': False}
