# V2 当前基线复测与瓶颈定位

本阶段只补齐评测正确性、耗时观测与可重复执行入口；不修改 V1/V2 排序、不迁移、不做 UI 改动、不批准上线，也不放宽既有门槛。

## 实现与修正

- `diagnose_recommendations`：最多两个精确选择的授权账号。PostgreSQL 强制 `REPEATABLE READ, READ ONLY`，检查事务实际状态；拒绝 SQLite、非 V1 默认配置、缺失/停用/歧义账号、公开输出路径和报告覆盖。
- 报告保留显式选择顺序，只有 `sample_001` 等匿名编号；不导出邮箱、私人记录 ID、来源作品标题、个人评分、原始私人标签或长评。`resolved_records` 显式投影字段中不含长评；不调用任何外部 HTTP。
- 当前规则固定，只比较真实 `content.v1` / `content.v2` 函数，默认筛选一致。每次重复交替两臂执行顺序，不改变原服务端配置或算法路由。
- 修正旧离线对照遗漏增强 8 状态的问题：`compare_recommendations` 也读取选定用户的当前反馈快照；两臂及留出均携带相同排除集合。输入 HMAC 纳入反馈版本与精确作品集合，仅在内存中处理原值。
- `QueryCounter` 增加 execute 墙钟计时，不记录 SQL 或参数；`measured_rank` 补充完整排名墙钟、进程 CPU、SQL execute 时间。V2 复用原有五阶段 trace，不另建推荐引擎。
- 样本不足时仍测实际冷启动/明确偏好模式，但留出指标为 `null` / `insufficient_evidence`，不改分、不降门槛、不剔除账号来美化整体结论。
- 留出仅去掉当前来源身份在训练种子/已有记录中的排除；不撤销用户反馈。如果选定留出作品本来已隐藏，明确报告反馈阻碍，不替换成更容易命中的目标。
- 报告绑定公共目录指纹、授权样本输入 HMAC、现有发布代码指纹及新增诊断源码/测试哈希。运行期间代码变化不产生完整报告。
- **诊断永不生成 `approval.json`**，`eligible_for_manual_switch` 固定为 false。不调用旧 SQLite 发布凭据命令，也不重新签发任何上线许可。

## 计时解释

1. 公共目录读取、公共特征索引构建、账号上下文准备分别记录，不混进“排名本身”，也不能把它们当成每个线上请求都必付的成本。
2. 排名墙钟计时包含实际排名中的数据库操作，不扣除慢网或 SQL 时间来通过原 5000 ms 门槛。
3. SQL execute 时间包含驱动、网络往返与服务端执行，并不包含所有 fetch/传输解码；不能称为纯数据库服务器 CPU 时间。
4. V2 的 `public_fetch` / `feature_scoring` / `pool_balance` / `hydrate_and_safety` / `rerank` 来自真实函数，部分阶段本身包含 CPU 与 I/O，不与 SQL 计时相加求总耗时。
5. Windows 进程 CPU 计时存在采样粒度，wall−CPU 不能直接解释为网络耗时。
6. 首次/后续仅表示本诊断进程的执行次序：已先构建内存索引，不冒称生产缓存冷/热测试。少量重复不输出误导性 p95。
7. `--execution-context` 是执行者提供的位置标记，不验证部署拓扑。只有实际在部署进程运行的报告才可作为该环境诊断，不能将本机结果冒称 Render 端到端结果。

## 隔离自动化测试（PostgreSQL）

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts/verify_recommendation_diagnostics.py
# 确认退出码为 0，再执行检查。
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

2026-10-17 完成：**211 项通过，0 失败，0 跳过**，其中 9 项诊断专项、V2 数学/候选池及对照兼容用例，并包含原 8C 的 16 项真实 PostgreSQL 并发测试。Django check 通过，无迁移漂移。报告写入 `.tools/acceptance-v2-diagnostics/`；不以功能场景数冒充代码行覆盖率，也不称为 V2 上线安全凭据。

所有写入/并发测试仅使用独立本机 `test_recommendation_isolated_tests` 中的合成账号。真实授权账号只读诊断与这些测试是两种不同执行入口，不混用数据库。

## 显式授权后的真实只读诊断

**默认 `.env` 指向正式 Neon，只有已明确授权的测试账号才能使用以下入口。** 不在命令中传密码；使用现有后端数据库配置进行数据库强制只读审计，不是绕过站点进行业务写入。

```powershell
.\.venv\Scripts\python.exe manage.py diagnose_recommendations --user-email '<授权账号A精确邮箱>' --user-email '<授权账号B精确邮箱>' --confirm-authorized-samples --execution-context local_client --top-k 10 --max-holdouts 4 --repeats 3 --output-dir .tools/v2-diagnostics/<新的唯一目录>
```

每个目录生成 `diagnostic.json`、`report.md` 和最后写入的 `manifest.json`，不覆盖历史报告。源作品/用户身份仅在进程内使用；JSON 包含匿名摘要、公共候选 ID、指标及分阶段计时。完整真实报告受 Git 忽略，不放入静态资源/CDN，也不提交 Git。

本次已对用户明确选定的两个测试账号完成多轮只读复测。实际结果和结论保存在私有 `.tools/v2-diagnostics/`，未修改这些账号的评分、标签、长评、身份关联或反馈。

## 如何据此决定下一阶段

- 先分开审阅高分画像和明确偏好/冷启动两类结果，不能用大样本账号的改善掩盖小样本账号的题材集中。
- 性能是环境相关观测。若旧慢请求未复现，应写“本次未复现”，不能把差异算作本轮算法优化收益；部署环境仍需独立测量。
- 关联代理、题材重复、探索方向、真实留出分别报告。即使关联代理提升，零命中仍需如实写零，不宣称喜欢概率或准确率提升。
- 后续若修改 V2，只修明确定位的问题并重新版本化，不顺带改 V1、放宽安全过滤或上线默认。正式发布仍需新的完整评测与人工批准。
- 诊断工具停用无需回退数据或迁移。保持 `RECOMMENDATION_ALGORITHM_VERSION=content.v1` 即维持当前服务路径；不要全局清缓存、重置反馈版本或复用旧许可。

后续单偏好修订与目标去向观测见 [V2 单偏好发现修正](recommendations-v2-preference-discovery.md)。工具新增 `--compare-previous-v2` 可在同快照增加本轮前版计分对照；这是私有离线标签，不是新的线上算法参数。
