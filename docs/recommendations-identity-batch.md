# 旧作品身份核对：跨页草稿与批量关联

## 范围

复用候选搜索、私人身份关联表、审计事件表与 `bind_record` 事务服务。新增批量 API 和前端工作台，不新增业务表、无数据库迁移，不改 V1 排序，不自动猜测身份或合并手账。候选搜索仅查询安全目录；原记录评分、长评、观看状态、私人标签及公共作品资料不修改。

## 用户操作

1. 每页 5 条，逐条选择候选并明确勾选核对；支持跨页保留，最多暂存 20 条。
2. 切换候选或重新搜索必须重新确认；资料版本变化的条目不能继续沿用旧确认。
3. 底部固定显示已核对数、跨页清单、清空及批量提交。只提交已核对的条目，没有全选确认或自动勾选。
4. 批量完成仅更新核对列表，不重算推荐。成功项移出草稿，失败项保留并提示原因；跨页失败项可从清单直接定位、重新搜索和核对。
5. 关闭时，如有草稿须明确放弃。发生过提交（包括结果未知）才通知父页统一重读画像/推荐一次；纯草稿取消不写入、不重算。

原名、日期、类型与 Bangumi ID 保持可见；日期差异提示只比较原放送字段，兼容月份补零，不从标题/简介猜测季度。日期相同不代表身份相同，仍需人工核对。

桌面居中宽卡片，列表独立滚动、操作栏固定；窄屏为同一原生 Dialog 底部面板。搜索只占用当前行，旧搜索在翻页、关闭及离开页面时取消，并用请求代次防止迟到响应污染。

## API

保留 `GET/PATCH /api/recommendations/identities/`，新前端使用：

```http
POST /api/recommendations/identities/batch/
Content-Type: application/json

{
  "batch_id": "<客户端生成的 UUID>",
  "items": [{
    "record_id": 123,
    "bangumi_id": 13557,
    "ticket": "<服务端签发候选凭证>",
    "expected_version": 0,
    "confirmed": true
  }]
}
```

严格限定 `request.user`，拒绝外部用户参数、重复记录、未知字段、空批次、超过 20 条和超过 64 KiB 的请求。整个请求结构不合法返回 400，鉴权和限流沿用现有规则。

有效请求返回 200 的逐条结果，合同 `recommendations.identity-batch.v1`，包含 `batch_id`、`processed_count`、`success_count`、`failed_count`、`results`。每行含输入的记录 ID、Bangumi ID 及：

- `success`：`linked` / `already_linked` / `authoritative`，返回版本。
- `needs_review`：记录/候选变更、凭证过期、无权限或不安全候选；不泄漏他人数据。
- `retryable`：本条数据库操作暂不可用。

每项复用独立事务及“用户 → 私人记录 → 目录/关联”锁顺序。一项失败不撤销其他已提交项；并发相同确认不重复生成审计事件，不同目标的陈旧版本只能有一方成功。多条旧记录指向同一 Bangumi 作品也不合并、删除原记录。

## 超时、重试和签名边界

前端快速连击只产生一个 POST；在途提交不能关闭面板。响应丢失或确认合同不兼容时保留原 UUID、所有条目、版本及签名，锁定编辑，允许明确重试原批次。绝不自动连续重试或改签名绕过版本校验。

批次中的暂时失败也重试原批次，已成功项只读确认，不重复写入。业务冲突条目必须重新搜索并人工确认，下一次才是新批次。完成数量只在真实响应后展示，单请求等待期间不伪造逐条完成进度。

候选有效期仍为 15 分钟。若凭证已过期，只有同一批次已提交的审计事件、当前关联版本及目标、原记录/目录指纹、真实签名和用户归属全部一致，才可只读确认已完成状态；绝不允许过期凭证创建/覆盖关联。目标变为成人或未知分级仍直接拒绝。复用已有事件 `batch_id`，无需新增持久化回执表。

## 本地验证

测试数据均为合成，不连接生产 Neon、不登录真实账号、不写真实手账。PostgreSQL 使用已授权的本机便携实例及严格设置 `config.recommendation_pg_test_settings`，只接受 loopback 的 `recommendation_isolated_tests`，Django 测试库为 `test_recommendation_isolated_tests`，未使用 SQLite。

```powershell
# 先运行已存在的本机 PostgreSQL 准备脚本，并按其测试连接设置环境变量。
powershell -NoProfile -File scripts/setup-recommendation-postgres.ps1
.venv/Scripts/python.exe manage.py test recommendations.tests.test_identity_batch recommendations.tests.test_legacy_identity recommendations.tests.test_want_api recommendations.tests.test_evidence_contract --settings=config.recommendation_pg_test_settings --noinput --keepdb
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_pg_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
cd frontend
node --test --test-reporter=dot tests
node scripts/test-recommendation-identity-batch.mjs
node scripts/test-recommendation-motion.mjs
node scripts/test-recommendation-preferences-workbench.mjs
node scripts/test-recommendation-loading.mjs
node node_modules/vite/bin/vite.js build
```

本轮结果：

- 后端 PostgreSQL 85 项通过，含 14 项新增批量/API/事务测试；其中 4 项为真实多连接竞争（8 次重复提交、逆序批次、冲突目标、用户隔离）。
- 全部前端测试 195 项通过，含 11 项新增批量合同/状态测试。
- 新批量浏览器 7 项、既有 R1–R3 34 项、偏好工作台 12 项、加载链路 10 项通过。
- Django 检查、无迁移检查和生产构建通过；已有主包 >500 kB 的警告未在本次处理。
- 旧证据测试有一项 V2 兼容用例原先依赖 SQLite 专属审批旁路；现在仅该测试方法内 mock 审批函数，以便在独立 PostgreSQL 验证版本令牌。没有放宽实际 V2 门槛或切换线上引擎。

忽略目录 `.tools/acceptance-identity-batch/` 包含合成浏览器报告和桌面/手机截图，不进入提交。

## 部署与回退

先部署后端批量接口，再部署前端；旧版单条 PATCH 接口保留。上线后先只读核查候选与草稿取消；实际批量写入仅使用授权测试账号，或另获用户明确授权。不把本地合成验收当作生产验收。

若异常，正常 revert 本次提交并重新部署，回到单条确认 UI；已有私人关联与事件无需删除/回滚，不清理用户手账、偏好或反馈，不开启 V2，不全局清空缓存。
