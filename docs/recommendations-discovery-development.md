# 发现类整体口味评分修正：离线开发协议

日期：2026-10-18。线上默认仍为 `content.v1`，不批准或启用 V2。

## 问题与单变量假设

[上一轮补充验证](recommendations-unused-holdout-report.md) 中，一个 V1 排名第 9 的目标进入 V2 候选池、通过安全与证据复核，却没有进入返回前 120。该目标集已经揭晓，只能用于开发诊断。

目前 V2 发现类基础分中，最近来源题材集合占 0.50，证据来源题材关联占 0.20；整体画像 IDF 向量关联占 0.15，公共评分占 0.10，新题材占 0.05。它可能偏向某一部来源作品的完整题材集合，而不够重视用户整体口味。最近题材集合不是续作/系列判断，更不是用户喜欢的概率。

本轮**只测试一个评分层修正**：个性化发现类改用已有 V1 的整体口味公式 `0.85 × IDF 余弦关联 + 0.10 × 公共评分归一项 + 0.05 × 新题材项`。系数直接复用现有 V1，不根据目标表现拟合。保留 V2 的前置筛选、源题材召回、400 上限、差异化重排和 120 返回上限不变。这里并不声称候选结果与 V1 相同，也不声称该公式一定能解决重排阶段损失。

候选版本 `aggregate-discovery.v1` 在独立配置 `evaluation/v2-discovery.v1.json` 声明；完整候选配方另带其版本和摘要。参考 V2、现有保护型探索 V2 都可原样执行。新候选不能通过 HTTP、线上配置或 `serving_enabled=True` 上线。

## 范围与不变量

- 只有 `personalized + all` 采用新基础分；明确偏好冷启动、空手账、熟悉类与探索类保持现有保护型 V2 行为。
- “贴近口味”的原有明确加成只加一次；“均衡/开放一些”沿用原有重排强度。
- 不修改来源作品选择、个人评分、共有题材、推荐理由或原始题材。
- 不依据账号、目标 ID、标题、长评、系列猜测或评测结果调分；Bangumi ID 只按既有规则去重/打破平分。
- 保持数据库只读一致快照、用户上下文绑定、反馈/手账排除、成人与未知分级硬过滤。
- 不新增前端入口、业务表、迁移、共享用户缓存或线上开关。

## 开发对照与证据边界

使用上一轮**已消耗**的冻结锁和完整签名结果，校验原始签名、目标集、全部活跃账号输入、目录版本与历史档案。代码版本变化是本次开发实验的预期；旧源码凭据不作为新代码测试凭据使用。不重选账号或目标，任何数据漂移都停止。

四臂：V1、参考 V2、上一版保护型 V2、本次发现类候选。每账号四个目标联合移出训练和手账排除集合，两次重复、三类推荐保持一致。

排名前写入绑定当前源码、测试凭据和配方的签名开发计划；保存结果时强制 `independent_validation=false`、`approval_issued=false`。重复运行只算开发复测，绝不恢复目标的未使用资格。原始一次性验证命令仍拒绝重跑该目标集。

记录各阶段目标序号位置、返回前十留出、题材关联/重复代理、前 24 基础得分行的实际题材组合数量/完整来源集合数量、SQL 与耗时。只导出匿名位置和计数，不导出账号、目标 ID、私人标签或长评。留出相关性门槛不放宽，零命中不是成功。

## 执行

先在本机独立 PostgreSQL 运行完整回归与锁测试并签发本次源码的测试凭据。不要在生产 Neon 中运行测试或迁移。

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='<仅本机独立 PostgreSQL 连接>'
.\.venv\Scripts\python.exe manage.py verify_unused_holdout_validation `
  --output .tools/v2-discovery/新的测试目录/tests.json

# 以下回到正常应用环境，通过数据库 READ ONLY / REPEATABLE READ 只读真实输入。
.\.venv\Scripts\python.exe manage.py compare_recommendation_discovery `
  --confirm-sitewide-readonly `
  --prior-site-lock .tools/v2d3/20261018-expanded-freeze/cohort.lock.json `
  --prior-history-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json `
  --consumed-cohort .tools/v2-unused/20261018-freeze/cohort.lock.json `
  --consumed-result .tools/v2-unused/20261018-evaluation/evaluation.json `
  --test-report .tools/v2-discovery/新的测试目录/tests.json `
  --output-dir .tools/v2-discovery/新的开发结果目录

.\scripts\setup-recommendation-postgres.ps1 -Stop
```

结果只保留在忽略的 `.tools`。归档输入锁失效、数据漂移、测试未通过、超出时间/账号预算或输出目录已存在时停止，不能静默重签/换样/覆盖。

## 开发集与未来验证隔离

1. 之前固定历史集及本次已揭晓 13 账号/52 目标都是开发/回归数据，不再作为“独立验证”证明泛化。
2. 开发候选应先固定配方与代码，再预注册新的验证选择协议。新协议必须排除所有已用目标对，包括 `.tools/v2-unused/20261018-freeze/cohort.lock.json`，不能仅改文件名或换一小部分目标。
3. 优先用后续自然新增的真实评分做前瞻留出；若使用未用目标对，必须明确它仍是回顾性同用户验证，不宣称全新用户盲测。数据不足则等待，不能制造私人评分。
4. 候选冻结后不能基于验证结果再调参并继续称该批目标为独立验证。失败只回到开发阶段。
5. 即使开发指标全部通过，也仍需新的验证、部署性能、服务游标和回滚凭据，不能直接发布。
