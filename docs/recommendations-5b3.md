# 核心 5B-3：同条件双轮对照与安全发布

## 范围

不改前端布局，不切换默认算法，不改 `.env`，不运行迁移，不回写评分或标签，不扫全站私人手账，不调用外部服务。完整保留 content.v1 的排序行为，并保留 V1 冻结测试。

第一轮 `legacy_v1 → current_v1`：固定 V1，仅比较 5A 的 genres.v2（26 题材/10 别名）和 5B-1 的 genres.v4（33 题材/21 别名）。旧规则由 5A 提交 eca457f 核实并显式冻结，不从当前词表反推。题材规则通过参数传入，不用全局 monkeypatch，支持并行隔离。私人成果从同一批只读原标签副本分别提取，不能只在已清洗的标签上删词来模拟旧规则。

第二轮 `current_v1 → current_v2`：固定新规则、个人评分、明确偏好、过滤和样本；仅比较 V1 固定强惩罚与 V2 分层池/差异化重排。

两轮处于同一个数据库强制只读一致快照（PostgreSQL REPEATABLE READ/READ ONLY，SQLite query_only），公共数据和用户记录只读一次。实际排名仍调用真正的 V1/V2 引擎及当前数据库安全复核，不维护另一套近似算法。`rank_versions` 显式选择版本、复制请求私有上下文；不会改变在线开关。

题材关联/重复代理统一用新词表计算，避免指标定义改变冒充质量增益；证据按各自实际训练规则独立核查。账号自然有效样本必须 ≥ max(4, 画像最低样本+1)。每轮固定相同目标 ID；只有两臂训练都满足门槛时才计入配对留出；旧规则冷启动或无题材不能被悄悄移出账号队列。保留明确偏好可能引入先验知识，报告写明。指标只在本地报告，不展示喜欢概率/匹配百分比、不进行评分可信度修正。

## 执行顺序

### 1. 本地安全回归（不接触 Neon/R2）

