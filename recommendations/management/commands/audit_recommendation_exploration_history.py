from django.core.management.base import CommandError
from recommendations.management.commands.audit_recommendation_recall import Command as AuditCommand
from recommendations.exploration_audit import audit_history, metadata


class Command(AuditCommand):
    help = 'Original historical targets, three-arm exploration comparison; no serving or refreeze.'
    sample_auditor = staticmethod(audit_history)
    report_metadata = staticmethod(metadata)
    # Generic historical renderer expects old arm names; retain full JSON instead.
    report_renderer = staticmethod(lambda report: '# Frozen exploration history\n\nSee recall-audit.json; offline only, no approval.\n')

    def handle(self, *args, **options):
        if options.get('freeze_only') or not options.get('cohort_lock'):
            raise CommandError('Original historical lock required; no refreeze')
        return super().handle(*args, **options)
