# N2-2：关闭式后端范围接入与上下文失效

日期：2026-10-21。范围经用户确认：只接入后端安全边界，不开放成人推荐，不增加前端开关，不给任何账号授权，不改变 V1 排序。

## 1. 当前实际能力与限制

- 统一入口：推荐列表、详情、加入想看、反馈/撤销、已隐藏列表及上下文探针。
- `safe` 沿用 `safe_catalog()`；没有删除或放宽任何 `nsfw=False` 条件，未知分级仍拒绝。
- `include_adult` 在查询账号资格或作品前返回 **403** / `adult_scope_closed`。管理员、超级用户、隔离测试的有效合成资格、未定义的环境开关都不能放行。
- 年龄依据仍未配置；逐作品放行审核未建立。`release-review.unconfigured.v1` 是明确的关闭标识，不是已经完成审核的声明。
- 不获取成人原图，不向外部服务发送私人数据；画像继续仅使用安全样本。不改公共搜索/公开手账的规则，不因资格状态自动公开作品。
- N2-1 状态接口继续返回 `adult_access_allowed=false` / `serving_integration=false`；这里表示成人服务未开放，不代表本阶段没有接入失效检查。
- 这不是完整的成人功能上线：真实资格依据、作品审核、前端会话清理和后续开放仍需独立确认与测试，不能设置变量直接启用。

## 2. 请求合同

以下接口支持可选查询参数 `content_scope=safe`，省略与显式 safe 等价：

| 接口 | 原有合同保持 |
| --- | --- |
| `GET /api/recommendations/` | 分类、筛选、旧分页及固定批次仍沿用原合同 |
| `GET /api/recommendations/{bangumi_id}/` | 证据令牌/详情上下文仍需校验 |
| `POST /api/recommendations/{bangumi_id}/want/` | JSON 仍只能是 `{}`；不接受评分、长评或用户 ID |
| `POST /api/recommendations/feedback/`、`feedback/undo/` | 操作 UUID 与反馈版本仍必填 |
| `GET /api/recommendations/feedback/hidden/` | 原反馈分页版本仍有效 |
| `GET /api/recommendations/context/` | 不运行候选排序或扫描作品目录 |

查询由共同的范围边界解析器处理，再将剩余字段交给原严格序列化器；不是把任意字段静默丢弃。重复范围、空范围、未知范围为 400；其他用户/算法覆盖参数仍 400。未登录仍 401。成人/未知作品的 safe 详情和加入为 404。

资格 API、偏好/画像 API 不接收新的 scope 参数；原本的本人读取/修改合同保持。旧身份核对继续仅处理安全作品，没有新增身份推断或自动关联逻辑。

可选请求头：`X-Recommendation-Scope: <scope_revision>`。格式错误 400，陈旧或其他用户的指纹 409；它仅是前置条件，绝不是授权。现有前端不必发送。CORS 仅在原本允许的 Origin 上允许此请求头，没有放宽来源白名单或认证。

## 3. 返回合同与版本隔离

成功的受控接口增量返回 `content_scope_context`：

```json
{
  "content_scope_contract_version": "recommendations.content-scope.n2-2.v1",
  "content_scope_policy_version": "content-scope.closed.v2",
  "content_release_policy_version": "release-review.unconfigured.v1",
  "content_scope": "safe",
  "eligibility_version": 0,
  "scope_revision": "64位用户绑定HMAC指纹"
}
```

列表和详情的 `evidence_context`、`evidence.versions` 同时携带这六项，避免只更新按钮或顶层字段却继续使用旧证据。没有返回管理原因、原始依据、用户 ID 或私人长评。

- `eligibility_version` 独立于偏好/反馈版本；有效期跨过截止时刻也改变 `scope_revision`，不依赖定时任务或 GET 写入。
- 指纹绑定本人、资格管理状态/版本/期限、当前安全范围及关闭策略。它不声称当前依据可信，也不能证明年龄或合法性。
- 旧分页 HMAC 状态与固定批次签名上下文均绑定范围；资格/范围策略/放行策略变化返回 409。原有画像、偏好、反馈、目录及安全复核继续有效。
- `profile_version` 仍为不透明的解释上下文修订，也纳入范围，使现有前台上下文探针可检测失效；没有修改实际画像样本或题材。
- GET 结束时在浏览只读事务之外再次检查范围修订，防止 REPEATABLE READ 期间已提交的变更被旧事务遮蔽。丢弃旧结果，返回 409，而非混合返回新旧证据。
- 加入/反馈在既有“用户行 → 作品/反馈行”锁内复核预读范围。等待锁期间资格变更会 409，并回滚本次写入；已提交成功的写入不会在响应收尾时被误报为保存失败。

