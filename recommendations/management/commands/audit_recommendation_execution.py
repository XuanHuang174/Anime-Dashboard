from django.core.management.base import CommandError

from recommendations.management.commands.audit_recommendation_recall import Command as AuditCommand
from recommendations.execution import recipe_descriptor
from recommendations.execution_audit import audit_execution, metadata, markdown_report


class Command(AuditCommand):
    help = 'V2-D1：原冻结集均衡等价与真实风格/年份前置筛选验收，不开启V2。'
    sample_auditor = staticmethod(audit_execution)
    report_metadata = staticmethod(metadata)
    report_renderer = staticmethod(markdown_report)

    def handle(self, *args, **options):
        if options.get('freeze_only') or not options.get('cohort_lock'):
            raise CommandError('D1 requires original cohort lock; no target refreezing.')
        try:
            recipe_descriptor()
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        return super().handle(*args, **options)
