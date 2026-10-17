"""V2-B single-variable comparison using the signed, previously frozen A lock."""
from django.core.management.base import CommandError

from recommendations.management.commands.audit_recommendation_recall import Command as AuditCommand
from recommendations.recall_comparison import compare_sample, markdown_report, metadata, verify_policy


class Command(AuditCommand):
    help = 'V2-B：只读固定V2-A目标集；V1 / 原V2 / 新召回三臂对照，不上线V2。'
    sample_auditor = staticmethod(compare_sample)
    report_renderer = staticmethod(markdown_report)
    report_metadata = staticmethod(metadata)

    def handle(self, *args, **options):
        if options.get('freeze_only') or not options.get('cohort_lock'):
            raise CommandError('V2-B requires an existing V2-A cohort lock; refreezing is forbidden.')
        try:
            before = verify_policy()
            result = super().handle(*args, **options)
            if verify_policy() != before:
                raise ValueError('Recall policy changed during comparison')
            return result
        except ValueError as exc:
            raise CommandError(str(exc)) from None
