"""Offline exact public-row reuse, gated by a LIVE database content digest.

Not a TTL/version-counter guess: updates bypassing updated_at are detected.
Only public metadata is retained, bounded to four query scopes per reader.
No user, profile, evidence, results, cursor or private data is cached here.
"""
from collections import OrderedDict
import hashlib

from django.db import connection

from .version_control import canonical_bytes

VERSION = 'public-manifest.v1'


class PublicManifestReader:
    def __init__(self):
        self._scopes = OrderedDict()
        self.last_read = {}

    def read(self, query, options, expression, limit):
        from . import engine, v2_controls
        if connection.vendor != 'postgresql' or not connection.in_atomic_block:
            raise ValueError('Public manifest requires a read-only PostgreSQL snapshot')
        if type(limit) is not int or not 0 < limit <= engine.MAX_CATALOG_ROWS:
            raise ValueError('Invalid public row budget')
        raw_query = query.annotate(feature_revision=expression).order_by('bangumi_id').values_list(
            'bangumi_id', 'score', 'feature_revision', 'season', 'status_type')[:limit + 1]
        sql, params = raw_query.query.sql_with_params()
        key = hashlib.sha256(canonical_bytes([sql, params, limit])).hexdigest()
        # Hash each bounded row INCLUDING score/season/type, then hash the
        # ordered row hashes. JSONB encodes field boundaries/nulls safely; all
        # serialization is performed on the SAME server on both comparisons.
        # Only count + digest are transferred. No extension or schema change.
        summary_sql = '''WITH live AS (''' + sql + '''), summary AS (
            SELECT COUNT(*) AS n,
                ENCODE(SHA256(CONVERT_TO(COALESCE(STRING_AGG(
                    ENCODE(SHA256(CONVERT_TO(JSONB_BUILD_ARRAY(bangumi_id, score,
                        feature_revision, season, status_type)::text, 'UTF8')), 'hex'),
                    '' ORDER BY bangumi_id), ''), 'UTF8')), 'hex') AS digest FROM live)
            SELECT n, digest, current_setting('transaction_read_only'),
                current_setting('transaction_isolation') FROM summary'''
        with connection.cursor() as cursor:
            cursor.execute(summary_sql, params)
            count, digest, readonly, isolation = cursor.fetchone()
        if readonly != 'on' or isolation != 'repeatable read':
            raise ValueError('Public manifest refuses a writable/inconsistent snapshot')
        if count > limit:
            raise engine.RecommendationUnavailable()
        cached = self._scopes.get(key)
        hit = cached is not None and cached[:2] == (count, digest)
        if hit:
            rows = cached[2]
        else:
            # Same repeatable-read transaction as the digest. Never pair an
            # old digest with a newer scan; no cross-transaction warm-up writes.
            rows = tuple(tuple(row) for row in raw_query)
            if len(rows) != count:
                raise engine.RecommendationSnapshotChanged()
            self._scopes[key] = (count, digest, rows)
        self._scopes.move_to_end(key)
        while len(self._scopes) > 4:
            self._scopes.popitem(last=False)
        self.last_read = {'version': VERSION, 'cache_hit': hit, 'live_rows': count,
                          'transferred_rows': 1 if hit else count + 1}
        selected, metadata = [], {}
        for identity, score, revision, season, kind in rows:
            row = {'score': score, 'season': season, 'status_type': kind}
            if v2_controls.matches(row, options):
                selected.append((identity, score, revision)); metadata[identity] = row
        return selected, metadata


def recipe_with_reader(recipe):
    body = {**recipe, 'public_read_version': VERSION, 'serving_enabled': False}
    body.pop('recipe_revision')
    return {**body, 'recipe_revision': hashlib.sha256(canonical_bytes(body)).hexdigest()}
