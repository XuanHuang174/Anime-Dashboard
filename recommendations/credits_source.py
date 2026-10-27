"""13B verified archive -> disk-backed bounded plans. No Django or HTTP.

Only this process's single SQLite spool is removed, never a directory/tree.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import zipfile

from .credits_aliases import ALIAS_VERSION, parse_names
from .credits_contract import (MAPPING_VERSION, CAST_TYPES, integer, label, normalize_person,
    normalize_subject_character, normalize_archive_staff, normalize_archive_cast)
from .source_pipeline import SourceError

MAX_CATALOG = 50000
MAX_BATCH = 25
MAX_ROOT_ROWS = 5000
MAX_ENTITIES = 400000
MAX_LINE = 2 * 1024 ** 2
MAX_SPOOL = 2 * 1024 ** 3
MEMBERS = {'subject.jsonlines': 4 * 1024**3, 'person.jsonlines': 512 * 1024**2,
    'character.jsonlines': 1024**3, 'subject-persons.jsonlines': 1024**3,
    'subject-characters.jsonlines': 512 * 1024**2, 'person-characters.jsonlines': 512 * 1024**2}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class ArchivePlan:
    def __init__(self, path, expected_sha256, seeds, *, timeout_seconds=600):
        self.path = Path(path)
        if not isinstance(seeds, (list, tuple)) or not 1 <= len(seeds) <= MAX_CATALOG:
            raise SourceError('来源数量超限或为空。')
        self.seeds = sorted({integer(value) for value in seeds})
        match = re.fullmatch(r'(dump-\d{4}-\d{2}-\d{2}\.\d{6}Z)\.zip', self.path.name)
        if (not match or not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)
                or not 0 < self.path.stat().st_size <= 2 * 1024**3
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 900):
            raise SourceError('归档名称、大小、摘要或超时上限无效。')
        self.version, self.checksum = match[1], expected_sha256
        self.source_at = datetime.strptime(self.version, 'dump-%Y-%m-%d.%H%M%SZ').replace(tzinfo=timezone.utc).isoformat()
        self.deadline = time.monotonic() + timeout_seconds
        self.stats = defaultdict(lambda: {'count': 0, 'initial_scope_rows': 0, 'hash': hashlib.sha256(), 'issues': Counter()})
        self.members = {}
        self.db = None
        self.spool = None

    def check(self):
        if time.monotonic() > self.deadline:
            raise SourceError('参与关系预检超时；未写入业务数据库。')
        if self.spool and self.spool.stat().st_size > MAX_SPOOL:
            raise SourceError('本地临时索引超过处理上限。')

    def verify(self):
        h = hashlib.sha256()
        with self.path.open('rb') as source:
            while chunk := source.read(1024 * 1024):
                self.check(); h.update(chunk)
        if h.hexdigest() != self.checksum:
            raise SourceError('归档摘要不匹配或读取期间发生变化。')

    def __enter__(self):
        try:
            self.verify()
            descriptor, name = tempfile.mkstemp(prefix='anime-credits-13b-', suffix='.sqlite3')
            os.close(descriptor); self.spool = Path(name)
            self.db = sqlite3.connect(name)
            self.db.executescript('''PRAGMA journal_mode=MEMORY; PRAGMA cache_size=-8192;
                CREATE TABLE raw (source INTEGER, kind TEXT, payload TEXT);
                CREATE INDEX raw_source ON raw(source,kind);
                CREATE TABLE entities (kind TEXT, identity INTEGER, payload TEXT, PRIMARY KEY(kind,identity));
                CREATE TABLE bindings (source INTEGER, character INTEGER, payload TEXT, PRIMARY KEY(source,character));''')
            self._scan()
            self.db.commit(); self.verify()
            self.snapshot = {'snapshot_version': self.version, 'source_at': self.source_at,
                'source_reference': f'https://github.com/bangumi/Archive/releases/download/archive/{self.path.name}',
                'archive_sha256': self.checksum, 'member_hashes': self.members,
                'mapping_version': MAPPING_VERSION, 'alias_version': ALIAS_VERSION}
            self.snapshot['id'] = digest([self.version, self.checksum, MAPPING_VERSION, ALIAS_VERSION])
            return self
        except BaseException:
            self.close(); raise

    def close(self):
        if self.db is not None:
            self.db.close(); self.db = None
        if self.spool is not None:
            self.spool.unlink(missing_ok=True); self.spool = None

    def __exit__(self, *_):
        self.close()

    def _entity(self, kind, identity, payload):
        old = self.db.execute('SELECT 1 FROM entities WHERE kind=? AND identity=?', (kind, identity)).fetchone()
        # A repeated source identity is quarantined, not last-row-wins metadata.
        if old:
            self.db.execute('UPDATE entities SET payload=NULL WHERE kind=? AND identity=?', (kind, identity))
        else:
            self.db.execute('INSERT INTO entities VALUES (?,?,?)', (kind, identity, encoded(payload) if payload else None))

    def _get(self, kind, identity):
        if type(identity) is not int or not 1 <= identity <= 2147483647:
            return None
        row = self.db.execute('SELECT payload FROM entities WHERE kind=? AND identity=?', (kind, identity)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def _scan(self):
        seeds, people, characters = set(self.seeds), set(), set()
        with zipfile.ZipFile(self.path) as archive:
            names = archive.namelist()
            for name, limit in MEMBERS.items():
                if names.count(name) != 1 or archive.getinfo(name).file_size > limit or archive.getinfo(name).flag_bits & 1:
                    raise SourceError('归档成员缺失、重复、加密或超限。')

            def rows(name):
                h, count, size = hashlib.sha256(), 0, 0
                with archive.open(name) as stream:
                    while line := stream.readline(MAX_LINE + 1):
                        count += 1; size += len(line)
                        if len(line) > MAX_LINE or size > MEMBERS[name]:
                            raise SourceError('归档成员内容超限。')
                        if count % 1000 == 0:
                            self.check()
                        h.update(line)
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise SourceError('归档行不是对象。')
                        yield row
                self.members[name] = {'sha256': h.hexdigest(), 'rows': count}

            for member, kind in [('subject-persons.jsonlines', 'studio'), ('person-characters.jsonlines', 'voice_actor')]:
                for row in rows(member):
                    identity = row.get('subject_id')
                    if type(identity) is not int or not 1 <= identity <= 2147483647:
                        raise SourceError('关系来源 ID 格式漂移，不能把无法归属的行计为空。')
                    if identity not in seeds:
                        continue
                    stat = self.stats[identity, kind]
                    stat['count'] += 1; stat['hash'].update(encoded(row).encode()); stat['hash'].update(b'\n')
                    code = row.get('position') if kind == 'studio' else row.get('type')
                    stat['initial_scope_rows'] += type(code) is int and code == (67 if kind == 'studio' else 0)
                    if stat['count'] > MAX_ROOT_ROWS:
                        stat['issues']['row_limit'] += 1; continue
                    self.db.execute('INSERT INTO raw VALUES (?,?,?)', (identity, kind, encoded(row)))
                    for key, target in [('person_id', people), ('character_id', characters)]:
                        if key in row and type(row[key]) is int and row[key] > 0:
                            target.add(row[key])
                    if len(people) + len(characters) > MAX_ENTITIES:
                        raise SourceError('关联端点超过预检上限。')
            binding_counts = Counter()
            for row in rows('subject-characters.jsonlines'):
                source = row.get('subject_id')
                if type(source) is not int or not 1 <= source <= 2147483647:
                    raise SourceError('角色绑定来源 ID 格式漂移。')
                if source not in seeds:
                    continue
                stat = self.stats[source, 'voice_actor']
                stat['hash'].update(encoded(row).encode()); stat['hash'].update(b'\n')
                binding_counts[source] += 1
                if binding_counts[source] > MAX_ROOT_ROWS:
                    stat['issues']['binding_limit'] += 1; continue
                try:
                    binding = normalize_subject_character(row)
                    self.db.execute('INSERT INTO bindings VALUES (?,?,?)', (source, binding['character_id'], encoded(binding)))
                except (ValueError, sqlite3.IntegrityError):
                    stat['issues']['invalid_or_duplicate_binding'] += 1
            for row in rows('subject.jsonlines'):
                identity = row.get('id')
                if type(identity) is not int or not 1 <= identity <= 2147483647:
                    raise SourceError('条目身份格式漂移。')
                if identity not in seeds:
                    continue
                try:
                    if type(row['type']) is not int or row['type'] not in (1, 2, 3, 4, 6) or type(row.get('nsfw')) not in (bool, type(None)):
                        raise ValueError()
                    data = {'bangumi_id': integer(identity), 'subject_type': row['type'],
                        'name': label(row['name']), 'nsfw': row.get('nsfw'), 'source_revision': digest(row)}
                except (ValueError, KeyError):
                    data = None
                self._entity('subject', identity, data)
            for row in rows('person.jsonlines'):
                identity = row.get('id')
                if type(identity) is not int or not 1 <= identity <= 2147483647:
                    raise SourceError('人物身份格式漂移。')
                if identity not in people:
                    continue
                try:
                    person = normalize_person({key: row[key] for key in ('id', 'name', 'type', 'career')})
                    aliases = parse_names(person['name'], row.get('infobox', ''))
                    data = {'person_id': identity, 'name': person['name'], 'person_type': person['person_type'],
                        'careers': person['careers'], 'alias_status': aliases['status'], 'alias_issues': aliases['issues'],
                        'names': aliases['names'], 'source_revision': digest(row)}
                except (ValueError, KeyError):
                    data = None
                self._entity('person', identity, data)
            for row in rows('character.jsonlines'):
                identity = row.get('id')
                if type(identity) is not int or not 1 <= identity <= 2147483647:
                    raise SourceError('角色身份格式漂移。')
                if identity not in characters:
                    continue
                try:
                    data = {'character_id': integer(identity), 'name': label(row['name']),
                        'entity_role': integer(row['role']), 'source_revision': digest(row)}
                except (ValueError, KeyError):
                    data = None
                self._entity('character', identity, data)

    def batch(self, seeds):
        if not 1 <= len(seeds) <= MAX_BATCH or not set(seeds) <= set(self.seeds):
            raise SourceError('发布批次无效。')
        subjects, persons, characters, observations = {}, {}, {}, []
        for identity in seeds:
            subject = self._get('subject', identity)
            if subject:
                subjects[identity] = subject
            for kind in ('studio', 'voice_actor'):
                issues = Counter(self.stats[identity, kind]['issues'])
                raw_rows = [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM raw WHERE source=? AND kind=?', (identity, kind))]
                binding_map = {(identity, row[0]): json.loads(row[1]) for row in self.db.execute('SELECT character,payload FROM bindings WHERE source=?', (identity,))}
                fact_map, referenced = {}, {}
                for raw in raw_rows:
                    person = self._get('person', raw.get('person_id'))
                    character = self._get('character', raw.get('character_id')) if kind == 'voice_actor' else None
                    if person:
                        persons[person['person_id']] = person
                        referenced['person', person['person_id']] = person['source_revision']
                    if character:
                        characters[character['character_id']] = character
                        referenced['character', character['character_id']] = character['source_revision']
                    try:
                        sm = {identity: {'id': identity, 'type': subject['subject_type']}} if subject else {}
                        pm = {person['person_id']: person} if person else {}
                        cm = {character['character_id']: {'id': character['character_id']}} if character else {}
                        fact = (normalize_archive_staff(raw, sm, pm) if kind == 'studio'
                            else normalize_archive_cast(raw, sm, pm, cm, binding_map))
                        key = (fact['person_id'], fact.get('character_id', 0), fact.get('source_position', fact.get('source_cast_type')))
                        if key in fact_map:
                            issues['duplicate_or_conflicting_credit'] += 1
                        else:
                            fact_map[key] = fact
                        relevant = raw.get('position') == 67 if kind == 'studio' else raw.get('type') == 0
                        if relevant and fact['review_reasons']:
                            for reason in fact['review_reasons']:
                                issues[reason] += 1
                        if kind == 'voice_actor' and raw.get('type') not in CAST_TYPES:
                            issues['unknown_cast_type'] += 1
                    except (ValueError, KeyError, TypeError):
                        issues['invalid_credit_row'] += 1
                facts = [fact_map[key] for key in sorted(fact_map)]
                failed = subject is None or subject['subject_type'] != 2
                coverage = 'failed' if failed else 'partial' if issues else 'observed' if any(f['source_eligible'] for f in facts) else 'empty_observed'
                diagnostics = {'issues': dict(sorted(issues.items())), 'raw_rows': self.stats[identity, kind]['count'],
                    'initial_scope_rows': self.stats[identity, kind]['initial_scope_rows'],
                    'retained_rows': len(facts), 'eligible_rows': sum(f['source_eligible'] for f in facts)}
                if failed:
                    diagnostics['issues']['missing_or_non_anime_root'] = 1
                obs = {'bangumi_id': identity, 'kind': kind, 'coverage': coverage, 'diagnostics': diagnostics, 'facts': facts}
                obs['fingerprint'] = digest([self.snapshot['id'], subject, kind, facts, diagnostics,
                    self.stats[identity, kind]['hash'].hexdigest(), sorted((list(key), value) for key, value in referenced.items())])
                observations.append(obs)
        body = {'snapshot': self.snapshot, 'subjects': list(subjects.values()), 'persons': list(persons.values()),
            'characters': list(characters.values()), 'observations': observations}
        return {**body, 'fingerprint': digest(body)}

    def preview(self, catalog=None):
        groups = dict(catalog or [])
        counters = {kind: {group: Counter(denominator=0, complete_observations=0, works_with_initial_relations=0,
                    initial_scope_rows=0, raw_rows=0) for group in ('safe', 'adult', 'unknown')} for kind in ('studio', 'voice_actor')}
        alias_counts, fingerprints, issues = Counter(), [], Counter()
        for offset in range(0, len(self.seeds), MAX_BATCH):
            self.check()
            batch = self.batch(self.seeds[offset:offset + MAX_BATCH])
            for person in batch['persons']:
                alias_counts[person['alias_status']] += 1  # reference occurrences; not distinct entities
            for obs in batch['observations']:
                subject = self._get('subject', obs['bangumi_id'])
                nsfw = groups.get(obs['bangumi_id']) if catalog is not None else (subject['nsfw'] if subject else None)
                group = 'safe' if nsfw is False else 'adult' if nsfw is True else 'unknown'
                counters[obs['kind']][group][obs['coverage']] += 1
                counters[obs['kind']][group]['denominator'] += 1
                counters[obs['kind']][group]['raw_rows'] += obs['diagnostics']['raw_rows']
                counters[obs['kind']][group]['initial_scope_rows'] += obs['diagnostics']['initial_scope_rows']
                for reason in ('missing_person', 'missing_character', 'missing_subject_character_binding'):
                    counters[obs['kind']][group][reason] += obs['diagnostics']['issues'].get(reason, 0)
                if obs['coverage'] in ('observed', 'empty_observed'):
                    counters[obs['kind']][group]['complete_observations'] += 1
                if obs['coverage'] == 'observed':
                    counters[obs['kind']][group]['works_with_initial_relations'] += 1
                issues.update(obs['diagnostics']['issues'])
                fingerprints.append([obs['bangumi_id'], obs['kind'], obs['fingerprint']])
        return {'contract_version': 'recommendations.credits.import-plan.v1', 'snapshot': self.snapshot,
            'source_count': len(self.seeds), 'batch_count': (len(self.seeds) + MAX_BATCH - 1) // MAX_BATCH,
            'scope': 'catalog_source_preflight' if catalog is not None else 'explicit_source_preflight_not_catalog_coverage',
            'coverage': {kind: {group: dict(count) for group, count in groups.items()} for kind, groups in counters.items()},
            'issue_counts': dict(issues), 'alias_reference_occurrences': dict(alias_counts),
            'maximum_root_raw_rows': max((stat['count'] for stat in self.stats.values()), default=0),
            'plan_fingerprint': digest([self.snapshot, fingerprints, catalog]), 'database_writes': False}