```powershell
.\.venv\Scripts\python.exe manage.py test --settings=config.recommendation_test_settings --noinput
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

### 2. 生成当前代码安全测试凭据

```powershell
.\.venv\Scripts\python.exe manage.py verify_recommendation_release --output .tools/recommendation-comparison/checks/test-attestation.json
```

父命令只签名和写本地文件；固定测试集合在子进程强制使用隔离 SQLite、隔离缓存和清空生产/R2 配置。任一失败、跳过或检查期间代码变化均不出凭据。不能传入任意测试集合或手写 `pass` 来代替安全验证。凭据绑定当前关键代码、测试、依赖文件、画像配置、题材和 V2 策略；最多 7 天有效，使用应用 SECRET_KEY 做 HMAC，不打印该密钥。不同平台换行规范化后哈希相同。

### 3. 真实授权账号只读对照

**这里的默认 `.env` 是正式 Neon。确认账号授权后才能执行。** 不读取密码，只按精确邮箱或明确 ID 查找；两种方式不能混用，邮箱重复/停用/样本不足直接拒绝，不扩大查询范围。示例不是授权任何新账号：

```powershell
.\.venv\Scripts\python.exe manage.py compare_recommendations --user-id <已授权ID> --confirm-authorized-samples --test-attestation .tools/recommendation-comparison/checks/test-attestation.json
```

也支持 `--user-email <已授权精确邮箱>`。最多 5 个显式账号、每账号 1–5 个留出目标、总目标 ≤20，Top-K 3–30，重复计算 2–3 次。脚本无“全站扫描”选项，也无改分/自动补关联/重命名标签选项。日志只写 sample 序号；不记录 SQL 或参数。报告只允许保存至私有且 Git 忽略的 `.tools`。

仅想验证公共规则覆盖和报告模板：

```powershell
.\.venv\Scripts\python.exe manage.py compare_recommendations --catalog-only
```

此模式不读取私人账号，六项门槛都是证据不足，不产生上线凭据。缺少安全测试凭据仍可生成诊断报告，但不能获得上线许可。

## 六项硬门槛（固定预声明，不看结果后改阈值）

| 门槛 | 规则 |
| --- | --- |
| 安全 | 固定隔离测试通过且零跳过；所有实际返回行安全、源身份有效；不存在安全错误 |
| 证据 | 全部实际返回行及留出结果独立核查：来源、个人分、真实共有题材、字段来源和文案；错误数为 0 |
| 相关性 | 三类关联代理均值不能下降；每账号/分类 Hit、MRR、binary NDCG 不下降；发现类重复 Jaccard 至少下降 .001 |
| 分类 | 熟悉类集中度 ≥ 发现类，熟悉类关联代理 ≥ 探索类；探索有真实关联，新增题材占比 >0 且至少 2 个新方向 |
| 性能 | 每次排名计算 ≤5000 ms、SQL ≤6、候选池 ≤400；重复运行顺序及证据一致；留出排名也受性能预算约束 |
| 回滚 | 固定缓存/分页/用户隔离与 V2 → V1 API 测试通过；实际快照绑定版本和用户 |

空结果、不能配对的留出或缺少测试凭据使用 `insufficient_evidence`，不当作 0 分成功，也不声明统计显著性。狭窄口味可能自然重叠，这不算身份错误；但若不能证明约定的分类定位或重复度改善，本批样本不批准切换。严格门槛可能不通过，这是实验结果，不可调高真实评分或事后放松门槛来“过验收”。

每个实际结果都复核安全和证据；报告导出数值、计数、公共候选 ID、指纹和匿名样本编号，不导出源作品标题、个人评分、私人 ID、原标签或长评。微观计时和 SQL 次数从实际引擎调用获得，不把纯内存微基准当作线上端到端 SLA。初始读取画像/目录、网络和客户端时间不在排名计时内；线上启用前还需环境延迟确认。

## 报告、审批、缓存和回滚

输出 `comparison.json`、`report.md`，最后写 `manifest.json` 完成标记；不覆盖已有目录。仅六项全部通过才生成 `approval.json`。该许可是**允许人工决定**，命令不改线上环境、数据库或默认算法。

线上代码默认仍为 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`。如后续明确批准启用：在部署环境设置 `RECOMMENDATION_V2_APPROVAL_FILE` 指向完整报告目录内的 approval.json（保持 comparison.json、manifest.json），并设置算法为 content.v2 后重启。应用验签、代码/配置指纹、有效期、六项通过状态与完整报告哈希；陈旧/修改/不完整凭据返回 JSON 503，不偷偷回退到另一算法。两台环境需要同应用 SECRET_KEY、同代码及策略；否则在部署环境重新生成只读验收。测试旁路只存在于隔离 test settings，绝不能用于部署。

回滚只需把算法改回 content.v1 并重启，**不需要许可**。公共特征缓存命名空间带 algorithm/taxonomy/IDF/extraction 版本；不存在用户结果缓存；旧无版本键不再使用。分页 HMAC 绑定这些版本、实际规则、V2 策略、用户、偏好、画像和结果；版本切换后旧第二页返回 409，复用前端重载机制。

需要清理时 `clear_version_cache('content.v2')` 只删除一个当前公共命名空间，不调用 cache.clear，不清 V1、不删除文件。回滚本身无需清缓存。空报告模板见 `recommendations-5b3-report-template.md`。

## 本次工程回归（2026-10-15）

- 新增 21 项评测/审批/版本隔离测试；包含完整审批报告、失效/篡改/不完整凭据、实际 API 切换回滚、旧规则冷启动和只读范围验证。
- 后端全量 523 项：518 通过，5 项环境条件跳过；Django 检查通过，无新增迁移。
- 固定安全验收子集独立运行，必须全部通过且零跳过，才签发测试凭据。
- 前端原有 72 项通过，构建通过，未修改前端布局。原有约 871 KB 主包体积提示仍存在。
- 单元测试通过仅证明工程约束，不等于实际推荐质量已达标。真实授权样本的诊断、计时及判定仅存放本地私有报告，不提交 Git；未经六项全部通过和人工确认，不启用 V2。
