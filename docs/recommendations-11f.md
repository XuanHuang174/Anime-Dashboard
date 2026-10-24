# 扩展 11F：发布验收、灰度与回退

日期：2026-10-24。工程、隔离回归与发布工具已交付；**线上灰度/全量配置及认证验收待部署操作完成**。用户选择自行在 Render 应用配置。本记录不把本地测试或公开页面 200 视为线上功能已开放。

## 交付内容

- `RECOMMENDATION_EXPLORATION_ENABLED` 保持默认 False。
- 新增 `RECOMMENDATION_EXPLORATION_CANARY_USERS`：留空时向所有已登录账号开放；非空时仅匹配精确用户名。无效配置关闭探索，不能扩大为全量。
- 能力接口、合同、目录、选项、两种查询、详情、上下文与私人记录入口统一执行账号门禁。响应不返回灰度名单。
- 能力接口新增 `rollout_contract_version: recommendations.explore.rollout.v1`，部署工具据此区分 11F 与旧版本。
- 每次灰度、全量、关闭、重新开启均更换 `RECOMMENDATION_EXPLORATION_EPOCH`；名单变化也参与签名代次。旧游标/详情/上下文不能跨发布代次使用。
- 配置生成器只输出探索相关三个变量，不改增强 9、V2、NSFW 总开关或审核资料；回退不删除手账或审核记录。
- 修复“想看后刷新完整详情，再收到后续失效通知”的边界：即使原列表已标记失效，也关闭这份详情及资源会话；自身想看确认时仍可保留刚刷新的完整详情。
- 首次查询尚未返回时收到失效通知，进入可重试错误并丢弃晚到响应，避免取消请求后一直停在加载中。

## 必须按顺序部署

**先部署本次 11F 提交的后端和前端，再应用灰度配置。** 旧后端不认识灰度名单；不能在旧后端上仅设置总开关 True 后误以为已经灰度。

三个变量应在一次部署中同时应用到所有 worker。以下命令只打印配置，不修改 Render、`.env` 或数据库。

### 1. 先灰度 XHcode

```powershell
.\.venv\Scripts\python.exe scripts/prepare_recommendation_exploration_rollout.py --mode canary --user XHcode
```

将输出 `environment_patch` 的三个值复制到 Render：

```text
RECOMMENDATION_EXPLORATION_ENABLED=True
RECOMMENDATION_EXPLORATION_CANARY_USERS=XHcode
RECOMMENDATION_EXPLORATION_EPOCH=<本次命令生成的新值>
```

部署完成并刷新网页后：XHcode 可进入“单番相似”“按标签找”，其他登录账号仍不可用；原个性化推荐和手账正常。用户名必须使用账号真实用户名，不使用手账标题或昵称。

### 2. 灰度验收后全量

```powershell
.\.venv\Scripts\python.exe scripts/prepare_recommendation_exploration_rollout.py --mode all
```

应用新的三个值：总开关 True，灰度名单留空或移除该变量，代次使用新生成值。此后所有正常登录用户可用，无需高分样本。NSFW 仍需用户主动选择并通过既有范围及作品审核。

### 3. 独立回退

```powershell
.\.venv\Scripts\python.exe scripts/prepare_recommendation_exploration_rollout.py --mode off
```

应用总开关 False、空名单和新的代次。关闭扩展 11，不关闭增强 9，不更改任何审核记录。重新开放必须再次生成代次，不能复用旧值。

## 线上只读验收工具

`scripts/verify_recommendation_exploration_deployment.py` 固定访问项目已有前后端域名，默认不联网；必须明确 `--deployment-confirmed`。只有正常登录可 POST，业务接口仅允许 GET，重定向不携带认证，最多 40 次请求。报告不保存密码、令牌、游标、上下文、作品内容或账号资料。

