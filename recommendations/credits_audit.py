"""13A bounded OFFLINE sample audit, not an importer or production coverage audit.

python -m recommendations.credits_audit --input SAMPLE --output NEW_REPORT
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from .credits_contract import (CONTRACT_VERSION, MAPPING_VERSION, MAX_ROWS, integer, label,
    normalize_person, normalize_subject_character, normalize_archive_staff, normalize_archive_cast,
    validate_provenance, alias_index)
from .source_pipeline import SourceError, artifact_writer

MAX_BYTES = 2 * 1024 * 1024


def audit_sample(sample):
    if not isinstance(sample, dict) or sample.get('sample_version') != 'recommendations.credits.source-sample.v1':
        raise ValueError('Invalid credits sample')
    provenance = validate_provenance({key: sample.get(key) for key in
        ('snapshot_version', 'source_reference', 'archive_sha256', 'observed_at', 'members')})
    for key in ('subjects', 'persons', 'characters', 'staff', 'cast', 'subject_characters', 'seed_ids', 'aliases'):
        if not isinstance(sample.get(key), list) or len(sample[key]) > MAX_ROWS:
            raise ValueError(f'Invalid/bounded collection: {key}')
    subjects, persons, characters, bindings = {}, {}, {}, {}
    for row in sample['subjects']:
        identity = integer(row['id'])
        if identity in subjects or type(row['type']) is not int or row['type'] not in (1, 2, 3, 4, 6):
            raise ValueError('Invalid/duplicate subject metadata')
        if row.get('nsfw') is not None and type(row['nsfw']) is not bool:
            raise ValueError('Invalid subject classification')
        subjects[identity] = row
    for row in sample['persons']:
        person = normalize_person(row)
        if person['person_id'] in persons:
            raise ValueError('Duplicate person metadata')
        persons[person['person_id']] = person
    for row in sample['characters']:
        identity = integer(row['id'])
        if identity in characters:
            raise ValueError('Duplicate character metadata')
        label(row['name']); integer(row['role'], 'character entity role')
        characters[identity] = row
    seeds = [integer(value) for value in sample['seed_ids']]
    if not seeds or len(set(seeds)) != len(seeds) or any(subjects.get(value, {}).get('type') != 2 for value in seeds):
        raise ValueError('Seeds must be distinct verified anime subjects')
    for row in sample['subject_characters']:
        binding = normalize_subject_character(row)
        if binding['subject_id'] not in seeds:
            raise ValueError('Binding outside selected sources')
        key = binding['subject_id'], binding['character_id']
        if key in bindings:
            raise ValueError('Duplicate subject-character binding')
        bindings[key] = binding
    for alias in sample['aliases']:
        if alias.get('person_id') not in persons:
            raise ValueError('Alias without a source entity')
    aliases = alias_index(sample['aliases'])
    assertions, errors, identities = [], [], defaultdict(set)
    duplicates = 0
    for collection, normalizer in [('staff', normalize_archive_staff), ('cast', normalize_archive_cast)]:
        for index, row in enumerate(sample[collection]):
            try:
                if row.get('subject_id') not in seeds:
                    raise ValueError('Credit outside selected sources')
                args = (characters, bindings) if collection == 'cast' else ()
                fact = normalizer(row, subjects, persons, *args)
                key = ((fact['kind'], fact['subject_id'], fact['person_id'], fact['source_position'])
                    if collection == 'staff' else (fact['kind'], fact['subject_id'], fact['person_id'], fact['character_id'], fact['source_cast_type']))
                encoded = json.dumps(fact, sort_keys=True, ensure_ascii=False)
                duplicates += encoded in identities[key]
                identities[key].add(encoded)
                assertions.append(fact)
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                errors.append({'collection': collection, 'row_index': index, 'reason': str(error)})
    supported = [fact for fact in assertions if fact['source_eligible']]
    return {'contract_version': CONTRACT_VERSION, 'mapping_version': MAPPING_VERSION,
        'scope': 'curated_public_archive_sample_not_catalog_coverage', 'provenance': provenance,
        'input_rows': len(sample['staff']) + len(sample['cast']), 'normalized_rows': len(assertions),
        'eligible_source_rows': len(supported),
        'eligible_kind_counts': dict(sorted(Counter(fact['kind'] for fact in supported).items())),
        'cast_type_counts': dict(sorted(Counter(str(fact['source_cast_type']) for fact in assertions if fact['kind'] == 'cast').items())),
        'review_reason_counts': dict(sorted(Counter(reason for fact in assertions for reason in fact['review_reasons']).items())),
        'annotation_warning_counts': dict(sorted(Counter(reason for fact in assertions for reason in fact.get('annotation_warnings', [])).items())),
        'duplicate_rows': duplicates, 'conflicting_credit_keys': [list(key) for key, values in sorted(identities.items()) if len(values) > 1],
        'row_errors': errors, 'alias_collision_keys': [key for key, ids in aliases.items() if len(ids) > 1],
        'per_seed': {str(seed): dict(sorted(Counter(fact['kind'] for fact in supported if fact['subject_id'] == seed).items())) for seed in seeds},
        'eligible_source_assertions': supported,
        'coverage_note': 'Source eligibility is not catalog coverage, source completeness, NSFW approval or a serving grant.',
        'production_writes': False, 'serving_enabled': False, 'preferences_inferred': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Input and output must differ')
    try:
        with args.input.open('rb') as handle:
            payload = handle.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError('Sample exceeds 2 MiB')
        report = audit_sample(json.loads(payload))
        report['sample_sha256'] = hashlib.sha256(payload).hexdigest()
        with artifact_writer(args.output) as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write('\n')
    except (ValueError, OSError, SourceError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps({key: report[key] for key in ('input_rows', 'normalized_rows', 'eligible_kind_counts', 'row_errors')}, ensure_ascii=False))
    return 1 if report['row_errors'] or report['conflicting_credit_keys'] or report['duplicate_rows'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
