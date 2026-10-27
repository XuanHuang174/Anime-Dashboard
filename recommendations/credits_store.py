"""Atomic public-only publication. Input is local ArchivePlan, never HTTP JSON."""
from datetime import datetime
from django.db import transaction

from .credits_aliases import ALIAS_VERSION
from .credits_contract import MAPPING_VERSION, normalize_alias
from .credits_models import (CreditsState, CreditsSnapshot, CreditsSubject, CreditsPerson, CreditsCharacter,
    CreditsAlias, CreditsObservation, CreditsHead, CreditsAssertion)
from .credits_source import digest, MAX_BATCH, MAX_ROOT_ROWS
from .source_pipeline import SourceError


class CreditsConflict(SourceError):
    pass


def _entities(model, snapshot, rows, identity_key):
    existing = {getattr(row, identity_key): row for row in model.objects.filter(snapshot=snapshot,
        **{identity_key + '__in': [row[identity_key] for row in rows]})}
    pending = []
    for data in rows:
        identity = data[identity_key]
        values = {key: value for key, value in data.items() if key != 'names'}
        if identity in existing:
            if any(getattr(existing[identity], key) != value for key, value in values.items()):
                raise CreditsConflict('同一快照的实体内容不同，拒绝覆盖。')
        else:
            pending.append(model(snapshot=snapshot, **values))
    model.objects.bulk_create(pending, batch_size=500)
    existing.update({getattr(row, identity_key): row for row in pending})
    return existing, pending


@transaction.atomic
def publish_batch(prepared):
    body = {key: value for key, value in prepared.items() if key != 'fingerprint'}
    if digest(body) != prepared.get('fingerprint'):
        raise CreditsConflict('参与关系预检内容已变化。')
    snapshot = prepared['snapshot']
    if snapshot['mapping_version'] != MAPPING_VERSION or snapshot['alias_version'] != ALIAS_VERSION:
        raise CreditsConflict('参与关系映射或别名解析版本不兼容。')
    observations = prepared['observations']
    roots = {row['bangumi_id'] for row in observations}
    if (not 1 <= len(roots) <= MAX_BATCH or len(observations) != 2 * len(roots)
            or len({(row['bangumi_id'], row['kind']) for row in observations}) != len(observations)
            or any(row['kind'] not in ('studio', 'voice_actor') or len(row['facts']) > MAX_ROOT_ROWS for row in observations)):
        raise SourceError('发布批次超限或观察种类不完整。')
    CreditsState.objects.get_or_create(pk=1)
    state = CreditsState.objects.select_for_update().get(pk=1)
    known = CreditsSnapshot.objects.filter(snapshot_version=snapshot['snapshot_version'],
        mapping_version=MAPPING_VERSION, alias_version=ALIAS_VERSION).first()
    if known and (known.pk != snapshot['id'] or any(getattr(known, key) != value
            for key, value in snapshot.items() if key not in ('id', 'source_at'))
            or known.source_at != datetime.fromisoformat(snapshot['source_at'])):
        raise CreditsConflict('同版本归档来源不同，拒绝替换快照。')
    heads = {(row.bangumi_id, row.kind): row for row in CreditsHead.objects.filter(bangumi_id__in=roots)
             .select_related('last_attempt__snapshot', 'current__snapshot')}
    existing = {(row.bangumi_id, row.kind): row for row in CreditsObservation.objects.filter(snapshot_id=snapshot['id'], bangumi_id__in=roots)}
    pending = []
    for row in observations:
        key = row['bangumi_id'], row['kind']
        if key in existing:
            old = existing[key]
            if old.fingerprint != row['fingerprint'] or old.coverage != row['coverage'] or old.diagnostics != row['diagnostics']:
                raise CreditsConflict('同一快照观察内容不同，拒绝覆盖历史。')
            continue
        head = heads.get(key)
        if head and head.last_attempt and head.last_attempt.snapshot.source_at > datetime.fromisoformat(snapshot['source_at']):
            raise CreditsConflict('不能用旧归档回退此作品的参与关系。')
        pending.append(row)
    if not pending:
        return {'changed': False, 'revision': state.revision, 'published': 0, 'quarantined': 0, 'replayed': len(existing)}
    if known is None:
        known = CreditsSnapshot.objects.create(**{**snapshot, 'source_at': datetime.fromisoformat(snapshot['source_at'])})
    _entities(CreditsSubject, known, prepared['subjects'], 'bangumi_id')
    people, new_people = _entities(CreditsPerson, known, prepared['persons'], 'person_id')
    characters, _ = _entities(CreditsCharacter, known, prepared['characters'], 'character_id')
    alias_rows = []
    current_aliases = {}
    old_ids = [person.pk for person in people.values() if person not in new_people]
    for person_id, name, key in CreditsAlias.objects.filter(person_id__in=old_ids).values_list('person_id', 'name', 'search_key'):
        current_aliases.setdefault(person_id, set()).add((name, key))
    new_ids = {person.pk for person in new_people}
    for data in prepared['persons']:
        person = people[data['person_id']]
        aliases = {(name, normalize_alias(person.person_id, name)['search_key']) for name in data['names']}
        if person.pk in new_ids:
            alias_rows.extend(CreditsAlias(person=person, name=name, search_key=key) for name, key in sorted(aliases))
        elif aliases != current_aliases.get(person.pk, set()):
            raise CreditsConflict('同一快照别名内容不同，拒绝覆盖。')
    CreditsAlias.objects.bulk_create(alias_rows, batch_size=500)
    new_observations = [CreditsObservation(snapshot=known, **{key: value for key, value in row.items() if key != 'facts'}) for row in pending]
    CreditsObservation.objects.bulk_create(new_observations, batch_size=100)
    assertion_rows = []
    published = 0
    for data, observation in zip(pending, new_observations):
        for fact in data['facts']:
            assertion_rows.append(CreditsAssertion(observation=observation,
                person=people.get(fact['person_id']), character=characters.get(fact.get('character_id')),
                source_person_id=fact['person_id'], source_character_id=fact.get('character_id', 0),
                source_code=fact.get('source_position', fact.get('source_cast_type')),
                role_type=fact.get('role_type'), source_order=fact.get('source_order'),
                source_note=fact.get('source_episode_note', fact.get('source_note', '')),
                source_eligible=fact['source_eligible'], review_reasons=fact['review_reasons'],
                annotation_warnings=fact.get('annotation_warnings', [])))
        key = data['bangumi_id'], data['kind']
        head = heads.get(key) or CreditsHead(bangumi_id=key[0], kind=key[1])
        head.last_attempt = observation
        if data['coverage'] in ('observed', 'empty_observed'):
            head.current = observation; published += 1
        heads[key] = head
    CreditsAssertion.objects.bulk_create(assertion_rows, batch_size=500)
    affected = [heads[row['bangumi_id'], row['kind']] for row in pending]
    CreditsHead.objects.bulk_create([head for head in affected if head.pk is None], batch_size=100)
    CreditsHead.objects.bulk_update([head for head in affected if head.pk is not None], ('last_attempt', 'current'), batch_size=100)
    state.revision += 1; state.save(update_fields=('revision',))
    return {'changed': True, 'revision': state.revision, 'published': published,
            'quarantined': len(pending) - published, 'replayed': len(existing)}
