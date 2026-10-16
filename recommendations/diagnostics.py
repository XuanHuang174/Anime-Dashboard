"""Current-rule, natural-account diagnostics. NEVER grants V2 deployment approval.

No mutations, user discovery, review reads, network calls or threshold changes.
Measures the real ranking functions, not a second approximate recommendation engine.
"""
from copy import deepcopy
from time import perf_counter
import hashlib
import math

from django.conf import settings

from .cold_start import cold_start_contract
from .comparison import context_snapshot, measured_rank, comparable_metrics
from .engine import build_feature_index, snapshot_token
from .quality_evaluation import (CATEGORIES, category_differences, evidence_errors,
                                 input_fingerprint, mean, without_seed)
from .taxonomy import TAXONOMY_VERSION
from .version_control import canonical_bytes

VERSIONS = ('content.v1', 'content.v2')


def prepare_sample(records, preference, owner_id, feedback):
    context = context_snapshot(records, preference, owner_id, TAXONOMY_VERSION, feedback=feedback)
    state = cold_start_contract(context['profile'], preference_tags=context['preferences']['tags'],
                                minimum=settings.RECOMMENDATION_PROFILE_MIN_SAMPLES)
    return context, {'state': state, 'mode': context['mode'],
        'identity_schema_available': getattr(records, 'schema_available', True),
        'holdout_eligible': (getattr(records, 'schema_available', True)
                            and len(context['seeds']) >= max(4, settings.RECOMMENDATION_PROFILE_MIN_SAMPLES + 1)),
        'automatic_genre_count': len(context['profile']['trusted_profile']['tag_summary']),
        'feedback_version': feedback['version'], 'hidden_work_count': len(feedback['hidden_ids']),
        'input_fingerprint': input_fingerprint(context)}


def result_checks(ranked, context, safe_ids):
    hidden = context['feedback']['hidden_ids']
    return {'unsafe_rows': sum(row['anime'].nsfw is not False or row['anime'].bangumi_id not in safe_ids
                              for row in ranked),
        'hidden_rows': sum(row['anime'].bangumi_id in hidden for row in ranked),
        'evidence_errors': sum(len(evidence_errors(row, context)) for row in ranked),
        'checked_rows': len(ranked)}


def diagnose_sample(context, summary, index, *, top_k, repeats, max_holdouts, progress=lambda text: None):
    """Both arms use exactly the same frozen inputs, feedback and default filters.

    Alternate execution order between repeats; report first vs subsequent runs,
    not purported cold/warm production-cache timings (index is already built).
    """
    options = {'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': top_k}
    safe_ids = {identity for identity, _, _, _ in index['features']}
    current_features = {identity: tags for identity, tags, _, _ in index['features']}
    runs = {version: {category: [] for category in CATEGORIES} for version in VERSIONS}
    outputs = {version: {} for version in VERSIONS}
    checks = dict.fromkeys(('unsafe_rows', 'hidden_rows', 'evidence_errors', 'checked_rows'), 0)
    sequences = {version: {category: [] for category in CATEGORIES} for version in VERSIONS}
    def compute(clone, category, version):
        ranked, measurement = measured_rank(clone, {**options, 'category': category}, index, version)
        for key, value in result_checks(ranked, clone, safe_ids).items():
            checks[key] += value
        return ranked, measurement
    for iteration in range(repeats):
        for category in CATEGORIES:
            for version in VERSIONS if iteration % 2 == 0 else reversed(VERSIONS):
                ranked, measurement = compute(context, category, version)
                sequences[version][category].append(hashlib.sha256(canonical_bytes([
                    (row['anime'].bangumi_id, row['evidence'], row['reason']) for row in ranked])).hexdigest())
                runs[version][category].append(measurement)
                outputs[version][category] = ranked
                progress(f'run {iteration + 1}/{repeats} {version} {category}: {measurement["compute_ms"]} ms')
    arms = {version: {category: {
        'metrics': comparable_metrics(outputs[version][category], context, context, current_features, top_k),
        'runs': runs[version][category],
        'stable_order_and_evidence': len(set(sequences[version][category])) == 1,
    } for category in CATEGORIES} for version in VERSIONS}
    holdout = {version: {category: [] for category in CATEGORIES} for version in VERSIONS}
    targets = sorted(seed['bangumi_id'] for seed in context['seeds'])[:max_holdouts] if summary['holdout_eligible'] else []
    blocked = 0
    for position, target in enumerate(targets, 1):
        if target in context['feedback']['hidden_ids']:
            blocked += 1  # Never unhide a held-out target to improve a metric.
            continue
        clone = without_seed(context, target, minimum=settings.RECOMMENDATION_PROFILE_MIN_SAMPLES)
        for category in CATEGORIES:
            for version in VERSIONS if position % 2 else reversed(VERSIONS):
                ranked, measurement = compute(clone, category, version)
                ids = [row['anime'].bangumi_id for row in ranked[:top_k]]
                rank = ids.index(target) + 1 if target in ids else None
                holdout[version][category].append({'hit_at_k': int(rank is not None),
                    'mrr_at_k': 1 / rank if rank else 0,
                    'binary_ndcg_at_k': 1 / math.log2(rank + 1) if rank else 0, 'measurement': measurement})
        progress(f'paired holdout {position}/{len(targets)} complete')
    aggregate = {version: {category: {metric: mean([row[metric] for row in values])
        for metric in ('hit_at_k', 'mrr_at_k', 'binary_ndcg_at_k')}
        for category, values in categories.items()} for version, categories in holdout.items()}
    proof = {version: snapshot_token(context['_owner_id'], deepcopy(context), {**options, 'category': 'all'},
        outputs[version]['all'], version, weights=index['weights']) for version in VERSIONS}
    return {'summary': summary, 'arms': arms, 'checks': checks,
        'category_differences': {version: category_differences(results, top_k=top_k) for version, results in outputs.items()},
        'holdout': {'status': 'evaluated' if targets and blocked < len(targets) else 'insufficient_evidence',
            'selected_targets': len(targets), 'feedback_blocked_targets': blocked,
            'paired_targets': len(targets) - blocked, 'metrics': aggregate, 'runs': holdout,
            'explicit_preferences_retained': True},
        'version_tokens_isolated': proof['content.v1'] != proof['content.v2']}


