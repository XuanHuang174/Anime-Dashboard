import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from recommendations.credits_release import audit, kinds_argument
from recommendations.source_pipeline import artifact_writer, SourceError


class Command(BaseCommand):
    help = '13F 只读审计迁移、固定覆盖门槛、容量与独立开关；不写库，不放行功能。'

    def add_arguments(self, parser):
        parser.add_argument('--stage', choices=('closed', 'canary', 'full'), default='closed')
        parser.add_argument('--kinds', choices=('both', 'studio', 'voice_actor'), default='both')
        parser.add_argument('--output', type=Path, required=True)

    def handle(self, *args, **options):
        try:
            if options['output'].exists(): raise SourceError('报告已存在，请换一个明确路径。')
            result = audit(options['stage'], kinds_argument(options['kinds']))
            with artifact_writer(options['output']) as file:
                json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False); file.write('\n')
        except (OSError, ValueError, DatabaseError, SourceError) as exc:
            raise CommandError('参与资料发布审计未完成（' + type(exc).__name__ + '），没有修改数据库或开关。') from None
        self.stdout.write(json.dumps({key: result[key] for key in ('status', 'issues', 'policy_fingerprint')}, ensure_ascii=False))
        if result['issues']: raise CommandError('发布条件不满足，诊断报告已保留。')
