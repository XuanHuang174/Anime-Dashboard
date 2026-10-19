# V2 保护型召回补全：一次有界实验

## 目的与止损

线上继续使用 `content.v1`，不改变任何页面、HTTP 合同、默认权重、缓存命名空间或业务数据。
本次只回答：保护型 V2 的空余候选名额是否导致可修复的推荐损失？

规则在自然数据排名前固定为 `evaluation/v2-recall-completion.v1.json`。
只做一次该规则的实验，不搜索系数、不看结果换目标。
如果没有通过预先固定的质量条件，停止当前 V2 路线上线推进，保留 V1。
候选池变大、耗时变短、总命中超过 V1，均不能单独证明 V2 可以上线。

## 唯一变量

- 范围：有足够真实高分样本的“为你发现”。
- 保留保护型 V2 的全部原候选，不驱逐任何身份；已满 400 时完全不变。
- 原池未满时，按既有 IDF cosine 题材关联补足空余名额，最多 400。
- 补入作品必须已经通过安全目录、过滤、手账/反馈排除、真实题材及证据检查。
- 不把公共评分当作喜欢概率，不重新混合两套分数，不用标题或目标名单补回作品。
- 作品基础分、证据、重排公式完全不变。候选增加可能改变最终顺序，不声称结果不变。
- “熟悉的心动”“换一种口味”、明确偏好冷启动与空手账路径必须与保护型 V2 完全一致。

## 对照与隐私

对照：原 V1、保护型 V2、仅补全候选的 V2；三类各运行两次。
沿用已经揭示的原 13 个账号、52 个目标及目标顺序，四目标整组留出。
新冻结只更新当前输入，不重新选取有利目标。输入漂移即停止，不悄悄刷新。
这些数据是开发诊断集，**不是独立盲测集，不构成上线证明**。

自然读取在 PostgreSQL `REPEATABLE READ, READ ONLY` 事务内；禁止外部 HTTP。
不读取长评作推断，不写用户评分、手账、标签、明确偏好或反馈。
公开报告仅输出样本序号、目标序号、去向和汇总指标，不输出用户身份、私人题材和个人评分。
详细签名产物及公开目录文件均留在被 Git 忽略的 `.tools` 中。

## 验收条件

必须同时满足：

1. 发现类 Top10 留出命中高于保护型 V2。
2. 每一个 V1 和保护型 V2 原命中目标都保留在 Top10，不能靠总数遮盖个体退步。
3. 平均题材关联代理指标不退步；该指标不是用户满意度或喜欢概率。
4. 平均题材组合重复指标不退步。
5. 安全与真实证据错误为零。

此外完整保留隔离测试、安全复核、同输出稳定性、其他类别不变检查与耗时/SQL 记录。
质量失败直接执行止损。即使上述条件通过，也仍需独立自然评测、真实部署性能、快照和回退验收；不会自动授权 V2 上线。

## 可复现执行

1. 启动既有便携 PostgreSQL：`scripts/setup-recommendation-postgres.ps1`。
2. 将 `RECOMMENDATION_TEST_DATABASE_URL` 指向仅本机的独立测试实例。
3. 运行 `manage.py verify_unused_holdout_validation --output .tools/v2-completion/20261019-tests/tests.json`，仅子进程使用隔离数据库。
4. 正常设置下运行新命令，分别加 `--freeze-only` 和 `--cohort-lock`，必须显式加 `--confirm-sitewide-readonly`：

```text
manage.py compare_recommendation_completion
  --confirm-sitewide-readonly
  --consumed-cohort .tools/v2-unused/20261018-freeze/cohort.lock.json
  --consumed-result .tools/v2-unused/20261018-evaluation/evaluation.json
  --test-report .tools/v2-completion/20261019-tests/tests.json
  --freeze-only
  --output-dir .tools/v2-completion/20261019-freeze
```

第二次把 `--freeze-only` 换为
`--cohort-lock .tools/v2-completion/20261019-freeze/cohort.lock.json`，输出目录换为全新目录。
生产读取进程不能设置测试设置模块或用测试库替代自然账号。
一次性标记在首次排名前排他写入，换输出目录或改源代码也不能使相同输入的同一实验重获一次机会。
文件签名、公共目录实时校验、当前代码测试证明和输入一致性有任何失败即安全停止。
