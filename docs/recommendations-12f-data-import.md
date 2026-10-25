# 扩展 12：正式系列资料导入与放行前审计

日期：2026-10-26（北京时间）。本轮在用户明确授权后，已将官方真实系列资料写入项目配置的 Neon 正式数据库。**数据准备通过；首次 Render 登录态验收在单番分区请求上超过耗时预算，优化后的部署复测及全量开关变更尚未完成。** 不以本机环境变量或未登录 401 检查代替线上放行验收。

## 已执行的真实导入

官方来源：[Bangumi Archive](https://github.com/bangumi/Archive)。使用官方 manifest 指定的 `dump-2026-10-20.210332Z.zip`，归档 SHA256 为 `9fe8de272398ff4bd12994b73e25aac37cca631372383f6aadc503528623aa93`；下载后与归档解析前后均校验。来源成员哈希、导入计划指纹与隔离原因留在本机报告。

| 项目 | 真实结果 |
| --- | ---: |
| 公共目录作品 | 31,104 |
| 独立系列公共节点（含跨媒介审计端点） | 73,826 |
| 来源观察 | 31,103 |
| 完整来源观察 | 30,848 |
| 部分观察，未发布为当前版本 | 255 |
| 归档未提供来源，跳过 | 1（Bangumi 350964） |
| 存储关系断言（含审计记录） | 84,705 |
| 普通目录新鲜完整观察覆盖率 | 28,428 / 28,636 = 99.274% |
| 成人目录新鲜完整观察覆盖率 | 2,420 / 2,468 = 98.055% |
| 最大原始接触度 / 读取上限 | 375 / 5,000 |

覆盖率表示已核验来源观察，不代表完整系列、每部都有关系或所有断言都能展示。完整观察允许官方来源返回空关系。成人目录的覆盖率不代替作品审核或用户访问资格。

异常来源包括缺失端点 221 部、时序环连通区域 24 部、互反关系不一致 10 部（原因可能重叠）；另 1 部来源未在归档中出现。异常原始内容通过成员哈希绑定，部分观察留存来源问题，不进入当前发布指针。不会猜测缺失节点、补造反向关系、按标题合并系列或下调 90% 门槛。

生产写入仅涉及 SeriesState、SeriesNode、SeriesObservation、SeriesHead、SeriesAssertion。命令额外限制 SQL 写入目标；没有修改主目录、手账、偏好、NSFW 审核、服务总开关或提醒设置。导入前后公共目录身份/分级指纹一致。未创建人工关系修订。

`0013_series_relations`、`0014_series_reminders` 已应用。来源/观察指针一致、部分观察未发布、待复核断言未发布、覆盖门槛及读取上限检查均通过。公开审计摘要见 [recommendations-12f-data-audit.json](recommendations-12f-data-audit.json)。完整本机报告在 `.tools/12f-live/`，归档在 `data/recommendations/archive/`，均被 Git 忽略。

## 可复用的目录导入工具

新增 `sync_series_catalog`，默认只读预检。一次流式扫描归档，按作品隔离异常，检查跨批次的时序环和互反冲突，再拆成最多 100 个来源、5,000 条关系、10,000 个节点的事务批次。无归档解压、网络抓取或跨作品的标题推断。

```powershell
python manage.py sync_series_catalog --input LOCAL_OFFICIAL_ZIP --expected-sha256 OFFICIAL_SHA256 --report NEW_PREVIEW_JSON
python manage.py sync_series_catalog --input LOCAL_OFFICIAL_ZIP --expected-sha256 OFFICIAL_SHA256 --report NEW_APPLY_JSON --apply --expected-plan PLAN_FINGERPRINT_FROM_PREVIEW
```

必须使用新的报告路径。执行计划绑定同一归档、成员、映射、全部批次及真实目录身份。只有预检指纹一致才允许写入。每批原子提交、每个来源观察不可覆盖；中断后可按原归档与计划续跑，已成功批次幂等回放。不同内容或质量状态不能复用同一快照覆盖历史。更旧归档不能回退当前节点。共享版本锁和人工修订依据不变。

发布服务改为在同一事务内批量建立观察、断言及更新指针，避免对每个来源发出独立远程 SQL 往返。100 来源的本地 PostgreSQL 发布查询数有独立上限测试，指针写入失败整批回滚。

## 尚待完成的线上验收

线上未登录的系列工作区、批量标识、提醒接口均返回 401，耗时分别为 0.809、0.749、0.361 秒。这仅确认访问保护；没有登录态时，不能据此确认 Render 灰度、功能响应、分页或性能。

用户已在本机正常登录 XHcode 执行脚本：三种原推荐开放、系列能力、工作区、批量标识及提醒合同通过；首次单番关联分区耗时 13.742 秒，超过固定 10 秒预算，因此结果为 `failed_or_blocked/http_budget_12`。不通过提高预算放行。

公共目录只读剖析定位到冷索引构建的标签投影开销。`feature_tags` 改为直接返回与原提取器完全一致的题材集合，省去随后丢弃的来源/忽略原因统计，并仅缓存小型、固定的发行版题材词典；没有缓存输入标签、作品名称、私有画像或结果。原完整审计提取器保留，排序规则和安全检查不变。

在同一批 28,636 部真实普通目录上，对原实现和优化实现各做 3 次纯 Python 索引构建，完整索引（全部作品特征、IDF 权重及指纹）严格相同。原中位 0.814867 秒，优化后 0.267367 秒，下降约 67.2%。另一次带 cProfile 的冷索引读取从 2.992 秒降到 1.213 秒。**均为本机测量，不能视作 Render 已低于 10 秒。**

最终完整推荐模块回归 1,158 项：1,157 项通过、1 项原有跳过，并通过 Django 检查及迁移一致性检查。5 万合成目录、1,000 条关系、100 条本人合成记录的带内存追踪测试中，分区首次请求 5.8843 秒，后续中位 3.9175 秒，最多 33 条 SQL；仍不代表生产 p95。前端没有修改。词典版本、全量 Unicode/别名/标题排除投影及真实目录完整索引一致性另行验证。

当前没有可用的 Render 管理连接或 XHcode 验收 token；内置浏览器连接超时。优化提交部署完成后，使用项目根目录 PowerShell 的同一正常登录命令复测：

```powershell
.\.venv\Scripts\python.exe scripts/verify_series_deployment.py --deployment-confirmed --expect enabled --expected-account XHcode --seed-bangumi-id 328609
```

该公开种子已在正式库确认有新鲜完整观察及 5 条当前普通目录内的原始关系。脚本在本机隐藏输入密码；不要把密码或 token 发到聊天。报告不落盘响应正文、密码或 token。还需按 [12F 验收说明](recommendations-12f.md) 完成浏览器及已授权普通账号检查。

验收通过后，在 Render 清空 `RECOMMENDATION_SERIES_CANARY_USERS`，三个系列开关继续 True；原探索名单继续为空。部署完成后在 Render Shell 运行：

```bash
python manage.py audit_series_release --stage full --output /tmp/series-full-NEW.json
```

再对 XHcode 和已授权普通账号执行登录态 `--expect enabled` 验收。**未完成这些步骤之前，本轮状态是数据审计通过、全量放行待验收。**
