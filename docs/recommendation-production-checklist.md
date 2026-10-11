# 推荐库正式入库与封面托管验收清单

## 当前约定

- 正式库是本地 `.env` 中的 Neon 数据库；不可把普通 Django 命令当成本地测试。
- R2 桶：`xh-anime-media`；公开域名：`media.xh-anime.com`。
- 成人标记 `nsfw=True`：保留名称、评分、标签、简介等数据，公开字段 `cover_url` 留空；不下载、不上传封面。原始来源 URL 仅存于内部 `source_cover_url`，后续接口不可将其作为公开图片回退。
- 标记未知 `nsfw=None`：转存任务也跳过，待核验为明确非成人后再处理。来源标记并非完整内容审核，不能据此保证所有未标记图片均适合公开展示。
- 明确非成人 `nsfw=False`：才允许下载与 R2 发布；上传前在数据库行锁内再次检查标记，防止排队期间分类变化。
- 不删除历史番剧，不删除已存在的 R2 对象；重分类为成人时仅清空数据库公开链接，已公开历史对象的处理需单独审批。

## 1. 等待并验收官方补齐输出（可由助手执行）

当前任务进度：`data/recommendations/enrichment_progress.json`。
最终输出：`data/recommendations/anime_enriched.jsonl`。
最终报告：`data/recommendations/enrichment_report.json`。

只有任务已退出、进度为 `completed` 或 `completed_partial`、最终文件及报告均存在时，才能按完成批次验收。`completed_partial` 表示全部条目已处理，但部分仍缺封面，不表示所有图片补齐成功。失败条目保留文字数据，不能为了齐图绕过官方访问限制。

不要导入隐藏 `.tmp` 文件，也不要另起全量请求覆盖正在运行的任务。验收总数、唯一 ID、成人数量、缺图与失败原因，以及重复导入的幂等性。

以下仅连接 SQLite 内存库，不连接 Neon，也不上传 R2：

```powershell
.venv/Scripts/python.exe manage.py import_anime data/recommendations/anime_enriched.jsonl --dry-run --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
```

## 2. 正式库保护点（需用户确认）

在 Neon 控制台确认当前恢复窗口，或建立可恢复的保护分支/备份，并记录对应时间点。确认保护措施可恢复后再允许正式小批入库；不要把代码提交、空推荐表或本地 JSON 当作正式库备份。

截至 2026-10-11 的只读核验：推荐表存在，`0001_initial`、`0002_recommendationanime_nsfw` 已登记，总记录数 0。这里只描述检查结果，不表示助手在本轮执行过生产迁移。

助手不需要 Neon 控制台密码或 R2 密钥明文；不要把它们粘贴到聊天或提交 Git。

## 3. R2 配置与网络验收

沿用项目已有的五项 `AWS_*` 配置，凭证限定目标桶。S3 接口使用 R2 endpoint，不使用公开图片域名上传。

只读实测：`HeadBucket` 200、`covers/` 前缀列表 200，未发现对象；CORS 管理接口 403。现有凭证可访问对象但无权读取该管理配置，不修改/扩大权限，也不因此断言 CORS 正确。用户可在 Cloudflare 控制台确认允许主站 `https://xh-anime.com` 的 GET/HEAD，保留已有规则。

本地封面转存显式支持 `--system-proxy` 或 `--proxy http://127.0.0.1:7890`，二选一；图片下载和 R2 SDK 使用同一个可信传输代理。不读隐式 HTTPX 代理，不改变源站，不关闭证书验证。下载与每次重定向仍执行 HTTPS/域名白名单及公网 DNS 检查，遇到代理 fake-IP 非公网解析应修正代理 DNS 配置，不能关闭安全校验。

2026-10-11：3 张明确非成人的真实 Bangumi 封面通过系统代理下载、解码并转换 JPEG；仅在内存处理，未上传、未修改正式库。

## 4. 正式小批试运行（须先满足上面的数据与备份条件）

由助手从验收后的最终文件生成并校验 20 条样本（包含成人条目验证文字保留，但上传数量另按非成人计算）。确认命令的数据库目标与桶目标后再执行。以下普通配置命令会访问正式库，**不能现在直接照抄执行**：

```powershell
.venv/Scripts/python.exe manage.py import_anime data/recommendations/production_pilot.jsonl --batch-size 20
.venv/Scripts/python.exe manage.py sync_recommendation_covers --dry-run --limit 20 --system-proxy
.venv/Scripts/python.exe manage.py sync_recommendation_covers --limit 20 --concurrency 2 --rate-limit 1 --system-proxy
```

`--dry-run` 不下载、不上传、不更新，但仍读取正式推荐表。排除的成人/未知条目不占 `--limit`。
核验固定对象键 `covers/{bangumi_id}.jpg`、JPEG MIME、公开 URL、浏览器跨域响应、成人公开链接为空、私人手账没有变化，以及重跑不重复上传已托管图片。

