"""Opt-in, read-only timings. Never retain SQL, parameters or private rows."""
from time import perf_counter

from django.db import connection


class QueryPhases:
    def __init__(self):
        self.phases = {}

    @staticmethod
    def classify(sql):
        normalized = sql.strip().lower()
        if normalized.startswith('show transaction_') or normalized.startswith("select current_setting('transaction_read_only')"):
            return 'transaction_contract'
        if normalized.startswith('with live as'):
            return 'public_manifest_digest'
        if 'recommendations_recommendationanime' not in normalized:
            return 'other_read'
        if 'feature_revision' in normalized:
            return 'public_revision_scan'
        # Only inspect SELECT columns, not predicates containing IDs.
        columns = normalized.split(' from ', 1)[0]
        if '"synopsis"' in columns or '"cover_url"' in columns:
            return 'candidate_hydration'
        if '"tags"' in columns:
            return 'changed_feature_rows'
        return 'source_safety'

    def __call__(self, execute, sql, params, many, context):
        phase = self.classify(sql)
        started = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            entry = self.phases.setdefault(phase, {'count': 0, 'execute_ms': 0., 'maximum_ms': 0.})
            elapsed = (perf_counter() - started) * 1000
            entry['count'] += 1; entry['execute_ms'] += elapsed
            entry['maximum_ms'] = max(entry['maximum_ms'], elapsed)

    def summary(self):
        return {key: {field: round(value, 3) if isinstance(value, float) else value
                      for field, value in row.items()} for key, row in sorted(self.phases.items())}


def public_server_probe():
    """Bounded EXPLAIN ANALYZE of public catalog ONLY, outside engine timing.

    Driver execution includes network/transfer/driver overhead. Its difference
    from EXPLAIN server time is not a pure network latency measurement.
    """
    from . import engine
    if connection.vendor != 'postgresql' or not connection.in_atomic_block:
        raise ValueError('Public probe requires a PostgreSQL read-only snapshot')
    with connection.cursor() as cursor:
        cursor.execute('SHOW transaction_read_only')
        if cursor.fetchone()[0] != 'on':
            raise ValueError('Public probe refuses a writable transaction')
        cursor.execute('SHOW transaction_isolation')
        if cursor.fetchone()[0] != 'repeatable read':
            raise ValueError('Public probe requires repeatable read')
        roundtrips = []
        for _ in range(3):
            started = perf_counter(); cursor.execute('SELECT 1'); cursor.fetchone()
            roundtrips.append(round((perf_counter() - started) * 1000, 3))
        # Exact default-control public revision SELECT, with the same row cap.
        query = engine.safe_catalog().filter(score__gte=0).annotate(
            feature_revision=engine.public_feature_expression()).order_by('bangumi_id').values_list(
                'bangumi_id', 'score', 'feature_revision', 'season', 'status_type')[:engine.MAX_CATALOG_ROWS + 1]
        sql, params = query.query.sql_with_params()
        started = perf_counter()
        cursor.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql, params)
        result = cursor.fetchone()[0][0]
        client_ms = (perf_counter() - started) * 1000
    return {'scope': 'public-catalog-default-control-only', 'read_only': True,
        'select_one_roundtrip_ms': roundtrips, 'explain_client_ms': round(client_ms, 3),
        'server_planning_ms': result['Planning Time'], 'server_execution_ms': result['Execution Time'],
        'public_rows': result['Plan']['Actual Rows'], 'sql_or_parameters_exported': False,
        'not_engine_performance': True, 'not_pure_network_measurement': True}
