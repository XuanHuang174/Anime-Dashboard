# 扩展 12F：工程验收与线上放行操作

日期：2026-10-26。12A–12E 功能与本轮发布工具已完成本地验收。**正式关系覆盖、Render 性能、XHcode 灰度及全量发布尚未执行。** 本文不能替代真实部署报告。本轮没有读取正式手账、导入生产关系、修改 .env/Render 或切换线上开关。

## 本轮改动

- 增加独立 `RECOMMENDATION_SERIES_CANARY_USERS`，覆盖系列摘要/详情、本人记录引用、想看写入、关联标识、单番相似分区和续作提醒。名单外账号继续使用原来的三种推荐。错误格式关闭系列能力，不会退化成全量。
- 读取结束前再次检查灰度资格；名单撤回后旧读写上下文停止使用。前端识别 `503/series_disabled`，撤下关系标识、停止旧分区操作；不会误判成 NSFW 授权丢失。普通短暂 503 与明确撤回区别处理。
- 新增只读覆盖/部署审计命令、真实前端合同验证、线上 GET 验收脚本、故障/回退测试和本机大目录性能测试。未新增迁移。

## Render 操作顺序

先部署后端与前端，并沿用正常迁移流程：

```bash
python manage.py migrate
python manage.py showmigrations recommendations
```

确认 `0013_series_relations` 和 `0014_series_reminders` 已应用。暂时保持三个系列功能开关 False；不删除表、回滚迁移或修改原 NSFW 审核与开关。

在目标 Render Shell 先生成真实覆盖报告，输出放在非公开临时目录，并使用新的文件名：

```bash
python manage.py audit_series_release --stage closed --output /tmp/series-before.json
```

报告缺少资料时，命令会以非零退出，但仍保留 `status=blocked` 的诊断报告。**没有正式报告就没有可确认的生产覆盖率。** 不要把本机合成报告复制成生产验收。

审计包括：

- 目录按 safe/adult/unknown 分别统计，分母是该分级目录 ID 数量，不是已导入节点数。
- `not_loaded`、`empty_observed`、`observed`、`partial`、`stale` 和最近失败分别统计。空观察代表来源已检查，不证明独立作品；45 天内、分级/媒介一致、完整观察才计入 fresh complete。
- 默认要求 safe 目录 fresh complete 至少 90%，且非空；分母为空不会得到 100%。成人审核及用户访问资格另行验证，不会由系列覆盖报告代替。
- 原始待复核、未知种类、跨媒介/分级不一致/目录外关系分开统计。原始断言种类计数不是人工覆盖后的有效图，也不是完整系列覆盖率。
- 检查迁移、读取上限及当前配置；审计前后核对数据快照与代码，变化则阻断。单次最多 5 万目录、20 万节点/来源、50 万相关断言/最新人工决定，超限不出具部分成功报告。

若缺少关系，报告给出 `next_safe_seed_ids`，最多 100 个需获取或更新的公开 ID。可结合 12B 的官方本地 Archive 导入器逐批处理。归档与 SHA256 须先按官方 manifest 核对；以下为占位示例，不能原样运行：

```bash
python manage.py sync_series_archive --input /tmp/dump-YYYY-MM-DD.HHMMSSZ.zip --expected-sha256 OFFICIAL_SHA256 --ids ID1,ID2
python manage.py sync_series_archive --input /tmp/dump-YYYY-MM-DD.HHMMSSZ.zip --expected-sha256 OFFICIAL_SHA256 --ids ID1,ID2 --apply
```

先查看预检结果，再执行对应的 `--apply`。每批完成后用新文件名重做审计，直到覆盖与资料问题已处理。不要为通过审计而随意下调阈值。这里提供操作流程，本轮没有替操作者下载、同步生产归档或人工核验异常。

覆盖准备好后，Render 灰度配置：

```text
RECOMMENDATION_SERIES_ENABLED=True
RECOMMENDATION_SERIES_EXPLORATION_ENABLED=True
RECOMMENDATION_SERIES_REMINDERS_ENABLED=True
RECOMMENDATION_SERIES_CANARY_USERS=XHcode
```

**原 `RECOMMENDATION_EXPLORATION_ENABLED=True` 保持不变，原 `RECOMMENDATION_EXPLORATION_CANARY_USERS` 保持空值。不要把 XHcode 填进原探索名单。** 否则会把已全量开放的单番相似/按标签找收回成灰度。V1/V2 排序、NSFW 配置与审核记录均沿用当前部署。

环境变量生效后：

```bash
python manage.py audit_series_release --stage canary --output /tmp/series-canary.json
```

无问题时只得到 `ready_for_operator_verification`，不是自动放行。这个命令不校验目标账号是否存在，也不包含浏览器或线上性能验收。

## 线上只读验收

在包含当前源码、Node 和 Python requests 的受信环境运行：

```bash
python scripts/verify_series_deployment.py --deployment-confirmed --expect enabled --expected-account XHcode --seed-bangumi-id PUBLIC_SEED_ID
```

`PUBLIC_SEED_ID` 必须由操作者选定：本站已收录、当前 safe 范围可见、来源近期完整、且有明确直接关联的作品。脚本只访问仓库原先使用的固定后端 `https://anime-dashboard-api-dv0s.onrender.com`，不允许任意目标或重定向。

