# V2-A：冻结基线与召回路径诊断

本阶段只增加离线审计，不优化候选选择或重排，不改变线上 V1、增强 9 控制、不增加业务表/迁移、不签发 V2 上线许可。前端没有改动。

## 代码入口

| 文件 | 职责 |
| --- | --- |
| `evaluation/v2a-recall.v1.json` | 预先确定的评测规则、历史4目标、扩展12目标、Top10、重复次数、类别与筛选探针 |
| `evaluation/v2a-engine-baseline.v1.json` | 修改前提交的排序函数/策略 AST SHA256 基准，不含任何用户数据 |
| `recommendations/pipeline_trace.py` | 只记录阶段数量和一个目标的序位，不接触 DB 或决策 |
| `recommendations/engine.py` | 显式 Python 离线 observer 钩子；普通请求默认为 None，不实例化观测器 |
| `recommendations/recall_diagnostics.py` | 固定集、逻辑门槛普查、真实引擎双臂校验、去向分类、报告 |
| `recommendations/management/commands/audit_recommendation_recall.py` | 两步命令、精确授权账号、数据库强制只读、私有产物与完成清单 |
| `scripts/verify_recommendation_v2a.py` | 独立本机 PostgreSQL 专项及既有 V1/V2/9C 回归 |

复用现有画像提取、身份解析、反馈快照、标签清洗、版本指纹、EvidencePresenter、独立证据检查、公共目录审计与 read-only 事务。没有第二套推荐计分或从标题/简介推断题材的逻辑；标题仅沿用原清洗规则排除“作品名称本身被当作题材”的碰撞。

## 路径与分类

```text
当前安全目录
→ 公共类型/评分筛选（年份能力单独说明）
→ 手账、训练种子及反馈排除
→ 有效题材/真实关联
→ 引擎实际合格集合
→ 400候选池
→ 当前水合/安全过滤
→ 当前证据、关联及分类复核
→ 返回前120
→ 展示证据安全复核
→ Top10评测切片
```

早期门槛是同一数据库快照上的**逻辑普查**；后半段是现有 V1/V2 实际执行步骤的观测。V1 原实现先做排除再做部分公共条件检查，本阶段没有为了图示顺序重排它的代码；不能把逻辑普查顺序误称为每条 SQL/分支的物理执行顺序。

每条目标事件包含匿名样本序号、目标序号、是否历史集成员、算法/类别、各阶段数量/目标位置和最终去向。不导出真实账号、目标 ID、来源作品标题、个人评分、私人标签或长评。

| 去向组 | 典型代码 | 含义 |
| --- | --- | --- |
| 安全/条件/排除/分类 | `adult_filtered`、`unknown_content_rating_filtered`、`public_filter_rejected`、`feedback_excluded`、`recorded_excluded`、`explore_requires_new_topic` | 不合格不等于召回失败；不撤销反馈、不猜系列 |
| 缺少题材/关联 | `no_effective_genres`、`no_real_preference_association` | 不补猜标签，不编造证据 |
| 未召回 | `outside_400_pool` | 合格但没进入400池 |
| 排序/展示截断 | `outside_returned_120`、`returned_below_top_10` | 入池后没有进入前120，或已返回但不在前10 |
| 水合/证据复核 | `current_hydration_or_filter_rejected`、`current_evidence_association_or_category_rejected` | 实际看到目标在池中但后续消失；不猜未观测到的具体原因 |
| 命中 | `hit_top_10` | 真实进入前10，不是喜欢概率 |

未知阶段用 `observed=false/count=null`，不能冒充已观察的0。来源安全/反馈版本冲突或证据真实性失败时，审计直接停止且不生成完整成功清单；不将失败请求转换为空结果。Top10是离线指标切片，**不是浏览器曝光记录**，生产页面仍是原有12条/页。

### 年份的真实边界

生产 V1 的增强9浏览适配支持年份，底层独立 `rank_context` 历史 V1/V2 接口没有年份参数。本轮同条件基线使用“不限年份”。年份探针：

- 调用已有 `browse_controls.filter_index` 产生公共资格预览，兼容补零月份，缺失/非法年份不算符合区间。
- 明确标记 `eligibility_preview_only/not_used_for_engine_ranking=true`；不会把预览输入 V2 来改变它的候选。
- 实际旧引擎排名标记 `unsupported_in_engine_entry`、`ranking_performed=false`；V2 增强9兼容仍属于后续 V2-D。

类型/公共评分探针则真正调用现有两版引擎，观察其实际前置筛选，不做截取后的页面过滤。

## 固定集：先冻结，再评测

两个步骤分开执行，冻结时测试强制证明排名函数未被调用。

