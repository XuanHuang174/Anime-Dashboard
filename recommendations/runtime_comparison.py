"""V2-C same-pool, bit-exact runtime ablation on the signed V2-A cohort."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter_ns, process_time_ns

from django.conf import settings

from . import engine, ranking_v2, recall_v2, rerank_runtime
from .comparison import DEFAULT_LIMITS
from .recall_comparison import RecallObserver, verify_policy
from .recall_diagnostics import audit_sample, traced_rank, output_digest
from .version_control import canonical_bytes

CONTRACT = 'recommendations.runtime-comparison.v1'
REFERENCE_ARM = 'content.v2.recall-candidate'
FAST_ARM = 'content.v2.runtime-candidate'
ARMS = ('content.v1', REFERENCE_ARM, FAST_ARM)


def verify_runtime():
    config = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2c-runtime.v1.json').read_text(encoding='utf-8'))
    if config != rerank_runtime.runtime_contract():
        raise ValueError('Runtime preregistration differs from implementation')
    verify_policy()
    return hashlib.sha256(canonical_bytes(config)).hexdigest()


class RuntimeObserver(RecallObserver):
    def __init__(self, target=None):
        super().__init__(target)
        self.validated = []

    def audit_items(self, stage, items):
        super().audit_items(stage, items)
        if stage == 'validated':
            self.validated = list(items)  # One call only; never export rows.


def benchmark_kernel(items, context, weights, category):
    if not items:
        return {'status': 'not_measured_empty_pool'}
    vector = engine.taste_vector(context, deterministic=True)
    runs, digests = {'reference': [], 'optimized': []}, []
    stats = None
    for repeat in range(rerank_runtime.MICRO_REPEATS):
        # Alternate within the same hydrated pool; no DB/cache-size advantage.
        order = ('reference', 'optimized') if repeat % 2 == 0 else ('optimized', 'reference')
        for name in order:
            start, cpu = perf_counter_ns(), process_time_ns()
            if name == 'reference':
                rows = ranking_v2.rerank_candidates(items, vector, weights, category=category, limit=engine.MAX_RESULTS)
            else:
                rows, stats = rerank_runtime.rerank_candidates(items, vector, weights, category=category, limit=engine.MAX_RESULTS)
            runs[name].append({'wall_ms': (perf_counter_ns() - start) / 1e6,
                               'cpu_ms': (process_time_ns() - cpu) / 1e6})
            digests.append(output_digest(rows))
    if len(set(digests)) != 1:
        raise ValueError('Kernel changed full order/base score/evidence')
    return {'status': 'measured', 'same_hydrated_pool': True, 'output_equal': True,
            'repeats_per_arm': rerank_runtime.MICRO_REPEATS, 'candidate_count': len(items),
            'runs': runs, 'median_wall_ms': {name: round(median(row['wall_ms'] for row in values), 6)
                                           for name, values in runs.items()}, 'runtime_stats': stats,
            'not_http_or_production_cache_measurement': True}


def rank_arm(user, context, index, filters, arm, category, *, target=None):
    if arm not in ARMS:
        raise ValueError('Unknown runtime arm')
    observer = RuntimeObserver(target)
    v2 = arm != 'content.v1'
    rows, run = traced_rank(user, context, index, filters, 'content.v2' if v2 else 'content.v1', category,
        target=target, recall_policy_version=recall_v2.VERSION if v2 else None,
        runtime_version=rerank_runtime.VERSION if arm == FAST_ARM else None,
        observer_type=lambda identity=None: observer)
    # Benchmark is separately timed and never included in engine stage/wall.
    if arm == FAST_ARM and target is None:
        run['kernel_benchmark'] = benchmark_kernel(observer.validated, context, index['weights'], category)
    return rows, run


def assert_equivalent(left, right):
    for key in ('output_hmac', 'presentation_hmac'):
        if left['noninterference'][key] != right['noninterference'][key]:
            raise ValueError('Runtime changed ordering/scores/evidence/presentation')
    if left['pipeline'] != right['pipeline']:
        raise ValueError('Runtime changed eligibility/pool/hydration/target destination')
    for name in ('control', 'observed'):
        if left['runs'][name]['sql_count'] != right['runs'][name]['sql_count']:
            raise ValueError('Runtime changed SQL count')


def compare_runtime_sample(user, context, summary, index, catalog, states, config, progress=lambda text: None):
    local = deepcopy(config); local['algorithms'] = list(ARMS)
    result = audit_sample(user, context, summary, index, catalog, states, local, progress, ranker=rank_arm)
    for category in config['categories']:
        for left, right in zip(result['baseline'][REFERENCE_ARM][category]['runs'],
                               result['baseline'][FAST_ARM][category]['runs']):
            assert_equivalent(left, right)
    for fold in result['folds']:
        for category in config['categories']:
            assert_equivalent(fold['arms'][REFERENCE_ARM][category], fold['arms'][FAST_ARM][category])
    for probe in result['filter_probes'].values():
        if probe['status'] == 'evaluated':
            assert_equivalent(probe['results'][REFERENCE_ARM]['run'], probe['results'][FAST_ARM]['run'])
    result['runtime_equivalence'] = 'pass'
    measurements = [run['runs'][name] for categories in result['baseline'].values()
                    for cell in categories.values() for run in cell['runs'] for name in ('control', 'observed')]
    measurements += [run['runs'][name] for fold in result['folds'] for categories in fold['arms'].values()
                     for run in categories.values() for name in ('control', 'observed')]
    maximum_ms = max(row['wall_ms'] for row in measurements)
    maximum_sql = max(row['sql_count'] for row in measurements)
    result['performance_budget_observation'] = {
        'status': 'within_unchanged_local_budget' if maximum_ms <= DEFAULT_LIMITS['maximum_compute_ms']
            and maximum_sql <= DEFAULT_LIMITS['maximum_sql_queries'] else 'exceeded_local_budget',
        'maximum_wall_ms': maximum_ms, 'maximum_sql_queries': maximum_sql,
        'limits': {key: DEFAULT_LIMITS[key] for key in ('maximum_compute_ms', 'maximum_sql_queries')},
        'includes_observer_overhead': True, 'not_a_production_release_gate': True}
    return result


def metadata():
    return {'contract_version': CONTRACT, 'runtime_arms': list(ARMS), 'runtime_policy': rerank_runtime.runtime_contract(),
            'runtime_policy_sha256': verify_runtime(), 'recall_policy': recall_v2.policy_contract(),
            'recall_policy_sha256': verify_policy(), 'candidate_not_served_or_cached': True,
            'decision': 'equivalent-offline-runtime-only-no-release-approval'}


def markdown_report(report):
    lines = ['# V2-C 等价运行时与分阶段性能对照', '',
        '线上V1不变，不开启V2；固定修正召回v2，同候选池/同分数/同证据比较。',
        f"运行时：{report['runtime_policy']['version']}；配置SHA256：{report['runtime_policy_sha256']}",
        '准备索引耗时：' + str(report['setup']) + '；不混入单次排名，不冒充冷/热缓存测量。', '']
    for number, sample in enumerate(report['samples'], 1):
        lines += [f'## sample_{number:03d}', '', f"模式：{sample['summary']['mode']}；有效样本：{sample['summary']['state']['usable_sample_count']}；完整等价校验：{sample['runtime_equivalence']}。", '',
            f"原预算本机观测（非上线许可）：{sample['performance_budget_observation']}", '',
            '| 对照臂 | 类别 | 排名wall ms（控制运行） | SQL次数 |', '| --- | --- | --- | --- |']
        for arm, categories in sample['baseline'].items():
            for category, cell in categories.items():
                lines.append(f"| {arm} | {category} | {[run['runs']['control']['wall_ms'] for run in cell['runs']]} | {[run['runs']['control']['sql_count'] for run in cell['runs']]} |")
        lines += ['', '| 类别 | 同池数量 | 原内核中位ms | 优化内核中位ms | 重叠缓存命中/未命中 |',
                  '| --- | ---: | ---: | ---: | --- |']
        for category, cell in sample['baseline'][FAST_ARM].items():
            for run in cell['runs']:
                bench = run['kernel_benchmark']
                if bench['status'] == 'measured':
                    stats = bench['runtime_stats']
                    lines.append(f"| {category} | {bench['candidate_count']} | {bench['median_wall_ms']['reference']} | {bench['median_wall_ms']['optimized']} | {stats['pair_cache_hits']}/{stats['pair_cache_misses']} |")
        lines += ['', '留出原分母及去向未改变：' + str(sample['holdout_sets']['expanded']['metrics']), '']
    lines += ['## 口径与回退',
        '- 完整排名包含实时公共SHA256读取、水合、源安全复核及计算；展示复核另计，不扣除SQL耗时放宽门槛。',
        '- 内核只比较同一已水合候选池，交替执行5次；不是新增独立样本，不报告伪精确p95。',
        '- 缓存仅为单次重排的题材对重叠值；不含跨用户画像缓存，不跳过任何实时安全/版本校验。',
        '- 不改变固定目标/400池/120返回/Top10，少样本仍为证据不足。',
        '- 停止离线命令即可回退；普通V1/V2入口未选新运行时，未签发approval.json。',
        '- Render端HTTP性能、增强9/V2兼容及正式上线仍需后续独立验收。']
    return '\n'.join(lines) + '\n'
