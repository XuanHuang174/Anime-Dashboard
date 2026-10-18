# V2-D3 探索退步诊断与离线修正

本轮只优化离线候选方案，不启用 V2、不替换服务端审批配方、不修改线上 V1。结果见 [本轮报告](recommendations-v2d3-exploration-report.md)；此前失败基线保留在 [D3 报告](recommendations-v2d3-report.md)。

## 诊断与两个候选版本

固定退步目标在 V1 探索类排第 8；旧 V2 的合格集合中基础分排第 387，未被分层候选池收录。不是安全、水合或证据失败。

旧 V2 探索基础分中，最近来源作品题材相似度权重 0.45、整体画像关联 0.30、新题材 0.15、公共质量 0.10。目标虽然有真实关联和新题材，却在最近来源模板主导的基础分中靠后。

本轮不试权重网格、不添加目标/账号特例，采用已有 V1 探索权重作为预先固定对照：整体题材关联 0.50、新题材 0.40、公共质量 0.10。公共质量仍只是公共评分，不是喜欢概率，不声称评分可信度修正。

- `association-novelty-explore.v1`：仅修改已形成画像的探索类基础分；保留 V2 原候选平衡及重复惩罚。第一轮恢复了退步命中，但造成历史候选覆盖退步，**保留为未通过实验，不覆盖记录**。
- `association-novelty-explore.v2`：评分权重不再调整；对同一批安全、关联、证据有效作品，分别计算旧 V2 来源视角和新探索视角的候选提案。每视角分相关性核心与题材组合保留区，形成四个等额通道；总额 400 时各先取 100 个提案。按 Bangumi ID 去重，剩余名额轮流补齐，总池不超过 400；水合仍最多 400，返回最多 120。最终排序仅使用新探索分与原 V2 重复惩罚，不混入旧分数。

这不是保留旧池全部作品的保证，不扩大为 800 个水合对象。题材组合只是取样层，绝不当作系列关系或合并作品身份。最终是否保留历史覆盖、前 120 和前十表现，必须分别检查。

两个版本均满足：

- “为你发现／熟悉的心动”、仅明确偏好的冷启动路径，继续执行原 V2；不是把所有类别换成 V1。
- 风格 close 的相关性加权和 open 的重复惩罚仍沿用现有版本，默认控制未被悄悄修改。
- 安全、筛选、手账和反馈排除、真实证据检查不变；没有来源或关联不补作品。
- `execute_v2(..., serving_enabled=True, exploration_policy_version=...)` 明确拒绝。离线候选有独立配方摘要，现有服务审批只接受原完整配方，不能借用旧审批上线候选。

## 固定集复测

真实账号只读；写入和并发只在本机独立 PostgreSQL。原历史 4／扩展 12 目标和全站 D3 17 账号／68 目标都不重新挑选。

```powershell
# 全站冻结输入复测，当前命令固定使用双路 v2 候选。
.\.venv\Scripts\python.exe manage.py compare_recommendation_exploration `
  --confirm-sitewide-readonly `
  --cohort-lock .tools/v2d3/20261018-expanded-freeze/cohort.lock.json `
  --output-dir .tools/v2d3-exploration/新的唯一目录

# 原两个授权账号、原历史锁：邮箱参数由操作者明确指定，不扫描其他账号。
.\.venv\Scripts\python.exe manage.py audit_recommendation_exploration_history `
  --user-email <已授权账号一> --user-email <已授权账号二> `
  --confirm-authorized-samples `
  --cohort-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json `
  --output-dir .tools/v2d3-exploration/新的历史目录
```

数据库事务强制 READ ONLY / REPEATABLE READ。代码变更、固定输入/目录/账号集合漂移或预算超限均停止，不输出部分成功；不覆盖既有目录。全站命令对全部 68 个探索目标记录五阶段匿名计数和位置，不只记录已知退步目标。

D3 冻结锁来自旧代码，导入函数只允许验证其签名、有效期、原配方和原评测配置，再逐项比较实时输入。报告保留锁摘要及原发布指纹。这**仅是不可变实验输入的导入**，没有放宽测试、性能和上线审批凭据的当前代码校验；过期锁不能静默重新冻结或替换目标。

完整输出在忽略的 `.tools`，不提交账号、作品清单、评分、长评或标签明细。单路候选函数 `execute_candidate` 与双路 `execute_dual_candidate` 均保留，可在明确只读实验中复用；不提供 HTTP 版本参数。

## 隔离回归与回退边界

```powershell
.\scripts\setup-recommendation-postgres.ps1
# 显式设为脚本建立的本机 recommendation_isolated_tests 连接，不能使用 DATABASE_URL。
$env:RECOMMENDATION_TEST_DATABASE_URL='<本机独立 PostgreSQL 连接>'
.\.venv\Scripts\python.exe scripts/verify_recommendation_v2d3.py
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

固定回归包纳入探索合同测试，检查配额、确定性、两路去重与原分数保留、用户隔离、安全、筛选、旧类别与冷启动等价、四目标留出、只读及线上拒绝。并发包仍包含反馈 6／想看 4／边界 6 项。

本轮没有新的线上路径需要切换或回退。保持 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`、`RECOMMENDATION_V2_BROWSE_ENABLED=False`。不清空认证/全站缓存、不改评分/标签/反馈、不生成生产审批。未来若晋升候选，必须重新设计完整配方和前后端合同，重跑六门槛、快照/详情和部署性能验收；离线实验报告不是上线许可。
