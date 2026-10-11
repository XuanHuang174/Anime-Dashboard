"""Local-only final catalog audit and bounded production-pilot preparation.

Run with python -m recommendations.audit_catalog. No Django, credentials,
network, database or R2 access; all outputs are derived public-source artifacts.
"""
import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from .normalization import clean_id, clean_nsfw
from .source_pipeline import SourceError, artifact_writer, iter_jsonlines


PILOT_QUOTAS = {'ordinary_cover': 17, 'ordinary_missing': 1,
                'adult_cover': 1, 'adult_missing': 1}


def read_catalog(path):
    with Path(path).open(encoding='utf-8-sig') as incoming:
        for number, row in iter_jsonlines(incoming):
            if not isinstance(row, dict):
                raise SourceError(f'第 {number} 行不是对象')
            identity = clean_id(row.get('bangumi_id'))
            if not isinstance(row.get('nsfw'), bool):
                raise SourceError(f'第 {number} 行成人标记不明确，需人工核验')
            score = row.get('score', 0)
            if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 10:
                raise SourceError(f'第 {number} 行评分不符合规范')
            yield identity, row


def audit_catalog(source, enriched, enrichment_report, report, pilot, *, log_path=None, overwrite=False):
    paths = [Path(p).resolve() for p in (source, enriched, enrichment_report, report, pilot)]
    if len(set(paths)) != len(paths) or (log_path and Path(log_path).resolve() in paths):
        raise SourceError('输入、输出、报告与日志路径必须不同')
    # Check both destinations before publishing either artifact.
    if not overwrite and any(p.exists() for p in paths[3:]):
        raise SourceError('验收输出已存在，请换路径或明确使用 --overwrite')
    metadata = json.loads(paths[2].read_text(encoding='utf-8'))
    if any(metadata.get(key) for key in ('stopped_for_authorization', 'stopped_for_outage', 'stopped_by_user')):
        raise SourceError('补齐提前停止，不能作为全量完成批次')
    source_ids = {}
    for identity, row in read_catalog(paths[0]):
        if identity in source_ids:
            raise SourceError('原始标准文件存在重复 ID')
        source_ids[identity] = clean_nsfw(row['nsfw'])
    counts, seen, selected = Counter(), set(), {key: [] for key in PILOT_QUOTAS}
    for identity, row in read_catalog(paths[1]):
        if identity in seen or identity not in source_ids:
            raise SourceError('最终文件 ID 重复或出现来源之外的 ID')
        if source_ids[identity] is True and row['nsfw'] is not True:
            raise SourceError('成人标记被降级，停止验收')
        seen.add(identity)
        adult, cover = row['nsfw'] is True, bool(row.get('cover_url'))
        counts['rows'] += 1
        counts['adult' if adult else 'ordinary'] += 1
        counts['cover_links' if cover else 'missing_cover'] += 1
        category = ('adult' if adult else 'ordinary') + ('_cover' if cover else '_missing')
        counts[category] += 1
        if len(selected[category]) < PILOT_QUOTAS[category]:
            selected[category].append(row)
    if seen != source_ids.keys() or counts['rows'] != metadata.get('counts', {}).get('rows'):
        raise SourceError('来源、最终文件及补齐报告总数不一致')
    if counts['missing_cover'] != metadata['counts'].get('missing_cover_url', 0) or counts['adult'] != metadata['counts'].get('nsfw', 0):
        raise SourceError('最终文件的缺图/成人统计与补齐报告不一致')
    if any(len(selected[key]) != quota for key, quota in PILOT_QUOTAS.items()):
        raise SourceError('无法满足小批测试的边界样本配额，不发布不完整样本')
    reasons = Counter()
    if log_path:
        with Path(log_path).open(encoding='utf-8-sig') as log:
            for line in log:
                match = re.search(r'条目 [0-9]+ 未补齐：([^；\r\n]+)；', line)
                if match:
                    reasons[match[1]] += 1
        if sum(reasons.values()) != metadata['counts'].get('failed', 0):
            raise SourceError('日志失败总数与最终报告不一致')
    digests = {}
    for key, path in zip(('source_sha256', 'enriched_sha256'), paths[:2]):
        with path.open('rb') as f:
            digests[key] = hashlib.file_digest(f, 'sha256').hexdigest()
    result = {'counts': dict(counts), 'source_ids_equal': True, **digests,
              'api_failure_reasons': dict(reasons), 'pilot_quotas': PILOT_QUOTAS,
              'pilot_ids': [row['bangumi_id'] for rows in selected.values() for row in rows],
              'note': '仅本地验收；封面URL不等于托管成功；成人公开图在Django入库时清空'}
    with artifact_writer(paths[4], overwrite=overwrite) as output:
        for rows in selected.values():
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + '\n')
    with artifact_writer(paths[3], overwrite=overwrite) as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--enriched', required=True)
    parser.add_argument('--enrichment-report', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--pilot', required=True)
    parser.add_argument('--log')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    try:
        result = audit_catalog(args.source, args.enriched, args.enrichment_report,
                               args.report, args.pilot, log_path=args.log, overwrite=args.overwrite)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (SourceError, ValueError, OSError) as exc:
        print(f'验收失败：{str(exc) if isinstance(exc, SourceError) else type(exc).__name__}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
