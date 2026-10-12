# 3C：加入想看与旧记录保护

## API

`POST /api/recommendations/{bangumi_id}/want/`，必须登录，仅接受空 JSON `{}`。拒绝外部用户 ID、公共资料 ID、私人记录 ID、观看状态、评分或长评参数。

新增返回 201；已存在返回 200。响应只包含：

```json
{
  "contract_version": "recommendations.want.v1",
  "bangumi_id": 123,
  "anime_id": 9,
  "record_id": 7,
  "watch_status": "plan",
  "created": true,
  "outcome": "created",
  "content_rating": "non_adult"
}
```

三个 ID 独立：Bangumi ID 是外部作品锚点；Anime PK 是公共资料主键；record PK 是当前用户私人记录主键。不能把它们互相替代。

## 幂等、隔离与容量

1. 在事务内锁当前用户，与既有手账 create/add、JSON 导入以及 Bangumi 导入使用同一容量锁。
2. 锁当前安全推荐作品，禁止 NSFW 或未知分级在请求过程中绕过过滤。
3. 优先识别已有权威 Bangumi 记录或指纹仍有效的私人身份关联。已有记录只读取返回，不调用 save，不初始化标签，不改评分、长评、状态、进度、私人资料或时间戳。容量满时仍可返回已有记录。
4. 仅无已有记录且身份/字段无冲突时创建。公共资料使用唯一 `bgm_id`，私人记录使用现有 `(user, anime)` 唯一约束；用户锁防止并发容量越界。公共 `get_or_create` 的保存点捕获唯一身份冲突，错误时整笔事务回滚，不留下孤立新作品。
5. 新记录状态为想看，个人评分 null，长评空，进度 0。Bangumi 评分不是个人评分。不下载外部图片，只接受安全 R2 封面或空链接。已有公共资料不被更新。
6. 身份确认/自动关联也先获取用户锁，避免与本账号创建请求并行检查。历史重复身份记录仍原样保留，不自动删除或合并；画像继续按标准 ID 去重。

无需新增模型迁移；复用已存在的唯一约束和 3A 的身份表。

## 安全异常合同

| 状态 | code | 行为 |
| --- | --- | --- |
| 409 | identity_confirmation_required | 自己的旧记录缺 ID/关联失效时提示核对；不猜 ID、不自动合并 |
| 409 | journal_field_conflict | 超长标题/资料无法无损写入，拒绝创建；已有记录仍优先返回 |
| 409 | public_identity_conflict | 公共资料已占用同名原名季度但 ID 不同/缺失，需管理员核对，不能借标题匹配覆盖 |
| 409 | work_unavailable | 已下架公共作品不能新建，不自动恢复 |
| 403 | capacity_exceeded | 容量达到既有上限，不创建记录或公共资料 |
| 404 | — | 不存在、成人或分级未知作品统一不可用 |
| 503 | — | 数据库/身份服务不可用，可重试，不回退演示数据 |

同标题、标点变体仅用于提示可能存在旧记录，绝不是作品身份合并依据。原名、季度、续作、OVA、剧场版需用户确认。核对接口增加 `scope=all`，允许无评分的想看/在看/搁置记录进行现有的签名候选确认；默认仍是 `high-score`。

## 前端闭环

- 同一 Bangumi ID 在主推卡、网格、详情共用一个请求状态；连击共用 Promise，等待时禁用，不展示未经后端确认的成功。
- 后端返回已有状态时显示“已在想看/在看中/已看过/已搁置”；不将它们覆盖为想看。
- 未确认响应可明确重试；后端幂等处理响应丢失后重试。
- 旧身份冲突提供全部状态的核对入口；字段、容量、公共身份错误展示不同说明。
- 加入后取消过期分页并标记快照变化，下一次加载刷新推荐，而非继续使用旧 `state_token`。不强行关闭详情。
- 组件卸载取消请求并屏蔽晚返回；状态只存在当前页面，不存 localStorage，不在账号之间复用。
- 缺图继续使用基础 2 的统一占位图，没有外部原图兜底。

## 验证与部署

```powershell
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
cd frontend
npm test
npm run build
```

以上后端测试使用隔离 SQLite 内存库，绝不使用本地 `.env` 的 Neon 正式库。覆盖重复创建、所有字段/长评完整保留、多用户、旧身份、字段超长、安全过滤、容量、回滚和前端请求/实际 JSX 渲染。

真实行锁并发用例包含：同用户 8 线程连击、两个用户同作品、同用户不同作品容量争抢、并发读取旧记录后评分/长评/全部字段不变。SQLite 不支持 PostgreSQL 行锁，这四个用例会跳过，不能把 SQLite 的通过结果当作真实并发压力验证。

若有独立本机 PostgreSQL 和具有创建测试数据库权限的测试账号：

```powershell
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/recommendation_isolated_tests'
.venv/Scripts/python.exe manage.py test recommendations.tests.test_want_api.WantConcurrencyTests --settings=config.recommendation_pg_test_settings
```

此配置只接受 loopback 主机与明确测试库名称，拒绝回退到正式 `DATABASE_URL`，不会连接 R2。需先由操作者准备本机测试角色/库；不要为了压力测试向正式账号塞入测试记录。提交部署后需要前后端同时更新，POST 路由才会生效；不需执行新的数据迁移。
