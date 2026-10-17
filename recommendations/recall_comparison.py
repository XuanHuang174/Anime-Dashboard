"""Paired V2-B recall ablation, reusing V2-A locked targets and real engine.

No score/rerank changes, new target selection, target injection or approval.
The baseline A config/hash/signature must still verify before this runs.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from django.conf import settings

from . import recall_v2
from .evidence import _token
from .pipeline_trace import PipelineObserver
from .recall_diagnostics import audit_sample, traced_rank
from .version_control import canonical_bytes

CONTRACT = 'recommendations.recall-comparison.v1'
CANDIDATE_ARM = 'content.v2.recall-candidate'
ARMS = ('content.v1', 'content.v2', CANDIDATE_ARM)
POLICY_PATH = 'evaluation/v2b-recall-policy.v1.json'


def verify_policy():
    value = json.loads((Path(settings.BASE_DIR) / POLICY_PATH).read_text(encoding='utf-8'))
    if value != recall_v2.policy_contract():
        raise ValueError('Predeclared recall policy differs from implementation; no silent tuning')
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class RecallObserver(PipelineObserver):
    def __init__(self, target=None):
        super().__init__(target)
        self.items = {}

    def audit_items(self, stage, items):
        # Order-independent eligible digest checks scores + evidence, not only
        # membership counts. Keyed fingerprints never expose private evidence.
        self.items[stage] = _token(CONTRACT + '.items', [
            (row['bangumi_id'], float(row['value']).hex(), sorted(row['tags']), row['evidence'], row['reason'])
            for row in sorted(items, key=lambda row: row['bangumi_id'])])

    def export(self, **kwargs):
        return {**super().export(**kwargs), 'item_hmacs': dict(self.items)}


def rank_arm(user, context, index, filters, algorithm, category, *, target=None):
    if algorithm not in ARMS:
        raise ValueError('Unknown comparison arm')
    candidate = algorithm == CANDIDATE_ARM
    return traced_rank(user, context, index, filters, 'content.v2' if candidate else algorithm, category,
        target=target, recall_policy_version=recall_v2.VERSION if candidate else None, observer_type=RecallObserver)


def assert_recall_only(left, right):
    a, b = left['pipeline'], right['pipeline']
    if (a['stages']['eligible'] != b['stages']['eligible']
            or a['item_hmacs'].get('eligible') != b['item_hmacs'].get('eligible')):
        raise ValueError('Recall-only invariant failed: eligible IDs/base scores/evidence changed')
    for run in (left, right):
        if run['pipeline']['stages']['pool']['count'] > 400 or run['pipeline']['stages']['returned']['count'] > 120:
            raise ValueError('Candidate/result cap changed')


def review_quality(result):
    """Conservative diagnostic checks, NOT a release gate or significance test.

    A successful command means a complete audit, never that a new policy won.
    Keep regressions and zero/insufficient holdout evidence explicitly visible.
    """
    checks = []
    for category, baseline in result['baseline']['content.v2'].items():
        left = baseline['metrics']['topic_overlap_proxy_mean']
        right = result['baseline'][CANDIDATE_ARM][category]['metrics']['topic_overlap_proxy_mean']
        checks.append({'kind': 'top10_topic_proxy', 'category': category, 'baseline': left, 'candidate': right,
                       'regression': left is not None and right is not None and right < left})
    group = result['holdout_sets']['expanded']
    positive_hit_gain = False
    for category, baseline in group['metrics']['content.v2'].items():
        candidate = group['metrics'][CANDIDATE_ARM][category]
        for key in ('conditional_pool_recall', 'hit_at_10', 'mrr_at_10', 'binary_ndcg_at_10'):
            left, right = baseline[key], candidate[key]
            checks.append({'kind': key, 'category': category, 'baseline': left, 'candidate': right,
                           'regression': left is not None and right is not None and right < left})
        left, right = baseline['hit_at_10'], candidate['hit_at_10']
        positive_hit_gain |= left is not None and right is not None and right > left
    regression = any(row['regression'] for row in checks)
    status = ('not_accepted_regression' if regression else 'insufficient_holdout_evidence'
              if not group['paired_count'] else 'no_proven_top10_gain' if not positive_hit_gain
              else 'requires_independent_validation')
    return {'status': status, 'checks': checks, 'positive_hit_gain_observed': positive_hit_gain,
            'not_statistical_significance_or_release_approval': True}


def compare_sample(user, context, summary, index, catalog, states, config, progress=lambda message: None):
    # Only labels are expanded; selection/K/repeats/filters remain verified A
    # values. No second config may select more favorable heldout identities.
    local = deepcopy(config)
    local['algorithms'] = list(ARMS)
    result = audit_sample(user, context, summary, index, catalog, states, local, progress, ranker=rank_arm)
    for category in config['categories']:
        for a, b in zip(result['baseline']['content.v2'][category]['runs'],
                        result['baseline'][CANDIDATE_ARM][category]['runs']):
            assert_recall_only(a, b)
    for fold in result['folds']:
        for category in config['categories']:
            assert_recall_only(fold['arms']['content.v2'][category], fold['arms'][CANDIDATE_ARM][category])
    result['recall_only_invariant'] = 'pass'
    result['quality_review'] = review_quality(result)
    result['comparison_limits'] = 'Two authorized accounts; no human preference probability/online lift claims.'
    return result


def metadata():
    return {'contract_version': CONTRACT, 'comparison_arms': list(ARMS),
        'recall_policy': recall_v2.policy_contract(), 'recall_policy_sha256': verify_policy(),
        'frozen_scoring_and_reranking': True, 'candidate_not_served_or_cached': True,
        'decision': 'offline_evidence_only_not_release_approval'}


def markdown_report(report):
    lines = ['# V2-B 相关性约束召回消融对照', '',
        '线上默认V1不变；V2基线保留。候选仅离线调用，评分/重排冻结；不签发上线许可。',
        f"策略：{report['recall_policy']['version']}；SHA256：{report['recall_policy_sha256']}",
        f"固定A配置：{report['config_sha256']}；目标锁：{report['cohort_signature']}",
        '历史4 / 扩展12 / Top10 / 400池 / 返回120均不变。条件池覆盖不等同于命中率。', '']
    for number, sample in enumerate(report['samples'], 1):
        lines += [f'## sample_{number:03d}', '',
            f"有效样本：{sample['summary']['state']['usable_sample_count']}；模式：{sample['summary']['mode']}；召回单变量约束：{sample['recall_only_invariant']}。", '',
            f"质量复核：**{sample['quality_review']['status']}**；命令执行成功仅代表审计完整，不等于优化通过。", '',
            '| 集合 | 对照臂 | 分类 | 目标数 | Hit@10 | 条件池覆盖 | 去向 |',
            '| --- | --- | --- | ---: | ---: | ---: | --- |']
        for name, group in sample['holdout_sets'].items():
            for arm, categories in group['metrics'].items():
                for category, metrics in categories.items():
                    lines.append(f"| {name} | {arm} | {category} | {group['paired_count']} | {metrics['hit_at_10']} | {metrics['conditional_pool_recall']} | {metrics['reason_counts_all_selected']} |")
        lines += ['', '| 对照臂 | 分类 | 题材关联代理 | 题材组合重复度 | 新题材方向数 | 最大墙钟ms | 最大SQL |',
                  '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
        for arm, categories in sample['baseline'].items():
            for category, cell in categories.items():
                m = cell['metrics']; runs = [run['runs']['control'] for run in cell['runs']]
                lines.append(f"| {arm} | {category} | {m['topic_overlap_proxy_mean']} | {m['duplicate_genre_set_ratio']} | {m['novel_direction_count']} | {max(run['wall_ms'] for run in runs)} | {max(run['sql_count'] for run in runs)} |")
        lines += ['', f"留出状态：{sample['holdout_sets']['expanded']['status']}（不足样本保持null，不造分）。", '']
    lines += ['## 限制与后续',
        '- 同一只读REPEATABLE READ快照，固定目标/目录校验失败立即停止；不重选目标。',
        '- 合格池外减少不代表Top10改善；必须同时阅读原分母Hit/MRR/NDCG和相关性指标。',
        '- 只测当前题材关联合理性，不推断续作/系列，不用标题简介或长评补题材。',
        '- 公共评分只是公共评价；相对分数门槛为内部排序单位，不是喜欢概率。',
        '- 本机跨网Neon、预建公共索引的引擎耗时不是Render端完整HTTP性能，不承担V2上线结论。',
        '- 无私人结果缓存、无前端改动、未接增强9/V2游标；仍需后续性能优化、正式重评及兼容验收。']
    return '\n'.join(lines) + '\n'
