"""Read-only operator audit, never a recommendation/content-access grant."""
from collections import Counter
from datetime import timedelta
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from .models import RecommendationAnime
from .credits_models import CreditsHead, CreditsSubject, CreditsState, CreditsAssertion
from .credits_source import MAX_CATALOG, digest
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
                  'current__snapshot__source_at', 'last_attempt__coverage', 'last_attempt__diagnostics')
            .annotate(source_type=Subquery(source.values('subject_type')[:1]), source_nsfw=Subquery(source.values('nsfw')[:1]))[:2 * MAX_CATALOG + 1])
        if len(heads) > 2 * MAX_CATALOG:
            raise SourceError('来源指针超过覆盖审计上限。')
        head_map = {(head.bangumi_id, head.kind): head for head in heads}
        cutoff = timezone.now() - timedelta(days=45)
        segments, integrity, next_safe = {}, Counter(), {'studio': [], 'voice_actor': []}
        relations = Counter()
        current_heads = CreditsHead.objects.filter(bangumi_id__in=ids).values('current_id')
        all_facts = CreditsAssertion.objects.filter(observation_id__in=current_heads)
        raw_assertions = all_facts.count()
        assertion_count, missing, aliases, seen_people = 0, Counter(), Counter(), set()
        for fact in all_facts.filter(source_eligible=True).select_related('observation', 'person', 'character').only(
            'observation_id', 'person_id', 'character_id', 'source_person_id', 'source_character_id', 'source_eligible',
            'observation__kind', 'observation__snapshot_id', 'person__snapshot_id', 'person__person_id', 'person__person_type',
            'person__alias_status', 'character__snapshot_id', 'character__character_id').iterator(chunk_size=2000):
            assertion_count += 1
            if assertion_count > MAX_ASSERTIONS:
                raise SourceError('断言超过审计上限，不出具部分成功报告。')
            if fact.source_eligible:
                relations[fact.observation_id] += 1
                if (not fact.person_id or fact.person.snapshot_id != fact.observation.snapshot_id
                        or fact.person.person_id != fact.source_person_id
                        or fact.person.person_type != (2 if fact.observation.kind == 'studio' else 1)):
                    integrity['eligible_fact_missing_person'] += 1
                if fact.observation.kind == 'voice_actor' and (not fact.character_id
                        or fact.character.snapshot_id != fact.observation.snapshot_id or fact.character.character_id != fact.source_character_id):
                    integrity['eligible_fact_missing_character'] += 1
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
                        integrity['invalid_current_pointer'] += 1
                    if current and current.snapshot.source_at < cutoff:
                        status = 'stale'
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
        return {'contract_version': 'recommendations.credits.coverage.v1', 'schema_ready': True,
            'status': 'foundation_audit_not_release_acceptance', 'catalog_count': len(catalog), 'credits_revision': revision,
            'segments': segments, 'integrity_issues': dict(integrity), 'latest_voice_endpoint_issues': dict(missing),
            'current_assertions': raw_assertions, 'current_initial_eligible_assertions': assertion_count,
            'current_eligible_person_alias_statuses': dict(aliases),
            'next_safe_source_ids': next_safe,
            'fingerprint': digest([revision, catalog, [(h.bangumi_id, h.kind, h.current_id, h.last_attempt_id) for h in heads], segments]),
            'semantics': 'scoped-source-observation-coverage-not-full-filmography-or-approved-candidates',
            'adult_review_or_access_verified': False, 'private_records_read': False, 'production_writes': False}
