# V2-B：相关性约束召回覆盖优化

范围：只优化离线候选召回，不改变线上默认 V1，不改前端、评分公式、重排强度、题材规则、400候选/120返回/Top10评测上限。既有 `rank_context(..., algorithm_version='content.v2')` 继续使用冻结的原V2候选池。

## 单变量设计

新增候选策略 `source-pattern-recall.v1`，与 `balanced-rerank.v3` 计分/重排策略分开命名。

| 配额 | 来源 | 约束 |
| --- | --- | --- |
| 前50% | 冻结基础相关性排序头部 | 保留原始分数、证据与确定性并列顺序 |
| 最多25% | 每个真实高分样本题材组合、用户明确偏好组合 | 题材组合去重后轮转；不是作品/系列去重，不用留出目标ID |
| 剩余 | 实际共有偏好题材与新增题材方向 | 按组合轮转；同作品只能入池一次 |

保留区候选需达到该类别最佳基础分的75%；来源路径还需达到该来源路径最佳IDF加权题材交集比的75%。这是内部相关性门槛，不是“喜欢概率”。不足时不以无关联或低于门槛的作品补满。小于400的合格集合直接保留，相关性头部不受保留区门槛二次裁剪。

只读取已经通过安全、公共条件、当前用户反馈/手账排除及真实证据生成的合格候选。标题、简介、长评不参与召回；不猜题材、续作或系列。来源集合只用于召回调度，最终推荐理由仍由冻结的 `evidence_for` 选择，不能把召回路径误称为唯一解释。

策略在真实复测前固定于 `evaluation/v2b-recall-policy.v1.json`，命令严格核对其与实现一致，不接受命令行配额/门槛调参。测试用的随机打乱仅检查输入顺序不影响结果；生产召回没有随机抽样。

## 实现边界

- `recall_v2.py`：纯内存、无数据库、无共享缓存，不修改输入行的分数、标签与证据。
- `engine.rank_recall_candidate`：仅可信Python离线入口，复用现有V2完整安全/评分/水合/重排路径。普通HTTP没有召回策略参数，也没有新部署开关。
- `_rank_context_v2` 唯一决策增量是显式指定离线策略后的候选选择分支；默认else仍是原候选池。V2-A冻结测试剥离该显式分支及观测钩子后，函数AST与原基线一致；整个 `ranking_v2.py` 完全不变。
- 无候选V2私有结果缓存/游标/上线令牌。报告和发布源代码指纹包含新策略号/源码/配置；不能用旧V2上线凭据批准新代码。增强9/V2兼容不是本阶段交付。

## 同条件消融与只读保护

复用V2-A管理命令的精确授权账号校验、PostgreSQL `READ ONLY + REPEATABLE READ` 事务、目录审计、签名目标锁、报告完成清单与无外部HTTP保护。B命令不允许重新冻结目标。

三臂同快照：V1 / 原V2 / V2新召回；历史4、扩展最多12、相同Top10、相同筛选、相同留出训练删除规则。少样本账号正常评估明确偏好/冷启动，但留出指标为 `insufficient_evidence/null`。

每次运行仍比较控制/观测两条真实路径，确保观测不改变完整排序、分数、证据、SQL次数和展示解释；新旧V2的**完整合格候选ID、原始浮点分数、题材、证据、理由**另用同域HMAC核对，证明只改变了“如何从合格集合取400池”。池内与返回数继续检查硬上限。

报告并列给出去向分类、条件候选池覆盖、原分母Hit/MRR/NDCG、题材关联代理、组合重复度、新方向数量、墙钟/CPU/SQL。合格池外减少不等于Top10收益；不因结果不好换目标、放大K或事后改配置。

报告另列 `quality_review`：相关性代理/扩展集条件池覆盖/留出指标任一退步标记 `not_accepted_regression`；均未退步但没有Top10增益标记 `no_proven_top10_gain`；无足够留出为 `insufficient_holdout_evidence`。即使观察到增益也仅为 `requires_independent_validation`，不签发上线许可，不把两账号结果冒充统计显著性。命令退出成功只表示审计完整。

## 执行

### 隔离PostgreSQL

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.\.venv\Scripts\python.exe scripts/verify_recommendation_v2b.py
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

脚本拒绝远程/生产测试库及SQLite；写入、分级变化、并发锁测试只作用于本机合成数据。

### 已授权真实账号（只读）

```powershell
.\.venv\Scripts\python.exe manage.py compare_recommendation_recall --user-email '<原授权账号A>' --user-email '<原授权账号B>' --confirm-authorized-samples --cohort-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json --output-dir .tools/v2b-recall/<新的运行目录>
```

要求原账号顺序与锁一致。`.env` 仍可能指向正式Neon；命令数据库强制只读，不读取长评、不改评分或任何记录。锁/目录/画像/反馈/偏好变化即停止，不自动重新建集。无需登录密码。

产物为 `.tools` 私有目录下的 `recall-audit.json`、`target-events.jsonl`、`report.md`、`manifest.json`。匿名报告不导出账号、目标ID、来源标题、私人评分/标签，禁止提交真实产物。拒绝覆盖现有目录。不会生成 `approval.json`。

## 回退

停止离线命令即可；线上一直保持 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`。无需迁移、清理用户记录或切V2，不清空反馈/偏好/手账。候选未进入在线浏览缓存，无需清空在线V1缓存。

工程测试通过与候选覆盖改善都不代表V2获准上线；性能优化、正式重评、增强9兼容和最终批准仍须后续独立验收。
