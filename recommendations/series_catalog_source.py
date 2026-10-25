"""One verified ZIP scan, per-source quarantine, bounded publication batches.

Public metadata only. No Django, SQL, downloads, extraction or title inference.
The result is server-local input to publish_archive, never a client payload.
"""
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import zipfile

from .normalization import clean_id
from .series_contract import MAPPING_VERSION, inspect_assertions, normalize_archive_relation
from .series_source import (MAX_ARCHIVE, MAX_MEMBERS, MAX_LINE, MAX_SEEDS, MAX_NODES,
                            MAX_EDGES, _node, digest)
from .source_pipeline import SourceError

MAX_CATALOG = 50000
MAX_INDEX_NODES = 200000
MAX_INDEX_EDGES = 500000


def _cycle_component_sources(rows):
    """Conservatively quarantine whole chronology components containing a cycle.

    Iterative traversal avoids recursion limits on long real-world chains.
    Weak links and cross-media links never join these components.
    """
    graph, adjacent = defaultdict(set), defaultdict(set)
    for row in rows:
        if row['kind'] not in ('prequel', 'sequel'):
            continue
        source, target = row['source_bangumi_id'], row['target_bangumi_id']
        before, after = (target, source) if row['kind'] == 'prequel' else (source, target)
        graph[before].add(after)
        adjacent[source].add(target)
        adjacent[target].add(source)
    incoming = dict.fromkeys(adjacent, 0)
    for targets in graph.values():
        for target in targets:
            incoming[target] += 1
    queue = deque(key for key, count in incoming.items() if count == 0)
    while queue:
        for target in graph.get(queue.popleft(), ()):
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    affected = {key for key, count in incoming.items() if count > 0}
    queue = deque(affected)
    while queue:
        for target in adjacent[queue.popleft()]:
            if target not in affected:
                affected.add(target)
                queue.append(target)
    return affected


