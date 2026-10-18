# 双视图发现类候选与查询分段诊断

## 定位与预先固定的规则

`dual-view-discovery.v1` 仅为显式调用的离线开发候选。线上默认仍为 `content.v1`，参考 V2、保护型探索与 `aggregate-discovery.v1` 原样保留；HTTP 参数不能启用本候选。它不是发布批准。

本次修正针对上一轮的取舍：来源作品最近邻视图保留了少数兴趣方向，但可能压过整体画像；用整体画像完全替换又丢掉原有命中。因此保留两者，不针对具体账号或目标补救：

1. 来源视图使用既有 V2 分数；整体画像视图复用已有 V1 的 IDF 关联/公共质量/新题材公式，系数不搜索。
2. 两个视图各按既有召回策略生成最多 400 个候选，在内存中等份轮转、按 Bangumi ID 去重，最终只水合最多 400 部。不关联的作品不会进入，也不随机补数。
3. 对**当前安全与证据复核后的集合**重新计算两种视图名次，以等权归一化倒数名次融合；平滑常数固定为现有批次大小 24，不根据目标命中调参。内部融合分不是概率。
4. 继续使用原 V2 的重复惩罚，返回上限 120 不变。仅个性化“为你发现”生效；其他分类与冷启动须与保护型 V2 逐项相同。

这同时改变了发现类候选覆盖与基础排序，不能声称是单变量或保持 V2 排序不变。推荐证据仍来自真实的已有证据服务，不把融合分包装成原因或匹配率。

## 测量规范

五臂：当前 V1、参考 V2、保护型 V2、上一轮整体画像替代候选、双视图候选。固定原来已经消费的 13 账号/52 目标、四目标共同移出训练，覆盖全部三分类，每次无观测/有观测各运行一次并比对顺序与证据摘要。样本漂移则停止，不重新挑选。运行前写入签名计划，沿用 1,200 秒总预算和原有性能门槛，不因网络慢而放宽。

这是已揭晓目标上的开发回归，绝非新的独立验证；即使命中提高也不能直接上线。历史目标集与原始失败报告保留，不重签为新验证集。

## 查询诊断与隐私

`QueryPhases` 只输出固定阶段名称、次数与耗时；不保存或导出 SQL、参数、作品标题、用户标识、私人标签或长评。每次引擎调用统计事务合同检查、公共目录指纹扫描、水合、变化行补读、来源安全复核。

另在只读重复读快照中执行三次 `SELECT 1` 与一次**公共目录**查询的 `EXPLAIN (ANALYZE, FORMAT JSON)`，只输出服务端规划/执行时间和客户端总时间。EXPLAIN 不针对私人手账查询；计划与 SQL 不导出。它独立于引擎性能采样，不能将客户端减去服务端时间称为纯网络延迟，也不能把本机跨网读取当作 Render 首屏时间。

## 执行

先启动项目已有的隔离 PostgreSQL，再设置测试库环境变量（见现有 PostgreSQL 测试文档），运行：

```powershell
.\.venv\Scripts\python.exe manage.py verify_unused_holdout_validation --output .tools/v2-dual/20261018-tests/tests.json
```

自然数据开发复测只读生产数据库（使用正常 `.env` 配置），不带测试 settings：

```powershell
.\.venv\Scripts\python.exe manage.py compare_recommendation_discovery --dual-view --confirm-sitewide-readonly --prior-site-lock .tools/v2d3/20261018-expanded-freeze/cohort.lock.json --prior-history-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json --consumed-cohort .tools/v2-unused/20261018-freeze/cohort.lock.json --consumed-result .tools/v2-unused/20261018-evaluation/evaluation.json --test-report .tools/v2-dual/20261018-tests/tests.json --output-dir .tools/v2-dual/20261018-development
```

输出目录必须全新；`.tools` 内包含私有签名输入，只留本地，不提交。失败不覆盖历史文件，不生成伪造通过凭据。

若原始输入漂移，比较命令写入仅含聚合漂移计数的 `stopped.json` 并停止，不自动重冻或换目标。仍可独立诊断性能：

```powershell
.\.venv\Scripts\python.exe manage.py profile_recommendation_discovery --confirm-sitewide-readonly --prior-site-lock .tools/v2d3/20261018-expanded-freeze/cohort.lock.json --output .tools/v2-dual/20261018-profile.json
```

该命令预先规定只读取原锁定账号中主键最小的仍有效账号，使用当前画像，不选择任何留出目标，不生成质量评测指标。五臂各运行两次，仅保存耗时与阶段次数；不能将这一小样本或当前数据上的时长与过去不同网络条件混合计算优化收益。

## 验收与回退

安全、证据、用户隔离、零副作用、其他分类/冷启动严格一致是硬门槛。质量同时报告总命中、逐账号得失、题材重复度与类别差异；不能只报告总命中掩盖退步。仍需独立验证及部署环境性能/游标/回退验收才能讨论上线。

候选不接生产，无需更改线上开关即可停止实验。不要删除用户手账、偏好、反馈、缓存或历史目标锁；本阶段不应用迁移。
