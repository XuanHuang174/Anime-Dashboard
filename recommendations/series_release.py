"""12F bounded operator-only coverage audit; no journal, network or business writes."""
from collections import Counter
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Q, Subquery, Max
from django.utils import timezone

from .browsing import catalog_revision
from .models import RecommendationAnime
from .quality_readonly import readonly_snapshot
from .series_contract import BY_LABEL, MAPPING_VERSION
from .series_models import SeriesNode, SeriesHead, SeriesAssertion, SeriesState, SeriesOverrideEvent
from .series_rollout import canary_users
from .series_source import digest

VERSION = 'recommendations.series.release.v1'
MAX_CATALOG = 50000
MAX_NODES = 200000
MAX_EDGES = 500000
REQUIRED_MIGRATIONS = ('0013_series_relations', '0014_series_reminders')


def code_revision():
    root = Path(settings.BASE_DIR)
    paths = sorted([*root.joinpath('recommendations').rglob('*.py'),
                    *root.joinpath('frontend/src').rglob('*.js'), *root.joinpath('frontend/src').rglob('*.jsx'),
                    *root.joinpath('frontend/src').rglob('*.css'), root / 'config/settings.py',
                    root / 'scripts/verify_series_deployment.py'])
    import hashlib
    value = hashlib.sha256()
    for path in paths:
        value.update(path.relative_to(root).as_posix().encode()); value.update(b'\0'); value.update(path.read_bytes())
    return value.hexdigest()


def rollout(stage):
    if stage not in ('closed', 'canary', 'full'):
        raise ValueError('Unknown release stage')
    users = canary_users()
    flags = {key: getattr(settings, key, False) is True for key in (
        'RECOMMENDATION_SERIES_ENABLED', 'RECOMMENDATION_SERIES_EXPLORATION_ENABLED', 'RECOMMENDATION_SERIES_REMINDERS_ENABLED')}
    issues = []
    if users is None:
        issues.append('invalid_series_canary_config')
    if stage == 'closed':
        if flags['RECOMMENDATION_SERIES_ENABLED']:
            issues.append('core_gate_not_closed')
    else:
        if not all(flags.values()):
            issues.append('series_gates_incomplete')
        if not getattr(settings, 'RECOMMENDATION_EXPLORATION_ENABLED', False) is True:
            issues.append('existing_exploration_disabled')
        # This release must not restrict the three already released modes.
        if getattr(settings, 'RECOMMENDATION_EXPLORATION_CANARY_USERS', []) != []:
            issues.append('existing_exploration_not_full')
        if stage == 'canary' and not users:
            issues.append('series_canary_accounts_required')
        if stage == 'full' and users != ():
            issues.append('series_canary_not_cleared')
    return {'expected_stage': stage, 'flags': flags, 'series_canary_count': len(users) if users is not None else None,
            'issues': issues, 'account_existence_verified': False}