公开基线（不读取登录凭据）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_recommendation_exploration_deployment.py --deployment-confirmed --expect public
```

部署后对已授权的测试账号验证开启状态。将 `12345` 换为你确认存在、有标准题材的普通作品 Bangumi ID；它不是私人记录主键。

```powershell
.\.venv\Scripts\python.exe scripts/verify_recommendation_exploration_deployment.py --deployment-confirmed --expect enabled --expected-account XHcode --seed-bangumi-id 12345 --expected-dist frontend/dist
```

工具使用掩码提示读取密码，或读取操作者事先设置的进程变量 `EXPLORATION_AUDIT_TOKEN`；先验证服务端实际用户名与指定账号一致。不要把凭据粘贴到聊天、命令参数、日志或提交文件。`--expected-dist` 应指向本次经过测试的构建，校验线上推荐 JS/CSS 的 SHA256。未提供该参数时报告保留 `frontend_bundle_verified: false`。

可额外提供 `--adult-seed-id`，仅对操作者确认已审核的公开成人作品执行主动 NSFW 范围查询；不自动审批或更改总开关。

关闭状态或非灰度账号用 `--expect disabled --expected-account <已授权账号>` 验证。工具必须识别 11F 发布标识并确认探索 503、原推荐正常，不能把旧版本缺接口当作回退成功。

验收覆盖：私有缓存头、合同与能力一致、选项实际前端校验、精确参照、单番和标签独立证据/详情、上下文、游标重试、ALL/ANY、两种排序、排除硬约束、查询前后画像/偏好/隐藏记录摘要不变。真实业务写入、跨账号攻击、撤销审核和故障注入只在本地隔离环境测试。

报告写到 `.tools/deployment-11f/`。`passed_public_baseline_only` 仅代表公开可达和登录保护；`passed_scoped_readonly` 也只代表该账号、本次数据及所列检查。空结果或无续页会明确列为限制。HTTP 耗时不能替代服务端 SQL、CPU、内存或缓存遥测，未测指标为 null。

## 本轮验收记录

- 完整推荐模块：1054 项，1053 通过、1 项跳过。跳过项需要显式真实样本与 SQLite 安全测试配置；本轮仅使用本机隔离 PostgreSQL 与合成数据。
- 发布标识、重签上下文和哈希门禁最终 53 项定向回归通过，详见本地 `.tools/11f-final-contract.log`。
- 前端 343 项通过；React 生产构建、Django check、无新增迁移检查通过。构建仍有既有主包体积提示。
- 灰度/全量/回退、同账号代次变更、其他账号上下文、禁用后原推荐正常、失效与私人信息不变均有自动化覆盖。
- 浏览器使用实际组件及合成客户端：1152×480（1440×600 的 125% 缩放等效 CSS 视口）、1920×800（75% 等效）、390×640；选择面板滚动、固定按钮、Escape 关闭与焦点恢复正常。等效视口检查没有更改用户浏览器缩放设置。
- 低动态效果由隔离 fixture 模拟 `prefers-reduced-motion`，未更改浏览器/系统设置。真实组件仍可打开详情、想看、资源并返回原推荐。
- 浏览器复现并验证：想看后完整新详情 → 打开资源 → `Alt+Shift+U` 模拟后续失效 → 两个面板均关闭，列表位置保留并提示重新探索。
- 线上只做了四次公开 GET：主页 200，能力/探索/选项均 401。未使用线上认证账号，未改 Render 或线上数据；认证验收与正式开放仍待部署操作。

## 性能与预算

已记录同一隔离环境、同一空手账账号下目录搜索、标签选项、单番/标签首屏、续页、详情及增强 9 基线的冷/热耗时、SQL、Python CPU/分配峰值与公共目录缓存命中。原始可公开的数值快照在 [本地性能记录](recommendations-11f-local-profile.json)。

1000 部合成目录的上限：单次带追踪耗时 <15 秒、SQL ≤36、Python 分配峰值 <128 MiB；实际首屏冷/热约 0.27/0.17 秒，最大 SQL 为 20。续页与详情不读取公共召回缓存；它们继续核验实时目录与签名。增强 9 使用相同空手账，但语义为冷启动，不能据此宣称正常画像场景等价或排序质量提升。

单独 50000 部合成目录、无 tracemalloc：单番冷/热 1.4837/0.6707 秒、SQL 20/19，续页 0.3586 秒、SQL 19；标签冷/热 1.5857/0.7134 秒、SQL 19/18，选项 0.4139 秒、SQL 10，续页 0.3763 秒、SQL 18。50k 边界测试约束首屏 SQL ≤36、选项 ≤30，不逐作品查库，续页不重新召回。

这些是单次本机样本，不是生产 p95、并发压测、数据库服务端 CPU/内存或线上缓存命中率。生产指标尚未取得，不能用上述数值代替线上观测。
