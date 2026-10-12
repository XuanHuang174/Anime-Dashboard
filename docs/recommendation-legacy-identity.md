# 3B 补充：全站旧手账身份兼容

## 目标与边界

关联层 `RecommendationRecordIdentity` 一对一连接私人记录、多对一连接推荐目录；不修改 `Anime.bgm_id`，不合并共享 Anime，不改变记录主键、评分、长评、状态、进度或私人资料。其他用户已导入的标准 Anime 可以和旧别名行同时存在。高分画像按标准 Bangumi ID 去重，重复记录取个人评分最高的一条作为证据；已有作品隐藏也使用有效关联身份。

`0004_recommendationidentityevent_and_more` 仅创建关联和审计两张新表，没有数据回填操作。运行测试采用 SQLite 内存配置，不能用正式 .env 执行默认 test。

## 严格匹配与确认

- `dual-title-quarter.v1`：NFKC、空白与一层《书名号》归一化；中文名和原名都精确一致、季度一致、候选唯一、标记非成人，才能自动关联。已知 OVA/剧场版类型提示必须一致。
- 唯一性判定包含成人与未知条目，避免只过滤安全集合后误称唯一。它们不会出现在候选响应中。
- 标点归一化只用于人工候选检索；不删除季数、OVA 或剧场版关键字来进行自动绑定。年份或日期差异、缺少原名、多个候选都留待确认。
- 用户可以在核对工具中搜索安全标准目录、选择作品，勾选“不是其他季或版本”后明确确认。服务端重新检查当前用户、记录、目标分级、元数据指纹与版本。
- 候选票据由后端签名，绑定用户、记录、目标、双端元数据和版本，15 分钟有效；更换 record_id/bangumi_id 或编辑元数据后重放都会被拒绝。不能提交未经后端签发候选的任意 ID。
- 关联指纹对改名、季度/版本变化失效；目录分级变更立即过滤，目录核心资料变化使旧关联待核对。自动扫描不覆盖已有、失效、撤销或人工确认的关联。
- 关联工具显示来源为用户自己的私人记录；标准候选只包含已知非成人条目。这不是把未确认的私人旧标题当作安全推荐结果。

## API 与页面

```text
GET /api/recommendations/identities/?page=1&page_size=5
GET /api/recommendations/identities/?record_id=<自己的记录>&keyword=<搜索词>
PATCH /api/recommendations/identities/
```

PATCH 字段：`record_id`、`bangumi_id`、`expected_version`、`ticket`、`confirmed=true`。票据仅用于身份确认，不是登录凭证；不要输出到日志。401 未登录，404 非自己的记录或不安全目标，400 不支持字段/没有确认，409 票据过期/状态变化，429 限流，503 资料/数据库不可用。GET 不执行绑定。

3A profile 保持原版本并新增以下兼容字段：

```json
{
  "identity_service_ready": true,
  "diagnostics": {
    "high_score_record_count": 77,
    "linked_high_score_record_count": 29,
    "pending_identity_count": 48,
    "unavailable_identity_count": 0,
    "stale_identity_count": 0,
    "conflict_identity_count": 0
  }
}
```

这是示意结构，不是已实施的生产统计。高分记录数与去重后的 `eligible_sample_count` 不一定相等。pending 包含未关联、失效与身份冲突；unavailable 包含未收录及分级限制。标签仍只来自合法高分样本，不为改善显示而虚增样本。

页面分别显示手动偏好、画像标签、已关联样本及待关联数量。没有合法种子时显示“高分旧作品尚待关联”，不再误导用户重新评分；核对成功刷新真实画像与结果，仍使用取消请求/序号防竞态。加入想看仍留待 3C。

部署先上线代码、后迁移时：仅对新关联表不存在的数据库错误使用只读兼容路径，继续使用原有权威 Bangumi 身份；返回 `identity_service_ready=false`，页面说明工具待部署并禁用确认入口。其他数据库故障不伪装成没有数据。迁移后自动使用新关联层，不需要启动 GET 自动补数据。

## 新增与导入防回退

普通记录创建与私人资料编辑会尝试严格本地匹配，无外部 API/图片请求；已有或失效关联不自动覆盖。Bangumi 导入继续使用已有的权威 subject ID。任意手填 bgm_id 不会被写入共享 Anime。

JSON 备份继续兼容 version=2，增加可选 `anime.identity_reference`。签名绑定导出用户、规范化元数据和目录指纹，有效期一年；恢复时验证当前用户和分级，不把客户端裸 ID 当作可信身份。旧备份、签名不符、跨用户或目录已变化时仅尝试严格本地匹配，无法证明则保持待关联。匹配失败不破坏原有导入/导出流程。原有导入的“替换手账”语义没有改变，本模块本身不触发该操作。

## 全站操作步骤（需单独批准生产写入）

1. 检查最新 Neon 备份/分支；检查迁移计划。不要盲目执行所有 app 的迁移。
2. 默认审计命令可以在 0004 尚未应用时运行，不创建虚拟空表。报告含记录 ID，保存在 `.tools/` 等 gitignore 目录，不公开分享。

```powershell
.venv/Scripts/python.exe manage.py sync_recommendation_identities --mode audit --scope all --output .tools/identity-preflight-new.json
```

3. 审核清单、确认生产新表迁移和首次全站严格关联后，再执行：

```powershell
.venv/Scripts/python.exe manage.py showmigrations recommendations
.venv/Scripts/python.exe manage.py migrate recommendations 0004
.venv/Scripts/python.exe manage.py sync_recommendation_identities --mode apply --scope all --batch-size 100 --confirm-production
```

数据库已配置远程地址时，缺少 `--confirm-production` 会直接拒绝写入。每批事务、当前元数据与分级复核、目录版本变化时本批回滚；日志保留 `batch-id`。审计清单是预检，不是忽略后续数据变化的强制覆盖。命令报告可能含私人记录 ID，不应进入 Git。

4. 写入后核对：用户/手账数量、评分与评价摘要不变；新关联数量、已关联样本、待确认数量、异常清单、实际用户 profile 与推荐理由。存在新目录资料更新或候选变化时重新审计，不强行继续。
5. 撤销某次批次：

```powershell
.venv/Scripts/python.exe manage.py sync_recommendation_identities --mode rollback --batch-id <原批次 UUID> --confirm-production
```

撤销采用失活/恢复旧关联并递增版本，不批量删除任何文件/记录。后续用户确认或元数据变化的记录跳过，避免覆盖用户的新决定。撤销后自动扫描不会再次激活旧关联，必须人工确认。

## 测试

```powershell
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
cd frontend
npm.cmd test
npm.cmd run build
```

覆盖跨用户隔离、多旧行同一身份、画像去重、私有快照变化、实时分级过滤、票据篡改/重放、并发版本保护、批次幂等与撤销、JSON 往返、新建记录自动匹配、迁移前只读过渡及 GET 无写入。生产 PostgreSQL 的真实并发验收建议在备份分支进行，不以 SQLite 测试声称已验证线上锁行为。