支持已有 `SERIES_AUDIT_TOKEN` 环境变量；未设置则正常提示输入该账号密码（隐藏输入），只调用普通登录。不要把密码/token 写进命令参数、报告或聊天。脚本在创建网络客户端前要求确认目标、指定账号及必要 seed；随后核对服务端账号精确匹配。

脚本仅做固定 GET 与可选登录 POST，禁止加入想看、隐藏、导入、设置和开关写入。检查私有禁止缓存、三种旧推荐仍可用、能力声明、系列资料、分区上下文/分页、批量关系标识与提醒的真实 JavaScript 合同，并比较偏好/隐藏反馈前后未变。默认每次 HTTP 响应预算 10 秒，超预算失败；`--max-response-seconds` 可明确指定业务预算（0–45 秒）。它不是并发负载测试，也不能测服务器 CPU/SQL/内存。

NSFW 是额外、明确的范围验收，可以加入 `--adult-seed-id REVIEWED_ADULT_SEED_ID`；来源和目标仍须通过当前审核。安全范围验收不等于 NSFW 验收，只有显式加了该参数的报告才包含这部分。

再用**本人明确授权的另一个名单外账号**验证灰度隔离：

```bash
python scripts/verify_series_deployment.py --deployment-confirmed --expect excluded --expected-account AUTHORIZED_NONCANARY_ACCOUNT --seed-bangumi-id PUBLIC_SEED_ID
```

报告保存在执行机器 `.tools/deployment-12f/`；只含检查名、状态与耗时，不落盘密码、token、响应正文、观看状态或签名上下文。`--expect public` 仅检查未登录访问受保护，结果明确标为 `passed_public_baseline_only`，不能当作账号或功能验收。

浏览器补充验收：XHcode 的预览详情关系页、编辑窗口不出现探索入口、三种模式关系标识、分区分页与返回、NSFW 往返、提醒隐藏/恢复/收起及加入想看后移除；另一个已授权普通账号维持原三种推荐。工具不执行真实账号写入，写入类界面验收由操作者明确进行。

确认覆盖、账号读取和浏览器验收后，将 **`RECOMMENDATION_SERIES_CANARY_USERS` 置空**；三个系列开关保持 True。再次生成 `--stage full` 的覆盖/配置报告，再对原灰度与普通已授权账号分别运行 `--expect enabled`。真实记录、关系覆盖、实际耗时与监控均稳定后才算全量验收完成。

## 回退

- 只停提醒：`RECOMMENDATION_SERIES_REMINDERS_ENABLED=False`。
- 停分区/标识但保留关系详情：`RECOMMENDATION_SERIES_EXPLORATION_ENABLED=False`；提醒依赖此策略，也会停止。刷新后单番相似回到原接口，旧分区不能混用游标。
- 停止全部系列：三个系列开关均设 False，刷新页面。原三个推荐入口保持开放；保留表、历史关系、隐藏/收起及本人记录。
- 全量收回灰度：系列名单恢复 `XHcode`。名单外旧系列上下文失效；不要修改原探索名单。

全部系列关闭、或针对名单外账号收回灰度后，使用 `--expect excluded` 验证。只停提醒/分区属于部分回退，应分项检查实际关闭的接口及仍开放的关系页，不能用“全部 excluded”代替部分回退验收。恢复使用时重新检查当前数据与配置。现有页面探测到明确撤回后会撤下旧关系与操作；正在浏览的分区需刷新重新协商，不能静默把新旧排序混合。NSFW 选择不会因为系列撤回而重置。

## 本地证据

完整推荐模块 1147 项：1146 通过、1 项原有真实样本/配置跳过。最终线上工具/覆盖审计定向回归 12 项通过。前端 368 项通过、生产构建及 Django/迁移检查通过；现有主包大于 500 kB 的提示保留。

性能环境：本机独立 PostgreSQL，5 万合成目录、1000 条直接关系、100 条本人记录。每接口 1 次首次读取、3 次后续读取；启用 Python 内存追踪，CPU/内存均不含 PostgreSQL 服务端。不代表生产 p95、并发负载或真实个性化画像排序基准。

| 接口 | 首次秒数 | 后续中位秒数 | 最大 SQL 查询数 |
| --- | ---: | ---: | ---: |
| 系列详情 | 1.799 | 1.133 | 24 |
| 24 部批量标识 | 0.994 | 0.998 | 25 |
| 单番关联分区 | 9.083 | 3.963 | 33 |
| 后续提醒 | 1.682 | 1.842 | 47 |
| 原单番相似 | 3.317 | 3.387 | 20 |
| 原标签探索 | 3.624 | 3.805 | 19 |

首次分区请求包含公共索引构建；接口顺序执行，不能把两组首次时间当作独立 A/B 对照。最高 Python 追踪分配约 88.7 MiB，低于 128 MiB 本机预算；全目录覆盖读取约 0.258 秒。带追踪的单次耗时预算 15 秒、查询上限 65；实际 Render 冷启动/资源配置仍须测量，不因本机预算通过而放宽线上要求。

浏览器合成回退验证：关系标识从 12 个清至 0，原 12 张推荐卡保留，重新读取后详情操作可用，`包含 NSFW` 筛选保留。所有夹具只用合成目录和内存写入，没有操作真实用户。
