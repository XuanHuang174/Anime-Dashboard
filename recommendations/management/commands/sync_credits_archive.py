"""Operator-only: prepare first, then explicitly apply the identical local plan."""
import json
from pathlib import Path
import re
import zipfile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from recommendations.models import RecommendationAnime
from recommendations.credits_source import ArchivePlan, MAX_CATALOG, MAX_BATCH, digest
from recommendations.credits_store import publish_batch
from recommendations.credits_models import CreditsSnapshot, CreditsObservation, CreditsHead, CreditsState
from recommendations.quality_readonly import readonly_snapshot
from recommendations.source_pipeline import SourceError, artifact_writer


class CreditsWriteGuard:
    allowed = {f'recommendations_credits{name}' for name in
        ('state', 'snapshot', 'subject', 'person', 'character', 'alias', 'observation', 'head', 'assertion')}

    def __call__(self, execute, sql, params, many, context):
        text = sql.strip()
        if re.match(r'^(INSERT|UPDATE|DELETE)\b', text, re.I):
            match = re.match(r'^(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?([a-z_]+)"?', text, re.I)
            if not match or match[1].lower() not in self.allowed:
                raise SourceError('参与关系导入拒绝修改独立资料表之外的数据。')
        elif not re.match(r'^(SELECT|SET|SAVEPOINT|RELEASE|ROLLBACK|BEGIN|COMMIT)\b', text, re.I):
            raise SourceError('参与关系导入拒绝非预期 SQL。')
        return execute(sql, params, many, context)


class CreditsPreviewGuard:
    """Explicit preview cannot emit business writes, DDL, COPY or writable CTEs."""
    def __call__(self, execute, sql, params, many, context):
        if not re.match(r'^(SELECT|SET|SAVEPOINT|RELEASE|ROLLBACK|BEGIN|COMMIT)\b', sql.strip(), re.I):
            raise SourceError('只读预检禁止执行写入 SQL。')
        return execute(sql, params, many, context)


def catalog_snapshot():
    with readonly_snapshot():
        rows = list(RecommendationAnime.objects.filter(bangumi_id__gt=0).order_by('bangumi_id')
                    .values_list('bangumi_id', 'nsfw')[:MAX_CATALOG + 1])
        if not rows or len(rows) > MAX_CATALOG:
            raise SourceError('目录为空或超过预检上限。')
        return rows


def verified_resume(plan, report, path):
    """A saved receipt is only a hint: verify its committed prefix against the DB."""
    if path.stat().st_size > 16 * 1024 ** 2:
        raise SourceError('续跑报告超过读取上限。')
    previous = json.loads(path.read_text(encoding='utf-8'))
    keys = ('snapshot', 'plan_fingerprint', 'source_count', 'scope', 'batch_size',
            'batch_count', 'catalog_identity_fingerprint')
    if (not isinstance(previous, dict) or previous.get('mode') != 'apply'
            or any(previous.get(key) != report[key] for key in keys)):
        raise SourceError('续跑报告与当前归档、目录或批次计划不一致。')
    publication = previous.get('publication')
    if not isinstance(publication, list) or len(publication) > report['batch_count']:
        raise SourceError('续跑报告的已提交批次无效。')
    revision = 0
    for index, result in enumerate(publication):
        count = 2 * len(plan.seeds[index * report['batch_size']:(index + 1) * report['batch_size']])
        if (not isinstance(result, dict) or type(result.get('changed')) is not bool
                or any(type(result.get(key)) is not int or result[key] < 0
                       for key in ('revision', 'published', 'quarantined', 'replayed'))
                or sum(result[key] for key in ('published', 'quarantined', 'replayed')) != count
                or result['revision'] < revision or result['changed'] and result['revision'] <= revision):
            raise SourceError('续跑报告的提交计数或修订号无效。')
        revision = result['revision']
    selected = plan.seeds[:len(publication) * report['batch_size']]
    expected = {}
    for offset in range(0, len(selected), MAX_BATCH):
        for row in plan.batch(selected[offset:offset + MAX_BATCH])['observations']:
            expected[row['bangumi_id'], row['kind']] = {
                field: row[field] for field in ('fingerprint', 'coverage', 'diagnostics')}
    with readonly_snapshot():
        snapshot = CreditsSnapshot.objects.filter(pk=plan.snapshot['id']).values().first()
        if publication and (snapshot is None or any(
                snapshot[key].isoformat() != value if key == 'source_at' else snapshot[key] != value
                for key, value in plan.snapshot.items())):
            raise SourceError('续跑来源快照与数据库不一致。')
        state = CreditsState.objects.filter(pk=1).values_list('revision', flat=True).first() or 0
        observations = {(row['bangumi_id'], row['kind']): row for row in
            CreditsObservation.objects.filter(snapshot_id=plan.snapshot['id'], bangumi_id__in=selected)
            .values('id', 'bangumi_id', 'kind', 'fingerprint', 'coverage', 'diagnostics')}
        heads = {(row['bangumi_id'], row['kind']): row for row in
            CreditsHead.objects.filter(bangumi_id__in=selected).values('bangumi_id', 'kind', 'current_id', 'last_attempt_id')}
        if state < revision or observations.keys() != expected.keys() or heads.keys() != expected.keys():
            raise SourceError('续跑记录未在数据库中完整提交，不能跳过。')
        for key, row in expected.items():
            stored, head = observations[key], heads[key]
            complete = row['coverage'] in ('observed', 'empty_observed')
            if (any(stored[field] != row[field] for field in ('fingerprint', 'coverage', 'diagnostics'))
                    or head['last_attempt_id'] != stored['id']
                    or complete != (head['current_id'] == stored['id'])):
                raise SourceError('续跑批次的观察或发布指针已变化，不能跳过。')
    return publication, digest(previous)


