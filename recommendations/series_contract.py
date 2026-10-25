"""12A: pure source assertions, not series membership or a serving policy.

No Django, network, storage, title inference, private data or ranking changes.
An A -> B relation describes B relative to A. Never derive viewing order.
"""
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
import re
import unicodedata

from .normalization import clean_id

CONTRACT_VERSION = 'recommendations.series.assertion.v1'
MAPPING_VERSION = 'bangumi.anime-relations.v1'
SUBJECT_TYPES = frozenset((1, 2, 3, 4, 6))


@dataclass(frozen=True)
class RelationKind:
    code: int
    key: str
    label: str
    section: str


# Source: bangumi/common/subject_relations.yml, anime-to-anime ONLY.
# Sections are display hints, never transitive franchise/continuity membership.
KINDS = (
    RelationKind(1, 'adaptation', '改编', 'related'),
    RelationKind(2, 'prequel', '前传', 'chronology'),
    RelationKind(3, 'sequel', '续集', 'chronology'),
    RelationKind(4, 'summary', '总集篇', 'editions'),
    RelationKind(5, 'full_story', '全集', 'editions'),
    RelationKind(6, 'side_story', '番外篇', 'branches'),
    RelationKind(7, 'character', '角色出演', 'related'),
    RelationKind(8, 'same_setting', '相同世界观', 'related'),
    RelationKind(9, 'alternative_setting', '不同世界观', 'branches'),
    RelationKind(10, 'alternative_version', '不同演绎', 'branches'),
    RelationKind(11, 'spin_off', '衍生', 'branches'),
    RelationKind(12, 'parent_story', '主线故事', 'branches'),
    RelationKind(14, 'collaboration', '联动', 'related'),
    RelationKind(99, 'other', '其他', 'related'),
)
BY_CODE = {kind.code: kind for kind in KINDS}
BY_LABEL = {kind.label: kind for kind in KINDS}
# Deliberately do NOT invent parent/side/spin-off inverses or weak-link closure.
CHECKED_INVERSES = {'prequel': 'sequel', 'sequel': 'prequel',
                    'summary': 'full_story', 'full_story': 'summary'}
PROVENANCE_FIELDS = ('source_kind', 'source_reference', 'snapshot_version',
                     'observed_at', 'payload_sha256')


def validate_provenance(value):
    """Validate an observation envelope, not authenticity of its source bytes.

    Future importers must verify payload_sha256 against bytes BEFORE persisting.
    No user credentials or arbitrary source URLs are accepted by this contract.
    """
    if not isinstance(value, dict) or set(value) != set(PROVENANCE_FIELDS):
        raise ValueError('Invalid provenance fields')
    if any(not isinstance(part, str) or not part or len(part) > 500
           or any(unicodedata.category(c).startswith('C') for c in part) for part in value.values()):
        raise ValueError('Invalid provenance values')
    snapshot = value['snapshot_version']
    reference = value['source_reference']
    if value['source_kind'] == 'bangumi_archive':
        if (not re.fullmatch(r'dump-\d{4}-\d{2}-\d{2}\.\d{6}Z', snapshot)
                or reference != f'https://github.com/bangumi/Archive/releases/download/archive/{snapshot}.zip'):
            raise ValueError('Invalid archive provenance')
    elif value['source_kind'] == 'bangumi_api':
        match = re.fullmatch(r'https://api\.bgm\.tv/v0/subjects/([1-9][0-9]*)/subjects', reference)
        if not match:
            raise ValueError('Invalid API provenance')
        clean_id(match[1])
    else:
        raise ValueError('Unsupported source kind')
    try:
        observed = datetime.fromisoformat(value['observed_at'])
    except ValueError:
        raise ValueError('Invalid observation time') from None
    if observed.tzinfo is None or not re.fullmatch(r'[0-9a-f]{64}', value['payload_sha256']):
        raise ValueError('Observation requires timezone and SHA256')
    return dict(value)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum or value > 2_147_483_647:
        raise ValueError(f'{name} must be an integer in range')
    return value


def _subject_type(value):
    if value is not None and (type(value) is not int or value not in SUBJECT_TYPES):
        raise ValueError('Unknown subject type')
    return value