def public_index(catalog):
    started = perf_counter()
    index = build_feature_index(catalog, taxonomy_version=TAXONOMY_VERSION)
    return index, round((perf_counter() - started) * 1000, 3)


def markdown_diagnostic(report):
    lines = ['# V2 当前基线与瓶颈诊断', '',
        '仅诊断，不签发上线许可；线上默认、门槛、评分和手账不变。',
        f"代码指纹：`{report['release_fingerprint']}`", f"执行位置标记：`{report['execution_context']}`", '',
        f"公共目录读取：{report['setup']['catalog_read_ms']} ms；公共索引构建：{report['setup']['index_build_ms']} ms。",
        '以下排名计时不含上述准备阶段、HTTP 序列化和浏览器网络；原 5000 ms 门槛不扣除数据库耗时。', '']
    for number, sample in enumerate(report['samples'], 1):
        summary = sample['summary']; state = summary['state']
        lines += [f'## sample_{number:03d}',
            f"手账 {state['journal_record_count']} 部，未评分 {state['unrated_record_count']} 部；有效样本 {state['usable_sample_count']} 部。",
            f"状态 `{state['code']}`；模式 `{summary['mode']}`；手动题材 {state['explicit_preference_count']} 个；反馈版本 {summary['feedback_version']}。",
            '', '| 引擎 | 分类 | 各次排名 ms | 各次 CPU ms | 各次 SQL execute ms | SQL 次数 |',
            '| --- | --- | --- | --- | --- | --- |']
        for version, categories in sample['arms'].items():
            for category, data in categories.items():
                rows = data['runs']
                lines.append(f"| {version} | {category} | {[r['compute_ms'] for r in rows]} | "
                    f"{[r['process_cpu_ms'] for r in rows]} | {[r['sql_execute_ms'] for r in rows]} | {[r['sql_queries'] for r in rows]} |")
        lines += ['', '### V2 分阶段（每次运行，ms）']
        for category, data in sample['arms']['content.v2'].items():
            lines.append(f"- {category}: " + str([r.get('stage_ms', {}) for r in data['runs']]))
        lines += ['', '### 质量代理与留出（非喜欢概率）',
            '| 分类 | 关联代理 V1/V2 | 题材重复 V1/V2 | 新方向数 V1/V2 | Hit@K V1/V2 |', '| --- | --- | --- | --- | --- |']
        for category in CATEGORIES:
            metrics = [sample['arms'][version][category]['metrics'] for version in VERSIONS]
            hits = [sample['holdout']['metrics'][version][category]['hit_at_k'] for version in VERSIONS]
            lines.append(f"| {category} | {[r['topic_overlap_proxy_mean'] for r in metrics]} | "
                f"{[r['pairwise_genre_jaccard_mean'] for r in metrics]} | {[r['novel_direction_count'] for r in metrics]} | {hits} |")
        lines += [f"留出状态：{sample['holdout']['status']}，配对 {sample['holdout']['paired_targets']} 个。",
            f"安全/反馈排除/证据检查：{sample['checks']}。", '']
    lines += ['## 边界',
        '- SQL execute 计时含驱动/网络往返和执行，不含所有 fetch、传输解码；不能冒充纯服务器 SQL 时间。',
        '- 进程 CPU 时间不是请求总时间；wall−CPU 不等同于网络耗时。',
        '- 只比较当前固定规则；保留显式偏好，留出是回溯性小样本，不宣称无偏预测或统计显著。',
        '- 首次/后续是本进程执行顺序，不声称是生产缓存冷/热测量；重复次数少，不报告伪精确 p95。',
        '- 数据库强制只读快照与真实线上 HTTP/部署环境不是同一种测试。',
        '- 本报告永不生成 approval.json；旧发布许可不可复用，不启用 V2。']
    return '\n'.join(lines) + '\n'
