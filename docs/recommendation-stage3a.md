# 基础 3A：身份与真实数据合同

> 这是 3A 阶段的历史基线。当前画像已由 [核心 4A](recommendations-4a.md) 兼容扩展：
> 新增自定义题材与三类提取合同，`cold_start` 按可分析样本数判断（不足默认 3 部为真），
> 不再只按已关联高分作品总数判断。下面的旧版本示例和无题材判定以 4A 说明为准。

本阶段仅实现 Django 画像摘要、明确偏好读写及只读身份合同，不进行推荐排序，不接入/修改前端，不创建想看记录，不合并旧作品，也不运行导入或封面任务。

## 1. 接口与认证

- `GET /api/recommendations/profile/`
- `GET /api/recommendations/preferences/`
- `PATCH /api/recommendations/preferences/`

沿用 JWT / Session 认证，权限为 `IsAuthenticated`。所有查询和写入仅使用 `request.user`，管理员也不通过这些接口访问其他用户。无 `user_id`、用户名或其他用户路径入口；查询参数一律返回 400，PATCH 未定义字段也返回 400。请求仅接受 JSON，偏好请求体上限 16 KiB。

未登录返回 401；非法字段/操作返回 400；偏好版本过期返回 409；不支持的写入方法返回 405，表单编码返回 415。不存在推荐列表/详情/加入想看 API，这些属于后续 3B / 3C。

## 2. 有效样本与安全画像

同时满足以下条件才计入 `eligible_sample_count`：

1. 当前用户自己的记录。
2. `watch_status=completed`，`personal_score >= 8.5`，不是源站综合评分。
3. `Anime.bgm_id` 精确对齐 `RecommendationAnime.bangumi_id`。
4. 对应推荐作品 `nsfw=False`。成人、未知分级、推荐库缺失和未对齐 ID 的记录都排除。

按唯一 Bangumi ID 计数；少于 3 部时 `cold_start=true`。策略在 `config/settings.py` 的 `RECOMMENDATION_PROFILE_SCORE_THRESHOLD` / `RECOMMENDATION_PROFILE_MIN_SAMPLES` 中集中配置。

安全分类和用户过滤在同一个数据库查询中执行，不缓存摘要；分类改变后下次读取立即反映。画像只返回计数与中性题材摘要，不返回任何种子标题、作品/记录 ID、评分明细、长评或成人/未知作品列表。此阶段不会因为旧手账缺少 ID 就按标题偷偷回填，所以旧用户可能拥有高分记录但有效样本较少。

示意响应（非实际用户数据）：

```json
{
  "contract_version": "recommendations.3a.v1",
  "taxonomy_version": "genres.v1",
  "eligible_sample_count": 3,
  "cold_start": false,
  "has_usable_tags": true,
  "readiness": "ready",
  "tag_summary": [
    {"tag": "日常", "sample_count": 3},
    {"tag": "治愈", "sample_count": 2}
  ],
  "sample_policy": {
    "watch_status": "completed",
    "minimum_score": "8.5",
    "minimum_samples": 3,
    "requires_safe_bangumi_identity": true
  }
}
```

有效作品可以没有有效题材标签：达到 3 部但无标签时仍 `cold_start=false`，另用 `readiness=needs_tags` / `has_usable_tags=false` 提示资料不足，不能伪造可用推荐依据。样本不足为 `readiness=needs_more_samples`。

题材使用 `taxonomy.py` 的版本化规范词表；别名如“搞笑”统一为“喜剧”，“轻百”统一为“百合”。日本、年份、年度最佳、作品名和未定义标签均不参与画像。使用已经初始化的私人基础标签；旧的未初始化快照读取公共基础标签。私人基础标签为空时不强行覆盖为公共/推荐库标签；自定义管理标签和个人长评均不参与分析。

## 3. 偏好存储与增量更新

`RecommendationPreference` 仅存：用户 OneToOne、规范标签数组、版本号、创建/更新时间。迁移 `0003_recommendationpreference` 只新增这张表，不变更现有番剧/手账表。用户删除时仅级联清理本人的偏好；未注册到后台管理员编辑界面。

GET 无副作用；没有偏好行时返回默认 `tags=[]` / `version=0` / `updated_at=null`，并返回 `available_tags`、词表版本与合同版本。

PATCH 支持二选一：

```json
{"tags": ["日常", "治愈"], "expected_version": 0}
```

```json
{"add_tags": ["音乐"], "remove_tags": ["科幻"], "expected_version": 1}
```

