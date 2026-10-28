import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from recommendations.credits_performance import profile
from recommendations.credits_release import kinds_argument
from recommendations.source_pipeline import artifact_writer, SourceError


class Command(BaseCommand):
    help = '13F 有界只读测量公共参与关系查询，不模拟登录，不扫描私人记录，不执行写库。'

    def add_arguments(self, parser):
        parser.add_argument('--kinds', choices=('both', 'studio', 'voice_actor'), default='both')
        parser.add_argument('--samples', type=int, choices=(1, 2), default=2)
        parser.add_argument('--output', type=Path, required=True)

    def handle(self, *args, **options):
        try:
            if options['output'].exists(): raise SourceError('报告已存在。')
            result = profile(kinds_argument(options['kinds']), options['samples'])
            with artifact_writer(options['output']) as file:
                json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False); file.write('\n')
        except (OSError, ValueError, DatabaseError, SourceError) as exc:
            raise CommandError('查询性能测量未完成（' + type(exc).__name__ + '），没有修改数据库。') from None
        self.stdout.write(json.dumps({key: result[key] for key in ('status', 'issues')}, ensure_ascii=False))
        if result['issues']: raise CommandError('查询测量条件不满足，报告已保留。')
