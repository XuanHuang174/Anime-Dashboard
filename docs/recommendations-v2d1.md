# V2-D1：统一执行合同、三档控制与实时前置筛选

D1仅交付准备服务与隔离/只读验收。线上仍为V1，`browsing.capabilities()`和现有V1浏览路径不变。D2的游标、详情证据版本绑定与前端恢复未接入，D3的部署服务端验收未执行；不能只修改环境变量就声称V2浏览可用。

## 完整执行配方

`execution.recipe_descriptor()` 将以下内容组合并生成确定性 `recipe_revision`：

| 层次 | 版本 |
| --- | --- |
| 执行合同 | recommendations.execution.v1 |
| 算法 | content.v2 |
| 数学计分/重排策略 | balanced-rerank.v3 |
| 修正召回 | source-pattern-recall.v2 |
| 执行运行时 | pattern-prefix-runtime.v1 |
| 浏览控制 | browse-controls.v2 |

同时记录控制配置SHA256、400候选/120结果、24批次、12默认页大小和 `serving_enabled=false`。新增序列化类只描述准备合同，未添加到现有HTTP响应。现有原V2、召回v1消融与V2-C入口均保留。

`execution.execute_v2(user, options, index, context=...)` 只接受当前用户的准备上下文；匿名/外来owner被拒绝。没有外部用户、算法、运行时或游标参数。外部HTTP继续走原严格参数校验。服务自行建立PostgreSQL READ ONLY REPEATABLE READ；若调用者已有事务，则验证其只读/隔离属性，不接受可写事务。

## 先固定控制，再评测

`evaluation/v2d-controls.v1.json` 在自然数据运行前固定，不根据命中结果调参。

- 均衡：零额外基础分运算，调用已验收的修正召回与V2-C运行时。完整排序/浮点基础分/证据/展示令牌及管道成员须与V2-C一致。
- 贴近口味：基础分加 `0.03 × V2原IDF余弦关联信号`（内部单位，不是概率）；各分类重复惩罚乘0.70。仍要求真实关联、有效证据及类别资格。
- 开放一些：基础分不改，各分类原重复惩罚乘1.15；不放宽关联或探索新题材门槛，不引入无关联随机作品。
- 非默认档明确是版本化控制排序调整，不声称排序完全不变；不使用V1 `candidate_score` 公式。

运行时新增受控的内部重复惩罚入参，仅接收有限且有效的数值；其默认行为和原C合同完全不变。三类推荐各自原强度仍不同，风格不能将熟悉类变成探索类。

## 实时前置与防补读绕过

不能仅调用V1 `filter_index`：V2会补读索引缺失/变化作品，只裁缓存会让被筛除的作品重新出现。

D1在**同一次实时安全目录扫描**中读取当前 `season/status_type/score`，按既有 `season_year` 精确解析，并在进入合格集合/400池前执行条件。允许正常补读新/变更作品，但同时校验当前条件：

1. 当前公共类型/评分及安全条件；
2. 当前放送字段解析的年份，不从标题/简介猜；
3. 反馈、种子及手账排除；
4. 有效题材、真实关联及原类别资格；
5. 变更补读和最终水合再检查当前年份/类型/评分。

`2026-1`、`2026-01`、可靠ISO日期兼容；缺失、非法月份/日期不符合指定区间，不限年份时仍可参与。IDF继续来自完整安全目录，绝不重写原字段。年份之外的画像来源作品仍可提供真实依据，过滤只约束候选，不改变用户喜好历史。

## 固定集只读验收

沿用V2-A原签名锁，历史4/扩展12/Top10不变，三臂为V1参考、已验收V2-C、D1均衡。逐项对照完整结果与展示令牌；D1比对照额外2次 `SHOW` 验证事务，明确记录而不隐瞒SQL成本。

另预先固定9个实际控制探针：close/open各三类别，以及 `TV + 公共评分≥7 + 2020—2026` 的三类别。新探针真正执行年份；旧独立引擎年份能力仍在旧报告字段中标记不支持，不能混淆。

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.\.venv\Scripts\python.exe scripts/verify_recommendation_v2d1.py
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

自然账号只读（顺序与原锁一致）：

```powershell
.\.venv\Scripts\python.exe manage.py audit_recommendation_execution --user-email '<原授权账号A>' --user-email '<原授权账号B>' --confirm-authorized-samples --cohort-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json --output-dir .tools/v2d1/<全新目录>
```

禁止重建目标集、覆盖已有目录、改分造样本或调用外部模型；原目录/画像变更即停止。输出到忽略的 `.tools`，不签发上线许可。

## 新增全站只读授权的最小使用

用户已明确扩大后续只读测试授权，现阶段先检查可用样本规模，不开展全站排名或导出用户明细：

```powershell
.\.venv\Scripts\python.exe manage.py audit_recommendation_population --confirm-sitewide-readonly --max-accounts 200 --output-dir .tools/population-readiness/<全新目录>
```

数据库强制只读一致快照。只读活跃账号主键和既有安全画像所需字段，不取邮箱、用户名、密码、长评/私人简介；使用原身份/私人标签清洗规则，不猜身份。不调用外部HTTP，不生成推荐结果。

仅输出活跃账号数、拥有手账账号数、画像/明确偏好/冷启动账号数及自然满足留出门槛的账号数，不输出个人样本数、来源作品、评分、题材或逐账号标识。账号上限和120秒总预算超出则停止，不静默截取一个有利子集，不生成部分成功报告。

这只是授权范围内的匿名可用性盘点；**不会替换原冻结集或凭结果挑选容易命中的账号**。后续扩大评测须先独立固定新群体选取规则与目标集，保留原历史回归，并继续严格零写入。此命令不把全站私人数据送往任何外部服务。

## 后续与回退

D1没有HTTP V2开关、快照或业务表。停止命令即可；线上维持V1，无需删缓存、清用户数据或回退迁移。D2还需将完整配方绑定列表/详情/游标及切换恢复，D3才进行隔离部署和生产只读验收；当前不能上线V2。
