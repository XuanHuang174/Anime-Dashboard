# 增强 8A：作品级反馈合同与安全服务

## 本阶段边界

仅后端：新增模型、增量迁移、服务、API 和 PostgreSQL 验证。没有修改前端 UI，也没有启用 V2。

“不感兴趣”只按当前用户和**精确 Bangumi ID**排除候选。既不按标题猜身份，也不排除相同题材、同系列或续作。不修改手账、个人/公共评分、长评、私人标签或明确偏好。即使标记的是已完成的高分作品，也不把它的题材转成负面信号，不移除原画像样本。

撤销只恢复候选资格，不承诺原排名；仍须通过 `nsfw=False`、题材关联、筛选、种子排除及 `hide_recorded` 规则。反馈不是作品封禁：安全作品的详情和加入想看仍可使用。

为使后端合同可直接验证，候选排除已在 8A 接入 V1/V2 的候选池截断**之前**；两者计分、权重与重排公式均未改变。纯离线 V2 函数测试不代表批准线上 V2。8B 再完成页面反馈入口、撤销提示和客户端刷新/竞态闭环。

## 数据与锁

迁移：`recommendations/migrations/0005_recommendation_feedback.py`，仅三个 `CreateModel`，不回填、不触碰既有业务表。

| 表模型 | 职责 |
| --- | --- |
| `UserRecommendationFeedback` | `(user, bangumi_id)` 唯一；保存当前激活状态、最近隐藏时间、该作品最近变更的反馈版本与创建/更新时间 |
| `UserRecommendationFeedbackState` | 每用户唯一、从 0 单调递增的全局反馈版本；独立于 `RecommendationPreference.version` |
| `RecommendationFeedbackOperation` | `(user, operation_id)` 唯一的最小幂等回执；只保存作品 ID、操作、请求指纹、执行版本与时间，不复制私人内容 |

同一事务内按 `用户行 → 当前安全目录行 → 反馈行` 加锁，沿用加入想看的锁顺序。用户行锁也保护尚未创建的反馈状态，避免首次请求的空行竞争；数据库唯一约束作最终防线。整数 ID/版本有数据库范围约束，版本溢出拒绝写入。

状态变更、用户版本、操作回执原子提交；任何一步失败全部回滚。重复设置相同状态不增加版本、不更新原反馈时间，只记录新操作的幂等回执。同一 UUID 的重试不再产生回执。

回执暂不自动过期、不设置清理任务，避免迟到重试失去幂等保护；后续若设计保留期，需同步定义过期重试合同，不能直接清空回执。用户删除时反馈相关行随所属用户级联清理。

## API 合同：`recommendations.feedback.v1`

所有接口要求正常登录，所属用户只能来自 `request.user`。不接受用户 ID、手账 ID、公共库 PK、评分或长评。仅 JSON；未知字段、重复查询参数、超限请求都拒绝。

沿用现有节流：写接口 `recommendation_write`（30/min），读接口 `recommendation_read`（60/min），以及用户总节流。返回 `Cache-Control: private, no-store`，并区分 Authorization/Cookie。

### 1. 获取当前版本 / 隐藏清单

```http
GET /api/recommendations/feedback/hidden/?page=1&page_size=12
```

```json
{
  "contract_version": "recommendations.feedback.v1",
  "feedback_version": 0,
  "count": 0,
  "page": 1,
  "page_size": 12,
  "next_page": null,
  "results": []
}
```

GET 不创建任何状态行。默认每页 12，最多 24。按最近隐藏时间、行 ID 倒序稳定排序；后续页**必须**携带第一页的用户反馈版本：

```http
GET /api/recommendations/feedback/hidden/?page=2&page_size=12&feedback_version=3
```

清单项含 `bangumi_id / is_active / feedback_version / hidden_at / updated_at` 及安全目录的 `title_zh / title_jp / season / status_type`；不返回用户 ID、内部记录 PK、评分或长评。

版本变化返回 409，重新读取第一页。目录安全分级是实时硬过滤，不是被冻结的分页数据：作品新变成成人、未知分级或已不存在时，立即从清单排除，不能依赖缓存继续展示。此时历史反馈保留；写入/撤销也保守返回 404，不提供绕过分级的入口。

### 2. 标记不感兴趣

```http
POST /api/recommendations/feedback/
Content-Type: application/json

{
  "bangumi_id": 123,
  "expected_feedback_version": 0,
  "operation_id": "00000000-0000-4000-8000-000000000001"
}
```

`operation_id` 是每次**新决定**生成的 UUID。`expected_feedback_version` 是之前读到的用户级版本。成功 HTTP 200，示例：

```json
{
  "contract_version": "recommendations.feedback.v1",
  "action": "hide",
  "operation_id": "00000000-0000-4000-8000-000000000001",
  "replayed": false,
  "changed": true,
  "feedback_version": 1,
  "operation_feedback_version": 1,
  "feedback": {
    "bangumi_id": 123,
    "is_active": true,
    "feedback_version": 1,
    "hidden_at": "2026-10-16T12:00:00+08:00",
    "updated_at": "2026-10-16T12:00:00+08:00"
  }
}
```

根 `feedback_version` 是**当前用户全局版本**；内层是**该作品最后一次状态变更版本**；`operation_feedback_version` 是**该 UUID 原执行时版本**，三者不要混用。

### 3. 撤销

```http
POST /api/recommendations/feedback/undo/
Content-Type: application/json

{
  "bangumi_id": 123,
  "expected_feedback_version": 1,
  "target_feedback_version": 1,
  "operation_id": "00000000-0000-4000-8000-000000000002"
}
```

`target_feedback_version` 是要撤销的**该作品**反馈版本。既校验全局版本，也校验目标版本，防止旧提示条误撤销后来重新隐藏的状态。撤销写成 `is_active=false` 的保留行，不删除反馈历史。

