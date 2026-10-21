import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from recommendations.content_scope_audit import audit_public_content_scope


class Command(BaseCommand):
    help = 'N1 public catalog aggregate audit, PostgreSQL READ ONLY; no private users or media requests.'

    def handle(self, *args, **options):
        try:
            report = audit_public_content_scope()
        except (DatabaseError, ValueError) as exc:
            # No SQL/DSN, titles, cover URLs or credentials in diagnostic errors.
            raise CommandError(f'N1 audit stopped safely ({type(exc).__name__}); no grants or writes') from None
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