def coverage():
    with readonly_snapshot():
        applied = set(MigrationRecorder.Migration.objects.filter(app='recommendations').values_list('name', flat=True))
        missing = sorted(set(REQUIRED_MIGRATIONS) - applied)
        if missing:
            return {'schema_ready': False, 'missing_migrations': missing}
        catalog = dict(RecommendationAnime.objects.filter(bangumi_id__gt=0).order_by('bangumi_id')
                       .values_list('bangumi_id', 'nsfw')[:MAX_CATALOG + 1])
        if len(catalog) > MAX_CATALOG:
            raise ValueError('Catalog exceeds 50000; no partial coverage report issued')
        heads = list(SeriesHead.objects.order_by('source_id').values('source_id', 'current_id', 'last_attempt_id',
            'current__coverage', 'current__source_at', 'last_attempt__coverage')[:MAX_NODES + 1])
        if len(heads) > MAX_NODES:
            raise ValueError('Head count exceeds audit bound')
        nodes = list(SeriesNode.objects.order_by('pk').values_list('pk', 'subject_type', 'nsfw', 'metadata_revision',
            'release_date', 'release_format')[:MAX_NODES + 1])
        if len(nodes) > MAX_NODES:
            raise ValueError('Node count exceeds audit bound')
        node_map = {row[0]: row for row in nodes}
        visible = {identity for identity, rating in catalog.items() if rating is not None and identity in node_map
                   and node_map[identity][1] == 2 and node_map[identity][2] is rating}
        head_map = {row['source_id']: row for row in heads}
        cutoff = timezone.now() - timedelta(days=45)
        buckets = {}
        for name, rating in (('safe', False), ('adult', True), ('unknown', None)):
            ids = {identity for identity, value in catalog.items() if value is rating}
            states, attempts, fresh = Counter(), Counter(), 0
            for identity in ids:
                head = head_map.get(identity)
                status = head['current__coverage'] if head and head['current_id'] else 'not_loaded'
                if head and head['current_id'] and head['current__source_at'] < cutoff:
                    status = 'stale'
                states[status] += 1
                attempts[head['last_attempt__coverage'] if head and head['last_attempt_id'] else 'not_loaded'] += 1
                if identity in visible and status in ('observed', 'empty_observed'):
                    fresh += 1
            buckets[name] = {'catalog_count': len(ids), 'identity_consistent': len(ids & visible),
                'coverage_states': dict(sorted(states.items())), 'last_attempt_states': dict(sorted(attempts.items())),
                'fresh_complete_count': fresh, 'fresh_complete_percent': round(100 * fresh / len(ids), 3) if ids else None}
        current = SeriesHead.objects.exclude(current_id=None).values('current_id')
        catalog_ids = RecommendationAnime.objects.filter(bangumi_id__gt=0).values('bangumi_id')
        edges = SeriesAssertion.objects.filter(observation_id__in=Subquery(current)).filter(
            Q(observation__source_id__in=Subquery(catalog_ids)) | Q(target_id__in=Subquery(catalog_ids))).order_by('pk').values_list(
                'observation__source_id', 'target_id', 'kind', 'requires_review')
        count, kinds, blocked, degrees = 0, Counter(), Counter(), Counter()
        import hashlib
        edge_hash = hashlib.sha256()
        known = {spec.key for spec in BY_LABEL.values()}
        for source, target, kind, review in edges.iterator(chunk_size=2000):
            count += 1
            if count > MAX_EDGES:
                raise ValueError('Assertions exceed audit bound; no partial report issued')
            edge_hash.update(digest([source, target, kind, review]).encode())
            if source in catalog: degrees[source] += 1
            if target in catalog and target != source: degrees[target] += 1
            if review: blocked['requires_review'] += 1
            elif kind not in known: blocked['unknown_kind'] += 1
            elif source not in visible or target not in visible: blocked['identity_or_catalog_scope'] += 1
            elif source == target: blocked['self_relation'] += 1
            else: kinds[kind] += 1
        # Raw degree is an admission bound; it is NOT the effective/manual relation count.
        overrides = SeriesOverrideEvent.objects.count()
        last = SeriesOverrideEvent.objects.order_by().values('source_id', 'target_id').annotate(last=Max('pk')).values('last')
        manual_degrees, manual_count = Counter(), 0
        for source, target in SeriesOverrideEvent.objects.filter(pk__in=Subquery(last)).filter(
                Q(source_id__in=Subquery(catalog_ids)) | Q(target_id__in=Subquery(catalog_ids))).values_list('source_id', 'target_id').iterator(chunk_size=2000):
            manual_count += 1
            if manual_count > MAX_EDGES: raise ValueError('Manual decisions exceed audit bound')
            if source in catalog: manual_degrees[source] += 1
            if target in catalog and target != source: manual_degrees[target] += 1
        revision = SeriesState.objects.filter(pk=1).values_list('revision', flat=True).first() or 0
        fingerprint = digest([revision, catalog_revision(), nodes, edge_hash.hexdigest(), overrides,
            [[row['source_id'], row['current_id'], row['last_attempt_id'], row['current__coverage'],
              row['current__source_at'].isoformat() if row['current__source_at'] else None] for row in heads]])
        return {'schema_ready': True, 'missing_migrations': [], 'mapping_version': MAPPING_VERSION, 'catalog_count': len(catalog), 'segments': buckets,
            'series_revision': revision, 'fingerprint': fingerprint, 'current_touching_assertions': count,
            'raw_displayable_kind_counts': dict(sorted(kinds.items())), 'raw_blocked_counts': dict(sorted(blocked.items())),
            'manual_events': overrides, 'maximum_raw_touching_degree': max(degrees.values(), default=0),
            'maximum_latest_manual_touching_degree': max(manual_degrees.values(), default=0),
            'catalog_ids_over_read_bound': len({key for counter in (degrees, manual_degrees) for key, value in counter.items() if value > 5000}),
            'next_safe_seed_ids': sorted(identity for identity, rating in catalog.items() if rating is False and (
                identity not in visible or identity not in head_map or not head_map[identity]['current_id']
                or head_map[identity]['current__source_at'] < cutoff or head_map[identity]['current__coverage'] not in ('observed', 'empty_observed')))[:100],
            'semantics': 'source_observation_coverage_not_complete_franchise_or_effective_manual_graph',
            'adult_review_or_access_verified': False, 'private_records_read': False}


def audit(stage='closed', min_fresh_percent=90):
    if not 0 <= min_fresh_percent <= 100:
        raise ValueError('Coverage threshold must be 0–100')
    before = code_revision()
    data, config = coverage(), rollout(stage)
    issues = list(config['issues'])
    if data != coverage():
        issues.append('series_data_changed_during_audit')
    if not data['schema_ready']:
        issues.append('required_migrations_missing')
    else:
        safe = data['segments']['safe']
        if not safe['catalog_count']: issues.append('safe_catalog_empty')
        if not safe['fresh_complete_count'] or (safe['fresh_complete_percent'] or 0) < min_fresh_percent:
            issues.append('safe_fresh_coverage_below_threshold')
        if data['catalog_ids_over_read_bound']: issues.append('direct_assertion_read_bound_exceeded')
    if before != code_revision():
        raise ValueError('Code changed during audit')
    return {'contract_version': VERSION, 'generated_at': timezone.now().isoformat(), 'code_revision': before,
            'status': 'blocked' if issues else 'ready_for_operator_verification', 'issues': issues,
            'coverage': data, 'rollout': config, 'min_safe_fresh_percent': min_fresh_percent,
            'production_acceptance': 'not_performed', 'performance_acceptance': 'not_performed',
            'switches_changed': False, 'network_requests': 0}
