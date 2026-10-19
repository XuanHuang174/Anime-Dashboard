"""Offline bootstrap from authenticated PUBLIC data, always live-verified.

Never trusts a timestamp/TTL. No user, evidence or results are stored. A stale
bundle fails closed: rebuilding it is explicit, not a silent partial fallback.
"""
from collections import OrderedDict
import gzip
import hashlib
import hmac
import json
import math
import struct

from django.conf import settings
from django.db import connection
from django.db.models import TextField, Value
from django.db.models.functions import Cast, Coalesce, Concat, Length

from . import engine, v2_controls
from .public_fingerprint import NativeSHA256, public_feature_fingerprint
from .version_control import canonical_bytes

VERSION = 'public-bootstrap.v1'
BUNDLE_VERSION = 'public-bootstrap-bundle.v1'
COLUMNS = ('bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type', 'season')
MAX_BYTES = 64 * 1024 * 1024


def _transaction(count, readonly, isolation, limit):
    if readonly != 'on' or isolation != 'repeatable read':
        raise ValueError('Public bootstrap requires a read-only repeatable-read transaction')
    if count > limit:
        raise engine.RecommendationUnavailable()


def _require_pg():
    if connection.vendor != 'postgresql' or not connection.in_atomic_block:
        raise ValueError('Explicit PostgreSQL snapshot required')


def compact_catalog_revision():
    """EXACT old browsing.catalog_revision string, one returned row, not 50k."""
    from .browsing import CATALOG_FIELDS
    _require_pg()
    parts = []
    for field in CATALOG_FIELDS:
        value = Coalesce(Cast(field, TextField()), Value('<null>'), output_field=TextField())
        parts.extend((Cast(Length(value), TextField()), Value(':'), value))
    from .models import RecommendationAnime
    query = RecommendationAnime.objects.order_by('pk').annotate(
        revision=NativeSHA256(Concat(*parts, output_field=TextField()))).values_list(
            'pk', 'revision')[:engine.MAX_CATALOG_ROWS + 1]
    sql, params = query.query.sql_with_params()
    # pk and SHA256 are ASCII only. This is the exact compact JSON emitted by
    # browsing._digest(list(values_list(...))), including empty-list handling.
    statement = '''WITH live(pk, revision) AS (''' + sql + ''')
        SELECT COUNT(*), ENCODE(SHA256(CONVERT_TO('[' || COALESCE(STRING_AGG(
            '[' || pk::text || ',"' || revision || '"]', ',' ORDER BY pk), '') || ']', 'UTF8')), 'hex'),
            current_setting('transaction_read_only'), current_setting('transaction_isolation') FROM live'''
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        count, digest, readonly, isolation = cursor.fetchone()
    _transaction(count, readonly, isolation, engine.MAX_CATALOG_ROWS)
    return digest


def _prefix(value):
    return '-' if value is None else f'{len(value)}:{value}'


def row_digest(rows):
    digests = []
    for identity, score, revision, season, kind in rows:
        values = (str(identity), struct.pack('!d', float(score)).hex(), revision, season, kind)
        digests.append(hashlib.sha256(''.join(_prefix(value) for value in values).encode()).hexdigest())
    return hashlib.sha256(''.join(digests).encode()).hexdigest()


def validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > engine.MAX_CATALOG_ROWS:
        raise ValueError('Invalid public bundle size')
    previous = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(COLUMNS):
            raise ValueError('Only declared public fields allowed')
        identity = row['bangumi_id']
        if type(identity) is not int or not previous < identity <= 2147483647:
            raise ValueError('Public identities must be unique, positive and ordered')
        previous = identity
        if type(row['score']) not in (int, float) or not math.isfinite(row['score']):
            raise ValueError('Invalid public score')
        if any(row[field] is not None and not isinstance(row[field], str) for field in COLUMNS if field not in ('bangumi_id', 'score')):
            raise ValueError('Invalid public text field')
    return rows


