"""Exact public feature-input revisions; no private state or DB extensions.

Length prefixes disambiguate arbitrary punctuation/Unicode inside field values.
Safety flags and current scores are deliberately read separately on every rank.
"""
import hashlib

from django.db.models import TextField, Value
from django.db.models.functions import Cast, Coalesce, Concat, Length, SHA256

FIELDS = ('tags', 'title_zh', 'title_jp')


def public_feature_fingerprint(row):
    values = [row[field] or '' for field in FIELDS]
    payload = ''.join(f'{len(value)}:{value}' for value in values)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class NativeSHA256(SHA256):
    def as_postgresql(self, compiler, connection, **extra_context):
        # PostgreSQL's core bytea SHA256 avoids a pgcrypto migration/extension.
        return self.as_sql(compiler, connection,
            template="ENCODE(SHA256(CONVERT_TO(%(expressions)s, 'UTF8')), 'hex')", **extra_context)


def public_feature_expression():
    expressions = []
    for field in FIELDS:
        value = Coalesce(field, Value(''), output_field=TextField())
        expressions.extend((Cast(Length(value), TextField()), Value(':'), value))
    return NativeSHA256(Concat(*expressions, output_field=TextField()))
