"""D1 same frozen cohort parity plus real control/filter probes; never serves V2."""
from copy import deepcopy

from . import v2_controls, recall_v2, rerank_runtime
from .comparison import comparable_metrics
from .execution import execute_v2, recipe_descriptor
from .recall_comparison import RecallObserver
from .recall_diagnostics import audit_sample, traced_rank

REFERENCE = 'content.v2.runtime-reference'
PREPARED = 'content.v2.execution-balanced'
ARMS = ('content.v1', REFERENCE, PREPARED)


def trace_controls(user, context, index, filters, category, *, style='balanced', target=None):
    def execute(owner, options, public_index, **kwargs):
        return execute_v2(owner, {**options, 'style': style,
            **{key: filters[key] for key in ('year_from', 'year_to')}}, public_index, **kwargs)
    return traced_rank(user, context, index, filters, 'content.v2', category, target=target,
                        observer_type=RecallObserver, prepared_executor=execute)


def rank_arm(user, context, index, filters, arm, category, *, target=None):
    if arm == PREPARED:
        return trace_controls(user, context, index, filters, category, target=target)
    if arm not in ('content.v1', REFERENCE):
        raise ValueError('Unknown D1 execution arm')
    return traced_rank(user, context, index, filters, 'content.v1' if arm == 'content.v1' else 'content.v2',
        category, target=target, observer_type=RecallObserver,
        recall_policy_version=recall_v2.VERSION if arm == REFERENCE else None,
        runtime_version=rerank_runtime.VERSION if arm == REFERENCE else None)


def assert_balanced(left, right):
    for key in ('output_hmac', 'presentation_hmac'):
        if left['noninterference'][key] != right['noninterference'][key]:
            raise ValueError('D1 balanced differs from verified V2-C')
    if left['pipeline'] != right['pipeline']:
        raise ValueError('D1 balanced changed full pipeline membership or destination')
    # The prepared facade verifies transaction_read_only + isolation with SHOW.
    # Exactly two extra contract checks; ranking queries themselves unchanged.
    for name in ('control', 'observed'):
        if right['runs'][name]['sql_count'] != left['runs'][name]['sql_count'] + 2:
            raise ValueError('Unexpected D1 SQL change beyond the two snapshot checks')


def audit_execution(user, context, summary, index, catalog, states, config, progress=lambda text: None):
    local = deepcopy(config); local['algorithms'] = list(ARMS)
    result = audit_sample(user, context, summary, index, catalog, states, local, progress, ranker=rank_arm)
    for category in config['categories']:
        for left, right in zip(result['baseline'][REFERENCE][category]['runs'], result['baseline'][PREPARED][category]['runs']):
            assert_balanced(left, right)
    for fold in result['folds']:
        for category in config['categories']:
            assert_balanced(fold['arms'][REFERENCE][category], fold['arms'][PREPARED][category])
    features = {identity: tags for identity, tags, _, _ in index['features']}
    probes = []
    baseline = config['baseline_filters']
    # Fixed probes declared before natural audit; no target/score tuning.
    for style, filters in [('close', baseline), ('open', baseline),
            ('balanced', {**baseline, 'type': 'TV', 'min_score': 7, 'year_from': 2020, 'year_to': 2026})]:
        for category in config['categories']:
            rows, run = trace_controls(user, context, index, filters, category, style=style)
            if any(row['anime'].nsfw is not False or not v2_controls.matches({
                    key: getattr(row['anime'], key) for key in ('score', 'season', 'status_type')}, filters) for row in rows):
                raise ValueError('Actual D1 returned row violates live filters/safety')
            metrics = comparable_metrics(rows, context, context, features, 10)
            metrics.pop('top_bangumi_ids', None)
            probes.append({'style': style, 'category': category, 'filters': filters,
                           'metrics': metrics, 'run': run, 'live_filters_passed': True})
            progress(f'D1 actual controls {style}/{category}/year={filters["year_from"] is not None}')
    result.update(d1_balanced_equivalence='pass', d1_actual_control_probes=probes)
    return result


def metadata():
    return {'contract_version': 'recommendations.execution-audit.v1',
            'execution_recipe': recipe_descriptor(), 'serving_changed': False,
            'decision': 'D1-prepared-only-no-V2-browse-or-release-approval'}


def markdown_report(report):
    lines = ['# V2-D1 执行合同与控制适配只读验收', '',
        'D1准备服务，线上仍为V1；D2快照/详情绑定及D3部署验收未执行。',
        '执行合同：' + str(report['execution_recipe']), '']
    for number, sample in enumerate(report['samples'], 1):
        lines += [f'## sample_{number:03d}', '',
            f"模式：{sample['summary']['mode']}；有效样本：{sample['summary']['state']['usable_sample_count']}；均衡完整等价：{sample['d1_balanced_equivalence']}。", '',
            '| 风格 | 类别 | 年份区间 | 返回数 | 实时筛选检查 |', '| --- | --- | --- | ---: | --- |']
        for probe in sample['d1_actual_control_probes']:
            lines.append(f"| {probe['style']} | {probe['category']} | {probe['filters']['year_from']}—{probe['filters']['year_to']} | {probe['metrics']['result_count']} | {probe['live_filters_passed']} |")
        lines += ['', '原冻结目标集去向与指标：' + str(sample['holdout_sets']['expanded']['metrics']), '']
    lines += ['- 旧独立引擎的年份探针仍标记不支持；D1新控制探针实际执行年份，不混淆两条路径。',
        '- 均衡分数/排序/证据/展示令牌及全管道相等；D1额外2次SHOW检查只读快照，不隐瞒SQL成本。',
        '- 非默认风格使用V2相关性小幅加权及版本化重复惩罚，不套用V1公式、不引入无关联作品。',
        '- 只读两个原授权账号，原历史4/扩展12集不变，未扩展/挑选目标，不签发上线许可。',
        '- 全站样本可用性是另一个显式授权、仅聚合的命令，不自动替换本报告的固定集。']
    return '\n'.join(lines) + '\n'