class Command(BaseCommand):
    help = '默认只读预检官方参与关系归档；--apply 与匹配的 --expected-plan 才写入独立资料表。'

    def add_arguments(self, parser):
        parser.add_argument('--input', type=Path, required=True)
        parser.add_argument('--expected-sha256', required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--ids', help='明确的1至50000个作品 Bangumi ID，逗号分隔')
        group.add_argument('--catalog', action='store_true', help='按真实公共目录生成全量预检')
        parser.add_argument('--report', type=Path, required=True)
        parser.add_argument('--timeout-seconds', type=int, default=600)
        parser.add_argument('--batch-size', type=int, default=10)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--apply', action='store_true')
        mode.add_argument('--preview-only', action='store_true', help='显式只读预检，SQL 守卫禁止所有写库操作')
        parser.add_argument('--expected-plan')
        parser.add_argument('--resume-from', type=Path, help='核验原 apply 报告的已提交前缀，再继续后续批次')

    def handle(self, *args, **options):
        if options.get('preview_only') and options.get('apply'):
            raise CommandError('--preview-only 与 --apply 互斥。')
        if options.get('preview_only'):
            with connection.execute_wrapper(CreditsPreviewGuard()):
                return self.run(options)
        return self.run(options)

    def run(self, options):
        report = None
        try:
            if options['report'].exists() or not 1 <= options['batch_size'] <= MAX_BATCH:
                raise SourceError('报告已存在或批次大小无效。')
            if options['apply'] and not options['expected_plan']:
                raise SourceError('写入必须提供之前预检的 --expected-plan。')
            if options.get('resume_from') and not options['apply']:
                raise SourceError('--resume-from 只能用于显式 --apply。')
            catalog = catalog_snapshot() if options['catalog'] else None
            if catalog is None:
                if not re.fullmatch(r'[1-9][0-9]*(,[1-9][0-9]*)*', options['ids'] or ''):
                    raise SourceError('ID 格式无效。')
                seeds = [int(value) for value in options['ids'].split(',')]
            else:
                seeds = [row[0] for row in catalog]
            with ArchivePlan(options['input'], options['expected_sha256'], seeds,
                    timeout_seconds=options['timeout_seconds']) as plan:
                report = plan.preview(catalog)
                report.update(mode='apply' if options['apply'] else 'preview', publication=[],
                    batch_size=options['batch_size'], catalog_identity_fingerprint=digest(catalog))
                report['batch_count'] = (len(plan.seeds) + options['batch_size'] - 1) // options['batch_size']
                if options['apply']:
                    if options['expected_plan'] != report['plan_fingerprint']:
                        raise SourceError('归档、映射、选定作品或目录与预检计划不一致。')
                    if catalog is not None and catalog != catalog_snapshot():
                        raise SourceError('目录在预检期间变化，请重新预检。')
                    if options.get('resume_from'):
                        prior, receipt_hash = verified_resume(plan, report, options['resume_from'])
                        report.update(publication=prior, resumed_verified_batches=len(prior),
                                      resume_receipt_fingerprint=receipt_hash)
                        self.stdout.write(json.dumps({'resumed_verified_batches': len(prior)}))
                        self.stdout.flush()
                    expected_catalog = dict(catalog or [])
                    start = len(report['publication']) * options['batch_size']
                    for offset in range(start, len(plan.seeds), options['batch_size']):
                        selected = plan.seeds[offset:offset + options['batch_size']]
                        if catalog is not None:
                            with readonly_snapshot():
                                current = dict(RecommendationAnime.objects.filter(bangumi_id__in=selected).values_list('bangumi_id', 'nsfw'))
                        else:
                            current = None
                        if catalog is not None and current != {identity: expected_catalog[identity] for identity in selected}:
                            raise SourceError('目录已变化，停止后续批次，请重新预检。')
                        prepared = plan.batch(selected)
                        with connection.execute_wrapper(CreditsWriteGuard()):
                            result = publish_batch(prepared)
                        report['publication'].append(result)
                        report['database_writes'] |= result['changed']
                        self.stdout.write(json.dumps({'batch': len(report['publication']), **result}))
                        self.stdout.flush()
                    report['catalog_identity_unchanged'] = catalog is None or catalog == catalog_snapshot()
                    if not report['catalog_identity_unchanged']:
                        raise SourceError('导入期间目录发生变化；公开来源已保存，但必须重新预检并审计。')
            with artifact_writer(options['report']) as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
            self.stdout.write(json.dumps({key: report[key] for key in ('mode', 'source_count', 'plan_fingerprint', 'database_writes')}, ensure_ascii=False))
        except (SourceError, ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile, DatabaseError) as error:
            if report is not None and not options['report'].exists():
                report['failure_type'] = type(error).__name__
                report['status'] = 'stopped_partial_batches_may_be_committed'
                with artifact_writer(options['report']) as output:
                    json.dump(report, output, ensure_ascii=False, indent=2)
            detail = type(error).__name__ if isinstance(error, DatabaseError) else str(error)
            raise CommandError(f'参与关系导入停止：{detail}；已提交批次可按原预检计划幂等续跑。') from None