若出现权限失败、对象键冲突、来源/数据库异常或资源预算问题，停止扩大批次，不自动提高权限/绕过保护。现有上传动作会覆盖同名对象，因此全量前需要确认 `covers/` 命名空间专属用途。

## 5. 全量与增量

小批通过后再导入全量最终文件（批次 500），然后单独运行有界并发的转存任务。文字入库与图片托管是两项独立验收，不能把 API 获取 URL、图片下载或 R2 凭证核验当作全量托管完成。

增量仍以 `bangumi_id` 更新；已托管且源地址未变的非成人封面保留永久链接。成人文字继续更新，公开封面字段保持空值；新的成人条目不会被转存。未能补齐的非成人封面保留来源或空值，后续公开 UI 必须支持占位图。

参考：[Neon 分支](https://neon.com/docs/manage/branches)、[Cloudflare S3 凭证](https://developers.cloudflare.com/r2/get-started/s3/)、[R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/)。

## 2026-10-11 最终验收与正式小批实测

本节是后续执行记录，上文“尚未上传/等待确认”均为前期检查历史，不代表当前状态。

- 用户已确认保护分支 `2026-10-11 backup`，父分支为 `production`；未执行恢复或修改备份分支。
- 主站及两个本地开发来源的公开图片请求均实测 200，并返回对应 `Access-Control-Allow-Origin`；OPTIONS 204。管理配置接口 403 不影响对象域名 CORS 验证，不扩大凭证权限。
- 全量补齐于北京时间 13:28:17 结束，状态 `completed_partial`：全部 31104 条处理完毕，但并非全部齐图。
- 最终记录 ID 与源文件集合完全相同，无遗漏、无重复；31104 条均通过 SQLite 内存库下的 Django 字段校验。
- 非成人 28636 条，其中有封面 URL 27171 条、缺图 1465 条；成人标记 2468 条，其中 7 条有来源 URL，但均不得托管。总缺图 3926 条。
- API 失败 2461 次，完整日志中均为 `missing_or_restricted`；保留数据、不绕过官方访问控制。
- 最终文件 SHA256：`7de81d27bc2a420917e3eb695cdb4e8c41b12160ea1480d053aa26b31220437f`。

新增独立本地验收工具，无 Django/网络/数据库/R2 操作：

```powershell
.venv/Scripts/python.exe -m recommendations.audit_catalog --source data/recommendations/anime_utf8.jsonl --enriched data/recommendations/anime_enriched.jsonl --enrichment-report data/recommendations/enrichment_report.json --report data/recommendations/final_audit_20261011.json --pilot data/recommendations/production_pilot_20261011.jsonl --log data/recommendations/full_enrichment_20261011_utf8.stdout.log
```

工具拒绝路径冲突、ID 丢失/重复、评分异常、成人标记降级、提前停止的报告及统计不一致；已有输出默认不覆盖，重生成须明确 `--overwrite`。它不负责迁移、入库或转存。

正式试运行已完成（不再是仅模拟）：

1. 从最终文件选择 20 条：17 条非成人有图、1 条非成人缺图、1 条成人有来源 URL、1 条成人缺图。
2. 正式推荐表从 0 条增至 20 条；成人 2 条文字保留，公开 `cover_url` 均为空；非成人缺图 1 条保留。
3. 转存前 `covers/` 命名空间为空；17 个候选 DNS 校验通过，17 张均成功上传，无失败。
4. 对全部 17 张实测公开 GET、JPEG 解码、文件大小上限、S3 SHA256 元数据、URL 版本与主站 CORS，全部通过。R2 精确对象集合为这 17 个非成人对象，无成人对象。
5. 重复导入显示 `updated: 20`、无新增；重跑转存显示 `already_hosted: 17`，无重复上传。
6. 私人手账相关表在小批前后计数一致；本次入库/转存代码只写独立推荐表。
7. 后端 249 项测试通过，包含真实小批样本的隔离导入及重复导入；Django 检查及迁移一致性检查通过。

Neon 池化连接不接受通过 `PGOPTIONS` 注入 `statement_timeout` 等启动参数。首次连接因此在写入前失败，未产生数据改动；去除进程内参数后正常执行，不修改 `.env`。如需超时保护，应在事务内使用 `SET LOCAL`，而不是向池化启动包添加不支持的参数。

完整源数据、20 条样本和验收 JSON 都位于被 Git 忽略的 `data/recommendations/`。已生成 `final_audit_20261011.json` 与 `production_pilot_verification_20261011.json`，不提交数据文件或密钥。**当前正式库仅有小批 20 条，R2 仅有推荐封面 17 张；尚未执行全量入库/全量图片转存。**