第一种只设置标签字段；第二种保留未涉及的标签。`tags` 不与增量字段混用；同一规范标签不能同时添加与移除。最多提交/最终保存 20 个标签；数组去重、别名归一、排序稳定；不接受任意新标签、NSFW 开关、用户 ID 或直接指定版本。

`expected_version` 可选，提供时须与当前版本完全一致，否则 409，不创建/修改数据。事务先锁定当前用户行，再锁偏好行，保护首次创建及并行更新；OneToOne 提供数据库唯一约束。同内容重复操作不递增版本或更新时间；携带旧版本重试仍返回 409，客户端应重新读取，不能自动强制覆盖。

GET 和 PATCH 响应字段：`contract_version`、`taxonomy_version`、`tags`、`version`、`updated_at`、`available_tags`。返回值不包含所属用户 ID 或推断画像。清空 `tags=[]` 不影响手账标签、个人评分和长评。

## 4. 作品身份合同（核心服务，不新增 HTTP 端点）

`identity.recommendation_identity(user=当前用户, bangumi_id=...)`：

- 从 `safe_catalog()` 读取明确非成人的作品；成人、未知、不存在和非法 ID 统一 NotFound。
- 仅按 `RecommendationAnime.bangumi_id == Anime.bgm_id` 对齐；不使用推荐库主键、公共 Anime 主键或私人记录主键猜测。
- `record_id` / `watch_status` 只能来自当前用户；其他用户的私人记录不会返回。
- 旧记录缺 ID 的标题相似仅设置“需要确认”，不返回旧记录内容，不自动合并或回填。规范化只消除空白、书名号等排版差异，不移除季数后缀。
- 检查已有发布身份唯一键冲突，返回 `mapping_status=identity_conflict`，不覆盖已有不同 Bangumi 身份。
- 检查推荐库 500 字符标题等字段与现有手账字段上限；返回 `journal_compatible=false` 及字段级冲突码，绝不截断。

合同字段：`bangumi_id`、`anime_id`、`record_id`、`watch_status`、`mapping_status`、`requires_identity_confirmation`、`journal_compatible`、`field_conflicts`。身份与分级待确认的旧作品不因出现中性冲突标记而被认定为安全条目。

`RecommendationPublicAnimeSerializer` 为未来 3B 提供只读数据合同：成人/未知实例拒绝序列化，标签使用规范词表，封面只允许配置的 R2 域名下正确作品键及既定版本参数。不返回 `source_cover_url` 或外部原图；未托管图片输出空字符串。3B / 3C 必须同时复用安全查询与序列化门禁，不能只调用普通 objects 查询。

## 5. 安全测试指南

**本地 `.env` 指向 Neon 正式库；测试必须显式使用下面的隔离配置。**

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe manage.py test recommendations.tests.test_contracts_api --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
```

专项测试 32 项：登录/真实 JWT 所属身份、冷启动、阈值、字段完整性、分级/缺 ID 排除、有效私人标签、旧快照、多用户隔离、GET 无写入、增量/清空/幂等/版本冲突、畸形/过大 JSON、外部用户参数及 NSFW 开关拒绝、身份命名空间、旧记录和发布身份冲突、超长字段、R2 白名单与源链接隐藏、账号删除仅清理本人偏好。

2026-10-12：以上 32 项通过；完整后端 288 项通过（包含显式指定的 20 条真实样本隔离导入验证），前端原有 18 项通过。Django 系统检查与迁移一致性检查通过。全部后端测试使用 SQLite 内存配置，没有向 Neon/R2 发起测试写入。

SQLite 内存测试核验合同和唯一约束，不等价于 PostgreSQL 并行压力测试。真实部署后应在受控测试账户中另行验证同时 PATCH 的行锁路径；不要拿现有用户长评或手账当测试数据。

## 6. 部署与人工联调

本轮不手动运行正式迁移。发布到目标 Django 环境前，确认备份及数据库目标，由正常部署流程或管理员应用新增迁移：

```powershell
# 正式环境操作：仅在部署目标与备份已确认后执行，不是隔离测试命令。
python manage.py migrate recommendations
```

无需重复导入 31104 条作品，不重新转存封面。不运行迁移就调用 preferences 会缺表，不能用默认空偏好掩盖未部署的 schema。

联调使用两个专用测试账户各自的 JWT，通过 HTTP 客户端执行 GET /profile、GET /preferences、PATCH /preferences。认证头为 `Authorization: Bearer <该账户自己的 JWT>`，不要将令牌粘贴到聊天/日志或 Git。校验另一账户的偏好不变、未登录 401、伪造 user_id 400、旧 expected_version 409，以及原手账评分/长评未变化。

下一阶段 3B 才接入推荐计算和前端数据 provider；3C 才实现幂等加入想看。
