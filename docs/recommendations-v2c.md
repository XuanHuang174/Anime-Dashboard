# V2-C：等价运行时与瓶颈定向优化

前提：探索召回退步已定位为旧保留区相对分数门槛，并以 `source-pattern-recall.v2` 恢复整个冻结原V2探索池。原V1、原V2和线上默认配置不切换。Top10收益仍未证明，不把工程优化当作推荐质量改善。

## 当前瓶颈与不做的事情

上轮计时中，实时公共指纹读取占数百毫秒，水合/安全读取也有明显跨网成本；重排则重复计算相同题材对的重叠与相同题材组合的累计状态。本阶段针对后者做数值等价优化，并持续分开记录前两者。

不跳过实时安全查询，不用可能漏掉 `QuerySet.update()` 的 `updated_at` 代替精确SHA256，不做跨用户画像/结果缓存，不引入后台预热/新业务表，不改评分权重、重复惩罚、400池/120返回/Top10、相关性门槛或线上V1。增强9/V2游标与部署服务端验收仍属后续兼容工作。

## 执行优化而非改变算法

`rerank_runtime.py` 的 `pattern-prefix-runtime.v1`：

1. 相同有效题材组合的候选，在同一个已选前缀下有相同的题材重复累计值。按组合只更新一次，替代每个作品的重复副本。
2. 题材组合对的IDF重叠值按冻结的 `weighted_overlap` 原始排序/求和次序计算；单次调用最多保留8192对，用完即释放。超限继续原计算，不截断候选。
3. **仍对每部作品的原始浮点基础分和ID逐项选择赢家**，不把同题材作品合并为一条，也不只看某组基础分最高的作品。特设浮点相减舍入并列用例，保证原本的低ID并列规则不变。
4. 每个题材组合的累计加法按原赢家次序执行，不重排浮点加法，不改个人评分、证据及解释来源。

`ranking_v2.py` 完全保留。新运行时仅由 `engine.rank_recall_candidate(..., runtime_version=...)` 可信Python离线入口选择，普通HTTP和原V1/V2路径不会触发。运行时版本与算法/召回版本分开；没有新私人快照或线上缓存，因此不能混用线上游标。

## 固定对照

- 原V2-A签名锁、历史4/扩展最多12目标、同筛选、同类别、同样本。两个授权账号只读；少样本继续明确偏好/冷启动验证，留出不足为null。
- 三臂：V1参考 / 修正召回v2+冻结重排 / 相同修正召回v2+等价运行时。
- 比较完整合格集合、400池、水合复核、完整返回排序/原始分数/证据、展示令牌、目标去向和SQL次数，任一不等即停止完整报告。
- 每次排名都保留无观测/有观测一致性验证。排名wall、CPU、SQL执行、各阶段分别输出；HMAC/展示复核不伪装为纯算法CPU。
- 相同已水合池上另做5次交替内核对照，严格同池/同评分/同证据，消除上轮“缩池导致看起来变快”的混淆。内核不访问数据库，其时间不包含在排名阶段时间中。
- 缓存指标仅为单次题材对缓存命中/未命中及精确公共指纹复用数，不冒充生产Redis命中率或浏览器首屏命中率。
- 原5000ms/6次SQL的本机预算原值记录，不能通过扣除跨网读取放宽；本机内核优化不能替代Render服务端正式验收。重复计算不是新增自然样本，不报告伪精确p95。

## 运行

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.\.venv\Scripts\python.exe scripts/verify_recommendation_v2c.py
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

测试只允许独立本机PostgreSQL，不用SQLite/生产Neon，写入/分级变化仅合成数据。只读自然账号对照：

```powershell
.\.venv\Scripts\python.exe manage.py compare_recommendation_runtime --user-email '<原授权账号A>' --user-email '<原授权账号B>' --confirm-authorized-samples --cohort-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json --output-dir .tools/v2c-runtime/<全新目录>
```

复用数据库强制 `READ ONLY + REPEATABLE READ`、明确账号校验、只读题材/画像、禁外部HTTP及输出目录/签名/发布指纹保护。不扫描全站手账，不读长评、不改真实评分或画像。输出JSON、Markdown、匿名目标事件、SHA256完成清单，全部在忽略的 `.tools` 中，不签发上线许可。

## 回退与后续

停止离线命令即可回退，无迁移、无用户数据清理、无线上V1缓存清空。原V2计算及新旧召回均可调用对照；不要设置线上V2来“验证速度”。

性能与原输出等价后，仍需重评质量、增强9兼容及部署环境只读验收，再由用户批准是否上线V2；不能因为优化了内核就越过这些步骤。
