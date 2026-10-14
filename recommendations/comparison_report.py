"""Private local comparison artifacts; never overwrite, publish or auto-enable."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .version_control import GATE_NAMES, canonical_bytes, sign_artifact

FILES = ('comparison.json', 'report.md', 'manifest.json', 'approval.json')


def delta(left, right):
    return round(right - left, 6) if left is not None and right is not None else None


def markdown_report(report):
    lines = ['# 5B-3 两轮同条件对照评测', '', f"生成时间：{report['created_at']}",
        f"代码/配置指纹：`{report['release']['fingerprint']}`",
        f"公共快照指纹：`{report['catalog_fingerprint']}`", '',
        '## 第一轮：固定 content.v1，仅改变题材规则', '',
        '| 规则 | 安全标准作品数 | 有效题材作品数 | 覆盖率 |', '| --- | --- | --- | --- |']
    for rule, value in report['coverage'].items():
        lines.append(f"| {rule} | {value['works']} | {value['with_genres']} | {value['coverage_fraction']} |")
    lines += ['', '旧规则为 5A 时的 genres.v2（26 题材/10 同义映射）；新规则为 5B-1 的 genres.v4（33 题材/21 同义映射）。',
        '两轮共用同一只读数据库快照和同一批显式授权账号。比较指标统一用新词表计算，避免指标定义改变制造虚假提升。',
        '旧规则冷启动或训练不足的留出目标只报告不可比较，不伪造评分或更换样本。', '',
        '## 授权自然样本：两轮分别比较', '']
    for position, sample in enumerate(report['samples'], 1):
        lines += [f'### sample_{position:03d}', '',
            f"自然有效高分样本：{sample['usable_samples']}；输入指纹：`{sample['input_fingerprint']}`。",
            f"规则改变后的样本数：`{sample['changed_rule_sample_counts']}`。"]
        for name, pair in sample['passes'].items():
            lines += ['', f"#### {name}: {pair['left']} → {pair['right']}", '',
                '| 分类 | 相关性代理 Δ | 重复 Jaccard Δ | 新题材占比 Δ | 证据错误（左/右） |',
                '| --- | --- | --- | --- | --- |']
            for category in ('all', 'familiar', 'explore'):
                left, right = (sample['arms'][arm]['categories'][category] for arm in (pair['left'], pair['right']))
                lines.append(f"| {category} | {delta(left['topic_overlap_proxy_mean'], right['topic_overlap_proxy_mean'])} | "
                    f"{delta(left['pairwise_genre_jaccard_mean'], right['pairwise_genre_jaccard_mean'])} | "
                    f"{delta(left['novel_genre_ratio_mean'], right['novel_genre_ratio_mean'])} | "
                    f"{left['evidence_error_count']}/{right['evidence_error_count']} |")
            lines += ['', f"同目标配对留出：{pair['paired_targets']}；不可比较目标：{pair['skipped_unpaired_targets']}。",
                      f"Hit/MRR/binary-NDCG：`{json.dumps(pair['holdout'], ensure_ascii=False)}`。"]
        lines += ['', '| 引擎/规则 | 分类 | 最大计算 ms | 最大 SQL 次数 | 候选池 | 重复运行稳定 |',
                   '| --- | --- | --- | --- | --- | --- |']
        for name, arm in sample['arms'].items():
            for category, performance in arm['performance'].items():
                pool = [row['candidate_pool'] for row in performance['runs']]
                lines.append(f"| {name} | {category} | {performance['maximum_compute_ms']} | "
                    f"{performance['maximum_sql_queries']} | {pool} | {performance['stable_order_and_evidence']} |")
    lines += ['', '## 六项硬门槛', '', '| 门槛 | 状态 | 诊断 |', '| --- | --- | --- |']
    for name, gate in report['gates'].items():
        lines.append(f"| {name} | {gate['status']} | {json.dumps(gate['details'], ensure_ascii=False)} |")
    lines += ['', f"固定门槛策略：`{json.dumps(report['configuration']['gates'], ensure_ascii=False)}`。",
              f"允许人工决定切换：{report['eligible_for_manual_switch']}（脚本不会切换线上）。",
              '任一 fail 或 insufficient_evidence：不生成 approval.json，继续 V1。', '', '## 合规与局限', '',
              'Bangumi 评分不是用户喜欢概率；没有评分人数，未进行评分可信度修正。',
              '不查询长评、不改真实评分、不调用外部模型、不按标题推断系列关系；结果和报告不展示匹配百分比。']
    lines += ['- ' + text for text in report['limitations']]
    lines += ['', '报告只保存在受控的 .tools 目录，不提交 Git，不进入静态资源/CDN。',
              '回滚：服务端 RECOMMENDATION_ALGORITHM_VERSION=content.v1，重启服务；无需验收凭据或清全局缓存。',
              '上线前还需确认部署环境可读取同 SECRET_KEY 签名的凭据、同代码/策略指纹及环境性能预算。']
    return '\n'.join(lines) + '\n'


def write_comparison(report, directory):
    directory = Path(directory)
    if any((directory / name).exists() for name in FILES):
        raise ValueError('Existing artifacts found; overwrite refused')
    directory.mkdir(parents=True, exist_ok=True)
    report = {**report, 'created_at': datetime.now(timezone.utc).isoformat()}
    contents = {'comparison.json': json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                'report.md': markdown_report(report)}
    if report['eligible_for_manual_switch']:
        payload = {'gates': {key: report['gates'][key]['status'] for key in GATE_NAMES},
                   'sample_count': len(report['samples']),
                   'comparison_sha256': hashlib.sha256(canonical_bytes(report)).hexdigest()}
        contents['approval.json'] = json.dumps(sign_artifact('approval', payload), ensure_ascii=False, indent=2) + '\n'
    for name, content in contents.items():
        with (directory / name).open('x', encoding='utf-8', newline='\n') as file:
            file.write(content)
    # Completion marker LAST; partial artifacts can never count as approval.
    with (directory / 'manifest.json').open('x', encoding='utf-8') as file:
        json.dump({'complete': True, 'eligible_for_manual_switch': report['eligible_for_manual_switch'],
                   'sha256': {name: hashlib.sha256(content.encode()).hexdigest() for name, content in contents.items()}}, file, indent=2)
    return [directory / name for name in (*contents, 'manifest.json')]
