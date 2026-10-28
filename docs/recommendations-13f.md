# 扩展 13F：发布审计、性能检查与上线流程

日期：2026-10-28。承接 [13E](recommendations-13e.md)。本轮完成发布工具、真实生产目录的只读归档预检和隔离验证；**没有导入生产参与资料、修改 Render 开关或完成全量放行**。工具验收与功能上线是两项独立结论。

## 当前真实状态

通过配置的 PostgreSQL 连接，在数据库强制只读事务中确认 `0016_credits_foundation` 已应用。公共目录为 31,104 部：普通 28,636、成人 2,468、未知 0；CreditsHead 为 0，因此仍缺实际落库资料。本轮没有读取生产私人手账。

随后用 `--preview-only --catalog` 完整读取已核验官方归档，生成绑定当前生产目录身份／分级的预检计划。显式只读模式与 `--apply` 互斥，额外 SQL 守卫拒绝 DML、DDL、COPY 和可写 CTE；导入功能没有在预检中执行。

| 目录范围 | 制作公司完整来源观察 | 有动画制作关系作品 | 原声完整来源观察 | 有原声出演关系作品 |
| --- | --- | --- | --- | --- |
| 普通 28,636 部 | 26,586（92.841%） | 13,652（47.674%） | 28,517（99.584%） | 9,431（32.934%） |
| 成人 2,468 部 | 2,329（94.368%） | 801（32.455%） | 2,468（100%） | 914（37.034%） |

这是**导入前的来源预检**，不是数据库已覆盖、作品已审核或用户已获访问资格。未映射的实体类型、缺失角色等仍隔离，不为达到阈值伪造／改写关联。来源归档为 `dump-2026-10-20.210332Z`，计划指纹为 `614987424cd51babdff7a51cd85eeccb00fe34f1f7d30f277d8070b9510efd5f`。摘要见 [13F 预检记录](recommendations-13f-preflight.json)，完整本机报告在 `.tools/13f/production-preview.json`。

## 冻结的独立放行门槛

版本为 `credits.release-policy.13f.v1`，在生产导入前固定，不能用 CLI 参数调低：

| 条件 | 制作公司 | 声优 |
| --- | --- | --- |
| 45 天内、非未来、映射／别名版本兼容且身份／分级一致的完整来源观察 | 普通和成人目录各 ≥90% | 普通和成人目录各 ≥98% |
| 普通目录实际有关联的作品 | ≥35% 且非零 | ≥25% 且非零 |
| 成人目录实际有关联的作品 | ≥20% 且非零 | ≥25% 且非零 |

普通目录不能为空；某范围分母为零时只记不适用，比例不能伪报为 100%。未知分级单独报告，不作为展示资格或放行分母。以上数值以 13B 的全目录预检分布为依据，实际关系覆盖独立于完整空观察，避免空观察使功能看似就绪。

两类资料分别检查最大实体作品量和最坏 12 部作品的参与事实数，遵守既有 50,000／10,000 读取上限；当前指针、人物／角色快照、原声类型／动画制作职务错误会阻止发布。审计前后重新读取资料和目录，并核对配置及代码修订。原有三个探索模式必须维持全量开放。

## 新增工具

```powershell
# 只读；在项目根目录运行。报告路径必须是尚不存在的新文件。
.\.venv\Scripts\python.exe manage.py audit_credits_release --stage closed --output .tools/13f/NEW-release.json
.\.venv\Scripts\python.exe manage.py profile_credits_release --output .tools/13f/NEW-performance.json
```

`audit_credits_release` 支持 `--stage closed|canary|full`、`--kinds both|studio|voice_actor`。检查实际调用进程的配置，不将本机 `.env` 当成 Render 已加载配置的证明；不修改开关、不模拟用户授权、不扫描私人记录。即使返回 ready，也只代表可进行操作者验收，不代表浏览器或正式环境已通过。

本轮导入前报告另列 `existing_exploration_disabled`，来自本机 Django 的默认配置，不是 Render 已关闭旧功能的证据；不要为匹配本机报告去关闭线上原有功能。后续应依据实际部署配置核对调用进程，并以双账号 HTTP 和页面结果复核线上状态。

`profile_credits_release` 每类取最多两位当前 safe 范围关联最多的实体，各读取首批及重复批。测量实际公开目录序列化、参与证据、SQL 数、墙钟、操作者进程 CPU 和进程内存；预算为每次 ≤10 秒／55 SQL。它不读取私人手账，不能代替完整登录态 HTTP 耗时、Render worker 内存、负载或并发测试。没有来源时明确阻塞，不生成假成功基线。

`scripts/verify_credits_deployment.py` 使用固定前后端地址，只允许明确白名单 GET，以及正常登录 `/api/token/` 的 POST；业务 POST／PATCH、任意 URL、重定向和向前端发送凭据均拒绝。密码本机隐藏输入，不保存密码、token、签名上下文、姓名或私人观看状态。单次最多 40 请求，每次固定 ≤10 秒；前端合同由实际 JavaScript 校验器验证。

它检查精确登录账号、两类能力、原三个模式、种子摘要、人物搜索／详情、参与作品、本人已看完筛选、作品详情、稳定续页、偏好／隐藏反馈未改变和能力稳定。一次只检查一个内容范围，safe 与 include_adult 分别执行；成人验收必须实际读到成人详情，不能用空列表当成功。原生窗口、动画、资源检索及真实加入操作还需浏览器验收。