def prepare_catalog_archive(path, expected_sha256, seeds, *, timeout_seconds=300):
    seeds = sorted({clean_id(value) for value in seeds})
    if not 1 <= len(seeds) <= MAX_CATALOG or not 1 <= timeout_seconds <= 900:
        raise SourceError('目录数量或预检时间上限无效。')
    path = Path(path)
    match = re.fullmatch(r'(dump-\d{4}-\d{2}-\d{2}\.\d{6}Z)\.zip', path.name)
    if (not match or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256 or '')
            or not 0 < path.stat().st_size <= MAX_ARCHIVE):
        raise SourceError('归档名称、大小或校验值无效。')
    version = match[1]
    source_at = datetime.strptime(version, 'dump-%Y-%m-%d.%H%M%SZ').replace(tzinfo=timezone.utc)
    deadline = time.monotonic() + timeout_seconds

    def check_time():
        if time.monotonic() > deadline:
            raise SourceError('全目录归档预检超时；没有提交数据库。')

    def verify():
        checksum = hashlib.sha256()
        with path.open('rb') as handle:
            while chunk := handle.read(1024 * 1024):
                check_time()
                checksum.update(chunk)
        if checksum.hexdigest() != expected_sha256:
            raise SourceError('归档 SHA256 不匹配或预检期间发生变化。')

    verify()
    selected, targets, issues = defaultdict(list), set(seeds), defaultdict(set)
    hashes, seed_set = {}, set(seeds)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name, bound in MAX_MEMBERS.items():
            if names.count(name) != 1 or archive.getinfo(name).file_size > bound:
                raise SourceError('归档成员缺失、重复或超限。')

        def rows(name):
            hasher, size = hashlib.sha256(), 0
            with archive.open(name) as stream:
                while line := stream.readline(MAX_LINE + 1):
                    check_time()
                    size += len(line)
                    if len(line) > MAX_LINE or size > MAX_MEMBERS[name]:
                        raise SourceError('归档内容超限。')
                    hasher.update(line)
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise SourceError('归档行不是对象。')
                    yield row
            hashes[name] = hasher.hexdigest()

        count = 0
        for row in rows('subject-relations.jsonlines'):
            source = row.get('subject_id')
            if type(source) is not int or source not in seed_set:
                continue
            count += 1
            if count > MAX_INDEX_EDGES:
                raise SourceError('目录关系超出预检上限。')
            if (set(row) != {'subject_id', 'related_subject_id', 'relation_type', 'order'}
                    or any(type(row[key]) is not int for key in row)
                    or any(not 1 <= row[key] <= 2147483647 for key in ('subject_id', 'related_subject_id', 'relation_type'))
                    or not 0 <= row['order'] <= 2147483647):
                issues[source].add('invalid_relation')
                continue
            selected[source].append(row)
            targets.add(row['related_subject_id'])
            if len(targets) > MAX_INDEX_NODES:
                raise SourceError('目录节点超出预检上限。')
        nodes, seen, invalid = {}, set(), set()
        for row in rows('subject.jsonlines'):
            identity = row.get('id')
            if type(identity) is not int or identity not in targets:
                continue
            if identity in seen:
                invalid.add(identity)
                nodes.pop(identity, None)
                continue
            seen.add(identity)
            try:
                nodes[identity] = _node(row)
            except (SourceError, ValueError, KeyError):
                invalid.add(identity)
    verify()
    types = {key: node['subject_type'] for key, node in nodes.items()}
    normalized = {}
    for source in seeds:
        if types.get(source) != 2:
            issues[source].add('source_metadata_unverified')
            continue
        rows = []
        for row in selected[source]:
            target = row['related_subject_id']
            if target not in nodes:
                issues[source].add('target_metadata_unverified')
            try:
                rows.append(normalize_archive_relation(row, types))
            except (ValueError, KeyError):
                issues[source].add('invalid_assertion')
        diagnostics = inspect_assertions(rows)
        for key in ('duplicate_count', 'unknown_count', 'multiple_relation_pairs'):
            if diagnostics[key]:
                issues[source].add(key)
        normalized[source] = rows
    all_rows = [row for rows in normalized.values() for row in rows]
    global_diagnostics = inspect_assertions(all_rows)
    for source, target, *_ in global_diagnostics['reciprocal_mismatches']:
        issues[source].add('reciprocal_mismatch')
        if target in normalized:
            issues[target].add('reciprocal_mismatch')
    for source in _cycle_component_sources(all_rows) & normalized.keys():
        issues[source].add('chronology_cycle_component')
    for source, rows in normalized.items():
        if len(rows) > MAX_EDGES or len({row['target_bangumi_id'] for row in rows} | {source}) > MAX_NODES:
            issues[source].add('source_exceeds_batch_bound')

    envelope = {'snapshot_version': version, 'source_at': source_at.isoformat(),
        'source_reference': f'https://github.com/bangumi/Archive/releases/download/archive/{version}.zip',
        'archive_sha256': expected_sha256, 'payload_sha256': hashes['subject-relations.jsonlines'],
        'subject_payload_sha256': hashes['subject.jsonlines'], 'mapping_version': MAPPING_VERSION}
    batches = []

    def emit(group, partial):
        assertions = [row for source in group for row in normalized[source] if row['target_bangumi_id'] in nodes]
        assertions = list({(row['source_bangumi_id'], row['target_bangumi_id'], row['source_relation']): row
                           for row in assertions}.values())
        ids = set(group) | {row['target_bangumi_id'] for row in assertions}
        diagnostics = inspect_assertions(assertions)
        diagnostics['source_issues'] = {str(key): sorted(issues[key]) for key in group if issues[key]}
        body = {**envelope, 'seed_ids': group, 'nodes': [nodes[key] for key in sorted(ids)],
                'assertions': assertions, 'diagnostics': diagnostics, 'partial': partial}
        batches.append({**body, 'fingerprint': digest(body)})

    eligible = [key for key in seeds if key in normalized and 'source_exceeds_batch_bound' not in issues[key]]
    for partial in (False, True):
        group, ids, edge_count = [], set(), 0
        for source in eligible:
            if bool(issues[source]) != partial:
                continue
            rows = normalized[source]
            additions = {source} | {row['target_bangumi_id'] for row in rows if row['target_bangumi_id'] in nodes}
            if group and (len(group) >= MAX_SEEDS or edge_count + len(rows) > MAX_EDGES or len(ids | additions) > MAX_NODES):
                emit(group, partial)
                group, ids, edge_count = [], set(), 0
            group.append(source)
            ids |= additions
            edge_count += len(rows)
        if group:
            emit(group, partial)
    quarantined = {str(key): sorted(value) for key, value in sorted(issues.items()) if value}
    report = {**envelope, 'catalog_count': len(seeds), 'verified_nodes': len(nodes),
        'selected_raw_relations': count, 'normalized_assertions': len(all_rows),
        'complete_sources': sum(not bool(issues[key]) for key in eligible),
        'partial_sources': sum(bool(issues[key]) for key in eligible),
        'skipped_sources': sorted(seed_set - set(eligible)), 'batch_count': len(batches),
        'quarantined_sources': quarantined,
        'issue_counts': dict(sorted(Counter(reason for reasons in quarantined.values() for reason in reasons).items())),
        'invalid_metadata_ids': sorted(invalid), 'missing_metadata_ids': sorted(targets - seen),
        'global_diagnostics': global_diagnostics}
    report['plan_fingerprint'] = digest([report, [batch['fingerprint'] for batch in batches]])
    return {'report': report, 'batches': batches}
