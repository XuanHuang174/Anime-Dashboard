# 扩展 12B：系列关系存储、同步与读取服务

日期：2026-10-25。状态：后端实现；用户页面、推荐排序及生产数据导入不在本轮范围。遵循 [扩展 12 方案](recommendations-extension-12-plan.md) 和 [12A 合同](recommendations-12a.md)。

## 存储与事务

增量迁移 `0013_series_relations` 仅新增独立系列表及修订权限，不修改原作品、推荐目录、私人手账或偏好：

| 表 | 职责 |
| --- | --- |
| SeriesNode | Bangumi ID、媒介、原始发行日期与精度、发行形式、分级及元数据指纹；不是推荐资格 |
| SeriesObservation | 每来源/快照的不可变观察：ZIP/关系成员摘要、观察时间、归档时间、映射版本、状态、诊断及条目成员摘要 |
| SeriesAssertion | 观察中的原始数字关系、方向、源排序、规范化种类及待核验标志 |
| SeriesHead | 当前可用观察和最近一次尝试分别指向，不以失败/部分结果覆盖旧版本 |
| SeriesOverrideEvent | 单对关系的更正、抑制、撤销，保留操作者、依据、原观察、操作编号和版本 |
| SeriesState | 单行发布版本及最后接受的归档时间，串行协调同步与人工修订 |

原始观察、断言和修订历史不删除。Django 后台普通表单不能增删改这些数据；统一使用受控服务。一次至多 100 个来源的同步在一个事务中发布，任一步 SQL 失败全部回滚。

同快照、同来源、同内容重跑不重复创建观察，不增加发布版本；同快照内容变化拒绝。不同来源可用同一 ZIP 分批增量收录。旧于最后已接受归档的版本拒绝回退，避免覆盖共享节点的新资料。

部分批次不更新旧节点资料和当前观察；可保留新节点供审核。未知码、缺失端点、重复、同对多种关系、已取得反向却不一致、故事关系环会使本批成为 partial，不自动择一或补齐。没有取得反向观察本身不算冲突。

成功的空观察可以替换原有当前观察，状态为 empty_observed；它不证明条目独立。预检失败的 apply 仅为已有来源登记通用 failed 收据，不保存原始异常、路径或响应正文；首次失败不会凭空创建已核验动画节点。

## 官方归档预检与增量同步

没有新增外部网络客户端、下载调度或 API 补齐。12A 未取得真实 API 响应，因此 API 来源保持未启用，不能自动回退到标题猜测。

纯预检实现 `series_source.prepare_archive` 不导入 Django/.env、不访问 SQL、不解压 ZIP 到目录、不修改源文件。管理命令使用当前 Django 配置；**默认 preview，不加 --apply 不执行系列写入**。运行 apply 前须自行确认目标数据库。

预检要求明确的官方归档文件名、从官方 manifest 核对的 SHA256 和 1–100 个来源 ID。对完整 ZIP 校验摘要，流式读完整关系及条目成员，再按明确 ID 抽取。只扫描指定成员，检查重复成员、逐行大小、总大小、时间和条数；不递归展开系列。

- 压缩文件 ≤2 GiB；条目成员 ≤4 GiB；关系成员 ≤512 MiB；单行 ≤2 MiB。
- 每批来源 ≤100、选中关系 ≤5000、端点 ≤10000；预检默认 120 秒，可设 1–300 秒。
- 非动画端点只留身份和媒介；动画端点保留必要公开资料，不复制简介、封面或用户内容。
- 日期保留年/月/日原精度；无效日期拒绝预检，缺失日期不补季度首日。
- 未知分级保持 null；不因为节点入库或人工关系更正就认为内容已通过审核。

预检示例（样本归档不是固定的数据版本要求，后续换成实际下载版本与官方摘要）：

```powershell
.venv/Scripts/python.exe manage.py sync_series_archive --input data/recommendations/archive/dump-2026-10-06.210359Z.zip --expected-sha256 2f4273f1aa5d6932c8aa2c448f91771fe0d07ec69c1ef64b4c5098f0760f8d99 --ids 1424,3774,328609
```

审核 preview 输出后，相同参数显式追加 `--apply` 才入库。导入不会自动打开读取开关。checksum 表示文件与操作者核对的官方摘要相符；它不是对任意用户提交文件的来源认证。

本轮对 12A 同一真实归档的 13 个来源完成纯预检：350 节点、415 断言、partial=false，最终含前后两次 ZIP 摘要校验的本机样本约 25.3 秒（与全量测试同时运行）；没有写入正式库。该耗时不是生产性能承诺，也没有全库入库或覆盖率结论。

## 人工核验入口

Django 后台“系列公共节点”提供“核验/修订直接关系”，可填写目标 ID、更正种类/暂不展示/撤销本地修订、原因与依据。页面显示最近来源断言和修订历史，明确目标相对来源的方向。

权限为 `recommendations.review_series_relations`，要求 active + staff；超级管理员拥有该权限。专门授权此权限不授予内容审核、作品编辑或推荐开关权限。服务在事务中重新读取并锁定操作者，不能仅凭打开表单时的权限提交。

表单保留版本和 UUID 操作号，提交时必须明确核对。相同请求可重放；复用操作号提交不同内容、过期版本或其他无效操作返回冲突。数据库记录保持原样，撤销以新的 clear 事件保存。

