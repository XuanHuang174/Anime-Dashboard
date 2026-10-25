"""12A bounded, OFFLINE source-sample audit; never initializes Django or .env.

Usage: python -m recommendations.series_audit --input SAMPLE --output REPORT
Input is a curated public archive sample, not a production import instruction.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .normalization import clean_id
from .series_contract import (CONTRACT_VERSION, MAPPING_VERSION, inspect_assertions,
                              normalize_archive_relation, validate_provenance)
from .source_pipeline import SourceError, artifact_writer

MAX_SAMPLE_BYTES = 2 * 1024 * 1024
MAX_SAMPLE_ROWS = 5000


def audit_sample(sample):
    if not isinstance(sample, dict) or not {'provenance', 'subjects', 'relations', 'seed_ids'} <= set(sample):
        raise ValueError('Invalid source sample envelope')
    provenance = validate_provenance(sample['provenance'])
    if provenance['source_kind'] != 'bangumi_archive':
        raise ValueError('This audit accepts archive samples only')
    subjects, rows, seeds = sample['subjects'], sample['relations'], sample['seed_ids']
    if any(not isinstance(values, list) or len(values) > MAX_SAMPLE_ROWS
           for values in (subjects, rows, seeds)):
        raise ValueError('Source sample exceeds row bounds')
    types = {}
    for subject in subjects:
        if not isinstance(subject, dict) or not {'id', 'type'} <= set(subject):
            raise ValueError('Invalid subject metadata')
        identity = clean_id(subject['id'])
        if identity in types:
            raise ValueError('Duplicate subject metadata')
        # Full type validation also covers metadata not referenced by any row.
        if type(subject['type']) is not int or subject['type'] not in (1, 2, 3, 4, 6):
            raise ValueError('Invalid subject type')
        types[identity] = subject['type']
    seeds = [clean_id(identity) for identity in seeds]
    if not seeds or len(seeds) != len(set(seeds)) or any(types.get(identity) != 2 for identity in seeds):
        raise ValueError('Seeds must be distinct verified anime')
    assertions, errors = [], []
    for index, row in enumerate(rows):
        try:
            if row.get('subject_id') not in seeds:
                raise ValueError('Relation source outside selected seeds')
            assertions.append(normalize_archive_relation(row, types))
        except (ValueError, AttributeError) as error:
            errors.append({'row_index': index, 'reason': str(error)})
    anime = [row for row in assertions if row['anime_pair']]
    return {
        'contract_version': CONTRACT_VERSION, 'mapping_version': MAPPING_VERSION,
        'scope': 'curated_public_archive_sample_not_catalog_coverage',
        'provenance': provenance, 'seed_ids': seeds,
        'input_rows': len(rows), 'normalized_rows': len(assertions),
        'anime_pair_rows': len(anime), 'kind_counts': dict(sorted(Counter(row['kind'] for row in assertions).items())),
        'seed_ids_without_normalized_assertions': sorted(set(seeds) - {row['source_bangumi_id'] for row in assertions}),
        'row_errors': errors, 'diagnostics': inspect_assertions(assertions),
        'anime_assertions': anime,
        'coverage_note': 'Reciprocal diagnostics cover only selected source rows, not the full archive.',
        'viewing_order_generated': False, 'production_writes': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Input and output must differ')
    try:
        with args.input.open('rb') as handle:
            payload = handle.read(MAX_SAMPLE_BYTES + 1)
        if len(payload) > MAX_SAMPLE_BYTES:
            raise ValueError('Sample exceeds 2 MiB')
        report = audit_sample(json.loads(payload))
        report['sample_sha256'] = hashlib.sha256(payload).hexdigest()
        # No overwrite by default. Source JSON/ZIP/database paths remain untouched.
        with artifact_writer(args.output) as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    except (OSError, ValueError, SourceError) as error:
        parser.error(str(error))
    print(json.dumps({key: report[key] for key in ('input_rows', 'normalized_rows', 'anime_pair_rows')}, ensure_ascii=False))
    return 1 if report['row_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
