# 增强 9A：控制合同、前置筛选与稳定批次

## 范围和兼容性

- 仅后端；没有改造 React，没有迁移、业务新表、生产数据写入或 V2 上线。
- `GET /api/recommendations/`，沿用认证、当前用户隔离、限流及 `private, no-store`。
- **旧请求不携带新参数时**仍使用原有 V1 / `page + state_token` 合同，响应结构和默认排序不变。
- `browse=true` 或提供 `style` / `year_from` / `year_to` / `cursor` 时，进入 `recommendations.browse.v1`。9B 必须显式使用新合同，不能把新响应交给旧分页合并器。
- 新路径固定要求服务端配置为 `content.v1`。若以后改成其他算法，已有浏览游标返回 409，新请求返回 503，绝不在本阶段调用 V2 或静默改变算法。

## 请求合同

| 参数 | 默认值 / 校验 |
| --- | --- |
| `browse` | false；显式 true 开启新合同 |
| `category` | `all` / `familiar` / `explore`，默认 all |
| `style` | `balanced` / `close` / `open`，默认 balanced |
| `type` | 复用现有类型枚举，默认 all |
| `min_score` | 0–10，一位小数，默认 0；只指 Bangumi 公共评分，不是个人评分或概率 |
| `year_from` / `year_to` | 可省略；提供时必须是 1000–9999 的四位整数，起点不得大于终点 |
| `hide_recorded` | 默认 true |
| `page_size` | 1–24，默认 12，整轮固定 |
| `cursor` | 服务端签发的游标，最多 8192 字符 |

未知、重复、空字符串、非法参数返回 JSON 400；禁止传 user ID、算法覆盖、原始排除 ID 集合等。不限条件请省略参数，不传空字符串或 null。

首次请求：

```http
GET /api/recommendations/?browse=true&style=balanced&year_from=2015&year_to=2026&type=TV&page_size=12
Authorization: Bearer <正常登录令牌>
```

后续请求：

```http
GET /api/recommendations/?cursor=<对返回游标做 URL 参数编码>
Authorization: Bearer <同一用户登录令牌>
```

后续省略的控制条件从签名快照继承；显式附加的条件必须与快照相同，否则 409。新条件应发起一个**不带旧游标**的新请求。不得混用 `state_token`；新批次不接受 `page>1`，带游标时也不能另外指定页码。

## 风格规则 `browse-controls.v1`

`algorithm_version=content.v1`，`control_policy_version=browse-controls.v1`，`style` 独立返回。

### 均衡

直接调用冻结的 V1 排序函数，不复制修改其系数、浮点运算或打破平局规则。使用整份安全目录的 IDF 权重；筛选只改变可用候选，不重算成“筛选子集 IDF”。在相同目录特征、画像和条件下，与冻结 V1 有序结果及证据一致。

批次路径的首次请求从当前一致性事务读取目录构建特征，不依赖旧进程中可能过期的公共缓存；它不改变旧路径的公共缓存策略。

### 非默认风格

独立模块执行有版本标识的有限调整，不调用 V2：

- `close`：`0.90 × V1类别基础分 + 0.05 × 零公共分的V1发现分 + 0.05 × IDF加权共有题材占比`。
- `open`：保持 V1 类别基础分。
- 两者使用最近 5 部已选作品的最大题材 Jaccard 重复度，按 `基础分 − 惩罚系数 × 重复度` 贪心选择，平局按 Bangumi ID 升序。

| 类别 | close 惩罚系数 | open 惩罚系数 |
| --- | ---: | ---: |
| all | 0.20 | 1.10 |
| familiar | 0 | 0.02 |
| explore | 0 | 0.12 |

注意冻结 V1 的 all 目标是 `0.5×基础分−0.5×重复度`，等效于 `基础分−1×重复度`，不是 0.5 的等效惩罚。open 的 1.10 是在同一尺度上小幅加强。

所有候选仍须有真实共有题材证据；familiar 保持原有画像来源约束，explore 保持实际新题材门槛。不会为了显示风格差异而塞入无关作品，不保证所有自然账号的三档结果都不同。内部得分不输出为匹配率或喜欢概率。

