"""Bounded local ZIP preparation. No Django, .env, network, extraction or SQL."""
import hashlib
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .normalization import clean_id
from .series_contract import normalize_archive_relation, inspect_assertions, MAPPING_VERSION
from .source_pipeline import PLATFORMS, SourceError

MAX_SEEDS = 100
MAX_NODES = 10000
MAX_EDGES = 5000
MAX_LINE = 2 * 1024 * 1024
MAX_ARCHIVE = 2 * 1024 ** 3
MAX_MEMBERS = {'subject.jsonlines': 4 * 1024 ** 3, 'subject-relations.jsonlines': 512 * 1024 ** 2}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def seed_ids(values):
    ids = sorted({clean_id(value) for value in values})
    if not 1 <= len(ids) <= MAX_SEEDS:
        raise SourceError('每批必须指定 1–100 个不同 Bangumi ID。')
    return ids


def _node(row):
    identity = clean_id(row['id'])
    kind = row.get('type')
    if type(kind) is not int or kind not in (1, 2, 3, 4, 6):
        raise SourceError('条目媒介无效。')
    if kind != 2:
        values = {'bangumi_id': identity, 'subject_type': kind, 'title': '', 'original_title': '',
                  'release_date': '', 'release_format': '', 'nsfw': None}
        return {**values, 'metadata_revision': digest(values)}
    def text(key, size):
        value = row.get(key, '')
        if value is None:
            value = ''
        if not isinstance(value, str) or len(value) > size or '\x00' in value:
            raise SourceError(f'节点字段 {key} 无效。')
        return value.strip()
    date = text('date', 10)
    if date:
        try:
            if re.fullmatch(r'\d{4}', date):
                datetime(int(date), 1, 1)
            elif re.fullmatch(r'\d{4}-\d{2}', date):
                datetime.strptime(date, '%Y-%m')
            elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
                datetime.strptime(date, '%Y-%m-%d')
            else:
                raise ValueError()
        except ValueError:
            raise SourceError('发行日期无效，不能猜测修复。') from None
    nsfw = row.get('nsfw')
    if nsfw is not None and type(nsfw) is not bool:
        raise SourceError('分级字段无效。')
    if type(row.get('platform', 0)) is not int:
        raise SourceError('发行形式无效。')
    values = {'bangumi_id': identity, 'subject_type': kind,
        'title': text('name_cn', 500), 'original_title': text('name', 500),
        'release_date': date, 'release_format': PLATFORMS.get(row.get('platform'), '未知') if kind == 2 else '', 'nsfw': nsfw}
    return {**values, 'metadata_revision': digest(values)}


def prepare_archive(path, expected_sha256, seeds, *, timeout_seconds=120):
    path, seeds = Path(path), seed_ids(seeds)
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256 or ''):
        raise SourceError('必须提供从官方 manifest 核对的 SHA256。')
    if not 1 <= timeout_seconds <= 300:
        raise SourceError('超时上限必须为 1–300 秒。')
    match = re.fullmatch(r'(dump-\d{4}-\d{2}-\d{2}\.\d{6}Z)\.zip', path.name)
    if not match or not 0 < path.stat().st_size <= MAX_ARCHIVE:
        raise SourceError('归档名称或大小无效。')
    version = match[1]
    source_at = datetime.strptime(version, 'dump-%Y-%m-%d.%H%M%SZ').replace(tzinfo=timezone.utc)
    deadline = time.monotonic() + timeout_seconds
    def check_time():
        if time.monotonic() > deadline:
            raise SourceError('归档预检超时；没有提交数据库。')
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            check_time()
            checksum.update(chunk)
    if checksum.hexdigest() != expected_sha256:
        raise SourceError('归档 SHA256 不匹配。')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name, bound in MAX_MEMBERS.items():
            if names.count(name) != 1 or archive.getinfo(name).file_size > bound:
                raise SourceError('归档成员缺失、重复或超限。')
        hashes = {}
        def rows(name):
            h, size = hashlib.sha256(), 0
            with archive.open(name) as stream:
                while line := stream.readline(MAX_LINE + 1):
                    check_time()
                    size += len(line)
                    if len(line) > MAX_LINE or size > MAX_MEMBERS[name]:
                        raise SourceError('归档内容超限。')
                    h.update(line)
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise SourceError('归档行不是对象。')
                    yield value
            hashes[name] = h.hexdigest()
        selected, targets = [], set(seeds)
        for row in rows('subject-relations.jsonlines'):
            if row.get('subject_id') in seeds:
                if (set(row) != {'subject_id', 'related_subject_id', 'relation_type', 'order'}
                        or any(type(row[key]) is not int for key in row)
                        or not 1 <= row['subject_id'] <= 2147483647
                        or not 1 <= row['related_subject_id'] <= 2147483647
                        or not 1 <= row['relation_type'] <= 2147483647
                        or not 0 <= row['order'] <= 2147483647):
                    raise SourceError('选中关系的字段或数字格式无效。')
                selected.append(row)
                targets.add(clean_id(row.get('related_subject_id')))
                if len(selected) > MAX_EDGES or len(targets) > MAX_NODES:
                    raise SourceError('本批关系或节点超限，请缩小 ID 批次。')
        nodes = {}
        for row in rows('subject.jsonlines'):
            if row.get('id') in targets:
                node = _node(row)
                if node['bangumi_id'] in nodes:
                    raise SourceError('归档条目 ID 重复。')
                nodes[node['bangumi_id']] = node
    # Detect replacement/mutation between checksum validation and member parsing.
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            check_time()
            checksum.update(chunk)
    if checksum.hexdigest() != expected_sha256:
        raise SourceError('归档在预检期间发生变化。')
    if any(nodes.get(identity, {}).get('subject_type') != 2 for identity in seeds):
        raise SourceError('来源 ID 必须在同一归档中确认为动画。')
    types = {identity: node['subject_type'] for identity, node in nodes.items()}
    normalized = [normalize_archive_relation(row, types) for row in selected]
    diagnostics = inspect_assertions(normalized)
    if targets - nodes.keys():
        diagnostics['missing_node_ids'] = sorted(targets - nodes.keys())
    partial = bool(diagnostics['duplicate_count'] or diagnostics['unknown_count'] or diagnostics['multiple_relation_pairs']
        or diagnostics['reciprocal_mismatches'] or diagnostics['chronology_cycle'] or diagnostics.get('missing_node_ids'))
    # No missing-endpoint FK fabrication. Such observations cannot publish.
    edges = list({(row['source_bangumi_id'], row['target_bangumi_id'], row['source_relation']): row
                  for row in normalized if row['target_bangumi_id'] in nodes}.values())
    body = {'snapshot_version': version, 'source_at': source_at.isoformat(),
        'source_reference': f'https://github.com/bangumi/Archive/releases/download/archive/{version}.zip',
        'archive_sha256': expected_sha256, 'payload_sha256': hashes['subject-relations.jsonlines'],
        'subject_payload_sha256': hashes['subject.jsonlines'], 'mapping_version': MAPPING_VERSION,
        'seed_ids': seeds, 'nodes': [nodes[key] for key in sorted(nodes)], 'assertions': edges,
        'diagnostics': diagnostics, 'partial': partial}
    return {**body, 'fingerprint': digest(body)}