### 4. 超时、不确定与冲突

- 未收到响应：用**完全相同的 UUID 和原请求体**重试；不要猜服务端是否成功，也不要自动改版本重放。
- 相同 UUID/相同内容：HTTP 200，`replayed=true, changed=false`，返回当前作品状态。例如隐藏已被撤销后，重试旧隐藏只报告当前未隐藏，不会重新激活。
- 相同 UUID/不同内容：409 `feedback_operation_conflict`。
- 旧全局版本：409 `feedback_version_conflict`；`reload_required=true`。
- 旧撤销目标：409 `feedback_target_conflict`。
- 版本耗尽：409 `feedback_version_exhausted`。
- 未登录 401，非法字段 400，非 JSON 415，节流 429；不安全/未知/缺失作品或无撤销记录 404。数据库故障返回现有统一 JSON 503，不能当作反馈已确认。

不同作品的同时操作也共享用户反馈版本：一处成功后，其他旧请求须重新读取并让用户确认新的决定，再使用**新 UUID**提交。不要静默绕过 409。

## 候选与旧证据失效

- 用户反馈只进入本请求上下文，不进入共享公共特征缓存。
- 两个候选引擎均在 400 条截断前按隐藏 ID 集合排除，允许候选不足，不补无关联作品。
- 分页 HMAC 和证据上下文带独立 `feedback_version`；反馈改变后，旧分页和旧详情令牌返回 409。
- `profile_version`、`preference_version` 不因单纯反馈改变。列表、详情共享同一证据版本序列化字段。
- 读取反馈快照前后、序列化证据前重核反馈版本；检测到并发变更就拒绝返回混合快照。网络中已发出的旧响应仍需 8B 的客户端请求序号/会话机制丢弃，不能把 HTTP 取消当作事务撤销。
- V1 排序公式未变化；无反馈时作品顺序及内部得分一致。证据/分页令牌因新增版本字段会在部署时自然失效一次，现有 409 重载流程继续生效。
- 发布指纹包含新增反馈服务与测试，旧 V2 批准文件自然失效；本阶段不重新批准或开启 V2。

## 独立 PostgreSQL 执行步骤

不能运行未显式指定隔离设置的 `manage.py test`；本地 `.env` 指向正式 Neon。

已授权的本机便携 PostgreSQL 复用核心 7 配置，仅监听回环地址，不安装系统服务。运行：

```powershell
.\scripts\setup-recommendation-postgres.ps1
# 这是脚本内公开的、仅本机合成测试库凭据，不是生产凭据。
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe scripts/verify_recommendation_8a.py
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

Runner 在导入 Django 前拒绝远程/Neon/生产库名/SQLite/缺失连接；只创建并销毁专用 `test_recommendation_isolated_tests`。新测试模块也在构建测试集阶段阻止错误配置。回归过程中禁用未模拟的外部 HTTP，不使用真实账户。

报告自动写入被忽略的 `.tools/acceptance-8a/*.json`，记录逐项测试 ID、结果和并发通过数，不记录连接字符串、私人长评或生产数据。

## 部署与回退（需运维执行，不在本次连接正式库）

新模型必须先迁移，再向新版应用放流；不能只推送代码而省略迁移。推荐列表/详情读取反馈状态依赖新表；缺表将按数据库故障失败关闭为 JSON 503，不会静默忽略隐藏决定。

1. 确认正式库连接、备份与发布窗口。
2. 在已核实的服务端发布环境检查 `python manage.py showmigrations recommendations` 和 `python manage.py migrate recommendations 0005_recommendation_feedback --plan`。
3. 审核仅新增三表后，在该发布环境执行 `python manage.py migrate recommendations 0005_recommendation_feedback`。
4. 发布/重启全部实例，保持 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`；只读核查认证、清单、正常推荐/详情。线上反馈写入须另行授权，8A 本地验收不等于线上验收。
5. 反馈没有共享私有缓存，无需全站 `cache.clear()`。公共特征缓存不含反馈，仍可复用；旧用户分页/详情令牌自然要求重新读取。

回退保留 0005 三表和全部反馈，不反向迁移、不批量删除数据。优先回退到兼容反馈合同的应用版本；若紧急回到 8A 以前版本，旧代码不识别反馈，必须明确暂停反馈功能并说明隐藏不能生效，不能声称仍在尊重隐藏记录。算法回退开关始终为 `content.v1`，不是反馈功能开关。

## 本次验收结果（2026-10-16）

| 项目 | 结果 |
| --- | --- |
| PostgreSQL 完整 8A 固定回归包 | 153/153 通过，0 失败、0 跳过 |
| 新反馈合同测试 | 26/26 通过 |
| 新反馈真实行锁竞争 | 6/6 通过，每次竞争验证不同 `pg_backend_pid()` |
| 原有加入想看并发 | 4/4 通过，含已有 0 分、长评、空私人标签保护 |
| 核心 7 原固定包 | 102/102 通过 |
| V1 证据及偏好闭环补充回归 | 13 + 6 项通过 |
| Django 检查 / 迁移漂移 | 无问题 / 无变更 |
| 现有前端测试（未改 UI） | 118/118 通过 |

6 项新并发：同 UUID 八连击、不同 UUID 同旧版本竞争、不同作品竞争、跨用户同 UUID 隔离、隐藏/撤销竞争、八路撤销且原手账逐字段保留。无生产数据库访问、无线上写入、未启用 V2。功能场景覆盖不等同于源代码行覆盖率百分比。

未完成事项：正式环境 0005 迁移与部署只读验收、8B 前端反馈/撤销入口及刷新闭环。不存在“已完成线上迁移”的声明。
