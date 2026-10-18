from django.core.management.base import CommandError
from recommendations.management.commands.audit_recommendation_recall import Command as AuditCommand
from recommendations.exploration_audit import audit_history
from recommendations.exploration_candidate import execute_protected_candidate, recipe_descriptor, PROTECTED_VERSION
from recommendations.execution import recipe_descriptor as reference_recipe


def audit(*args, **kwargs):
    return audit_history(*args, **kwargs, candidate_executor=execute_protected_candidate)


class Command(AuditCommand):
    help = 'Original fixed history, protected exploration candidate; read-only, no serving.'
    sample_auditor = staticmethod(audit)
    report_metadata = staticmethod(lambda: {
        'contract_version': 'recommendations.protected-exploration-history.v1',
        'candidate_recipe': recipe_descriptor(PROTECTED_VERSION), 'reference_recipe': reference_recipe(),
        'serving_changed': False, 'decision': 'offline-only-no-approval'})
    report_renderer = staticmethod(lambda report: '# Protected exploration history\n\nFull fixed-cohort results in recall-audit.json; no approval.\n')

    def handle(self, *args, **options):
        if options.get('freeze_only') or not options.get('cohort_lock'):
            raise CommandError('Original historical lock required; no refreeze')
        recipe_descriptor(PROTECTED_VERSION)
        return super().handle(*args, **options)
