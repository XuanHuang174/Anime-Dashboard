"""13A pure public-source contract. No Django, I/O, serving or ranking policy.

Source eligibility is NOT catalog eligibility, an NSFW grant, or completeness.
IDs are namespaced; names/aliases never merge persons, companies or characters.
"""
from collections import defaultdict
from datetime import datetime
import re
import unicodedata

CONTRACT_VERSION = 'recommendations.credits.assertion.v1'
MAPPING_VERSION = 'bangumi.anime-credits.v1'
PERSON_TYPES = {1: 'individual', 2: 'company', 3: 'group'}
CAST_TYPES = {0: 'CV', 1: '译配', 2: '演员', 3: '中配', 4: '日配', 5: '英配', 6: '韩配'}
# Exact cn labels in pinned bangumi/common/subject_staffs.yml. Only 67 is in scope.
STUDIO_POSITIONS = {67: '动画制作', 42: '製作', 63: '制作'}
COVERAGE_STATES = ('not_loaded', 'observed', 'empty_observed', 'partial', 'failed', 'stale')
MAX_ROWS = 5000


def integer(value, name='id', minimum=1):
    if type(value) is not int or not minimum <= value <= 2147483647:
        raise ValueError(f'Invalid {name}')
    return value


def label(value, maximum=500, allow_empty=False):
    if (not isinstance(value, str) or len(value) > maximum or value != value.strip()
            or not allow_empty and not value
            or any(unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError('Invalid public label')
    return value


def _shape(row, fields):
    if not isinstance(row, dict) or set(row) != set(fields):
        raise ValueError('Archive fields changed')


def _note(value):
    if (not isinstance(value, str) or len(value) > 2000
            or any(unicodedata.category(c).startswith('C') and c not in '\r\n\t' for c in value)):
        raise ValueError('Invalid source note')
    return value


def normalize_person(row):
    """Minimal explicit projection; infobox, biography, images and links omitted."""
    _shape(row, ('id', 'name', 'type', 'career'))
    identity, kind = integer(row['id']), integer(row['type'], 'person type')
    if kind not in PERSON_TYPES:
        raise ValueError('Unknown person type')
    career = row['career']
    if not isinstance(career, list) or len(career) > 20:
        raise ValueError('Invalid careers')
    career = sorted(set(label(item, 64) for item in career))
    return {'person_id': identity, 'name': label(row['name']), 'person_type': kind,
            'entity_kind': PERSON_TYPES[kind], 'careers': career}


def normalize_alias(person_id, name):
    """Caller must supply a source-bound alias, never an inferred transliteration."""
    return {'person_id': integer(person_id), 'name': label(name),
            'search_key': ' '.join(unicodedata.normalize('NFKC', name).casefold().split())}


def alias_index(aliases):
    """Ambiguous names retain all IDs; this is an index, not entity resolution."""
    if not isinstance(aliases, list) or len(aliases) > MAX_ROWS:
        raise ValueError('Alias sample exceeds bounds')
    result = defaultdict(set)
    for row in aliases:
        _shape(row, ('person_id', 'name'))
        alias = normalize_alias(**row)
        result[alias['search_key']].add(alias['person_id'])
    return {key: sorted(ids) for key, ids in sorted(result.items())}


def normalize_subject_character(row):
    _shape(row, ('subject_id', 'character_id', 'type', 'order'))
    return {'subject_id': integer(row['subject_id']), 'character_id': integer(row['character_id']),
            'role_type': integer(row['type'], 'character role', 0),
            'source_order': integer(row['order'], 'character order', 0)}


def _metadata(subject_id, person_id, subjects, persons):
    subject = subjects.get(subject_id)
    person = persons.get(person_id)
    reasons = []
    if subject is None:
        reasons.append('missing_subject')
    else:
        if integer(subject['id']) != subject_id:
            raise ValueError('Subject metadata identity mismatch')
        if type(subject['type']) is not int or subject['type'] not in (1, 2, 3, 4, 6):
            raise ValueError('Unknown subject type')
        if subject['type'] != 2:
            reasons.append('non_anime_subject')
    if person is None:
        reasons.append('missing_person')
    elif (integer(person['person_id']) != person_id
          or integer(person['person_type'], 'person type') not in PERSON_TYPES):
        raise ValueError('Person metadata identity/type mismatch')
    return person, reasons


def normalize_archive_staff(row, subjects, persons):
    _shape(row, ('subject_id', 'person_id', 'position', 'appear_eps'))
    subject_id, person_id = integer(row['subject_id']), integer(row['person_id'])
    position = integer(row['position'], 'staff position')
    # Source annotation, not a parsed episode list or proof of whole-series work.
    note = _note(row['appear_eps'])
    person, reasons = _metadata(subject_id, person_id, subjects, persons)
    if person and person['person_type'] != 2:
        reasons.append('not_company')
    if position != 67:
        reasons.append('position_outside_initial_scope')
    return {'kind': 'studio', 'subject_id': subject_id, 'person_id': person_id,
            'source_position': position, 'position_label': STUDIO_POSITIONS.get(position),
            'source_episode_note': note, 'source_eligible': not reasons, 'review_reasons': reasons}


def normalize_archive_cast(row, subjects, persons, characters, subject_characters):
    _shape(row, ('person_id', 'subject_id', 'character_id', 'type', 'summary'))
    subject_id, person_id, character_id = (integer(row[key]) for key in ('subject_id', 'person_id', 'character_id'))
    cast_type = integer(row['type'], 'cast type', 0)
    note = _note(row['summary'])
    person, reasons = _metadata(subject_id, person_id, subjects, persons)
    if person and person['person_type'] != 1:
        reasons.append('not_individual')
    character = characters.get(character_id)
    if character is None:
        reasons.append('missing_character')
    elif integer(character['id']) != character_id:
        raise ValueError('Character metadata identity mismatch')
    # Joining by character_id alone fabricates credits across adaptations/seasons.
    binding = subject_characters.get((subject_id, character_id))
    warnings = []
    if binding is None:
        reasons.append('missing_subject_character_binding')
    elif (binding['subject_id'], binding['character_id']) != (subject_id, character_id):
        raise ValueError('Subject-character metadata mismatch')
    elif binding['role_type'] not in (1, 2, 3):
        # The role classification is only a display annotation. An exact binding
        # still proves this subject's credit, without inventing a role label.
        warnings.append('unmapped_character_role')
    if cast_type != 0:
        reasons.append('cast_type_outside_initial_scope')
    return {'kind': 'cast', 'subject_id': subject_id, 'person_id': person_id,
            'character_id': character_id, 'source_cast_type': cast_type,
            'cast_label': CAST_TYPES.get(cast_type), 'source_note': note,
            'role_type': binding['role_type'] if binding else None,
            'source_order': binding['source_order'] if binding else None,
            'source_eligible': not reasons, 'review_reasons': reasons, 'annotation_warnings': warnings}


def parse_person_query(value):
    value = label(value, 120)
    normalized = unicodedata.normalize('NFKC', value)
    match = re.fullmatch(r'(?:https?://(?:bgm\.tv|bangumi\.tv|chii\.in)/person/|person:)([1-9][0-9]*)/?', normalized)
    if match:
        return {'person_id': integer(int(match[1])), 'name_query': None}
    if re.search(r'[:/\\?#@]', normalized) or normalized.isdecimal():
        raise ValueError('Use a person URL or person:ID; subject IDs are not person IDs')
    key = normalize_alias(1, normalized)['search_key']
    return {'person_id': None, 'name_query': key}


def validate_provenance(value):
    _shape(value, ('snapshot_version', 'source_reference', 'archive_sha256', 'observed_at', 'members'))
    version = value['snapshot_version']
    if not isinstance(version, str) or not re.fullmatch(r'dump-\d{4}-\d{2}-\d{2}\.\d{6}Z', version):
        raise ValueError('Invalid archive version')
    if value['source_reference'] != f'https://github.com/bangumi/Archive/releases/download/archive/{version}.zip':
        raise ValueError('Invalid archive reference')
    members = value['members']
    expected = {'subject.jsonlines', 'person.jsonlines', 'character.jsonlines', 'subject-persons.jsonlines',
                'subject-characters.jsonlines', 'person-characters.jsonlines'}
    if not isinstance(members, dict) or set(members) != expected:
        raise ValueError('Missing archive members')
    for digest in [value['archive_sha256'], *(row.get('sha256') for row in members.values() if isinstance(row, dict))]:
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Invalid source digest')
    for row in members.values():
        _shape(row, ('sha256', 'rows'))
        integer(row['rows'], 'member rows', 0)
    try:
        observed = datetime.fromisoformat(value['observed_at'])
        datetime.strptime(version, 'dump-%Y-%m-%d.%H%M%SZ')
    except (TypeError, ValueError):
        raise ValueError('Invalid source time') from None
    if observed.tzinfo is None:
        raise ValueError('Observation time requires timezone')
    return dict(value)


def contract_descriptor():
    return {'contract_version': CONTRACT_VERSION, 'mapping_version': MAPPING_VERSION,
            'serving_enabled': False, 'database_writes': False, 'changes_recommendations': False,
            'infers_preferences': False, 'merges_alias_identities': False,
            'initial_studio_position': 67, 'initial_cast_type': 0,
            'person_types': {str(key): value for key, value in PERSON_TYPES.items()},
            'staff_positions': {str(key): value for key, value in STUDIO_POSITIONS.items()},
            'cast_types': {str(key): value for key, value in CAST_TYPES.items()},
            'coverage_states': list(COVERAGE_STATES),
            'cast_identity': ['subject_id', 'character_id', 'person_id', 'source_cast_type'],
            'staff_identity': ['subject_id', 'person_id', 'source_position'],
            'scope_note': 'Source facts only; serving requires catalog, content scope and own-record checks.'}
