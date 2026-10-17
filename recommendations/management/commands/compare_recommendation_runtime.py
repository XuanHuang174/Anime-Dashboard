"""V2-C read-only exact runtime comparison; no production setting changes."""
from django.core.management.base import CommandError

from recommendations.management.commands.audit_recommendation_recall import Command as AuditCommand
from recommendations.runtime_comparison import compare_runtime_sample, metadata, markdown_report, verify_runtime


class Command(AuditCommand):
    help = 'V2-C：固定V2-A锁/修正召回v2的等价运行时对照，不开启线上V2。'
    sample_auditor = staticmethod(compare_runtime_sample)
    report_metadata = staticmethod(metadata)
    report_renderer = staticmethod(markdown_report)

    def handle(self, *args, **options):
        if options.get('freeze_only') or not options.get('cohort_lock'):
            raise CommandError('V2-C requires original cohort lock; no target refreezing.')
        try:
            verify_runtime()
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        return super().handle(*args, **options)
