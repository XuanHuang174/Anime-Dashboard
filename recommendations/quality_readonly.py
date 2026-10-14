"""Database-enforced read-only, consistent snapshots; no fallback to writable mode."""
from contextlib import contextmanager

from django.db import connection, transaction


@contextmanager
def readonly_snapshot(*, timeout_ms=30000):
    if not 1000 <= timeout_ms <= 120000:
        raise ValueError('statement timeout must be between 1000 and 120000 ms')
    if connection.vendor == 'postgresql':
        if connection.in_atomic_block:
            raise ValueError('Read-only PostgreSQL audit must start outside an existing transaction')
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
                cursor.execute("SET LOCAL lock_timeout = '3000ms'")
            yield
    elif connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            previous = cursor.execute('PRAGMA query_only').fetchone()[0]
            cursor.execute('PRAGMA query_only = ON')
        try:
            with transaction.atomic():
                yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'PRAGMA query_only = {int(previous)}')
    else:
        raise ValueError('Only PostgreSQL/SQLite support this enforced read-only audit')