def _label(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 100 or any(unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError('Invalid relation label')
    return value


def _assertion(source_id, target_id, target_type, kind, raw_relation, order):
    source_id, target_id = clean_id(source_id), clean_id(target_id)
    if source_id == target_id:
        raise ValueError('Self relations are not usable assertions')
    target_type = _subject_type(target_type)
    key = (kind.key if kind else 'unknown') if target_type == 2 else (
        'unknown' if target_type is None else 'cross_media')
    return {
        'source_bangumi_id': source_id, 'target_bangumi_id': target_id,
        'target_subject_type': target_type,
        'kind': key,
        'source_relation': raw_relation, 'source_order': order,
        'section': 'unclassified' if key == 'unknown' else ('related' if key == 'cross_media' else kind.section),
        # Even an authenticated relation endpoint does not prove content eligibility.
        'anime_pair': target_type == 2,
        'requires_review': key == 'unknown',
    }


def normalize_archive_relation(row, subject_types):
    """Only verified anime-to-anime codes use the anime relation mapping.

    subject_types must come from the same archive, not catalog absence or titles.
    Unknown target metadata is retained for audit, never assumed anime/non-adult.
    Cross-media rows contain book/music/game codes even for an anime source.
    """
    required = {'subject_id', 'related_subject_id', 'relation_type', 'order'}
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError('Archive relation fields changed')
    source_id, target_id = clean_id(row['subject_id']), clean_id(row['related_subject_id'])
    if _subject_type(subject_types.get(source_id)) != 2:
        raise ValueError('Source must be a verified anime subject')
    code = _integer(row['relation_type'], 'relation_type', minimum=1)
    order = _integer(row['order'], 'order')
    return _assertion(source_id, target_id, subject_types.get(target_id), BY_CODE.get(code), code, order)


def normalize_api_relation(source_id, source_type, row):
    """API labels are exact source labels; upstream extra metadata is ignored.

    A successful empty response is not proof of no franchise. Callers must keep
    HTTP status, scope, provenance and freshness outside this individual row.
    """
    if _subject_type(source_type) != 2:
        raise ValueError('Source must be a verified anime subject')
    if not isinstance(row, dict) or not {'id', 'type', 'relation'} <= set(row):
        raise ValueError('API relation fields changed')
    label = _label(row['relation'])
    # Unlike archive missing metadata, API requires an explicit valid target type.
    if row['type'] is None:
        raise ValueError('API target type missing')
    return _assertion(source_id, row['id'], row['type'], BY_LABEL.get(label), label, None)


def inspect_assertions(assertions):
    """Audit only: preserve multiple assertions and flag issues, do not repair.

    Missing reciprocal links are a coverage signal, not a reason to erase data.
    Cycles concern source chronology, not a generated viewing-order graph.
    """
    pairs = defaultdict(set)
    seen = set()
    duplicate_count = unknown_count = cross_media_count = 0
    graph = defaultdict(set)
    for row in assertions:
        source, target, kind = row['source_bangumi_id'], row['target_bangumi_id'], row['kind']
        key = (source, target, kind, str(row['source_relation']))
        duplicate_count += key in seen
        seen.add(key)
        unknown_count += kind == 'unknown'
        cross_media_count += row['target_subject_type'] is not None and not row['anime_pair']
        pairs[source, target].add(kind)
        if row['anime_pair'] and kind in ('prequel', 'sequel'):
            before, after = (target, source) if kind == 'prequel' else (source, target)
            graph[before].add(after)
    missing, mismatches = [], []
    for (source, target), kinds in sorted(pairs.items()):
        for kind in sorted(kinds):
            expected = CHECKED_INVERSES.get(kind)
            if expected:
                reverse = pairs.get((target, source))
                record = [source, target, kind, expected]
                if reverse is None:
                    missing.append(record)
                elif expected not in reverse:
                    mismatches.append(record)
    nodes = set(graph) | {target for targets in graph.values() for target in targets}
    incoming = dict.fromkeys(nodes, 0)
    for targets in graph.values():
        for target in targets:
            incoming[target] += 1
    queue = deque(sorted(node for node, count in incoming.items() if count == 0))
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for target in sorted(graph.get(node, ())):
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return {'duplicate_count': duplicate_count, 'unknown_count': unknown_count,
            'cross_media_count': cross_media_count,
            'multiple_relation_pairs': [[source, target, sorted(kinds)]
                for (source, target), kinds in sorted(pairs.items()) if len(kinds) > 1],
            'missing_reciprocals': missing, 'reciprocal_mismatches': mismatches,
            'chronology_cycle': visited != len(nodes)}


def contract_descriptor():
    return {
        'contract_version': CONTRACT_VERSION, 'mapping_version': MAPPING_VERSION,
        'identity_namespace': 'bangumi', 'source_subject_type': 2,
        'direction': 'target_relative_to_source',
        'kinds': [asdict(kind) for kind in KINDS],
        'provenance_required': list(PROVENANCE_FIELDS),
        'coverage_states': ['not_loaded', 'observed', 'empty_observed', 'partial', 'failed', 'stale'],
        'classification_axes': ['release_format', 'relation_kind'],
        'serving_enabled': False, 'database_writes': False,
        'infers_series_membership': False, 'infers_watch_order': False,
        'changes_recommendations': False,
    }
