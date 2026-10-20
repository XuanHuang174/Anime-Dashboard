"""Read-only public-tag inventory and isolated hypothetical coverage comparison."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unicodedata

from django.db.models import Count, Q

from . import features, taxonomy, tag_review_rules
from .engine import MAX_CATALOG_ROWS
from .features import extract_genres
from .models import RecommendationAnime
from .tag_review_rules import classify_unknown, review_key, validate_proposals

COVERAGE_VERSION = 'recommendations.tag-coverage.v1'
FIELDS = ('bangumi_id', 'title_zh', 'title_jp', 'tags', 'status_type')
ARTIFACTS = ('coverage.json', 'report.md', 'mapping-review.json', 'mapping-review.md', 'unknown-tags.json', 'manifest.json')


def public_tag_snapshot():
    """Adult/unknown counts only; no private table or synopsis/score/URL query."""
    scope = RecommendationAnime.objects.aggregate(total=Count('pk'),
        non_adult=Count('pk', filter=Q(nsfw=False)), adult=Count('pk', filter=Q(nsfw=True)),
        unknown=Count('pk', filter=Q(nsfw__isnull=True)))
    if scope['non_adult'] > MAX_CATALOG_ROWS:
        raise ValueError('Safe catalog exceeds bounded limit; no silent truncation')
    rows = list(RecommendationAnime.objects.filter(nsfw=False).order_by('bangumi_id', 'pk')
                .values(*FIELDS).iterator(chunk_size=500))
    return scope, rows


def _coverage(histogram):
    total = sum(histogram.values())
    covered = total - histogram[0]
    return {'works': total, 'with_effective_genres': covered, 'without_effective_genres': histogram[0],
            'one_genre': histogram[1], 'two_or_more_genres': total - histogram[0] - histogram[1],
            'coverage_fraction': covered / total if total else None,
            'genre_count_histogram': {str(key): value for key, value in sorted(histogram.items())}}


def analyze_public_tags(rows, scope, *, example_limit=3, taxonomy_version=None):
    """Rows must be explicitly non-adult. All derived features are copies only."""
    if not 0 <= example_limit <= 5:
        raise ValueError('example-limit must be 0–5')
    validate_proposals()
    rule = taxonomy_version or taxonomy.TAXONOMY_VERSION
    if rule not in taxonomy.AUDIT_TAXONOMY_VERSIONS:
        raise ValueError('Unsupported public tag audit baseline')
    approved_genres = (taxonomy.APPROVED_GENRES if rule == taxonomy.TAXONOMY_VERSION
                       else taxonomy.PREVIOUS_APPROVED_GENRES)
    before, draft = Counter(), Counter()
    eligible_before, eligible_draft = Counter(), Counter()
    missing, ignored, active_aliases = Counter(), Counter(), Counter()
    per_type, token_rows = {}, {}
    recovered = enriched = 0
    safety_review_works = 0
    candidate_genre_works = candidate_genre_zero_works = 0
    new_genre_frequency = Counter()
    approved_genre_frequency = Counter()
    approved_genre_works = 0
    fingerprint = hashlib.sha256()
    for row in rows:
        fingerprint.update(json.dumps({key: row[key] for key in FIELDS}, ensure_ascii=False,
                                      sort_keys=True).encode('utf-8') + b'\n')
        titles = (row['title_zh'], row['title_jp'])
        original = extract_genres(row['tags'], titles=titles, base_source='shared_base_tags', taxonomy_version=rule)
        baseline = original['tags']
        approved_here = baseline & approved_genres.keys()
        approved_genre_frequency.update(approved_here)
        approved_genre_works += bool(approved_here)
        hypothetical = set(baseline)
        ignored.update(original['ignored_counts'])
        if not baseline:
            missing['raw_tags_missing' if not row['tags'].strip() else
                    'unmapped_tags_present' if original['ignored_counts']['unknown'] else 'only_non_genre_tags'] += 1
        raw_tokens = [value.strip() for value in unicodedata.normalize('NFKC', row['tags']).split(',') if value.strip()]
        # A suspect public label is only a metadata-review flag, never an automatic
        # adult classification. Suppress such works' sample titles conservatively.
        safety_flag = any(classify_unknown(value)['rationale'] == 'safety_metadata_review' for value in raw_tokens)
        safety_review_works += safety_flag
        work_tokens, work_active = {}, set()
        for value in raw_tokens:
            result = extract_genres([value], titles=titles, base_source='shared_base_tags', taxonomy_version=rule)
            if result['tags']:
                canonical = next(iter(result['tags']))
                if value != canonical:
                    work_active.add((value, canonical))
                continue
            if not result['ignored_counts']['unknown']:
                continue  # Existing year/country/management/work-title exclusions stay excluded.
            key = review_key(value)
            if key not in work_tokens:
                work_tokens[key] = {'value': value, 'occurrences': 0}
            work_tokens[key]['occurrences'] += 1
        active_aliases.update(work_active)
        has_candidate_genre = False
        for key, work in work_tokens.items():
            classification = classify_unknown(work['value'])
            item = token_rows.setdefault(key, {'tag': work['value'], 'normalized_key': key,
                **classification, 'decision': 'pending_review', 'document_frequency': 0,
                'zero_genre_document_frequency': 0, 'occurrences': 0, 'type_distribution': Counter(),
                'examples': [], 'zero_genre_examples': [], 'suppressed_example_works': 0})
            item['document_frequency'] += 1
            item['zero_genre_document_frequency'] += not bool(baseline)
            item['occurrences'] += work['occurrences']
            item['type_distribution'][row['status_type'] or '(missing)'] += 1
            identity = row['bangumi_id']
            if safety_flag:
                item['suppressed_example_works'] += 1
            else:
                sample = {'bangumi_id': identity, 'title_zh': row['title_zh'],
                          'title_jp': row['title_jp'], 'status_type': row['status_type']}
                if len(item['examples']) < example_limit:
                    item['examples'].append(sample)
                if not baseline and len(item['zero_genre_examples']) < example_limit:
                    item['zero_genre_examples'].append(sample)
            if classification['category'] == 'candidate_genre':
                has_candidate_genre = True
            if classification['category'] == 'pending_synonym':
                target = classification['target']
                # Same title-exclusion rule applies AFTER mapping too. The title
                # is used only as an exclusion guard, never a feature source.
                if extract_genres([target], titles=titles, taxonomy_version=rule)['tags']:
                    hypothetical.add(target)
        candidate_genre_works += has_candidate_genre
        candidate_genre_zero_works += has_candidate_genre and not baseline
        new_genre_frequency.update(hypothetical - baseline)
        before[len(baseline)] += 1
        draft[len(hypothetical)] += 1
        recovered += not baseline and bool(hypothetical)
        enriched += hypothetical != baseline
        identity = row['bangumi_id']
        if type(identity) is int and 0 < identity <= 2_147_483_647:
            eligible_before[len(baseline)] += 1
            eligible_draft[len(hypothetical)] += 1
        work_type = row['status_type'] or '(missing)'
        bucket = per_type.setdefault(work_type, {'before': Counter(), 'draft': Counter()})
        bucket['before'][len(baseline)] += 1
        bucket['draft'][len(hypothetical)] += 1
    inventory = sorted(token_rows.values(), key=lambda item: (-item['document_frequency'], item['normalized_key']))
    for item in inventory:
        item['type_distribution'] = dict(sorted(item['type_distribution'].items()))
    categories, reasons = Counter(), Counter()
    for item in inventory:
        categories[item['category']] += 1
        if item['category'] == 'excluded_or_unknown':
            reasons[item['rationale']] += 1
    source_hashes = {name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        for name, module in (('features', features), ('taxonomy', taxonomy), ('review_rules', tag_review_rules))}
    source_hashes['tag_coverage'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    versions = {'active_taxonomy': rule, 'active_extraction': features.FEATURE_RULE_VERSION,
                'review': tag_review_rules.REVIEW_RULE_VERSION, 'proposal': tag_review_rules.PROPOSAL_VERSION}
    return {'contract_version': COVERAGE_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': scope, 'versions': versions,
        'catalog_fingerprint': fingerprint.hexdigest(),
        'report_fingerprint': hashlib.sha256(json.dumps({'catalog': fingerprint.hexdigest(), 'scope': scope,
            'versions': versions, 'example_limit': example_limit, 'source_sha256': source_hashes}, sort_keys=True).encode()).hexdigest(),
        'source_sha256': source_hashes,
        'example_limit': example_limit, 'before': _coverage(before),
        'after_active': _coverage(before),  # The audit itself never changes the active rules.
        'draft_synonyms_simulation': _coverage(draft),
        'valid_identity_coverage': {'before': _coverage(eligible_before), 'draft_synonyms_simulation': _coverage(eligible_draft)},
        'baseline_zero_genre_breakdown': dict(sorted(missing.items())),
        'ignored_token_occurrences': dict(sorted(ignored.items())),
        'draft_impact': {'recovered_zero_genre_works': recovered, 'enriched_works': enriched,
            'new_genre_document_frequency': dict(sorted(new_genre_frequency.items()))},
        'per_type': {key: {'before': _coverage(value['before']), 'draft_synonyms_simulation': _coverage(value['draft'])}
                     for key, value in sorted(per_type.items())},
        'unknown_unique_tags': len(inventory), 'unknown_categories': dict(sorted(categories.items())),
        'candidate_genre_observation': {'works_with_candidate_labels': candidate_genre_works,
            'zero_genre_works_with_candidate_labels': candidate_genre_zero_works,
            'meaning': 'Document counts only, not approved new features or a new-genre coverage simulation'},
        'excluded_or_unknown_unique_by_reason': dict(sorted(reasons.items())),
        'safety_metadata_review_works': safety_review_works,
        'already_active_aliases': [{'source': pair[0], 'target': pair[1], 'document_frequency': frequency,
                                  'decision': 'already_active_not_a_new_proposal'}
            for pair, frequency in sorted(active_aliases.items(), key=lambda item: (-item[1], item[0]))],
        'approved_alias_release': {
            'version': 'aliases.2026-10-14.approved-11' if rule == 'genres.v4' else taxonomy.APPROVED_ALIAS_RELEASE,
            'status': 'enabled_in_audited_rule_version',
            'aliases': dict(taxonomy.PREVIOUS_APPROVED_ALIASES if rule == 'genres.v4' else taxonomy.APPROVED_ALIASES)},
        'approved_genre_release': {'version': taxonomy.APPROVED_GENRE_RELEASE if rule == taxonomy.TAXONOMY_VERSION else 'genres.2026-10-14.approved-7',
            'status': 'enabled_in_audited_rule_version', 'definitions': dict(approved_genres),
            'exact_labels_only': True, 'automatic_parent_expansion': False},
        'approved_genre_observation': {'works_with_approved_labels': approved_genre_works,
            'genre_document_frequency': {tag: approved_genre_frequency[tag] for tag in approved_genres},
            'meaning': 'Effective exact public labels after exclusion/deduplication; per-genre counts overlap, not recommendation quality'},
        'unknown_tags': inventory,
        'policy': {'mapping_applied': False, 'ranking_changed': False, 'private_tables_read': False,
            'mapping_applied_semantics': 'This audit activates no mappings; the enabled code release is recorded separately.',
            'source_tags_modified': False, 'external_services_called': False,
            'empty_features_enter_personalized_pool': False,
            'new_genre_candidates_in_simulation': False,
            'frequency_unit': 'one normalized tag per work; occurrences separately counted',
            'samples': 'up to example_limit for all works and separately for zero-genre subset; explicitly non-adult public metadata only; title equality excludes, never infers genres or series',
            'safety_metadata': 'public warning labels flag review only; not a rating decision; sample titles suppressed'}}


def attach_release_comparison(report, baseline_directory):
    """Compare actual active coverage with a verified prior-rule local report.

    No taxonomy is loaded from JSON. Different catalog snapshots fail closed so
    data changes cannot be misreported as an approved feature-release effect.
    """
    directory = Path(baseline_directory)
    coverage_path, manifest_path = directory / 'coverage.json', directory / 'manifest.json'
    if coverage_path.stat().st_size > 2_000_000 or manifest_path.stat().st_size > 100_000:
        raise ValueError('Baseline report exceeds bounded size')
    content = coverage_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        baseline = json.loads(content)
        if (manifest['complete'] is not True or manifest['contract_version'] != COVERAGE_VERSION
                or manifest['sha256']['coverage.json'] != digest or baseline['contract_version'] != COVERAGE_VERSION):
            raise ValueError('Baseline manifest verification failed')
        prior_version = baseline['versions']['active_taxonomy']
        allowed = {'genres.v3': ('genres.v2',), 'genres.v4': ('genres.v2', 'genres.v3'),
                   'genres.v5': ('genres.v2', 'genres.v3', 'genres.v4'),
                   'genres.v6': ('genres.v2', 'genres.v3', 'genres.v4', 'genres.v5')}
        if prior_version not in allowed.get(report['versions']['active_taxonomy'], ()):
            raise ValueError('Comparison requires a supported prior taxonomy report, not current/future rules')
        if baseline['scope'] != report['scope'] or baseline['catalog_fingerprint'] != report['catalog_fingerprint']:
            raise ValueError('Catalog snapshot changed; cannot attribute coverage changes to approved aliases')
        prior = baseline['after_active']
        if prior != baseline['before']:
            raise ValueError('Pre-approval baseline must record unchanged actual coverage')
        histogram = prior['genre_count_histogram']
        if (not isinstance(histogram, dict) or any(not key.isdecimal() or type(value) is not int or value < 0
                                                  for key, value in histogram.items())):
            raise ValueError('Invalid baseline coverage histogram')
        recomputed = _coverage(Counter({int(key): value for key, value in histogram.items()}))
        if prior != recomputed or prior['works'] != report['before']['works']:
            raise ValueError('Baseline coverage is inconsistent')
    except (KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Invalid baseline report structure or encoding') from None
    current = report['before']
    if current['with_effective_genres'] < prior['with_effective_genres']:
        raise ValueError('Approved feature-only release unexpectedly reduced coverage; inspect rules before publishing')
    report['approved_release_comparison'] = {
        'status': 'verified_same_catalog_actual_comparison', 'baseline_report_sha256': digest,
        'before_taxonomy': prior_version, 'after_taxonomy': report['versions']['active_taxonomy'],
        'before': prior, 'after': current, 'identical_catalog_snapshot': True,
        'additional_covered_works': current['with_effective_genres'] - prior['with_effective_genres'],
        'coverage_fraction_delta': (current['coverage_fraction'] - prior['coverage_fraction'])
                                  if prior['coverage_fraction'] is not None else None,
        'meaning': 'Actual feature coverage only; the difference includes ALL rule changes between these versions, not recommendation accuracy or database row mutations'}
    report['report_fingerprint'] = hashlib.sha256((report['report_fingerprint'] + digest).encode()).hexdigest()


def _cell(value):
    # Public labels/titles are untrusted Markdown, not instructions or raw HTML.
    import html
    value = html.escape(str(value), quote=True).replace('\r', ' ').replace('\n', ' ')
    for char in ('\\', '`', '*', '_', '[', ']', '|'):
        value = value.replace(char, '\\' + char)
    return value


def _percent(value):
    return '不可计算（空目录）' if value is None else f'{value:.2%}'


def review_document(report):
    proposals = [{**item, 'reviewer': None, 'review_note': '', 'apply_to_production': False}
                 for item in report['unknown_tags'] if item['category'] != 'excluded_or_unknown']
    return {'proposal_version': report['versions']['proposal'], 'active_taxonomy': report['versions']['active_taxonomy'],
            'approved_alias_release': report['approved_alias_release'],
            'approved_genre_release': report['approved_genre_release'],
            'catalog_fingerprint': report['catalog_fingerprint'], 'status': 'pending_human_review',
            'application': 'This file is NOT an executable config; edits do not activate mappings. A separate reviewed code/version change is required.',
            'pending_synonyms': [row for row in proposals if row['category'] == 'pending_synonym'],
            'candidate_genres_not_simulated': [row for row in proposals if row['category'] == 'candidate_genre'],
            'already_active_aliases': report['already_active_aliases']}


def review_markdown(review):
    lines = ['# 待审核映射清单（未应用）', '', f"草案版本：{review['proposal_version']}", '',
        f"当前已启用映射发布：{review['approved_alias_release']['version']}；累计已批准的 {len(review['approved_alias_release']['aliases'])} 项不再重复列为待审。",
        f"当前已启用题材发布：{review['approved_genre_release']['version']}；累计已批准的 {len(review['approved_genre_release']['definitions'])} 项不再重复列为待审。",
        '下表剩余项目均待人工审核；修改 JSON 不会自动生效。新题材不参与覆盖试算。', '',
        '| 标签 | 拟映射/候选 | 作品数 | 其中原来无题材 | 说明 | 决策 |', '| --- | --- | --- | --- | --- | --- |']
    for row in review['pending_synonyms'] + review['candidate_genres_not_simulated']:
        lines.append(f"| {_cell(row['tag'])} | {_cell(row['target'] or '新题材候选，定义待确认')} | {row['document_frequency']} | {row['zero_genre_document_frequency']} | {_cell(row['rationale'])} | 待审核 |")
    lines += ['', '每项作品样例及类型分布见 mapping-review.json。各行作品可能重叠，不能直接相加当成覆盖提升。', '',
        '“搞笑 → 喜剧”等已有映射不重复申请；详见 JSON 的 already_active_aliases。',
        '玄幻、穿越、魔法少女是独立精确题材，不扩展父级；BL、耽美、实验、超现实、猎奇仍待审。', '']
    return '\n'.join(lines)


def coverage_markdown(report):
    lines = ['# 核心 5B-1：公共标签覆盖审计', '', f"生成时间：{report['created_at']}",
        f"目录指纹：`{report['catalog_fingerprint']}`", '',
        f"全库 {report['scope']['total']}；明确非成人 {report['before']['works']}；成人 {report['scope']['adult']}、未知分级 {report['scope']['unknown']} 仅计数不取样。",
        '不读取私人手账、简介或长评，不访问外部服务；统计命令不改原标签、身份、评分或线上排序。', '',
        f"当前规则：{report['versions']['active_taxonomy']} / {report['versions']['active_extraction']}；已启用映射：{report['approved_alias_release']['version']}。", '',
        f"已启用独立题材：{'、'.join(report['approved_genre_release']['definitions'])}；精确识别，不自动补父级或近义词。", '',
        f"有效题材中包含上述新标签的作品共 {report['approved_genre_observation']['works_with_approved_labels']} 部（作品去重）；各题材计数见 coverage.json，不能相加作为新增覆盖数。", '',
        '## 当前实际覆盖与剩余待审草案（假设）', '',
        '| 指标 | 当前规则实际 | 审计后实际（不写库） | 若剩余同义候选获批（离线试算） |', '| --- | --- | --- | --- |']
    for label, key in (('有有效题材', 'with_effective_genres'), ('无有效题材', 'without_effective_genres'),
                       ('仅一个题材', 'one_genre'), ('两个及以上', 'two_or_more_genres')):
        lines.append(f"| {label} | {report['before'][key]} | {report['after_active'][key]} | {report['draft_synonyms_simulation'][key]} |")
    lines.append('| 作品题材覆盖率 | ' + ' | '.join(_percent(report[key]['coverage_fraction'])
                 for key in ('before', 'after_active', 'draft_synonyms_simulation')) + ' |')
    lines += ['', f"**累计已批准的 {len(report['approved_alias_release']['aliases'])} 项映射与 {len(report['approved_genre_release']['definitions'])} 项题材已纳入当前规则；剩余草案尚未启用。试算只表示题材覆盖，不是推荐准确率、匹配概率或正式库修复数量。**",
        f"当前无有效题材的构成：{report['baseline_zero_genre_breakdown']}。",
        f"试算新增覆盖 {report['draft_impact']['recovered_zero_genre_works']} 部，题材集合变化 {report['draft_impact']['enriched_works']} 部（按作品去重）。",
        '覆盖率分母为全部明确非成人作品；合法 Bangumi ID 子集另见 coverage.json，不用缺失身份凑推荐候选。']
    if comparison := report.get('approved_release_comparison'):
        lines += ['', '## 已批准规则：同目录实际前后对比', '',
            f"基线 {comparison['before_taxonomy']} → 当前 {comparison['after_taxonomy']}；目录指纹一致，基线 manifest 校验通过。该差异包含这两个版本之间的全部规则变动。", '',
            '| 指标 | 启用前实际 | 当前代码实际 |', '| --- | --- | --- |',
            f"| 有有效题材作品 | {comparison['before']['with_effective_genres']} | {comparison['after']['with_effective_genres']} |",
            f"| 无有效题材作品 | {comparison['before']['without_effective_genres']} | {comparison['after']['without_effective_genres']} |",
            f"| 覆盖率 | {_percent(comparison['before']['coverage_fraction'])} | {_percent(comparison['after']['coverage_fraction'])} |",
            '', f"新增覆盖 {comparison['additional_covered_works']} 部。这是当前代码对同一真实目录的提取结果，不代表线上服务已完成部署或推荐准确率提升。"]
    lines += ['', '## 未识别标签三分类', '', f"不同未识别标签 {report['unknown_unique_tags']} 个；按 NFKC + 大小写归一，每作品计一次。",
        f"分类：{report['unknown_categories']}。完整频率、无题材子集频率、类型分布、样例见 unknown-tags.json。",
        '全部安全作品均统计，同时单独标记无有效题材子集，避免只看问题子集而忽略已有题材作品。', '',
        f"新题材候选标签涉及 {report['candidate_genre_observation']['works_with_candidate_labels']} 部作品，其中 {report['candidate_genre_observation']['zero_genre_works_with_candidate_labels']} 部当前无有效题材。这只是标签出现的去重统计，不是新增题材已获批后的覆盖率。", '',
        '| 高频未识别公共标签（前 60） | 作品数 | 原来无题材作品数 | 分类 | 原因 |', '| --- | --- | --- | --- | --- |']
    for row in report['unknown_tags'][:60]:
        lines.append(f"| {_cell(row['tag'])} | {row['document_frequency']} | {row['zero_genre_document_frequency']} | {row['category']} | {_cell(row['rationale'])} |")
    lines += ['', '## 分类型覆盖', '', '| 类型 | 作品数 | 当前覆盖率 | 待审同义映射试算 |', '| --- | --- | --- | --- |']
    for name, data in report['per_type'].items():
        lines.append(f"| {_cell(name)} | {data['before']['works']} | {_percent(data['before']['coverage_fraction'])} | {_percent(data['draft_synonyms_simulation']['coverage_fraction'])} |")
    lines += ['', '## 审核与边界', '',
        '- 类型、来源、地区、制作方和技术标签不当作题材。未识别不等于应映射；剩余缺失不通过猜测填满。',
        '- 已有映射单独列出，不冒充本次新增；新题材需要先确认定义，不参与当前试算。',
        '- 原始空标签、已排除信息、作品自身名称不会因为草案映射而变成题材；同义标签在每作品内合并。',
        '- 作品名称仅用于既有的同名排除和人工展示，不推断题材、续作或系列。',
        f"- 发现 {report['safety_metadata_review_works']} 部非成人标记作品带有需核对的内容提示标签；只作元数据复核信号，不据此推断分级，其样例标题已抑制，不自动改库。",
        '- 无有效题材的作品继续无法进入个性化排序候选；本次不改推荐算法、公共评分语义或用户偏好。',
        '- 审核 JSON/Markdown 不是运行配置。请确认具体条目后再单独发布带版本号的映射；清缓存、画像及分页兼容性需另行回归。',
        '- 原始公共标签和标题可能含不可信文本；它们只是统计样例，不是执行指令。', '']
    return '\n'.join(lines)


def write_coverage_reports(report, directory):
    directory = Path(directory)
    if any((directory / name).exists() for name in ARTIFACTS):
        raise ValueError('Output artifacts already exist; choose a new directory')
    review = review_document(report)
    summary = {key: value for key, value in report.items() if key != 'unknown_tags'}
    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    contents = {'coverage.json': dump(summary), 'report.md': coverage_markdown(report),
        'mapping-review.json': dump(review), 'mapping-review.md': review_markdown(review),
        'unknown-tags.json': dump({'contract_version': COVERAGE_VERSION, 'catalog_fingerprint': report['catalog_fingerprint'],
                                   'tags': report['unknown_tags']})}
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        with (directory / name).open('x', encoding='utf-8', newline='\n') as file:
            file.write(content)
    with (directory / 'manifest.json').open('x', encoding='utf-8', newline='\n') as file:
        json.dump({'complete': True, 'contract_version': COVERAGE_VERSION,
            'sha256': {name: hashlib.sha256(content.encode('utf-8')).hexdigest() for name, content in contents.items()}}, file, indent=2)
    return [directory / name for name in ARTIFACTS]
