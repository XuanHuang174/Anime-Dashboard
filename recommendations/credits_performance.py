"""Bounded public-source query profile, never a simulated production user login."""
import ctypes
import sys
import time
from django.db import connection
from django.db.models import Count, Subquery
from django.db.migrations.recorder import MigrationRecorder
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from . import credits_read as reads
from .credits_api_contract import KINDS
from .credits_coverage import REQUIRED_MIGRATION
from .credits_source import digest
from .content_scope import catalog_for_scope
from .quality_readonly import readonly_snapshot
from .serializers import RecommendationListAnimeSerializer


def process_memory():
    if sys.platform == 'win32':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return {'resident_bytes': counters.WorkingSetSize, 'process_lifetime_peak_bytes': counters.PeakWorkingSetSize}
    else:
        try:
            import resource
            return {'resident_bytes': None, 'process_lifetime_peak_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)}
        except ImportError: pass
    return {'resident_bytes': None, 'process_lifetime_peak_bytes': None}


def source_page(kind, person):
    scope = {'content_scope': 'safe'}
    entity, facts = reads.entity(scope, kind, person)
    page = list(catalog_for_scope(scope).filter(bangumi_id__in=Subquery(facts.values('observation__bangumi_id')))
                .defer('synopsis', 'source_cover_url').order_by('-score', 'bangumi_id')[:12])
    ids = [row.bangumi_id for row in page]
    related = reads.bounded(facts.filter(observation__bangumi_id__in=ids)
        .select_related('observation__snapshot', 'character'), reads.MAX_FACTS)
    from collections import defaultdict
    groups = defaultdict(list)
    for fact in related: groups[fact.observation.bangumi_id].append(fact)
    # Exercise actual public serialization and evidence, including multi-role works.
    data = [dict(RecommendationListAnimeSerializer(row, context={'content_scope': scope}).data,
                 evidence=reads.evidence(groups[row.bangumi_id], kind)) for row in page]
    return entity['work_count'], len(data)


def profile(kinds=KINDS, samples=2):
    if not kinds or any(k not in KINDS for k in kinds) or not 1 <= samples <= 2:
        raise ValueError('Bounded kinds/samples required')
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise ValueError('Standalone PostgreSQL profile required')
    report = {'contract_version': 'recommendations.credits.performance.v1', 'generated_at': timezone.now().isoformat(),
        'scope': 'safe_public_source_queries_not_personalized_http', 'private_records_read': False,
        'production_writes': False, 'server_sql_count': None, 'server_cpu_seconds': None,
        'browser_tested': False, 'measurements': [], 'issues': [], 'memory': process_memory()}
    with readonly_snapshot(timeout_ms=10000):
        if not MigrationRecorder.Migration.objects.filter(app='recommendations', name=REQUIRED_MIGRATION).exists():
            report['issues'].append('required_migration_missing')
            report['status'] = 'blocked'; return report
        before = (reads.revision(), reads.catalog_revision())
        report['safe_catalog_count'] = catalog_for_scope({'content_scope': 'safe'}).count()
        targets = {kind: list(reads.valid_facts({'content_scope': 'safe'}, kind).values('source_person_id')
            .annotate(n=Count('observation__bangumi_id', distinct=True)).order_by('-n', 'source_person_id')[:samples]) for kind in kinds}
    for kind in kinds:
        if not targets[kind]: report['issues'].append(kind + ':no_visible_sources')
        for target in targets[kind]:
            for phase in ('first', 'repeat'):
                start, cpu = time.perf_counter(), time.process_time()
                with CaptureQueriesContext(connection) as captured:
                    with readonly_snapshot(timeout_ms=10000):
                        count, page = source_page(kind, target['source_person_id'])
                seconds = time.perf_counter() - start
                passed = seconds <= 10 and len(captured) <= 55 and 0 < page <= 12
                report['measurements'].append({'kind': kind, 'person_id': target['source_person_id'], 'phase': phase,
                    'work_count': count, 'page_count': page, 'seconds': round(seconds, 4), 'sql_count': len(captured),
                    'operator_process_cpu_seconds': round(time.process_time() - cpu, 4), 'passed': passed})
                if not passed: report['issues'].append(kind + ':source_query_budget_failed')
    with readonly_snapshot(timeout_ms=10000):
        if before != (reads.revision(), reads.catalog_revision()): report['issues'].append('source_or_catalog_changed_during_profile')
    report['revision_fingerprint'] = digest(before)
    report['memory'] = process_memory()
    report['status'] = 'blocked' if report['issues'] else 'passed_source_query_profile'
    report['limitations'] = ['Operator process memory is not Render worker memory; lifetime peak is not a per-request allocation.',
        'Own-record/feedback resolution, login, HTTP and concurrency are not measured by this public-only profile.']
    return report
