"""V2-A frozen cohorts and exact offline pipeline audit. Never grants approval.

An observer sees existing engine stages; an independent logical census explains
earlier gates. The census is NOT a replacement ranker. Unsupported year filters
are labelled, never silently applied to make the old V2 appear compatible.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import hmac
import json
import math
from time import perf_counter, process_time

from django.conf import settings
from django.db import connection

from . import engine, ranking_v2
from .browse_controls import season_year, filter_index
from .comparison import QueryCounter, comparable_metrics
from .diagnostics import prepare_sample
from .evidence import EvidencePresenter, _token
from .features import feature_tags
from .models import RecommendationAnime
from .pipeline_trace import PipelineObserver
from .quality_evaluation import CATEGORIES, evidence_errors, input_fingerprint, mean, without_seed
from .version_control import canonical_bytes

CONTRACT = 'recommendations.recall-audit.v1'
COHORT_CONTRACT = 'recommendations.recall-cohort.v1'
CONFIG_KEYS = {'contract_version', 'selection_version', 'historical_limit', 'expanded_limit',
               'top_k', 'repeats', 'categories', 'algorithms', 'baseline_filters', 'filter_probes'}
FILTER_KEYS = {'type', 'min_score', 'hide_recorded', 'year_from', 'year_to'}


def validate_filters(value):
    if set(value) != FILTER_KEYS or type(value['hide_recorded']) is not bool:
        raise ValueError('Invalid filter contract')
    if value['type'] not in ('all', 'TV', 'OVA', 'WEB', '剧场版', '短片', '动态漫画'):
        raise ValueError('Unsupported work type')
    if type(value['min_score']) not in (int, float) or not math.isfinite(value['min_score']) or not 0 <= value['min_score'] <= 10:
        raise ValueError('Invalid public score filter')
    for key in ('year_from', 'year_to'):
        if value[key] is not None and (type(value[key]) is not int or not 1000 <= value[key] <= 9999):
            raise ValueError('Invalid year filter')
    if value['year_from'] and value['year_to'] and value['year_from'] > value['year_to']:
        raise ValueError('Reversed year interval')


def load_config(path):
    raw = path.read_bytes()
    if len(raw) > 16384:
        raise ValueError('Evaluation config too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Repeated evaluation key')
            result[key] = value
        return result
    config = json.loads(raw, object_pairs_hook=unique)
    if (set(config) != CONFIG_KEYS or config['contract_version'] != 'recommendations.recall-evaluation.v1'
            or config['selection_version'] != 'historical4-plus-even-id-spread12.v1'
            or type(config['historical_limit']) is not int or config['historical_limit'] != 4
            or type(config['expanded_limit']) is not int or config['expanded_limit'] != 12
            or type(config['top_k']) is not int or config['top_k'] != 10
            or type(config['repeats']) is not int or config['repeats'] not in (2, 3)
            or config['categories'] != list(CATEGORIES) or config['algorithms'] != ['content.v1', 'content.v2']):
        raise ValueError('Unsupported fixed evaluation contract; do not tune targets/K after outcomes')
    validate_filters(config['baseline_filters'])
    if config['baseline_filters'] != dict(type='all', min_score=0, hide_recorded=True, year_from=None, year_to=None):
        raise ValueError('Historical baseline must retain original default filters')
    probes = config['filter_probes']
    if not isinstance(probes, list) or len(probes) > 3:
        raise ValueError('At most three predefined filter probes')
    names = set()
    for probe in probes:
        if set(probe) != FILTER_KEYS | {'name'} or not isinstance(probe['name'], str) or not probe['name'].isascii() or not probe['name'].replace('_', '').isalnum() or probe['name'] in names:
            raise ValueError('Invalid or duplicate filter probe')
        names.add(probe['name']); validate_filters({key: probe[key] for key in FILTER_KEYS})
    return config, hashlib.sha256(canonical_bytes(config)).hexdigest()


def selected_targets(context, config):
    if context['mode'] != 'personalized' or len(context['seeds']) < max(4, settings.RECOMMENDATION_PROFILE_MIN_SAMPLES + 1):
        return {'historical': [], 'expanded': []}
    ordered = sorted({seed['bangumi_id'] for seed in context['seeds']})
    history = ordered[:config['historical_limit']]
    rest, count = ordered[len(history):], min(config['expanded_limit'], len(ordered)) - len(history)
    positions = ([0] if count == 1 else [n * (len(rest) - 1) // (count - 1) for n in range(count)]) if count else []
    return {'historical': history, 'expanded': history + [rest[position] for position in positions]}


def sample_binding(context, config):
    selected = selected_targets(context, config)
    handle = lambda identity: _token(COHORT_CONTRACT + '.target', [context['_owner_id'], identity])
    return {'input_hmac': _token(COHORT_CONTRACT + '.input', [input_fingerprint(context), context['profile']]),
            'usable_samples': len(context['seeds']),
            'sets': {name: [handle(identity) for identity in identities] for name, identities in selected.items()},
            'status': 'frozen' if selected['expanded'] else 'insufficient_evidence'}


def freeze_cohort(samples, config, config_sha, catalog_sha, release):
    body = {'contract_version': COHORT_CONTRACT, 'config_sha256': config_sha,
            'catalog_sha256': catalog_sha, 'frozen_release_fingerprint': release,
            'samples': [sample_binding(context, config) for _, context, _ in samples],
            'selection_is_outcome_independent': True, 'ranking_performed': False}
    return {**body, 'signature': _token(COHORT_CONTRACT, body)}


def verify_cohort(value, samples, config, config_sha, catalog_sha):
    body = {key: item for key, item in value.items() if key != 'signature'}
    if (value.get('contract_version') != COHORT_CONTRACT or not isinstance(value.get('signature'), str)
            or not hmac.compare_digest(value['signature'], _token(COHORT_CONTRACT, body))
            or value.get('config_sha256') != config_sha or value.get('catalog_sha256') != catalog_sha
            or value.get('ranking_performed') is not False
            or value.get('samples') != [sample_binding(context, config) for _, context, _ in samples]):
        raise ValueError('Frozen cohort/config/catalog/account inputs changed or signature invalid; no silent reselection')


def catalog_states():
    rows = list(RecommendationAnime.objects.order_by('pk').values_list('bangumi_id', 'nsfw')[:engine.MAX_CATALOG_ROWS + 1])
    if len(rows) > engine.MAX_CATALOG_ROWS:
        raise ValueError('Bounded catalog audit limit exceeded')
    return {identity: nsfw for identity, nsfw in rows if type(identity) is int and 0 < identity <= 2147483647}


def year_supported(filters):
    # Actual rank_context entry has no year filtering, in BOTH versions. The
    # production V1 browse adapter is a separate contract (future V2-D work).
    return filters['year_from'] is None and filters['year_to'] is None


def census(context, filters, catalog, states, target):
    if not year_supported(filters):
        raise ValueError('Year filters are not implemented in the frozen engine entry')
    safe = {identity for identity, state in states.items() if state is False}
    filtered = {identity for identity, row in catalog.items() if identity in safe
                and row['score'] >= filters['min_score']
                and (filters['type'] == 'all' or row['status_type'] == filters['type'])}
    hidden = context.get('feedback', {}).get('hidden_ids', ())
    seeds = {row['bangumi_id'] for row in context['seeds']}
    excluded = set(hidden) | seeds | (set(context['owned']) if filters['hide_recorded'] else set())
    remaining = filtered - excluded
    features = {identity: feature_tags(row['tags'], (row['title_zh'], row['title_jp'])) for identity, row in catalog.items() if identity in remaining}
    preferred = set(engine.taste_vector(context))
    associated = {identity for identity, tags in features.items() if tags & preferred}
    def stage(identities):
        return {'count': len(identities), 'target_present': target in identities if target is not None else None}
    tags = features.get(target, frozenset())
    if target not in states:
        first = 'missing_catalog_identity'
    elif states[target] is not False:
        first = 'adult_filtered' if states[target] is True else 'unknown_content_rating_filtered'
    elif target not in filtered:
        first = 'public_filter_rejected'
    elif target in hidden:
        first = 'feedback_excluded'
    elif target in seeds:
        first = 'training_seed_excluded'
    elif filters['hide_recorded'] and target in context['owned']:
        first = 'recorded_excluded'
    elif not tags:
        first = 'no_effective_genres'
    elif target not in associated:
        first = 'no_real_preference_association'
    else:
        first = None
    return {'stages': {'safe_catalog': stage(safe), 'public_filters': stage(filtered),
                       'journal_feedback_exclusion': stage(remaining), 'effective_association': stage(associated)},
            'first_failure': first if target is not None else None,
            'target_genre_count': len(tags) if target is not None else None,
            'target_new_topic_count': len(tags - preferred) if target is not None else None,
            'target_has_rated_work_overlap': any(tags & seed['tags'] for seed in context['seeds']) if target is not None else None,
            'missing_or_invalid_year_count': sum(season_year(row['season']) is None for row in catalog.values()),
            'year_gate': 'not_requested' if year_supported(filters) else 'unsupported_in_engine_entry'}


def destination(logical, pipeline, category, returned_rank, presentation_ok):
    first = logical['first_failure']
    if first:
        group = ('safety_filter_or_exclusion' if first not in ('no_effective_genres', 'no_real_preference_association') else 'missing_genres_or_association')
        return {'group': group, 'code': first}
    stages = pipeline['stages']
    if pipeline['cold_start']:
        return {'group': 'insufficient_evidence', 'code': 'cold_start'}
    if not stages['eligible']['observed']:
        return {'group': 'not_observed', 'code': 'engine_did_not_finish'}
    if stages['eligible']['target_position'] is None:
        code = ('explore_requires_new_topic' if category == 'explore' and logical['target_new_topic_count'] == 0
                else 'familiar_requires_rated_work_evidence' if category == 'familiar' and not logical['target_has_rated_work_overlap']
                else 'category_or_evidence_not_eligible')
        return {'group': 'safety_filter_or_exclusion', 'code': code}
    if stages['pool']['target_position'] is None:
        return {'group': 'outside_candidate_pool', 'code': 'outside_400_pool'}
    if stages['hydrated']['target_position'] is None:
        return {'group': 'hydration_or_evidence_recheck', 'code': 'current_hydration_or_filter_rejected'}
    if stages['validated']['target_position'] is None:
        return {'group': 'hydration_or_evidence_recheck', 'code': 'current_evidence_association_or_category_rejected'}
    if returned_rank is None:
        return {'group': 'ranking_below_display_limit', 'code': 'outside_returned_120'}
    if not presentation_ok:
        return {'group': 'hydration_or_evidence_recheck', 'code': 'presentation_safety_or_evidence_failed'}
    return {'group': 'hit' if returned_rank <= 10 else 'ranking_below_display_limit',
            'code': 'hit_top_10' if returned_rank <= 10 else 'returned_below_top_10'}


def output_digest(rows):
    return _token(CONTRACT + '.output', [(row['anime'].bangumi_id, float(row['value']).hex(),
        sorted(row['tags']), row['evidence'], row['reason']) for row in rows])


def traced_rank(user, context, index, filters, algorithm, category, *, target=None):
    """Unobserved control vs observed run, actual functions on the same snapshot.

    Extra presentation checking is timed separately, NEVER hidden in ranking
    performance. A fault is not converted to an empty, successful recommendation.
    """
    options = {**{key: filters[key] for key in ('type', 'min_score', 'hide_recorded')}, 'category': category}
    controls, runs, outputs = {}, {}, {}
    observer = PipelineObserver(target)
    for name, instrument in (('control', None), ('observed', observer)):
        clone, counter = deepcopy(context), QueryCounter()
        started, cpu = perf_counter(), process_time()
        with connection.execute_wrapper(counter):
            ranked, weights = engine.rank_context(clone, options, index, algorithm_version=algorithm,
                                                  **({'pipeline_observer': instrument} if instrument is not None else {}))
        runs[name] = {'wall_ms': round((perf_counter() - started) * 1000, 3),
                      'cpu_ms': round((process_time() - cpu) * 1000, 3), 'sql_count': counter.count,
                      'sql_execute_ms': round(counter.seconds * 1000, 3)}
        controls[name], outputs[name] = output_digest(ranked), ranked
    if controls['control'] != controls['observed'] or runs['control']['sql_count'] != runs['observed']['sql_count']:
        raise ValueError('Observer changed ordering/scores/evidence/SQL count; audit stopped')
    started, counter = perf_counter(), QueryCounter()
    with connection.execute_wrapper(counter):
        presenter = EvidencePresenter(user, context, index['weights'], algorithm)
        presenter.ensure_safe([row['anime'] for row in outputs['observed']])
        presentation = {}
        for name, ranked in outputs.items():
            envelopes = [presenter.present(row['anime'], row['tags']) for row in ranked]
            if any(evidence_errors(row, context) for row in ranked) or any(not item['evidence'] for item in envelopes):
                raise ValueError('Actual returned evidence failed independent audit')
            presentation[name] = _token(CONTRACT + '.presentation', envelopes)
    if presentation['control'] != presentation['observed']:
        raise ValueError('Observer changed presentation evidence/token')
    rows = outputs['observed']
    ids = [row['anime'].bangumi_id for row in rows]
    position = ids.index(target) + 1 if target in ids else None
    pipeline = observer.export(cold_start=context['mode'] == 'cold_start')
    pipeline['stages'].update(presentation={'observed': True, 'count': len(rows), 'target_position': position},
        top_10={'observed': True, 'count': min(10, len(rows)), 'target_position': position if position and position <= 10 else None})
    return rows, {'engine_path': 'rank_context', 'algorithm_version': algorithm,
                  'v2_policy_version': ranking_v2.POLICY_VERSION if algorithm == 'content.v2' else None,
                  'pipeline': pipeline, 'target_returned_rank': position,
                  'noninterference': {'order_scores_evidence_equal': True, 'sql_count_equal': True,
                                      'presentation_equal': True, 'output_hmac': controls['observed']},
                  'runs': runs, 'presentation_audit': {'wall_ms': round((perf_counter() - started) * 1000, 3),
                                                      'sql_count': counter.count, 'passed': True}}


def audit_sample(user, context, summary, index, catalog, states, config, progress=lambda message: None):
    features = {identity: tags for identity, tags, _, _ in index['features']}
    baseline, probes, folds = {}, {}, []
    filters = config['baseline_filters']
    for algorithm in config['algorithms']:
        baseline[algorithm] = {}
        for category in config['categories']:
            repetitions, digests = [], []
            for repeat in range(config['repeats']):
                rows, run = traced_rank(user, context, index, filters, algorithm, category)
                repetitions.append(run); digests.append(run['noninterference']['output_hmac'])
                progress(f'baseline {algorithm}/{category} {repeat + 1}/{config["repeats"]}')
            if len(set(digests)) != 1:
                raise ValueError('Repeated baseline output drift')
            metrics = comparable_metrics(rows, context, context, features, config['top_k'])
            # Public candidate IDs in older metrics are unnecessary here.
            metrics.pop('top_bangumi_ids', None)
            baseline[algorithm][category] = {'runs': repetitions, 'metrics': metrics,
                'logical_census': census(context, filters, catalog, states, None), 'stable': True}
    for probe in config['filter_probes']:
        selected = {key: probe[key] for key in FILTER_KEYS}
        if not year_supported(selected):
            # A public eligibility preview of the EXISTING V1 browse filter,
            # not a changed V2 candidate pool or a second ranking algorithm.
            preview = filter_index(index, list(catalog.values()), selected)
            probes[probe['name']] = {'status': 'unsupported_in_engine_entry', 'requested_filters': selected,
                'v1_production_browse_supports_years': True, 'v2_browse_compatibility': 'not_implemented',
                'ranking_performed': False, 'reason': 'Do not silently add year filters to frozen legacy V1/V2.',
                'eligibility_preview_only': {'implementation': 'browse_controls.filter_index',
                    'not_used_for_engine_ranking': True, 'safe_catalog_count': len(catalog),
                    'matching_count': len(preview['features']),
                    'missing_invalid_years_excluded': sum(season_year(row['season']) is None for row in catalog.values())}}
            continue
        probes[probe['name']] = {'status': 'evaluated', 'results': {}}
        for algorithm in config['algorithms']:
            rows, run = traced_rank(user, context, index, selected, algorithm, 'all')
            probes[probe['name']]['results'][algorithm] = {
                'logical_census': census(context, selected, catalog, states, None), 'run': run}
            progress(f'filter probe {probe["name"]}/{algorithm}')
    sets = selected_targets(context, config)
    for ordinal, target in enumerate(sets['expanded'], 1):
        clone = without_seed(context, target, minimum=settings.RECOMMENDATION_PROFILE_MIN_SAMPLES)
        if clone is None:
            raise ValueError('Insufficient training samples; never lower threshold')
        logical = census(clone, filters, catalog, states, target)
        fold = {'target_ordinal': ordinal, 'historical_member': target in sets['historical'],
                'feedback_blocked': target in clone['feedback']['hidden_ids'], 'logical_census': logical, 'arms': {}}
        for algorithm in config['algorithms']:
            fold['arms'][algorithm] = {}
            for category in config['categories']:
                rows, run = traced_rank(user, clone, index, filters, algorithm, category, target=target)
                rank = run['target_returned_rank']
                code = destination(logical, run['pipeline'], category, rank, True)
                fold['arms'][algorithm][category] = {**run, 'destination': code,
                    'hit_at_10': int(rank is not None and rank <= 10),
                    'mrr_at_10': 1 / rank if rank is not None and rank <= 10 else 0,
                    'binary_ndcg_at_10': 1 / math.log2(rank + 1) if rank is not None and rank <= 10 else 0}
        folds.append(fold); progress(f'fixed holdout {ordinal}/{len(sets["expanded"])}')
    aggregates = {}
    for set_name in ('historical', 'expanded'):
        selected = [fold for fold in folds if set_name == 'expanded' or fold['historical_member']]
        paired = [fold for fold in selected if not fold['feedback_blocked']]
        aggregates[set_name] = {'status': 'evaluated' if paired else 'insufficient_evidence',
            'selected_count': len(selected), 'paired_count': len(paired),
            'feedback_blocked_count': len(selected) - len(paired), 'metrics': {}}
        for algorithm in config['algorithms']:
            aggregates[set_name]['metrics'][algorithm] = {}
            for category in config['categories']:
                cells = [fold['arms'][algorithm][category] for fold in paired]
                aggregates[set_name]['metrics'][algorithm][category] = {
                    **{metric: mean([row[metric] for row in cells]) for metric in ('hit_at_10', 'mrr_at_10', 'binary_ndcg_at_10')},
                    'reason_counts_all_selected': dict(Counter(fold['arms'][algorithm][category]['destination']['code'] for fold in selected)),
                    'conditional_pool_recall': mean([int(row['pipeline']['stages']['pool']['target_position'] is not None)
                        for row in cells if row['destination']['group'] in ('outside_candidate_pool', 'ranking_below_display_limit', 'hit', 'hydration_or_evidence_recheck')])}
    return {'summary': summary, 'baseline': baseline, 'filter_probes': probes,
            'holdout_sets': aggregates, 'folds': folds, 'explicit_preferences_retained_in_holdout': True,
            'pipeline_semantics': 'Logical early-gate census + actual engine observations; top10 is a returned slice, not browser exposure.'}


def markdown_report(report):
    lines = ['# V2-A 冻结基线与召回路径审计', '',
        '仅只读诊断；排序/分数/证据不变。不启用V2，不签发上线许可。',
        f"执行位置：{report['execution_context']}；配置SHA256：{report['config_sha256']}",
        '历史4目标保留；扩展至最多12目标，按预先固定的ID间距规则选取，不按命中挑选。',
        '年份请求在旧引擎入口不支持，明确标记而不补筛；V1增强9浏览适配是另一条生产路径。', '']
    for ordinal, sample in enumerate(report['samples'], 1):
        lines += [f'## sample_{ordinal:03d}', '', f"可分析样本：{sample['summary']['state']['usable_sample_count']}；模式：{sample['summary']['mode']}", '',
                  '| 目标集 | 引擎 | 分类 | 配对目标 | Hit@10 | 条件候选池覆盖 | 去向（全部选中目标） |',
                  '| --- | --- | --- | ---: | ---: | ---: | --- |']
        for name, group in sample['holdout_sets'].items():
            for algorithm, categories in group['metrics'].items():
                for category, metrics in categories.items():
                    lines.append(f"| {name} | {algorithm} | {category} | {group['paired_count']} | {metrics['hit_at_10']} | {metrics['conditional_pool_recall']} | {metrics['reason_counts_all_selected']} |")
        lines += ['', f"历史状态：{sample['holdout_sets']['historical']['status']}；扩展状态：{sample['holdout_sets']['expanded']['status']}。", '']
    lines += ['## 解释边界', '- 条件池覆盖仅统计符合该类别关联/资格门槛的配对目标；不能替代保留原分母的Hit/MRR/NDCG。',
        '- 反馈阻碍目标保留去向日志，不撤销、不替换；历史配对均值沿用旧工具不评估反馈阻碍目标的口径。',
        '- 同一目标/账号/分类的重复计算不是新增独立样本；只有两个授权账号，不能外推全站准确率。',
        '- 未评分/样本不足指标为null和insufficient_evidence，不改分、不制造样本。',
        '- 基线测量包含控制/观测两臂；观测耗时不是优化收益，展示证据复核另计，不冒充完整线上HTTP性能。',
        '- 严格只读、无长评、无外部请求、不改候选池400/返回120/前10，不生成approval.json。']
    return '\n'.join(lines) + '\n'
