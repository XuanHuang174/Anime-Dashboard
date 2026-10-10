# 个性化番剧推荐：阶段一入库与 R2 封面托管

本阶段只新增独立 Django app `recommendations`。不修改 `Anime`、私人手账记录、Bangumi 收藏导入接口或前端页面，也不在启动时自动导入/下载。本阶段交付的是数据管线，不包含推荐算法、推荐 API 或新的推荐页面。

> 当前本地 `.env` 指向 Neon 正式库。以下普通 Django 导入/迁移/R2 命令均属于后续生产操作，需要确认备份和授权，**本次不执行**。官方真实数据准备与安全验证请先使用 [官方数据接入指南](recommendation-data-sources.md)，其中的独立 CLI 不读取 `.env`，Django 测试强制使用内存库。

## 1. 文件与模型

- `recommendations/models.py`：`RecommendationAnime` 独立推荐库。
- `recommendations/migrations/0001_initial.py`：只创建推荐库表、字段、索引和检查约束，不依赖/重写现有业务表。
- `recommendations/ingestion.py`：字段清洗和原子幂等更新。
- `recommendations/management/commands/import_anime.py`：本地 JSON / JSONL 导入。
- `recommendations/covers.py`：异步限流下载、JPEG 转码、R2 上传与数据库更新。
- `recommendations/management/commands/sync_recommendation_covers.py`：独立后台任务入口。
- `recommendations/admin.py`：后台只读查看推荐库。
- `recommendations/tests/`：完全使用临时数据和模拟网络/R2 的自动化测试。

模型保留所要求的 `bangumi_id`、`title_zh`、`title_jp`、`score`、`tags`、`cover_url`、`season`、`status_type`、`synopsis`，并增加 `source_cover_url`、`created_at`、`updated_at`。`source_cover_url` 是增量同步的来源锚点：同一来源重新导入时保留已托管直链；来源变化时重新排队托管。`status_type` 是作品类型，不是“在看/看过”等用户状态。`score` 是 0–10 的源站综合评分，不是用户个人评分或推荐排序分。

官方接入增加了可空、带索引的 `nsfw` 成人内容标记（迁移 `0002`）。默认导入保留成人条目；未知标记保持 `null`，不自动当作安全内容。后续公开推荐展示应单独设计用户选择与内容分级。

`bangumi_id` 为可空的唯一 IntegerField（满足建模要求），但导入命令只接受非空正整数 ID。缺失 ID 的数据必须先补齐来源映射，不能通过 `update_or_create(bangumi_id=None)` 合并，也不能靠标题猜测身份。正数 ID 和评分范围有数据库检查约束。

## 2. 先安装依赖与执行迁移

