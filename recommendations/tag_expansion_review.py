"""Offline proposal audit ONLY. Never imported by serving/profile/ranking paths."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unicodedata

from .features import extract_genres
from .identity import normalized_title
from .tag_coverage import analyze_public_tags, _cell
from .tag_review_rules import review_key
from .taxonomy import AVAILABLE_TAGS, TAXONOMY_VERSION, PREVIOUS_TAXONOMY_VERSION, canonical_tag

GROUPS = ('synonyms', 'new_genres', 'deferred', 'excluded_examples')


def load_proposal(path):
    path = Path(path)
    if path.stat().st_size > 100_000:
        raise ValueError('Proposal exceeds size limit')
    proposal = json.loads(path.read_text(encoding='utf-8'))
    validate_proposal(proposal)
    return proposal


def validate_proposal(proposal):
    if (proposal.get('status') != 'pending_human_review'
            or proposal.get('apply_to_production') is not False
            or proposal.get('baseline_taxonomy') not in (TAXONOMY_VERSION, PREVIOUS_TAXONOMY_VERSION)):
        raise ValueError('Only inactive proposals for current/frozen previous taxonomy may be audited')
    rule = proposal['baseline_taxonomy']
    seen = set()
    for group in GROUPS:
        items = proposal.get(group)
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError('Each review group must be a bounded list')
        for item in items:
            source, target = item.get('source'), item.get('target')
            if (not isinstance(source, str) or not source.strip() or ',' in source
                    or not isinstance(item.get('rationale'), str) or not item['rationale']):
                raise ValueError('Review entries need an exact source label and rationale')
            key = review_key(source)
            if key in seen or canonical_tag(source, taxonomy_version=rule) is not None:
                raise ValueError('Sources must be unique and not already active')
            seen.add(key)
            if group == 'synonyms' and target not in AVAILABLE_TAGS:
                raise ValueError('Synonym target must already exist')
            if group == 'new_genres' and (target != source or canonical_tag(target, taxonomy_version=rule) is not None):
                raise ValueError('New genres use an inactive exact label, no implicit aliases')
            if group in ('deferred', 'excluded_examples') and target is not None:
                raise ValueError('Deferred/excluded items cannot be simulated as mappings')


def build_expansion_review(rows, scope, proposal):
    """Read public copies only; count work-level, deduplicated hypothetical gains.

    Source AND target title exclusions match serving extraction. No substring
    matching, parent expansion, score inference, private fallback or rule mutation.
    """
    validate_proposal(proposal)
    rule = proposal['baseline_taxonomy']
    baseline = analyze_public_tags(rows, scope, example_limit=2, taxonomy_version=rule)
    inventory = {item['normalized_key']: item for item in baseline['unknown_tags']}
    items = deepcopy(proposal)
    for group in GROUPS:
        for item in items[group]:
            observed = inventory.get(review_key(item['source']), {})
            item.update(document_frequency=observed.get('document_frequency', 0),
                        zero_genre_document_frequency=observed.get('zero_genre_document_frequency', 0),
                        examples=observed.get('examples', []),
                        suppressed_example_works=observed.get('suppressed_example_works', 0),
                        decision=('already_active_in_code' if group == 'synonyms'
                                  and canonical_tag(item['source']) == item['target'] else 'pending_review'),
                        apply_to_production=False)
            if group in ('synonyms', 'new_genres'):
                item.update(target_feature_new_to_works=0, recovered_zero_genre_works=0)
    proposed = {review_key(item['source']): (group, item)
                for group in GROUPS[:2] for item in items[group]}
    affected = {group: set() for group in ('synonyms', 'new_genres', 'combined')}
    recovered = {group: set() for group in affected}
    eligible_count = covered_count = 0
    seen_ids = set()
    for row in rows:
        identity = row['bangumi_id']
        if type(identity) is not int or not 0 < identity <= 2_147_483_647:
            continue
        if identity in seen_ids:
            raise ValueError('Duplicate Bangumi identity in public snapshot')
        seen_ids.add(identity)
        eligible_count += 1
        titles = (row['title_zh'], row['title_jp'])
        features = extract_genres(row['tags'], titles=titles, taxonomy_version=rule)['tags']
        covered_count += bool(features)
        title_keys = {normalized_title(title) for title in titles if title}
        tokens = {review_key(value): value for value in
                  unicodedata.normalize('NFKC', row['tags']).split(',') if value.strip()}
        for key in tokens.keys() & proposed.keys():
            group, item = proposed[key]
            source = extract_genres([tokens[key]], titles=titles, taxonomy_version=rule)
            if (not source['ignored_counts']['unknown']
                    or normalized_title(item['target']) in title_keys
                    or item['target'] in features):
                continue
            item['target_feature_new_to_works'] += 1
            item['recovered_zero_genre_works'] += not bool(features)
            affected[group].add(identity)
            affected['combined'].add(identity)
            if not features:
                recovered[group].add(identity)
                recovered['combined'].add(identity)
    return {
        'contract_version': 'recommendations.tag-expansion-review.v1',
        'created_at': baseline['created_at'], 'catalog_fingerprint': baseline['catalog_fingerprint'],
        'baseline_report_fingerprint': baseline['report_fingerprint'],
        'proposal_sha256': hashlib.sha256(json.dumps(proposal, ensure_ascii=False,
                                                     sort_keys=True).encode()).hexdigest(),
        'scope': scope, 'active_taxonomy': TAXONOMY_VERSION, 'baseline_taxonomy': rule,
        'active_preset_count': len(AVAILABLE_TAGS),
        'eligible_public_works': eligible_count, 'covered_before': covered_count,
        'unknown_distinct_labels': baseline['unknown_unique_tags'],
        'safety_metadata_review_works': baseline['safety_metadata_review_works'],
        'simulation': {group: {
            'feature_enriched_works': len(affected[group]),
            'newly_eligible_works': len(recovered[group]),
            'covered_after_if_approved': covered_count + len(recovered[group]),
            'coverage_fraction_if_approved': (covered_count + len(recovered[group])) / eligible_count
                                            if eligible_count else None,
        } for group in affected},
        'review': items,
        'policy': {'mapping_applied': False, 'private_tables_read': False,
                   'source_data_modified': False, 'ranking_changed': False,
                   'new_genres_only_hypothetical': True,
                   'deferred_in_simulation': False,
                   'counts_are_not_accuracy_or_personal_preference': True},
    }


def expansion_markdown(report):
    review = report['review']
    current_count = report['active_preset_count']
    new_count = len(review['new_genres'])
    current_coverage = (report['covered_before'] / report['eligible_public_works']
                        if report['eligible_public_works'] else None)
    current_percent = '不可计算' if current_coverage is None else f'{current_coverage:.2%}'
    active_alias_count = sum(item.get('decision') == 'already_active_in_code' for item in review['synonyms'])
    lines = ['# 标签扩充与待审核映射清单（未启用）', '',
        f"统计时间（UTC）：{report['created_at']}",
        f"目录指纹：`{report['catalog_fingerprint']}`", '',
        f"当前代码 {report['active_taxonomy']}，{current_count} 个预设题材。以下试算基于 {report.get('baseline_taxonomy', report['active_taxonomy'])}；安全且具备合法身份的公共作品 {report['eligible_public_works']:,} 部，基线有有效题材 {report['covered_before']:,} 部，覆盖率 {current_percent}。",
        f"全库 {report['scope']['total']:,} 部；成人 {report['scope']['adult']:,}、未知分级 {report['scope']['unknown']:,} 仅计数、不取样。",
        '仅读取公共作品标签及名称/类型用于统计与人工核对，不读取私人手账、个人评分、长评、简介，不调用外部 AI。作品名称只用于排除同名标签及展示样例，不能推断题材或系列。', '',
        '## 如何审核', '',
        f"1. A 组 {len(review['synonyms'])} 项精确同义映射中，{active_alias_count} 项已在当前代码启用；其余待审核。它们不增加预设数量。",
        f'2. 再逐项确认 B 组 {new_count} 个新题材的定义；若全部批准，预设由 {current_count} 增至 {current_count + new_count}，个人选择上限仍为 20。',
        '3. C 组暂缓，不因频率高就直接合并；D 组继续排除。历史草案中的同义映射若已启用，以 JSON 的 already_active_in_code 标识为准；其他项目仍待审核。',
        '4. 确认后才单独修改版本化规则、预设合同及缓存/画像/证据版本，并回归验证；本报告及 JSON 都不是运行配置。', '',
        '## 同一快照离线试算', '',
        '| 假设批准范围 | 原无题材作品新增资格 | 题材集合丰富的作品（含新增资格） | 有题材总量 | 覆盖率 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for group, label in (('synonyms', '仅 A 组'), ('new_genres', '仅 B 组'), ('combined', 'A+B 去重合计')):
        data = report['simulation'][group]
        fraction = data['coverage_fraction_if_approved']
        percent = '不可计算' if fraction is None else f'{fraction:.2%}'
        lines.append(f"| {label} | {data['newly_eligible_works']} | {data['feature_enriched_works']} | {data['covered_after_if_approved']} | {percent} |")
    lines += ['', '“出现作品数”与“新增特征数”不同：已有相同规范题材不重复加权；作品可同时带多个标签，各行不能直接相加。新增资格只表示跨过有效题材门槛，不保证进入某用户候选 400、单轮 120 或前排，更不是命中率/喜欢概率。',
        '未读取公共评分，也不把公共高分当成个人喜好；本次不能据此承诺“高分作品召回”收益。', '']
    lines += ['这是一批克制扩充，不是对剩余无题材目录的全面补齐。大量缺失来自形式、来源、制作信息等非题材标签；不能为了提高覆盖率而强行转成题材。以当前版本定义判断的安全元数据冲突也应另行复核，不应靠扩充标签掩盖。', '']
    names = ('A：建议优先审核的精确同义映射', 'B：建议逐项确认的新题材',
             'C：暂缓 / 需澄清边界', 'D：继续排除的典型标签')
    for group, name in zip(GROUPS, names):
        lines += [f'## {name}', '', '| 原标签 | 拟目标 | 出现作品数 | 其中原无有效题材 | 定义 / 审核边界 |',
                  '| --- | --- | ---: | ---: | --- |']
        for item in report['review'][group]:
            lines.append(f"| {_cell(item['source'])} | {_cell(item['target'] or '不映射 / 待定义')} | {item['document_frequency']} | {item['zero_genre_document_frequency']} | {_cell(item['rationale'])} |")
        lines.append('')
    lines += ['## 样例与限制', '',
        '每个标签至多两部安全公共作品样例见 JSON，按 Bangumi ID 固定顺序展示，供人工核对，不用来猜测标签含义；不是随机质量评测。',
        f"安全标记目录有 {report['safety_metadata_review_works']} 部带需复核的内容提示标签；这些作品的样例名称已抑制。只作元数据核对线索，不自动改分级；安全统计仍依据当前后端显式分级。",
        '“神作”等被现行清洗器提前排除的标签不在未知题材库存中，表中 0 表示本次库存未计数，不代表原始目录完全没有该词。',
        '审计命令不启用规则、不修改原始目录或私人数据；已发布同义映射由独立代码发布决定。本命令不新增前端选项，不改变 V1 排序公式，不启用 V2。', '',
        '## 复跑', '', '```powershell',
        '.venv/Scripts/python.exe -X utf8 manage.py audit_tag_expansion --output-dir .tools/tag-expansion/新的目录',
        '```', '',
        '命令强制 PostgreSQL 只读一致性事务，默认使用 evaluation/tag-expansion.20261020.proposal.json；支持当前及冻结的前一版基线，拒绝已批准执行配置、重复标签、不支持的基线和覆盖既有报告。', '']
    return '\n'.join(lines)
