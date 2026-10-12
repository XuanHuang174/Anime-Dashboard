"""Default is read-only audit. Apply is explicit, batched and independently reversible."""
import json
import uuid
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db import DatabaseError
from django.db.models import Count, Max

from anime.models import UserAnimeRecord
from recommendations.binding import auto_link, rollback_event
from recommendations.matching import catalog_matcher, effective_metadata
from recommendations.models import RecommendationAnime, RecommendationIdentityEvent, RecommendationRecordIdentity
from recommendations.resolution import RECORD_FIELDS
from rest_framework.exceptions import APIException


class Command(BaseCommand):
    help = '审计/分批关联全站旧作品；默认 audit，不写数据库。'

    def add_arguments(self, parser):
        parser.add_argument('--mode', choices=('audit', 'apply', 'rollback'), default='audit')
        parser.add_argument('--username')
        parser.add_argument('--scope', choices=('all', 'high-score'), default='all')
        parser.add_argument('--batch-size', type=int, default=100)
        parser.add_argument('--batch-id', type=uuid.UUID)
        parser.add_argument('--confirm-production', action='store_true')
        parser.add_argument('--output', help='新建 JSON 报告，不覆盖已有文件；报告应保存在 gitignore 目录。')

    def handle(self, *args, **options):
        if options['mode'] != 'rollback':
            options['batch_id'] = options['batch_id'] or uuid.uuid4()
        with ExitStack() as stack:
            try:
                return self._execute(options, stack)
            except (APIException, DatabaseError) as exc:
                raise CommandError(f'身份操作中断（{type(exc).__name__}）；本批已回滚，已完成批次可按 {options["batch_id"]} 审计撤销。') from None

    def _execute(self, options, stack):
        mode = options['mode']
        if not 1 <= options['batch_size'] <= 500:
            raise CommandError('batch-size 必须为 1–500。')
        if mode != 'audit' and settings.DATABASE_URL and not options['confirm_production']:
            raise CommandError('已配置远程数据库；写入必须明确添加 --confirm-production。')
        if mode == 'rollback' and not options['batch_id']:
            raise CommandError('rollback 必须指定原 batch-id。')
        output_stream = None
        if options['output']:
            path = Path(options['output'])
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                output_stream = stack.enter_context(path.open('x', encoding='utf-8'))  # Reserve BEFORE any DB write.
            except OSError:
                raise CommandError('无法新建报告文件；尚未开始数据库写入。') from None
        # Audit also works BEFORE 0004 exists. No fake empty table is created.
        available = RecommendationRecordIdentity._meta.db_table in connection.introspection.table_names()
        if mode != 'audit' and not available:
            raise CommandError('须先应用 0004 身份关联迁移；本命令不会自动迁移。')
        batch_id = options['batch_id'] or uuid.uuid4()
        counts, entries = Counter(), []
        if mode == 'rollback':
            events = RecommendationIdentityEvent.objects.filter(batch_id=batch_id, action='linked').order_by('-pk')
            if options['username']:
                events = events.filter(record__user__username=options['username'])
            for event in events.iterator(chunk_size=options['batch_size']):
                counts[rollback_event(event)] += 1
        else:
            query = UserAnimeRecord.objects.filter(anime__bgm_id__isnull=True).order_by('pk')
            if options['username']:
                query = query.filter(user__username=options['username'])
            if options['scope'] == 'high-score':
                query = query.filter(watch_status='completed', personal_score__gte=settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD)
            matcher = catalog_matcher(force=True)
            linked = set(RecommendationRecordIdentity.objects.values_list('record_id', flat=True)) if available else set()
            rows = list(query.values(*RECORD_FIELDS))
            for start in range(0, len(rows), options['batch_size']):
                if mode == 'audit':
                    for row in rows[start:start+options['batch_size']]:
                        result = matcher.match(effective_metadata(row))
                        state = 'existing_link' if row['pk'] in linked else result['status']
                        counts[state] += 1
                        entries.append({'record_id': row['pk'], 'status': state,
                                        'target_bangumi_id': result['target']['bangumi_id'] if result['target'] else None})
                else:
                    matcher = catalog_matcher(force=True)
                    local = Counter()
                    with transaction.atomic():
                        signature = RecommendationAnime.objects.aggregate(count=Count('pk'), updated=Max('updated_at'))
                        for row in rows[start:start+options['batch_size']]:
                            record = UserAnimeRecord.objects.filter(pk=row['pk']).first()
                            if record:
                                local[auto_link(record, matcher=matcher, batch_id=batch_id)['status']] += 1
                        if signature != RecommendationAnime.objects.aggregate(count=Count('pk'), updated=Max('updated_at')):
                            raise CommandError(f'目录发生更新，本批已回滚。此前批次可通过 {batch_id} 撤销。')
                    counts.update(local)
                    self.stderr.write(f'已处理 {min(start+options["batch_size"],len(rows))}/{len(rows)}；batch-id={batch_id}')
        report = {'mode': mode, 'batch_id': str(batch_id), 'identity_schema_available': available,
                  'counts': dict(counts), 'entries': entries}
        if output_stream:
            json.dump(report, output_stream, ensure_ascii=False, indent=2)
        self.stdout.write(json.dumps(report, ensure_ascii=False))