任何系数、资格条件、权重解释或平局规则变更必须递增 `CONTROL_POLICY_VERSION`，并重新评测。默认档完全不进入这些非默认公式。

## 前置筛选

只从 `RecommendationAnime.season` 完整解析 `YYYY`、`YYYY-M(M)`、有效 ISO 日期，兼容 `2026-1` / `2026-01` / `2026-04`。不在标题、简介中寻找年份。非法月份、无效日期和缺失值在指定年份范围时排除，不限年份时允许参与。

类型、公共评分、年份先限制特征候选；自身种子、已有记录及当前用户反馈在取前 400 个候选前排除。仍最多返回 120 部，不能依赖前端过滤掩盖候选不足。不修改目录 season 或用户标签。

## 固定快照与游标

选择**无共享私人缓存的签名有界快照**，而不是单进程内存缓存或新增业务表：

- 游标包含最多 120 个公开作品 ID 的固定顺序、公开目录 IDF 权重、规范条件、批次位置、随机快照标识、固定过期时间以及用户/上下文 HMAC。
- 不包含用户 ID、私人手账内容、来源作品列表、个人评分、长评或明确偏好标签。
- 签名不是加密；浏览结果 ID 和本次条件可解码。游标仍属个性化浏览凭据，应避免在日志中记录完整 query、游标及 Authorization，不放入公共缓存或对外分享。
- 服务端签名校验且绑定当前用户；无法通过修改 ID 顺序、页大小、偏移或条件伪造下一批。
- 有效期 15 分钟，跟随游标不会延长原快照期限；换 worker、清空 LocMem 公共缓存不影响有效快照。
- 继续请求只读取/校验当前上下文并按原 ID 顺序取作品，**不重新排序**。重试同一个游标不前移位置。新签出的游标字符串时间戳可不同，但指向相同内容和位置。
- 不持久化“用户已经看到了哪些卡片”，所以可以重放旧游标查看原页。这是幂等重试能力；不重复保证是沿 next 游标向前遍历的跨页/跨批结果，不是禁止返回上一页。

每批最多 24 部，每页默认 12 部。页大小为 7 时，一批是 `7/7/7/3`，绝不跨到下一批。用户也可提前换批，跳过当前批未加载部分；这些作品不会自动塞回后续批次。

### 响应关键字段

| 字段 | 含义 |
| --- | --- |
| `contract_version` | 沿用基础列表合同标识；新客户端同时校验 browse_contract_version |
| `browse_contract_version` | `recommendations.browse.v1` |
| `snapshot_id` / `snapshot_expires_at` | 快照标识 / Unix 秒过期时间 |
| `control_policy_version` / `style` | 控制策略版本与风格 |
| `effective_conditions` | 服务端规范化的实际条件，最低公共分为一位小数字符串 |
| `batch` / `page` | 从 1 开始；page 为当前批内页码 |
| `count` | 本轮结果数，不代表全库匹配总量，最多 120 |
| `result_limit` / `batch_size` / `page_size` | 120 / 24 / 当前页大小 |
| `batch_count` | 当前批包含的作品数，不是批次总数量 |
| `batch_hero_bangumi_id` | 当前批第一条，分页时不变；空结果为 null |
| `next_page_cursor` | 本批下一页，没有则 null |
| `next_batch_cursor` | 下一批第一页，没有则 null |
| `batch_exhausted` / `exhausted` | 本批 / 本轮沿当前读取位置是否结束 |
| `mode` / `cold_start_state` | 保留冷启动与明确偏好等真实业务状态 |
| `evidence_context` / `results` | 复用现有真实证据合同，结果不包含简介或内部排序分 |

推荐理由说明的是题材关联，不是本次排序唯一原因。风格不会凭空改变依据，详情继续使用现有单条证据服务，不计算全列表。9B 需在前端切条件时关闭旧详情/资源会话，不能把浏览会话与资源搜索缓存混用。

## 一致性、安全和失效