## 发布顺序（不需要付费 Render Shell）

1. **确认生产导入。** 当前只读预检已完成；本次尚未执行写入。确认后沿用原归档导入器，以相同输入重新校验、匹配 plan_fingerprint，再分批写入九个独立 Credits 表。不会修改推荐目录、私人记录、审核或线上开关。中断保留已提交批次，以同一来源重新预检并幂等续跑，不清空表。
2. **导入后只读审计和性能检查。** 执行上面两条命令。只有数据库实际覆盖及容量通过后再开启灰度；预检报告不能替代落库审计。
3. **在 Render 配置灰度并重新部署。** 保留所有既有探索／系列／NSFW 配置，单独添加以下四项。两个开关可只开启已通过审计的一类，对应审计 `--kinds`。

```text
RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED=True
RECOMMENDATION_CREDITS_STUDIO_ENABLED=True
RECOMMENDATION_CREDITS_CANARY_USERS=XHcode
RECOMMENDATION_CREDITS_EPOCH=1
```

4. **Live 后双账号验收。** 默认种子 328609 仅在其实际来源完整时使用，缺资料应先核验来源，不绕过门槛。普通账号由用户自己选择并授权，不在脚本中自动创建或猜测。

```powershell
.\.venv\Scripts\python.exe scripts/verify_credits_deployment.py --deployment-confirmed --expect enabled --expected-account XHcode --seed-bangumi-id 328609 --expected-dist frontend/dist
.\.venv\Scripts\python.exe scripts/verify_credits_deployment.py --deployment-confirmed --expect excluded --expected-account 普通账号用户名 --seed-bangumi-id 328609
# 另外显式检查已审核成人内容；若首批没有实际成人详情，该项会阻塞，须选有效来源／实体复核。
.\.venv\Scripts\python.exe scripts/verify_credits_deployment.py --deployment-confirmed --expect enabled --expected-account XHcode --scope include_adult --seed-bangumi-id 8019
```

`--expected-dist` 必须指向此次已构建目录，核对入口及人物懒加载包的实际字节；缺少构建证据时 `frontend_bundle_verified=false`。单账号通过不能证明普通账号已放行，public 检查通过也不能证明登录态或上线完成。

5. **实际页面验收。** 两账号分别检查入口可见性、原三模式、预览详情摘要、原页返回、窄屏、默认折叠、本人状态／已看筛选、资源抽屉、加入想看、重复提交及内容范围切换。灰度外账号必须仍可使用原三模式。
6. **全量。** 用户确认上述检查通过后，在 Render 清空 `RECOMMENDATION_CREDITS_CANARY_USERS`，保留已放行种类的开关，再部署到 Live；用普通账号重新运行 enabled 验收并实际打开页面。只有这些步骤完成才能记录全量开放。
7. **回退。** 关闭有问题的种类开关；必要时两项均 False，并递增 `RECOMMENDATION_CREDITS_EPOCH` 使旧签名失效。重新部署并执行 excluded 验收。保留资料表、已创建记录及原有三个模式，不回滚迁移或删除数据。

### 导入中断后续跑

保留原 apply 报告，继续使用同一归档、SHA256、`--expected-plan`、目录和批次大小；给命令增加 `--resume-from 原报告路径`，`--report` 指向新的文件。该参数只能与显式 `--apply` 使用，不是直接信任本机报告跳过写入：命令重新构建原计划，并在强制只读快照中核验已提交前缀的来源快照、观察指纹／覆盖状态／诊断、发布指针和修订号。缺失、变化或计划不一致时停止，不写后续批次。首个未记录批次仍经过原有幂等发布流程，因此提交回执丢失不会重复创建关系。

2026-10-28 首次生产导入已记录 707 批，随后因 `OperationalError` 停止；恢复前只读确认连接正常，修订号为 707，观察和指针均为 35,350 条。续跑报告保留已验证的历史批次并记录其摘要，后续批号沿用原计划，便于核对完整导入数量。

## 当前验证与限制

- 后端完整定向回归 111 项通过，包括来源合同／归档／导入、人物 HTTP 和手账闭环、新发布／部署脚本、原系列发布及成人内容范围。前端 400 项、生产构建、Django check 和迁移漂移检查通过；构建保留既有主包超过 500 kB 的提示。
- 5 万目录／3 万事实的 HTTP 基线约 1.27–1.58 秒、35 SQL／请求；八部真实来源的本机公共查询约 0.019–0.050 秒、9 SQL／请求。两者都不能替代正式环境登录态性能。
- 生产只读：迁移 0016 已应用、公共目录分母已确认、完整归档预检完成且 database_writes=false。当前资料表为空，发布审计应阻塞。
- 四个生产受保护 GET 均返回 401。包含部署包核对的一次检查因前端域名连接失败而未完成；没有据此声称前端已通过。随后仅接口保护基线检查通过，结果为 `passed_public_baseline_only`。
- 八部真实来源在本机隔离库完成公共查询测量；它是小样本，不能作为生产全目录性能结论。较大体量由既有 5 万目录／3 万事实隔离回归提供基线，实际生产性能留待导入后测量。
- 本轮不向聊天索取密码或 token，不使用伪造登录绕过账号验收。真实登录态、生产导入、Render 灰度／全量、真实写入及最终浏览器验收均尚未完成。