1. 历史集：保留有效种子中按 Bangumi ID 升序的前4部，与旧工具默认完全相同，不避开隐藏/难命中目标。
2. 扩展集：保留历史4部，从剩余 ID 有序集合等间距选取，合计最多12部。若有 `m` 个剩余目标、还需 `k>1` 部，位置为 `floor(j*(m-1)/(k-1))`；`k=1` 取剩余首部。规则不读取命中/排名、作品名称或评分高低来挑目标。
3. 需要自然拥有至少 `max(4, 最低画像样本+1)` 部有效高分样本。少样本不造分、不降门槛，留出返回 null/`insufficient_evidence`，仍评估其实际冷启动或明确偏好基线。
4. 留出仅在内存中去掉目标训练种子和已有记录排除，保留用户明确偏好与反馈。反馈阻碍目标保留日志，不替换；历史配对指标沿用旧工具跳过反馈阻碍目标的口径，明确报告选中/配对/阻碍数量。
5. 条件候选池覆盖仅作为补充诊断，另有类别资格分母；原 Hit@10/MRR/NDCG 仍保留全部非反馈阻碍配对目标，不能用条件指标代替原指标美化收益。

冻结锁只存应用 HMAC 绑定的账号输入/目标句柄及目录/配置摘要，不存账号或原始目标 ID。签名不是 V2 上线许可。目标句柄按身份稳定，不因排序算法变化而选择新目标；未来优化可在数据和配置一致时继续使用同一锁，报告同时保留冻结时与执行时的代码指纹。

配置、目录、用户画像/偏好/反馈或账号顺序发生变化时拒绝重放，不自动冻结一个更容易命中的新集合。需要更新真实数据时，应明确建立新一轮基线并保留旧产物，不覆盖历史报告。

## 零侵入的验证

- 排序函数剥离新增观测节点后，AST 与 `780b3a63d0d9e7f03e1bc635b7c08cf297e67b23` 对应原函数一致；整个 `ranking_v2.py` 策略 AST 未变。未改变 V1 算式、V2策略号、400/120上限或任何权重。
- 每次真实基线、筛选探针和留出，运行无观测控制臂及有观测臂，对完整有序结果的身份、原始浮点分数、题材、证据、理由逐项生成同域 HMAC 比较；不只比较 Top10。
- 两臂排名 SQL 数量必须相等；EvidencePresenter 的最终展示证据/令牌也相等。故意注入分数/理由/顺序漂移的隔离测试必须拒绝。
- 观测与原始运行都是真实函数，不是近似模拟算法。计时分开记录；展示复核另外计时，控制/观测顺序并非随机试验，因此不能将观测臂快一点解释为优化收益。
- HTTP 无法指定 observer、目标、冻结锁或算法版本；普通 API 无诊断字段。源码/配置/测试进入发布指纹，旧 V2 凭据不能批准当前新代码。

## 执行步骤

### 1. 独立 PostgreSQL 回归

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.\.venv\Scripts\python.exe scripts/verify_recommendation_v2a.py
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
# 测试结束后正常停止，不批量删除文件或目录。
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

脚本拒绝远程库、生产 Neon 和 SQLite。写入/并发测试只用独立本机 PostgreSQL 的合成账号。可选 coverage.py：设置 `COVERAGE_FILE=.tools/.coverage-v2a` 后以 `python -m coverage run --branch --source=recommendations scripts/verify_recommendation_v2a.py` 执行。

### 2. 精确授权账号的只读冻结

默认 `.env` 连接正式 Neon，只有已明确授权的账号才能执行。无需账号密码；不是绕过授权进行修改，事务由 PostgreSQL 强制 READ ONLY。

```powershell
.\.venv\Scripts\python.exe manage.py audit_recommendation_recall --user-email '<授权账号A>' --user-email '<授权账号B>' --confirm-authorized-samples --freeze-only --output-dir .tools/v2a-recall/<新的冻结目录>
```

这里只读精确选中的1–2个账号，不提供全站手账发现/扫描选项。`cohort.lock.json`、`freeze.md`、`manifest.json` 写入新的私有目录，冻结不运行排名。

### 3. 使用冻结锁审计

```powershell
.\.venv\Scripts\python.exe manage.py audit_recommendation_recall --user-email '<授权账号A>' --user-email '<授权账号B>' --confirm-authorized-samples --cohort-lock .tools/v2a-recall/<冻结目录>/cohort.lock.json --output-dir .tools/v2a-recall/<新的审计目录>
```

输出 `recall-audit.json`、`report.md`、`target-events.jsonl`，最后写 `manifest.json` 完成标志及各文件 SHA256。已有目录、目录外输出、锁签名/输入不匹配、运行中代码/配置变化均拒绝；绝不覆盖，不生成 `approval.json`。

`--execution-context` 仅标注本机/部署服务/隔离测试，不能验证执行拓扑；本机计时不是 Render HTTP 或浏览器首屏延迟。评测门槛原值随报告记录，不在本阶段变更。

## 回退与后续

停止运行命令即可停止诊断，不需要回滚数据库、清缓存或切换引擎。保留 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`。本阶段没有改变线上开启 V2 的条件；通过工程测试不等于 V2 推荐质量已达标。

真实执行与遗留项见 [V2-A 验收报告](recommendations-v2a-report.md)。下一步先讨论 V2-B 召回策略，再经用户授权实施，不在诊断中顺带调整候选配额。
