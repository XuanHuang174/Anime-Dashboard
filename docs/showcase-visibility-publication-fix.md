# XHcode 公共番剧搜索可见性修复（阶段 1）

## 范围与兼容性

修复“普通用户先创建非公开共享资料，XHcode 后加入时仍不可被公共搜索找到”的状态衔接缺陷。普通添加、推荐想看、Bangumi 收藏导入统一调用 `anime.showcase_services.publish_new_showcase_record`；标准 records 创建接口也复用该规则。

不修改推荐 V1/V2、排序、标签映射、资源搜索或前后台浏览连续性。公开搜索仍只读且仍要求 XHcode 拥有记录、共享资料 `is_active=True`，不在 GET 请求中修复数据。未新增业务表或前端控件。

## 新字段与状态来源

`Anime.hidden_reason` 仅在 `is_active=False` 时有意义：

| 值 | 含义 | XHcode 新建手账记录后 |
| --- | --- | --- |
| `private_only` | 推荐加入/Bangumi 导入新建的仅私人共享资料 | 可以公开 |
| `showcase_removed` | 主理人曾删除且仍被其他用户引用 | 明确重新添加可以公开 |
| `admin_retired` | Django 管理后台明确关闭公开状态 | 保持隐藏，返回待审核通知 |
| `legacy_unknown` / 空值 / 未知值 | 历史资料或缺少可信隐藏原因 | 保持隐藏，返回待审核通知 |

0023 迁移只增加字段、调整帮助文字，不修改任何 `is_active`、手账、评分或标签。所有旧行新增字段取 `legacy_unknown`；已公开行不受该原因字段影响。默认原因同时设置 `default` 和 `db_default`，滚动部署期间旧进程仍可使用旧列清单 INSERT。

## 写入与隔离边界

- 仅服务端配置的准确用户名可触发公开协调，昵称和外部传入 ID 不提供权限。
- 只对**本次确实新建**的主理人手账记录执行协调；已有记录重试、读取、编辑、核对身份和导入已有记录不偷偷恢复公开。
- 推荐接口先保留现有 NSFW/未知分级、身份确认、容量、长度、幂等校验；公开服务不替代任何校验，也不按标题猜测身份。普通手工作品不必存在于推荐库。
- 共享资料行锁下重新核查当前隐藏原因，仅允许更新 `is_active`、`hidden_reason`、`updated_at`；不把任何人的私人资料复制回共享行。
- 沿用入口现有用户锁/事务，创建与公开在同一事务提交，失败一起回滚。不同用户仍各自拥有记录。
- 普通用户复用隐藏资料不更改公开状态。管理员的明确下架不能因导入补空字段或创建竞争而被覆盖。
- 主理人删除仍保留旧语义：没有其他引用时删除共享资料；有其他引用时只隐藏，并标记 `showcase_removed`。已隐藏的管理员/未知原因不被重标记。迟到的清理调用发现主理人已有新记录时不再隐藏。
- 收藏导入对本人已有记录的原有更新语义不改变；新增公开服务不增加任何个人字段写入。其他人的记录始终零改动。

## 增量响应通知

XHcode 创建记录响应可增加 `showcase_publication`：`published` / `already_public` / `review_required`。待审核不代表私人加入失败，不回滚正常私人记录。普通用户和公共读取不返回这个通知；公共读取不暴露隐藏原因。

收藏导入批次增加 `showcase_publication_notices`，最多列出 5 项待审核作品的 Bangumi ID 与通知。不含其他用户信息、长评或评分。当前阶段提供后端通知合同，不新增前端弹窗。

## 隔离验证

仅使用已有便携 PostgreSQL 的 `127.0.0.1:55437/test_recommendation_isolated_tests`。测试设置清空生产 DATABASE_URL 和云存储凭据；新测试额外断言数据库类型、地址和名称，拒绝 SQLite 与生产库。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup-recommendation-postgres.ps1
# 使用该本机脚本配置的测试连接设置 RECOMMENDATION_TEST_DATABASE_URL。
.venv/Scripts/python.exe -X utf8 manage.py test anime recommendations --settings=config.recommendation_pg_test_settings --noinput --keepdb -v 1
.venv/Scripts/python.exe -X utf8 manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
```

新测试包含：三入口公开与阻断、普通用户/昵称隔离、公共搜索后正常加入、个人 0 分/长评/状态/标签保护、只读搜索和已有记录重试无写入、管理员下架、移除后重加、迟到清理、事务回滚、导入竞争不覆盖、独立连接行锁竞争、迁移前后状态不变及旧进程 INSERT 兼容。另运行已有手账/推荐全量后端测试。

### 2026-10-20 执行结果

- 独立 PostgreSQL 全量运行 964 项：原有 941 项中 940 通过、1 项既有跳过；新增 23 项中最初 1 项将导入更新时间戳误当作公开状态写入，已修正断言，不修改导入业务语义。
- 修正后定向复验 57 项全部通过：全部 23 项新测试、32 项原有想看 API/并发测试、2 项跨前后端取样合同测试。新增测试包含 3 项真实 PostgreSQL 多连接并发测试，原有想看的 4 项行锁测试也通过。
- 前端 Node 测试 238 项全部通过；未修改任何前端文件。
- `makemigrations --check --dry-run`：No changes detected；Django `check`：无问题。
- 沙箱初跑有 Node 用户目录 EPERM；授权普通本机权限后补跑通过，未以跳过相关测试来规避。
- 本次未读取真实账号或执行生产数据修复。

## 部署与回退

1. 部署前备份数据库并先执行增量迁移 `python manage.py migrate`，再启动新后端。迁移不恢复任何旧隐藏作品。
2. 不需要切换推荐算法或清空私人推荐缓存；推荐目录与排序均未修改，原有写入后的上下文失效机制继续工作。
3. 回退时回退后端代码即可，建议保留兼容的新字段及数据库默认值，不反向删除字段，不清空手账/偏好/反馈。旧版本仍可读写，但不再有新的公开协调行为。
4. 如需撤回某条新公开决定，应先确认主理人意图，按准确资料 ID 处理；不要对全库 `is_active` 批量翻转。

**本阶段没有手动运行生产迁移、没有连接真实账号或修复历史隐藏条目。旧受影响作品仍需下一阶段只读审计、人工确认后定向修复。**