class SeededPublicReader:
    def __init__(self, rows):
        validate_rows(rows)
        self.rows = tuple((row['bangumi_id'], row['score'], public_feature_fingerprint(row), row['season'], row['status_type']) for row in rows)
        self._scopes = OrderedDict()
        self.last_read = {}

    def read(self, query, options, expression, limit):
        _require_pg()
        if type(limit) is not int or not 0 < limit <= engine.MAX_CATALOG_ROWS:
            raise ValueError('Invalid row budget')
        raw = query.annotate(feature_revision=expression).order_by('bangumi_id').values_list(
            'bangumi_id', 'score', 'feature_revision', 'season', 'status_type')[:limit + 1]
        sql, params = raw.query.sql_with_params()
        key = hashlib.sha256(canonical_bytes([sql, params, limit])).hexdigest()
        # Portable field boundaries + IEEE754 float bytes make the same digest
        # computable from already-loaded public data; no PG float-text guessing.
        fields = ("bangumi_id::text", "ENCODE(FLOAT8SEND(score), 'hex')", 'feature_revision', 'season', 'status_type')
        payload = ' || '.join(f"CASE WHEN {field} IS NULL THEN '-' ELSE LENGTH({field})::text || ':' || {field} END" for field in fields)
        statement = '''WITH live AS (''' + sql + '''), summary AS (SELECT COUNT(*) AS n,
            ENCODE(SHA256(CONVERT_TO(COALESCE(STRING_AGG(ENCODE(SHA256(CONVERT_TO(''' + payload + ''',
                'UTF8')), 'hex'), '' ORDER BY bangumi_id), ''), 'UTF8')), 'hex') AS digest FROM live)
            SELECT n, digest, current_setting('transaction_read_only'), current_setting('transaction_isolation') FROM summary'''
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            count, digest, readonly, isolation = cursor.fetchone()
        _transaction(count, readonly, isolation, limit)
        if key not in self._scopes:
            rows = tuple(row for row in self.rows if row[1] >= float(options['min_score'])
                         and (options['type'] == 'all' or row[4] == options['type']))
            self._scopes[key] = (len(rows), row_digest(rows), rows)
        expected_count, expected_digest, rows = self._scopes[key]
        self._scopes.move_to_end(key)
        while len(self._scopes) > 4:
            self._scopes.popitem(last=False)
        if (count, digest) != (expected_count, expected_digest):
            raise engine.RecommendationSnapshotChanged('公共快照已过期，请重新准备目录数据。')
        self.last_read = {'version': VERSION, 'source': 'live-verified-public-bootstrap',
                          'live_rows': count, 'transferred_rows': 1, 'cache_hit': True}
        selected, metadata = [], {}
        for identity, score, revision, season, kind in rows:
            row = {'score': score, 'season': season, 'status_type': kind}
            if v2_controls.matches(row, options):
                selected.append((identity, score, revision)); metadata[identity] = row
        return selected, metadata


def _signature(body):
    return hmac.new(settings.SECRET_KEY.encode(), b'public-bootstrap-only\0' + canonical_bytes(body), hashlib.sha256).hexdigest()


def write_bundle(rows, path):
    from .unused_validation import private_path
    path = private_path(path)
    validate_rows(rows)
    body = {'version': BUNDLE_VERSION, 'columns': list(COLUMNS), 'rows': rows}
    payload = canonical_bytes({**body, 'signature': _signature(body)})
    if len(payload) > MAX_BYTES:
        raise ValueError('Public bundle too large')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as compressed:
        compressed.write(payload)


def load_bundle(path):
    from .unused_validation import private_path
    path = private_path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Public bundle too large')
    with gzip.open(path, 'rb') as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Expanded public bundle too large')
    data = json.loads(raw)
    if set(data) != {'version', 'columns', 'rows', 'signature'}:
        raise ValueError('Unknown public bundle fields')
    body = {key: value for key, value in data.items() if key != 'signature'}
    if (body['version'] != BUNDLE_VERSION or body['columns'] != list(COLUMNS)
            or not hmac.compare_digest(data['signature'], _signature(body))):
        raise ValueError('Public bundle authentication failed')
    return validate_rows(body['rows'])


def recipe_with_seed(recipe):
    body = {**recipe, 'public_read_version': VERSION, 'serving_enabled': False}
    body.pop('recipe_revision')
    return {**body, 'recipe_revision': hashlib.sha256(canonical_bytes(body)).hexdigest()}