公共特征缓存仍只存安全公共作品，不存资格、用户画像或私人推荐结果。浏览公共索引使用新的 `recommendations.browse.public.n2-2.safe.v1` 命名空间；资格版本不塞进公共缓存造成用户间复用或私有缓存膨胀。私人列表没有新建共享缓存。

## 4. 排序与数据零副作用

`taste_vector`、`candidate_score`、`evidence_for`、V1/V2 排序与重排函数及历史 AST 基线未改变。新增内容只在上下文、合同和边界检查中使用。隔离用例比较移除范围元数据前后的三类 V1 ID 顺序、分数、理由和证据，要求完全一致。

既有记录的个人 0 分、长评、观看状态与私人标签逐字段保留。GET 不创建资格、审计、偏好或反馈状态。所有写入验证使用本机独立 PostgreSQL 合成账号，未对生产账号执行签发、撤销、反馈或加入。

## 5. 隔离验证

仅使用 `config.recommendation_pg_test_settings`；连接限定 `127.0.0.1:55437` / `test_recommendation_isolated_tests`，禁止 SQLite 和生产 Neon。

```powershell
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -X utf8 manage.py test --settings=config.recommendation_pg_test_settings --keepdb --noinput
```

新增覆盖：默认等价、查询严格性、成人请求零作品查询、有效合成资格仍关闭、用户隔离、无 GET 写入、自然到期、旧证据/分页/游标失效、浏览期间迟到变更、库故障 503、跨来源请求头、锁内陈旧上下文、私人字段零覆盖及真实 PostgreSQL 并发竞争。

同时修复旧迁移测试的隔离清理：从历史迁移返回时恢复所有当前叶节点，不能只恢复至 `0006` 后把后续资格表留在缺失状态。没有改生产迁移文件。

最终执行结果见本文件末尾记录。

## 6. 部署、只读验收与回退

1. 必须先确认 N2-1 的 `recommendations.0007_content_eligibility` 已应用。本阶段无新模型/迁移；缺少资格表会明确 503，不忽略异常继续发旧上下文。
2. 正常发布后端，不开启 NSFW，不配置模拟成年依据，不切换 V2。前端不需同步改版。
3. 老页面持有的分页/证据需重新加载；预期收到 409，不把它当成数据丢失。新旧后端滚动部署时可能需再加载一次；建议完成实例切换后验收。
4. 部署确认后，可按授权账号逐一运行只读脚本：

```powershell
.\.venv\Scripts\python.exe scripts/verify_recommendation_scope_deployment.py --deployment-confirmed --expected-account "明确授权的测试账号"
```

脚本通过 getpass 获取正常登录凭证；只允许 GET 与 `/api/token/` 的正常登录 POST。不加载 Django/.env、不连接数据库，不允许资格撤销、加入、反馈或偏好保存。`--adult-id` / `--unknown-id` 只接收已由操作者确认分级的公共 Bangumi ID；没有样例就明确标注未线上验证，不制造真实数据。报告存入忽略目录 `.tools/deployment-n2-2`，不记录密码、JWT、作品列表或私人来源作品。

5. 回退应用代码至 N2-1，保留新表、审计和版本，不反向删表、不清除手账/偏好/反馈。原有浏览开关只能回退浏览功能，不能开放成人范围。
6. 新旧浏览公共缓存命名空间分离，不需要全局 `cache.clear()`；旧游标/证据版本不一致时由既有 409 恢复入口重新读取。成人功能在两版中均关闭。

本轮没有执行线上部署或生产写入测试；部署后的只读验收需用户确认后单独运行。

## 7. 执行结果

- 完整后端回归执行 1,034 项（332.954 秒）：1,031 通过、1 项既有真实样本跳过、2 项前后端合同测试因 Node 沙箱目录权限失败；不是业务断言失败。随后在正常本机权限下连同最终专项补跑 78 项，全部通过，包含上述两项及历史算法 AST 冻结检查。
- 最终新增专项 23 项全部通过（8.462 秒）：20 项范围/状态/锁测试与 3 项只读脚本防写入测试。两项真实并发测试均通过 `pg_stat_activity.wait_event_type=Lock` 确认写请求实际等待用户行锁，再提交撤销并验证 409 与零写入；不是仅并行启动两个函数。
- 前端现有 250 项全部通过；无前端代码或布局修改。前端/合同 Node 需要正常目录读取权限，未为通过测试而更改断言或使用生产数据。
- Django `check` 无问题，`makemigrations --check --dry-run` 无变更。
- 第一轮全套曾被旧迁移测试未恢复资格表污染；已修复清理逻辑后重跑，后续完整回归无此类错误。未在生产修改表或运行反向迁移。
- 部署后只读验证脚本尚未在线执行；本文件不作为线上新版本已经生效的证明。
