"""Local artifacts only; reports with user samples are private, never public API data."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from django.conf import settings

from . import engine, features, taxonomy, quality_evaluation, quality_audit, profile_policy
from .quality_audit import AUDIT_VERSION
from .profile_policy import FIXED_EVALUATION_SCORE

METRIC_DEFINITIONS = {
    'topic_overlap_proxy_mean': 'Top-K mean maximum genre Jaccard against training works/explicit choices; topic proxy only, not human relevance or probability.',
    'pairwise_genre_jaccard_mean': 'Mean pairwise genre Jaccard among Top-K; higher means more topic repetition. Null if fewer than two results.',
    'duplicate_genre_set_ratio': '(N - unique genre sets)/N in actual Top-K, not a sequel/series inference.',
    'dominant_genre_share': 'Largest genre document frequency / actual Top-K count; internal concentration metric.',
    'evidence_valid_fraction': 'Fraction of Top-K with independently checked source, personal rating, shared genres, provenance and reason text.',
    'top_set_jaccard': 'Intersection/union of category Top-K IDs; null when either category has no results.',
    'hit_at_k': 'Single held-out high-personal-score label appears in Top-K / evaluated targets.',
    'mrr_at_k': 'Mean 1/rank if the single held-out label is in Top-K, otherwise zero.',
    'binary_ndcg_at_k': 'Mean 1/log2(rank+1) for the single held-out label in Top-K, otherwise zero; not full graded NDCG.',
}

EVALUATION_CASES = [
    {'case': 'empty_journal_without_preferences', 'expect': 'cold_start; no fabricated result or zero-valued accuracy'},
    {'case': 'explicit_preferences_only', 'expect': 'preference_based; explicit-preference evidence only; no personalized holdout'},
    {'case': 'all_unrated_or_saved_zero', 'expect': 'null is unrated; saved zero is rated, neither is a positive preference sample'},
    {'case': 'one_two_valid_samples', 'expect': 'no full personalized profile; holdout skipped'},
    {'case': 'three_valid_samples', 'expect': 'personalized live baseline; holdout skipped because training would have fewer than three'},
    {'case': 'four_or_more_valid_samples', 'expect': 'bounded canonical-ID holdout; masked target cannot remain in evidence or recorded exclusion'},
    {'case': 'custom_genres_and_management_tags', 'expect': 'only defined genre mappings; no raw/private unknown labels exported'},
    {'case': 'private_tags_intentionally_empty', 'expect': 'no catalog backfill for training; no rating/review rewrite'},
    {'case': 'unlinked_adult_or_unknown', 'expect': 'excluded from seeds/results/genre observations; excluded catalog counts only'},
    {'case': 'narrow_or_empty_category_pool', 'expect': 'report overlap/empty state honestly, not automatically blame ranking'},
    {'case': 'public_rating_zero_or_high_without_votes', 'expect': 'ambiguous zero and high-score review flags; no confidence correction claim'},
]


def report_envelope(catalog, evaluations, *, top_k, max_holdouts):
    user_samples = sum(row['sample_kind'] == 'authorized_user' for row in evaluations)
    source_hashes = {name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                     for name, module in (('engine', engine), ('features', features), ('taxonomy', taxonomy),
                                          ('quality_evaluation', quality_evaluation), ('quality_audit', quality_audit),
                                          ('profile_policy', profile_policy))}
    source_hashes['quality_report'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    versions = {'algorithm': engine.ALGORITHM_VERSION, 'features': engine.FEATURE_VERSION,
                'taxonomy': taxonomy.TAXONOMY_VERSION, 'extraction': features.FEATURE_RULE_VERSION,
                'profile_policy': profile_policy.PROFILE_POLICY_VERSION}
    config = {'top_k': top_k, 'max_holdouts_per_user': max_holdouts, 'candidate_pool': engine.CANDIDATE_POOL,
              'max_results': engine.MAX_RESULTS, 'personal_score_threshold': str(FIXED_EVALUATION_SCORE),
              'minimum_usable_samples': settings.RECOMMENDATION_PROFILE_MIN_SAMPLES,
              'type': 'all', 'min_public_score': 0, 'hide_recorded': True}
    payload = {'catalog': catalog['catalog_fingerprint'], 'scope': catalog['scope'], 'reference_year': catalog['reference_year'],
               'versions': versions, 'configuration': config,
               'sources': source_hashes, 'samples': [row['input_fingerprint'] for row in evaluations]}
    return {'contract_version': AUDIT_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'baseline_fingerprint': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        'versions': versions, 'source_sha256': source_hashes, 'configuration': config, 'catalog': catalog,
        'evaluation': {'status': 'evaluated' if evaluations else 'not_requested', 'sample_count': len(evaluations),
                       'authorized_user_sample_count': user_samples, 'synthetic_probe_count': len(evaluations) - user_samples,
                       'samples': [{**row, 'sample': f'sample_{index:03d}'} for index, row in enumerate(evaluations, 1)]},
        'metric_definitions': METRIC_DEFINITIONS, 'evaluation_cases': EVALUATION_CASES,
        'limitations': ['No rating-vote count in current schema; no rating-confidence correction performed.',
            'Topic overlap is only a proxy; this is not measured user satisfaction, conversion or causal online lift.',
            'Holdout is retrospective, deterministic and bounded, not random, chronological, or full-cohort unbiased estimation.',
            'Explicit choices may contain prior knowledge of held-out works; results are conditional on retained explicit preferences.',
            'Candidate-genre failures stay in the holdout denominator; limited candidate pool can also cause misses.',
            'Private input snapshots are not exported; repeatability requires the same selected users, settings, rules and database snapshot.'],
        'attribution': {'data_or_feature_policy': 'Missing raw tags, unmapped vocabulary, missing metadata and ambiguous scores are recorded independently; no automatic repair.',
            'ranking_observations': 'Top-K similarity, repetition and category overlap describe V1 behavior; alone they do not prove an algorithm bug.',
            'series_relations': 'No series/sequel relationship is inferred from titles.'},
        'privacy': {'read_only': True, 'reads_private_reviews': False, 'calls_external_models': False,
                    'exports_user_ids_or_names': False, 'exports_seed_scores_or_titles': False,
                    'private_reports_when_samples_selected': bool(user_samples)}}


def markdown_report(report):
    catalog = report['catalog']
    lines = ['# 推荐质量审计与 V1 基线（核心 5A）', '',
        f"生成时间：{report['created_at']}；算法：{report['versions']['algorithm']}",
        f"基线指纹：`{report['baseline_fingerprint']}`", '',
        '## 范围与评分语义', '',
        f"全库计数：{catalog['scope']}。仅分析明确非成人的 {catalog['audited_safe_rows']} 部作品；缺少标准 ID 的作品只审计、不进入推荐候选。",
        '成人及未知分级只输出数量，不读取其标题、标签或封面作为评测样本。',
        'Bangumi 评分是公共评价，不是用户喜欢的概率。当前无评分人数：没有进行评分可信度修正。',
        '0 分只能标记为“未评分或来源状态不明”；高评分只标记待核查，不认定为错误数据。', '',
        '## 数据问题与题材规则覆盖', '', '| 问题代码 | 作品数 |', '| --- | --- |']
    lines += [f'| {code} | {count} |' for code, count in catalog['issue_counts'].items()]
    lines += ['', '题材缺失需区分原始标签为空、只有管理信息、以及现行词表未覆盖；不是所有“无有效题材”都属于源数据错误。',
        '缺年份和封面影响筛选与呈现；当前 V1 不用年份/封面排序，不能将这些缺失直接认作排序偏差。', '',
        '## V1 只读样本', '',
        f"评测状态：{report['evaluation']['status']}；样本数：{report['evaluation']['sample_count']}。"]
    for sample in report['evaluation']['samples']:
        lines += ['', f"### {sample['sample']}", '',
            f"样本类别：{sample['sample_kind']}。" + ('固定合成题材探针，不是真实用户或个性化质量结论。' if sample['sample_kind'] == 'synthetic_preference_probe' else '已明确选择的授权用户，只输出匿名汇总。'),
            f"模式：{sample['mode']}；有效样本：{sample['usable_samples']}；明确题材数：{sample['explicit_preference_count']}。",
            '| 分类 | 结果数 | 题材关联代理均值 | 题材重复 Jaccard | 证据错误数 |', '| --- | --- | --- | --- | --- |']
        for category, metrics in sample['categories'].items():
            lines.append(f"| {category} | {metrics['result_count']} | {metrics['topic_overlap_proxy_mean']} | {metrics['pairwise_genre_jaccard_mean']} | {metrics['evidence_error_count']} |")
        lines += ['', f"三类差异：`{json.dumps(sample['category_differences'], ensure_ascii=False)}`",
                  f"留出评测：`{json.dumps(sample['holdout'], ensure_ascii=False)}`"]
    lines += ['', '## 指标解释与局限', '']
    lines += [f'- `{key}`：{value}' for key, value in report['metric_definitions'].items()]
    lines += ['', '这些内部指标不是匹配概率，不输出到页面。空结果/无可评测目标使用 null，不冒充 0 分表现。', '']
    lines += ['- ' + value for value in report['limitations']]
    lines += ['', '重复题材和分类重叠也可能来自口味集中或候选不足；需要对比后续算法，不能仅凭本报告作因果归因。',
              '完整机器可读结果见同目录 baseline.json；评测用例说明见 cases.json。报告含只读用户样本时仅限本地受控使用，不上传公共站点或 Git。']
    return '\n'.join(lines) + '\n'


def write_reports(report, directory):
    directory = Path(directory)
    paths = [directory / name for name in ('baseline.json', 'report.md', 'cases.json')]
    manifest = directory / 'manifest.json'
    if any(path.exists() for path in [*paths, manifest]):
        raise ValueError('Report artifacts already exist; choose a new directory, no overwrite performed')
    directory.mkdir(parents=True, exist_ok=True)
    contents = [json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', markdown_report(report),
                json.dumps(report['evaluation_cases'], ensure_ascii=False, indent=2) + '\n']
    for path, content in zip(paths, contents):
        with path.open('x', encoding='utf-8', newline='\n') as file:
            file.write(content)
    # Last-file completion marker: consumers should not treat partial artifacts as a complete run.
    with manifest.open('x', encoding='utf-8', newline='\n') as file:
        json.dump({'complete': True, 'contract_version': AUDIT_VERSION,
                   'sha256': {path.name: hashlib.sha256(content.encode()).hexdigest()
                              for path, content in zip(paths, contents)}}, file, indent=2)
    return [*paths, manifest]
