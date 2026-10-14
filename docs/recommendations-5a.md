# 核心 5A：数据审计与 V1 质量基线

## 边界

只新增后台命令、只读审计/评测和本地报告。不改前端、不改 V1 权重、不写入目录、私人评分、标签、长评、偏好或身份。不调用外部模型、HTTP 图片/资料服务；评测入口不读写公共特征缓存，避免依赖 Redis/缓存旧快照。默认不查询任何私人手账，只有显式 `--user-id` 才使用该授权用户样本，不提供全站用户扫描参数。

PostgreSQL 使用 **REPEATABLE READ + READ ONLY** 事务与查询/锁超时；SQLite 使用 `PRAGMA query_only`，退出后恢复原状态。其他数据库、已开启 PostgreSQL 事务、超预算输入都直接拒绝，不降级为可写操作。没有数据库迁移。

## 执行

先在隔离测试库运行：

```powershell
.venv/Scripts/python.exe manage.py test recommendations.tests.test_quality_baseline recommendations.tests.test_engine_api --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

只审计当前配置数据库的公共推荐库（不会查询私人手账）：

```powershell
.venv/Scripts/python.exe manage.py audit_recommendation_quality --output-dir .tools/recommendation-quality/catalog-run-01
```

得到明确授权的样本用户 ID 后，可增加只读基线：

```powershell
# 将 123 替换为明确授权的实际用户 ID，不是 Bangumi ID/手账记录 ID。
.venv/Scripts/python.exe manage.py audit_recommendation_quality --user-id 123 --top-k 10 --max-holdouts 5 --output-dir .tools/recommendation-quality/sample-run-01
```

可重复 `--user-id`，最多 10 个用户；Top-K 为 1–30；每用户留出目标 1–10 个，总留出预算不超过 50。SQL 查询超时默认 30000ms，可用 `--statement-timeout-ms` 在 1000–120000ms 内设置。非成人目录超出 V1 的 50000 条上限会停止，不静默截断。

如果暂时没有足够的授权画像样本，可以增加 `--include-probes`。它使用“音乐/日常”“科幻/悬疑”“百合/日常”三个固定**合成题材查询**对真实目录运行相同 V1 排序，明确标记 `synthetic_preference_probe`，不读取额外私人账号。探针可以检查候选、分类差异和证据，但不是用户画像效果或精准率，不能用于个人评分留出指标；默认不运行探针。

注意：未指定隔离 settings 的命令读取本地 `.env` 中配置的数据库，可能是 Neon 正式库。只读保护由数据库强制执行，但仍应在低峰期执行。不要在正式库运行测试、迁移或为了样本修改评分。样本账号建议包含至少 4 部有效高分作品；只有 3 部时仅能运行现状基线，不能留出一部后绕过最低样本门槛。

## 报告与隐私

输出目录默认在被 Git 忽略的 `.tools/recommendation-quality/<UTC时间>/`，也可显式指定一个新的目录：

- `baseline.json`：质量统计、版本、配置、数据指纹、真实样本结果与内部指标。
- `report.md`：可阅读的数据质量报告与解释。
- `cases.json`：评测场景和应满足的不变量，不是伪造的真实测评结论。
- `manifest.json`：最后写入的完成标记及前三个文件的 SHA-256；没有此文件时不要将目录视为一次完整交付。

已有报告一律不覆盖。用户样本使用 `sample_001` 等当次匿名编号，不输出邮箱、用户 ID、私人记录 PK、个人分数、来源作品标题或长评；推荐结果只展示非成人公共候选的 Bangumi ID。输入指纹采用服务端密钥 HMAC，密钥与原始输入不导出。含样本指标的文件仍是私有审计资料，不应提交 Git、公开网站或发送给外部模型。

## 数据质量语义

- 全库成人/未知分级仅统计数量；只读取明确非成人作品的标题、标签、年份、类型、评分及封面链接。缺少标准 ID 的非成人条目也会审计，但不进入评测候选。
- 标签分别标记：原始空标签、只有年份/国家/管理信息、现有词表未覆盖、0 个有效题材、仅 1 个有效题材。同义标签每作品只计一次，未知标签不猜测、不修改。
- 同时输出作品类型分布与分类型的题材覆盖，辅助检查目录范围和词表覆盖，不能仅凭总缺失量断言数据或排序算法有错。
- 年份区分：缺失、格式错误、无效日期、仅年份、季度、非季度月份及需要人工核查的年份。非季度月份不自动改写；未来作品不是天然错误。
- 评分区分：非法类型、非有限数、越界、正公共评分、0 分状态不明。当前没有可靠评分人数/未评分标志：**0 分不能确认为低分或已知未评分**；≥9.5 只标记待核查，不宣称数据错误。
- 没有评分人数，所以没有评分可信度修正。Bangumi 公共评分绝不是用户喜欢的概率。
- 完全相同的中/原名只计重复名称组，不推断系列、续作，不模糊合并作品。规范身份只来自 Bangumi ID。

## V1 基线如何保持一致

线上 `rank_candidates` 与评测共享 `rank_context`、`build_feature_index`。没有维护另一套“近似 V1”。保留所有权重、400 候选池、120 结果上限、三类策略和排序 tie-break 规则；新增冻结用例与重构前捕获结果逐条比较，且对比线上入口和评测入口结果完全一致。

重复运行可比较 `baseline_fingerprint`。指纹包含目录内容/全库分组计数、规则版本、源码 SHA、阈值/上限/查询配置及匿名样本输入指纹；不包含生成时间。私有输入本身不导出，因此后续复现需要保持用户选择、配置、代码和数据库快照一致；用户自然编辑手账后应形成新的基线，不能把前后变化全归因于算法。

## 内部指标，不是匹配百分比

- `topic_overlap_proxy_mean`：Top-K 与训练题材/明确偏好的最大 Jaccard 均值，仅是题材关联代理，不是人工相关性标注。
- `pairwise_genre_jaccard_mean`、`duplicate_genre_set_ratio`、`dominant_genre_share`：重复程度/集中度；不是系列去重。
- `category_differences`：三类 Top-K ID 集合重叠及顺序是否完全相同。任何一类为空时标记不可比较，不误判三类完全相同。
- `evidence_valid_fraction`：独立核验推荐理由的真实来源、个人评分、共有题材及来源字段；不只是检查文案非空。
- 留出 `hit_at_k`、`mrr_at_k`、单一二值标签 `binary_ndcg_at_k`：只用于内部对比，不输出页面。没有完整负标签，不能冒充 Precision 或全量分级 NDCG。

空结果/没有可评测标签使用 `null`，不是伪造的 0 分表现。关联高、题材重复高或三类重叠高不自动证明算法错误，也可能来自口味集中或候选有限。

## 无泄漏留出与局限

至少 `minimum_samples + 1` 部可用高分作品（默认 4 部）才留出：按 Bangumi ID 升序确定有限目标，复制上下文，移除目标的整个标准 ID、高分种子、手账已有排除和已生成解释缓存；重新计算训练向量和解释，不改任何数据库记录。仅 3 部时明确跳过，不降阈值或改走明确偏好来充数。

目标缺少目录有效题材仍保留在分母，并单独标记数据阻碍，不通过丢弃难样本抬高指标。明确偏好作为独立输入保留并明示，因此这是有条件的回顾性留出，不是随机/时序无偏测试，更不能声称线上转化提升。

## 5B 使用方式

先看原始数据/题材规则覆盖，再看候选池与排序的表现。缺年份/封面影响筛选与展示，当前 V1 不使用它们排序，不能把这些缺失当作排序问题。后续算法应使用相同数据、样本和指标与本基线对比；不预设“必须提高多少百分比”，不借审计自动补标签、纠正评分或猜续作关系。