新路径仅支持 PostgreSQL；每次读取使用数据库强制 `REPEATABLE READ, READ ONLY` 的独立事务，30 秒语句超时，3 秒锁等待超时。不在已有事务中偷偷降级运行；当前部署必须保持视图未被 `ATOMIC_REQUESTS` 包裹。

签名上下文包括当前用户的画像、明确偏好及版本、独立反馈版本/隐藏集合、自己的推荐相关手账字段/身份关联版本/进度/更新时间、目录精确摘要、规则和数量边界。不读取长评或私人简介来计算摘要。

目录摘要在 PostgreSQL 对各行相关字段（包括分级、季度、评分、标签、标题、封面、公开简介及更新时间）计算紧凑哈希，再组合；因此 `.update()` 未更新 auto_now 也会被检测。全目录包括不安全条目，最多 50,000 行，超过上限返回 503，不无界扫描私人手账。

- 修改当前用户画像、偏好、反馈、相关手账数据或身份 → 旧游标 409。
- 修改目录、分级、规则、算法或本轮条件 → 409。
- 过期或已签名但结构不兼容的快照 → 409。
- 未签名、篡改、错误长度等非法游标 → 400。
- 数据库不可用、预算超限或功能关闭 → JSON 503，不回退 Mock/V2。

全批次 ID 和证据来源在当前一致性读取中重新核验安全状态，绝不把消失或变为 NSFW/未知的作品补成空洞继续拼接。安全与上下文检查以当前请求数据库事务的快照时点为准；后续提交的变更由下一次请求检测。

这是正确性优先的 9A：后续每页会验证目录摘要和当前用户上下文，首屏构建有界特征。尚未宣称生产性能达标；9C 需记录真实部署耗时及 SQL/传输成本，再决定是否引入可靠目录修订计数优化，不能用不可靠的 MAX(updated_at) 缓存取代精确失效。

## 执行隔离验证

不使用 `.env` 的生产 DATABASE_URL。仅运行下列明确指定的本机测试配置：

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts/verify_recommendation_9a.py
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

测试脚本校验 loopback 专用库地址，阻断外部 HTTP，报告写入忽略的 `.tools/acceptance-9a/`。测试夹具覆盖三类默认 V1 全序列/证据一致、风格实际参与、前置过滤、重试去重、跨 worker、冷启动与空结果、用户隔离、各种失效、真实只读事务及零副作用，并复跑既有反馈/加入/证据/并发回归。

### 本地验收记录（2026-10-17）

- 最终回归：193 项通过，0 失败、0 错误、0 跳过，其中本阶段新增 26 项。
- 既有 PostgreSQL 并发回归：反馈 6 项、加入想看 4 项、反馈边界 6 项，共 16 项通过。
- 三类均衡推荐的完整结果顺序、首屏证据与冻结 V1 同条件对照通过；后续游标读取未调用排序函数。
- Django system check：无问题；迁移检查：No changes detected。
- 最终机器报告：忽略目录 `.tools/acceptance-9a/20261017T040344312044Z.json`，包含通过用例和并发计数；不进入 Git。
- 全部使用本机专用 PostgreSQL 合成夹具，没有读取或修改生产 Neon，没有操作真实账号。
- 本阶段没有前端文件改动，因此未进行页面验收，也未声称完成生产性能或上线联调。9B/9C 按原计划后续推进。

## 部署和关闭

无需数据库迁移；正常部署后旧前端行为不变。新合同通过显式参数使用。

```dotenv
RECOMMENDATION_ALGORITHM_VERSION=content.v1
RECOMMENDATION_BROWSE_ENABLED=True
```

如需关闭：设 `RECOMMENDATION_BROWSE_ENABLED=False` 并重新部署/重启服务。旧 V1 请求仍可用，新浏览请求返回 503；9B 应提供清除旧浏览状态、退回旧推荐的明确入口。无需清理私人数据或全局清空缓存。签名域单独版本化，SECRET_KEY 或合同版本变化会自然使旧凭据失效。

本阶段不执行生产写入、线上 V2 切换、前端改造、每日推荐或长期历史功能。
