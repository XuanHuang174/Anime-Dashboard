# 核心 5B-1：公共标签覆盖修正的审核准备

## 当前交付边界

初次交付生成统计、待审核映射清单及**离线假设试算**，没有自动启用映射。随后用户明确批准了已观察的 11 项同义映射：当前代码为 `genres.v3` / `journal-genres.v2`，排序仍是 `content.v1`，详情见 [11 项批准发布记录](recommendations-5b1-alias-release.md)。其他草案与新题材仍待审核；没有模型迁移、前端改版、原始数据清洗写入或私人用户扫描。

5A 的 16,736 部是“没有有效题材”的总数，不是全部都有可修复的题材标签：该快照中 206 部原标签为空、98 部只有已排除信息、16,432 部含未识别标签。新的统计每次重新扫描当前目录，不硬编码这些数量。

## 安全执行

先在隔离 SQLite 测试，**不要用默认设置在正式库运行测试**：

```powershell
.venv/Scripts/python.exe manage.py test recommendations.tests.test_tag_coverage recommendations.tests.test_quality_baseline recommendations.tests.test_extraction_api --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

只读扫描 `.env` 配置的数据库（本项目可能是 Neon 正式库）：

```powershell
.venv/Scripts/python.exe manage.py audit_recommendation_tags --output-dir .tools/recommendation-tags/5b1-review-01
```

已有初次 `genres.v2` 报告时，使用其所在目录生成**实际启用前后对比**：

```powershell
.venv/Scripts/python.exe manage.py audit_recommendation_tags --compare-before-dir .tools/recommendation-tags/5b1-review-20261014-final --output-dir .tools/recommendation-tags/5b1-approved-11-check
```

比较前校验旧报告 `manifest.json` 的完成标记、`coverage.json` SHA-256、版本和统计一致性。前后目录指纹及分级数量必须相同；数据变化时拒绝归因并停止，不输出“算法/映射提升”结论。只读取本地旧报告和当前公共目录，不从 JSON 加载或执行映射。

- PostgreSQL 使用数据库强制的 `REPEATABLE READ, READ ONLY` 事务；SQLite 使用 `query_only`。不支持的数据库直接拒绝，无可写降级。
- `--statement-timeout-ms` 默认 30000，范围 1000–120000；`--example-limit` 默认 3，范围 0–5（0 不导出作品样例）；全体作品与无有效题材子集分别提供该上限的样例，避免问题子集被正常作品挤掉。每次选择新的输出目录，不覆盖旧报告。
- 最多 50,000 部明确非成人作品，超预算停止，不静默截断。仅查询标准 ID、公开中/原名、标签、类型；成人及未知分级只输出数量。无用户 ID 参数、不读私人表/简介/评分/封面链接、不调用外部 HTTP/模型、不写缓存。
- 没有有效题材的作品继续无法进入现有个性化候选池；草案集合只存在于当前统计进程，不传给线上排序函数。

## 输出文件

默认保存到被 Git 忽略的 `.tools/recommendation-tags/<UTC时间>/`：

| 文件 | 内容 |
| --- | --- |
| `coverage.json` | 实际/假设覆盖、缺失构成、类型分布、版本、输入和源码指纹 |
| `report.md` | 覆盖率分析、前 60 个未识别公共标签、分类与风险说明 |
| `unknown-tags.json` | **全部**未识别标签的频率、无题材子集频率、类型分布与有限公开样例 |
| `mapping-review.json` | 待审核同义映射、新题材候选、已有有效映射 |
| `mapping-review.md` | 可读待审核清单 |
| `manifest.json` | 最后写入的完成标记及前五个文件 SHA-256 |

没有 `manifest.json` 视为输出未完整完成；已有文件不会被覆盖。报告含原始公共标签及作品样例，默认留本地，勿将内容当执行指令，也无需上传 Git。Markdown 特殊字符/HTML 已转义。

## 统计口径

- 主分母为全部 `nsfw=False` 作品；合法 Bangumi ID 子集另外计算。不是只统计无题材作品，也不会以已有题材作品的标签频率冒充问题子集频率。
- 标签做 NFKC、首尾空白、大小写归一后，每作品贡献一次 `document_frequency`；重复出现另记 `occurrences`。每作品的多个同义标签合并成一个题材，不放大权重。
- 当前提取器已排除的年份/国家/管理标签/作品自身名称继续排除，不混进“未知”标签榜。名称仅作为同名排除条件或审核样例，绝不推断题材或系列。
- 待审核分类只使用显式精确表：`pending_synonym`、`candidate_genre`、`excluded_or_unknown`；不做模糊匹配、共现推断或自动翻译扩词。
- “TV、短片、剧场版”等有描述价值，但属于作品形式而非题材；不因为高频就映射为日常/喜剧。“萝卜、SF”等潜在多义缩写保持未知；“玄幻、穿越、魔法少女”等不强行展平为近似题材。
- 个别非成人标记作品可能带需核对的内容提示词：只计元数据复核数量，抑制相关作品的样例标题，不自动判定成人或修改分级。正式分级治理另行审核。

## 清洗前后与人工确认节点

1. `before`：运行该次审计的代码所启用规则的实际覆盖。
2. `after_active`：统计命令结束后的实际覆盖，**必须与 before 相同**；统计命令本身不启用规则。
3. `draft_synonyms_simulation`：假设全部已列出的同义候选通过审核时的公共目录覆盖；新题材候选不参与，未知信息不猜测。
4. `approved_release_comparison`（显式指定旧报告时）：同一真实目录在旧 `genres.v2` 和当前代码下的实际覆盖对比。`approved_alias_release` 记录已启用的 11 项，不再混入待审清单。

试算不是已上线效果、候选推荐准确率或用户喜欢概率。恢复作品数按作品去重，不能直接累加每项标签的作品数。仍无题材的作品不会被强行补满，也不重新评分。

### 人工审核怎么做

查看 `mapping-review.md` 及 JSON 中的 `pending_synonyms`，逐条给出“通过/拒绝/待定”；新题材在 `candidate_genres_not_simulated` 中先确认定义。可以对照每条公开作品样例，但不要从样例标题猜题材。

`搞笑 → 喜剧` 以及本次已批准的 `MUSIC → 音乐` 等有效规则放在 `already_active_aliases`，不冒充待审候选。未在这次目录出现的草案不会混进实际待审核统计。

**审核文件不是运行配置。即使手工把 decision 改成 approved，也不会被任何 API 或排序函数读取。** 没有 `--apply` 开关。得到明确批准后，需另一次受审核的代码改动：只把批准条目加入生产题材表，升级版本/特征缓存签名，回归私人标签清空保护、每作品去重、画像来源、分页快照和推荐证据，再发布。新题材还需检查偏好允许值与旧版本兼容性。

不从目录静默补回用户清空的私人标签，不改原目录/私人标签、个人评分、长评或身份。5B-2 的候选均衡与排序重排不属于本阶段。