在项目根目录运行（Windows PowerShell）：

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py migrate recommendations
```

新增加的依赖仅 `httpx==0.28.1`，已有 `boto3` 和 `Pillow` 继续复用。若本地 pip 镜像发生 TLS 问题，可使用官方 PyPI，不要关闭证书校验：

```powershell
.venv/Scripts/python.exe -m pip install httpx==0.28.1 --index-url https://pypi.org/simple
```

迁移前确认实际 `DATABASE_URL` 指向预期数据库，并按现有运维流程备份。上述迁移由管理员部署时执行；开发验证只使用测试数据库，不会替你向生产库导入数据或上传 R2。

## 3. 准备可信的本地源数据

真实数据采用 Bangumi 官方 Archive + 官方 API 补齐，执行方法见 [官方数据接入指南](recommendation-data-sources.md)。数据不捆绑进 Git，不要提交私密文件、封面缓存或大型数据集；存放在已忽略的 `data/recommendations/`。

标准 JSON 示例（ID/标题仅作格式说明）：

```json
[
  {
    "bangumi_id": 12345,
    "title_zh": "示例番剧",
    "title_jp": "サンプル",
    "score": "8.6",
    "tags": ["百合", "日常"],
    "cover_url": "https://lain.bgm.tv/pic/cover/l/example.jpg",
    "season": "2026-10",
    "status_type": "TV",
    "synopsis": "一段简介。"
  }
]
```

支持顶层数组、包含 `data/items/results/anime` 数组的对象；`.jsonl` / `.ndjson` 文件则每行一个对象，按行流式读取，更适合内存有限的 Render 任务。普通 JSON 文件上限 64 MiB（读取后 Python 对象仍会增加内存占用）；JSONL 单行上限 2 Mi 字符、简介上限 200000 字符。无法解析的普通 JSON 整体停止；JSONL 的非法行可以单条隔离并继续处理。

### 字段映射与清洗

| 标准字段 | 可兼容来源 | 处理规则 |
| --- | --- | --- |
| bangumi_id | bgm_id / subject_id | 必须为真正的 Bangumi 正整数 ID，不接受 bool、浮点 ID、空 ID |
| title_zh | name_cn / title_cn | 新记录缺失时回退原名，再回退“未命名番剧 ID” |
| title_jp | name_jp / name | 缺失的新记录为空字符串 |
| score | rating（数值或含 score 的对象） | 非法、空、NaN/Infinity、超出 0–10 统一为 0.0 |
| tags | 字符串、字符串列表、含 name 的对象列表 | 中/英文逗号规范为英文逗号，去空、去重且保留顺序 |
| cover_url | image_url / cover / images | images 对象优先 large/common/medium/small；空值表示清空 |
| season | date / air_date | 日期归到 01/04/07/10 季度；支持 WINTER/SPRING/SUMMER/FALL + seasonYear |
| status_type | format | MOVIE → 剧场版，TV_SHORT → TV短篇，其余保留；不把用户收藏状态当类型 |
| synopsis | summary / description | 去 HTML 标签并解码实体；展示层仍须正常文本转义 |

同一字段存在多个别名时，以表格中标准名为最高优先级。文本字段超长/URL 非法会跳过记录并报告，不静默截断。

**重要：参考项目的通用 `id` 不一定是 Bangumi ID（可能是 AniList/MAL）。默认刻意不把 `id` 当作 `bangumi_id`。** 若源文件中的 `id` 确实来自 Bangumi，可以明确传 `--id-field id`；否则先建立跨站 ID 映射。本阶段不联网猜测或自动匹配片名。AniList 原始 0–100 分也需要先除以 10，不能直接传给本管线的 `score`。

## 4. 全量 / 增量导入

先校验，不写数据：

```powershell
.venv/Scripts/python.exe manage.py import_anime "data/recommendations/anime.json" --dry-run
```

确认后实际导入：

```powershell
.venv/Scripts/python.exe manage.py import_anime "data/recommendations/anime.json" --batch-size 500
```

后续同样命令导入新全量文件或只含变更记录的增量文件：

```powershell
.venv/Scripts/python.exe manage.py import_anime "data/recommendations/anime_delta.jsonl"
```

- 每批默认 500 条，有效记录通过 `update_or_create(bangumi_id=...)` 更新/新增；一条坏数据不会回滚同批有效记录。
- **增量文件没出现的字段保留旧值**；显式空值会清空为字段默认值（标题保留合理回退值）。新记录缺失字段使用模型默认值。
- 同文件重复 ID 后面的记录覆盖其明确提供的字段，日志显示 `duplicate_ids`；不会插入重复记录。
- 不删除“本轮文件没出现”的旧番剧，不改动手账，不进行封面网络请求。
- 每批独立事务；数据库故障停止并回滚当前批次，之前成功批次保留，可重跑恢复。
- 日志提供 processed/created/updated/skipped/duplicate_ids 等统计。坏行原因最多输出前 20 条，避免刷屏或泄漏源 URL 查询参数。
- 默认存在跳过项时最终退出码非零，即便有效数据已成功提交，便于 Cron/CI 发现数据质量问题。只有明确接受坏行时再加 `--skip-invalid`。
- `--dry-run` 仅清洗和校验源文件，不估算真实数据库新增/更新数量，也不上传封面。

## 5. R2 环境配置

复用项目现有服务器端环境变量，不新增前端凭据：

```dotenv
AWS_ACCESS_KEY_ID=你的R2访问密钥ID
AWS_SECRET_ACCESS_KEY=你的R2访问密钥
AWS_STORAGE_BUCKET_NAME=你的存储桶名称
AWS_S3_ENDPOINT_URL=https://你的ACCOUNT_ID.r2.cloudflarestorage.com
AWS_S3_CUSTOM_DOMAIN=media.xh-anime.com
RECOMMENDATION_COVER_ALLOWED_HOSTS=lain.bgm.tv,lain.bangumi.tv,lain.bangumi.lol,storage.moegirl.org.cn,s4.anilist.co
```

只放入本地 `.env` 或 Render 后端/后台任务服务的环境配置，不能放入 `VITE_*`、Git 或日志。R2 Token 只授予目标桶所需的对象权限，不使用 Cloudflare 全局 API Key。上传使用 S3 endpoint，而 `media.xh-anime.com` 是公开访问域名，不能把两者混用。自定义域名须已经绑定到正确桶并能公开访问；没有公开域名时任务会拒绝生成会过期的签名直链。

对象键固定为 `covers/{bangumi_id}.jpg`，此命名空间应专供推荐库。直接调用 boto3 `put_object`，不经过项目会自动改名的文件存储接口，不设置公共 ACL、不修改桶权限、不删除原手账媒体。

只有准确命中配置自定义域名的 HTTPS 地址才视为已经托管。外部图片域名必须在精确白名单中，重定向的 CDN 域名也须经过人工核实后加入；不是使用通配符允许任意地址。代码会拒绝非 HTTPS、账号密码、非 443 端口、IP 字面量和解析到内网的目标，逐跳校验最多 3 次重定向，禁用环境 HTTP 代理。白名单仍须只包含可信源；DNS 预检不是网络级沙箱，生产建议通过出口规则继续阻止私网访问，避免可信域名被接管或 DNS 变化带来的风险。

本阶段无需修改 CORS 就能服务端转存；未来浏览器 Canvas 读取 R2 图片时仍须按原项目配置 CORS 并使用 `crossOrigin="anonymous"`，不要为此开放所有源。

## 6. 先小批试运行，再异步全量转存

校验前 20 条候选 URL/DNS（有 DNS 查询，但不下载、不上传、不写 DB）：

```powershell
.venv/Scripts/python.exe manage.py sync_recommendation_covers --dry-run --limit 20
```

先实际转存 20 张并验证 R2 直链：

```powershell
.venv/Scripts/python.exe manage.py sync_recommendation_covers --limit 20 --concurrency 2 --rate-limit 2
```

确认日志、实际图片和数据库字段正确，再处理全部待托管外链：

```powershell
.venv/Scripts/python.exe manage.py sync_recommendation_covers --concurrency 2 --rate-limit 2 --retries 3 --batch-size 500
```

参数与保护：

- `--concurrency` 默认 4，上限 8；小内存 Render 实例建议 2。SQLite 自动降为 1，且不能并行运行多个导入/转存进程；正式生产应使用 PostgreSQL。
- `--rate-limit` 默认 2 次/秒，涵盖本进程所有 worker 的下载、重试和重定向请求。多进程没有全局共享限速，因此建议只运行一个封面任务服务；如确需多服务，需按总预算拆分各实例 rate-limit。
- `--retries` 默认额外 3 次，只对临时下载故障/429/5xx/超时进行指数退避加抖动；404、无效图、超限图不反复下载。上传使用 boto3 standard 重试，最多 3 次尝试。失败不改 DB 的原外链，下一轮仍可重试。
- 发现数据库故障、R2 鉴权/存储桶配置错误或 R2 endpoint 无法连接时，停止新增任务（已在运行的任务正常收尾），不对剩下上万张图片反复尝试同一个错误配置。排队但未执行的记录计入 blocked；修复配置后可重跑。
- HTTP 连接超时 5 秒、读超时 10 秒、单次完整下载上限 30 秒；DNS 上限 5 秒。R2 SDK 连接/读超时 5/10 秒；最终发布事务期间使用有上限的 SDK 重试，不使用无超时请求。
- 源文件同时检查 Content-Length 和流式实际字节，上限 8 MiB（覆盖原图超过 3 MB 的常见情况，但仍严格限制内存），拒绝 HTTP 压缩包络；解码限制 1200 万像素。
- 仅 JPEG/PNG/WebP/GIF，GIF/WebP 取首帧、EXIF 旋转修正、透明背景铺白、长边缩到 1600px、JPEG quality 85，输出限制 3 MB。不是将 PNG 改扩展名伪装 JPEG。
- 单个共享 HTTPX 客户端/连接池；最多 concurrency 张图片并行处理，每批最多 batch-size 个任务，不创建 13000 个并发图片下载。Pillow 与 boto3 在线程执行，不阻塞事件循环。
- 上传后才更新 cover_url；来源已变化/行被删除时，旧任务在上传前丢弃。最终 PostgreSQL 行锁涵盖固定键上传和 DB 更新，与导入的锁配合，避免仅做上传后 CAS 仍覆盖新对象的问题。代价是短暂持有该行的数据库事务；不影响普通手账表，但同一番剧导入可能短暂等待。
- R2 上传与数据库不是分布式事务：极端情况下上传成功而 DB 提交失败会留下同名对象；DB 仍保留外链，下次重跑同一键即可收敛，无需危险地批量删除对象。查询参数 `?v=内容哈希` 用于来源变化后的缓存刷新，对象键仍严格是 `covers/ID.jpg`。
- 日志按批次输出 scanned PK、attempted/uploaded/already_hosted/stale/failed/retries 等计数及失败 ID，错误不输出 SDK 原始敏感文本。任何失败最终返回非零，便于监控。成功直链不重复处理，中断后同命令重跑续传；来源未变化但图片内容悄悄替换的情况不做盲目探测，需要在后续版本设计定期刷新策略。

## 7. Render 运行方式

把上述封面命令放在**独立 Background Worker / Cron Job / 一次性管理任务**，不要放到 Gunicorn 启动命令、AppConfig.ready()、Web 请求或 migration 中。任务服务使用同一已迁移的 PostgreSQL 和同一组 R2 配置，安装项目 requirements。Linux 命令示例：

```bash
python manage.py import_anime /path/to/authorized/anime.jsonl --batch-size 500
python manage.py sync_recommendation_covers --concurrency 2 --rate-limit 2 --retries 3
```

文件路径必须是该服务实际可访问的路径。Render 临时文件系统不能当长期数据仓库；一次性任务应在当次运行时准备授权数据文件，持久数据落入 PostgreSQL/R2。不要把重任务与免费 Web 进程并跑后声称“异步不会消耗 CPU/内存”：异步只改善 I/O 等待，转码仍消耗资源，应使用独立任务实例。

首次全量耗时取决于源站、限速、图片大小和失败重试，不作“几分钟完成”的承诺。可分多次 `--limit 500` 执行，每次跳过已托管图片；持续失败的候选会占用 limit，须优先修正日志中的问题。部署及真实源站/R2 smoke test 需要运营方执行，本地单元测试不会接触真实存储桶。

## 8. 验证与回归

在已确认使用安全本地环境的项目根目录：

```powershell
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
.venv/Scripts/python.exe manage.py test recommendations
.venv/Scripts/python.exe manage.py test anime recommendations
```

测试覆盖字段清洗、未知 ID 命名空间、唯一约束、非法行、默认 500 条批次、13001 条模拟全量入库再增量更新、JSONL 超长行、重复导入、缺字段增量、R2 直链保留/来源变化、真实 JPEG 编码、下载大小与像素限制、逐跳 URL/内网检查、限流/重试、上传失败回滚、配置错误停止队列、过期快照、续传、dry-run 与 limit。网络/R2 使用 mocks，数据库使用 Django 临时测试库。PostgreSQL 的跨进程行锁和生产源站连通性仍须在预生产环境验证，不以 SQLite 测试替代该项验收。

官方接口依据：[Django 异步 ORM 与事务](https://docs.djangoproject.com/en/6.0/topics/async/)、[HTTPX 异步客户端与流式响应](https://www.python-httpx.org/async/)、[Cloudflare R2 boto3 示例](https://developers.cloudflare.com/r2/examples/aws/boto3/)。