更正/抑制绑定来源观察。上游换了当前观察时，对应目标暂不自动重新显示，需要重新核验或明确撤销本地修订；不能因同步而悄悄恢复曾经被人工纠正的关系。明确 clear 后撤销持续生效，未来来源更新正常采用新来源，不继续保留旧修订的隐藏效果。原始断言始终保留。

## 受保护的一层读取服务

```text
GET /api/recommendations/series/{bangumi_id}/
GET /api/recommendations/series/{bangumi_id}/?context=上次返回的签名上下文
```

新开关 `RECOMMENDATION_SERIES_ENABLED=False` 默认关闭；打开后仍必须登录和满足现有内容范围。该开关只控制新读取接口，不影响三种推荐。12C 页面尚未接入，不把它加入现有探索能力合同或一级导航。

仅支持既有 `content_scope` 和单个可选 context；伪造 user_id、depth、空/重复参数拒绝。与其他推荐读取共用限流，返回 private/no-store。不存在的系列表/数据库不可用返回 503，旧推荐路径不依赖新表。

结果合同 `recommendations.series.read.v1`：

- `selected_seed`：当前范围中已收录来源的公共 ID、名称。
- `coverage`：not_loaded / observed / empty_observed / partial / failed / stale；最近尝试单独返回 last_attempt_status。
- `results`：目标 ID、目录名称、原始发行日期/精度、发行形式、关系种类、分组提示、来源 archive/manual。
- `depth=1`：只返回出向直接关系，不推断逆关系、系列归属或连续剧情，也不返回观看攻略。
- `ordering=release_date_not_watch_order`：按已有日期排序，未知排后；年/月精度不冒充精确首播日。
- `can_add_from_series=false`：12B 没有新的想看写入路径；12C 需通过现有合格作品详情/想看服务闭环。
- `limit=200`、`truncated`：先在最多 5000 条直接关系中应用范围，再按日期截取；超上限明确标记，不冒充全系列。12B 不提供关系分页或递归图。
- `context`：15 分钟签名，绑定当前用户、来源、系列版本、目录指纹、节点指纹和内容范围；资料变更返回 409，不能静默沿用旧上下文。权限或开关失效也可能返回现有范围规则的 403/503，客户端须同样终止旧上下文。

仅展示两端同为动画，且目标存在于当前合格推荐目录、目录分级与归档节点明确一致的关系。跨媒介、待核验、目录外或未知分级节点保留在后台，不公开其名称、ID、诊断或未过滤数量。始终提示“当前范围和已收录资料可能不完整”。

当前观察超过归档时间 45 天时标记 stale，但内容仍须通过实时范围校验。最近同步失败不会把旧的有效关系清空。新请求在数据库强制只读的一致快照中读取，响应发出前重新核验发布版本、目录/节点及内容范围，避免读取中途撤销仍发出旧资料。

不读私人评分、长评、自定义标签、偏好或本人观看状态；12C 再独立叠加本人状态，不把私人状态放入公共关系缓存。本阶段无公共/私人缓存新增。

## 本地验证与部署边界

仅使用 `config.recommendation_pg_test_settings` 的本机隔离 PostgreSQL 和合成账号/数据验证数据库流程。真实归档只做纯预检。没有连接 Neon、操作 R2、修改 .env 或迁移/导入生产。

```powershell
# 专用本机测试库，禁止替换为正式 DATABASE_URL
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.venv/Scripts/python.exe manage.py test recommendations --settings=config.recommendation_pg_test_settings --noinput
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_pg_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
```

部署时先运行已有正常迁移流程，使 `0013` 可用；保持系列开关关闭。后续确认目标环境后单独预检/导入关系，再按 12C/12F 阶段接入与灰度。关闭系列读取开关即可停止该接口，不需删除表、历史断言或私人记录；本轮不执行逆迁移或删除数据。

本阶段前端未改，未重新运行无关的 React 构建。

## 验证结果

- 完整推荐模块：1099 项，1098 通过、1 项跳过。跳过为原有真实来源测试，要求额外样本路径和专用设置；不宣称用合成数据替代其验收。
- 新增 12B 的 27 项纯来源/PostgreSQL 测试均通过；最终定向重跑也包含真实 ZIP 格式 → 管理命令 preview/apply → 关系表 → 范围读取的合成闭环。12A 的 16 项合同测试在全量中一并通过。
- 新增测试覆盖同快照并发导入、人工修订竞争、事务回滚、预检后源文件被替换、未知/跨媒介/循环、失败和空结果区别、clear 后再同步、用户/来源/范围上下文绑定、响应中途失效、NSFW 实时审核及双开关、后台权限和禁止直接编辑/删除。
- 230 个合成目标的读取明确截取 200 项并标记 truncated，SQL 不超过 28；没有逐目标查询或私人字段读写。此为本机边界测试，不是生产并发或 p95 指标。
- Django check、无新增未生成迁移检查、Git 差异检查通过。
- 首次全量中的 12 项跨语言失败来自 Node 在文件沙箱下读取用户目录的 EPERM；改用允许本机运行时访问工作区的执行权限后，全量通过，没有绕过或移除这些测试。

本地原始日志：`.tools/12b/full-final.log`、`targeted-final.log`、`pure-final.log`、`real-preflight-final.log`。它们不进入 Git；交付代码、合成测试和本文随提交保存。
