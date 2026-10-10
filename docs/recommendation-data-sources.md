# 官方 Bangumi 数据接入与安全执行顺序

## 本次操作边界

- `.env` 的 `DATABASE_URL` 是 **Neon 正式库**，不修改 `.env`，不执行生产迁移/导入。
- 不连接 Neon，不上传 R2，也不更改当前手账或前端模块。
- 数据准备用独立 Python CLI，**不加载 Django 和 `.env`**。SQLite API 缓存是本地文件，和 Neon 无关。
- 默认保留成人内容，保留 `nsfw` 标记；后续展示策略与数据入库策略分开。
- 下载的 ZIP、JSONL、报告、缓存全部放在 Git 忽略的 `data/recommendations/`。需本地至少约 2 GB 空间；只读取 ZIP 中的 `subject.jsonlines`，不解压全归档。

## 1. 数据源与范围

官方来源：

- [bangumi/Archive](https://github.com/bangumi/Archive)：每周更新的公开条目归档。
- [最新版本元信息](https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json)：下载路径、大小、SHA256。
- [平台定义](https://github.com/bangumi/common/blob/master/subject_platforms.yml)：动画平台映射。
- [官方 API 定义](https://github.com/bangumi/api/blob/master/open-api/v0.yaml)：`GET https://api.bgm.tv/v0/subjects/{id}` 补齐封面及元数据。
- [官方 User-Agent 规范](https://github.com/bangumi/api/blob/master/docs-raw/user%20agent.md)。

只保留 `type=2`，并不是“前 13000 条”或“只保留日漫 TV”。剧场版、OVA、WEB、动态漫画、未评分条目也保留，不默认按年份、评分、NSFW 筛选。最终数量以具体快照转换报告为准。

归档 `id` 明确映射到 `bangumi_id`；`name_cn` 回退 `name`；`platform` 数字转 TV/OVA/剧场版/WEB 等；日期转 01/04/07/10 季度；标签兼容字符串和含 name 的对象，合并 `meta_tags` 并去重；评分非法值为 0。缺失值保留为空，不虚构内容或评分。

归档不提供封面 URL，不能把归档下载完成等同于图片已托管。缺失封面时 **不输出 `cover_url` 键**，避免日后重导归档清空已托管 R2 地址。超长简介（超过 200000 字符）记录警告并只省略这个字段，条目和其他字段保留，完整原文仍在官方 ZIP；其他无法规范化的行会记录失败而非隐瞒。

## 2. 安装依赖

在项目根目录的 PowerShell 执行：

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:PYTHONIOENCODING = 'utf-8'
```

已沿用现有 `httpx==0.28.1`，没有新增大型库。编码变量仅影响本次终端进程，不更改 `.env`。

## 3. 下载真实官方归档（不访问数据库）

```powershell
.venv/Scripts/python.exe -m recommendations.prepare_data download
```

默认位置 `data/recommendations/archive/`。终端输出实际版本路径，复制到下一步 `--input`。只允许 HTTPS 官方下载/重定向域名；流式下载限制声明大小，完成后必须 SHA256 匹配才发布文件。已校验的同名文件可复用；同名损坏文件不自动覆盖。下载中断时清理本次单个临时文件，重跑会重新下载，**不支持 HTTP 断点续传**。

## 4. 转换全量动画、输出 20 条样本和质量报告

下面版本仅为本次实际快照示例，以下载命令输出的文件名为准：

```powershell
.venv/Scripts/python.exe -m recommendations.prepare_data convert --input data/recommendations/archive/dump-2026-10-06.210359Z.zip
```

产出：

- `data/recommendations/anime.jsonl`：标准全量动画数据。
- `data/recommendations/anime_sample.jsonl`：20 条真实样本；若归档有成人标记条目，样本包含一条以验证标记保留。
- `data/recommendations/conversion_report.json`：总数、成人数量、作品类型、缺失字段和异常。

默认**不排除成人内容**；只有明确传 `--exclude-nsfw` 才排除已标记成人条目。默认不覆盖已完成的产物：换输出路径，或确认后使用 `--overwrite`。转换时不要并行运行读取同一输出文件的补齐任务，Windows 可能拒绝替换已打开文件。

## 5. 先补齐 20 条样本，验证后再扩大

```powershell
.venv/Scripts/python.exe -m recommendations.prepare_data enrich --input data/recommendations/anime_sample.jsonl --output data/recommendations/anime_sample_enriched.jsonl --report data/recommendations/sample_enrichment_report.json --limit 20
```

固定请求官方 API，不使用非官方镜像；可选认证仅从当前进程环境变量 `BANGUMI_ACCESS_TOKEN` 获取。**不读取 `.env` 中的 token**，不接受 token 命令行参数，不将它写进缓存、报告或日志。需要认证时请自行在安全终端/任务环境注入，不要把密钥发到聊天中。单个 `Bearer ` 前缀可兼容。

默认限速 1 次/秒，连接超时 5 秒、读超时 10 秒；429/5xx/网络失败最多重试 2 次并退避，Retry-After 最多等待 60 秒。连续 5 条服务/网络失败会熔断，401/403 立即停止后续请求。404 可能代表不存在或受限，**不删除条目，也不绕过访问控制**。

成功元数据缓存到 `data/recommendations/api_cache.sqlite3`，默认有效 7 天。失败不缓存为永久失败。重跑从成功缓存恢复；缓存命中不消耗 `--limit`，这个限额约束尝试的新条目数，重试还会增加实际 HTTP 次数。

所有失败/尚未处理的条目仍写入输出，缺图保持缺图并在报告说明，不用“默认海报”冒充真实封面。存在失败时进程返回码 **2**，输出仍完整；下载、输入格式或文件错误返回 **1**，单个输出文件通过临时文件原子发布。退出码 **0** 不保证所有条目都有图，应同时查看报告 `missing_cover_url`。

默认补齐缺失字段，不用缺失/空值清空已有内容；需要更新已有有效评分等时显式传 `--refresh-metadata`。有权访问也不代表每条都有封面。保留任一来源的成人标记，不把 `null` 自动当作非成人。

确认网络及授权后，全量补齐（可能数小时，不在 Web 请求中运行）：

```powershell
.venv/Scripts/python.exe -m recommendations.prepare_data enrich --input data/recommendations/anime.jsonl --output data/recommendations/anime_enriched.jsonl --report data/recommendations/enrichment_report.json --limit 0 --rate-limit 1
```

本次没有执行上述全量 API 请求。需要恢复时选新输出路径，或停掉旧任务再使用 `--overwrite`；不要同时写同一个缓存/输出。

## 6. 隔离数据库测试（可交给助手操作）

安全设置模块在加载正常配置之前，强制清空该进程 DATABASE_URL/R2 配置，然后只使用 SQLite **内存测试库**。不会修改 `.env`，不会读写本地 `db.sqlite3` 或 Neon。

```powershell
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
```

全量标准文件的字段校验也必须明确指定安全配置：

```powershell
.venv/Scripts/python.exe manage.py import_anime data/recommendations/anime.jsonl --dry-run --settings=config.recommendation_test_settings
```

实际样本的导入及重复导入验证：

```powershell
$env:RECOMMENDATION_SAMPLE_PATH = 'data/recommendations/anime_sample_enriched.jsonl'
.venv/Scripts/python.exe manage.py test recommendations.tests.test_real_source --settings=config.recommendation_test_settings
Remove-Item Env:RECOMMENDATION_SAMPLE_PATH
```

`0002_recommendationanime_nsfw` 只是新增迁移文件，测试仅在内存测试库应用迁移，**未对生产执行 migrate**。不要把安全测试配置用于正式部署；也不要用这个内存库配置执行持久导入，进程结束数据就消失。

## 7. 后续需要你确认/操作的生产步骤

1. 决定在哪里运行长时间补齐任务，确保该环境能访问官方 API。需要时申请/注入自己的 Bangumi token。
2. 备份正式库，确认 Render 部署配置是否自动执行迁移。Git 推送只发布代码，不等于授权后台自动迁移。
3. 单独授权后，才使用正常 Django 配置迁移 `recommendations`，小批导入、验收、再导入全量文件。
4. 在任务执行环境配置最小权限 R2 凭证和域名，另行授权后先试转存 20 张，验证 JPEG/CORS/URL，再扩大。
5. 后续增量：新官方快照 → 转换 → API 补齐/更新 → 以 bangumi_id 幂等导入 → 只托管外部封面。归档暂时缺失条目不自动删除历史库。

助手可完成脚本、公开数据下载转换、隔离测试、授权后的命令执行；账号登录、凭证签发、生产备份方案确认和费用/资源授权需由你把关。当前阶段不会读取私密手账备份文件，也不会拿现有用户数据充当推荐训练集。

## 本次实测记录（2026-10-11）

- 快照：`dump-2026-10-06.210359Z.zip`，444352951 bytes，官方 SHA256 校验通过。
- 扫描 694202 条源记录，筛选后 31104 条动画；成人标记 2468 条，非成人标记 28636 条，不因成人内容排除。
- 1 条超长简介字段省略并记录警告，番剧本身保留；全量 31104 条均通过导入命令 `--dry-run` 字段校验。
- 20 条真实样本在 SQLite 内存测试库导入、重复导入通过，成人标记保持；后端共 231 项测试通过。
- 官方 API 20 条实测因当前网络连接超时，0 条成功；输出仍保留 20 条，失败报告在 `sample_enrichment_report.json`。**尚未验证真实封面补齐成功，更没有上传 R2**。API 成功/失败、缓存恢复、鉴权、限流及熔断通过模拟网络测试；需在连通官方 API 的环境继续真实验证。
- 未执行生产迁移或 Neon 导入，未修改 `.env`。
