# P1：用户画像取样门槛后端设置与合同

## 发布边界

本阶段只完成 Django 后端、增量迁移和隔离 PostgreSQL 测试，不修改前端布局、设置工作台或现有前端证据校验，不执行生产迁移或修改真实用户数据。

支持个人评分门槛 6.0—10.0、步长 0.5，默认 8.5；至少 3 部有效样本的要求保持不变。安全分级、已看过、明确作品身份、有效题材及 Bangumi ID 去重条件保持不变。个人评分 0 不等于未评分，也不作为该范围内的画像样本。

V1 默认与公式结构不变：原先相对全局门槛的样本权重改为读取当前请求的有效门槛；8.5 分时保留原有运算顺序和数值。自定义参数不是最低 Bangumi 公共评分，不新增屏蔽题材或匹配概率。

## 模型与迁移

复用 `RecommendationPreference`，新增：

- `profile_min_score`：精确一位小数，默认 8.5；模型校验及 PostgreSQL CHECK 双重限制九个档位。
- `profile_policy_version`：后端规则版本 `profile-sampling.v1`，客户端不能指定或修改。

迁移：`0006_preference_profile_sampling`，只增字段/约束，不执行私人手账回填，不修改原偏好标签、版本或更新时间。保留数据库默认值，支持滚动部署中旧后端仅插入旧字段；不是仅依赖新 ORM 的默认值。

## 参数读取与保存

`GET /api/recommendations/preferences/` 保留原合同及 51 项题材选项，增加：

```json
{
  "profile_min_score": "8.5",
  "effective_profile_min_score": "8.5",
  "profile_policy_version": "profile-sampling.v1",
  "profile_min_score_control": {
    "enabled": false,
    "default": "8.5",
    "minimum": "6.0",
    "maximum": "10.0",
    "step": "0.5",
    "choices": ["6.0", "6.5", "7.0", "7.5", "8.0", "8.5", "9.0", "9.5", "10.0"]
  }
}
```

`PATCH /api/recommendations/preferences/`：

```json
{"profile_min_score": "7.0", "expected_version": 0}
```

可与 `tags` 或既有增量标签操作一起提交；标签整体设置仍不能和增量操作混用。修改门槛必须带 `expected_version`，通过登录用户行锁和偏好行锁串行化；标签与门槛在同一事务中保存。

- 只改门槛保留标签；只改标签保留已保存门槛；清空标签不恢复门槛。
- 保存相同值且版本正确为 no-op，不重复增加版本/更新时间。
- 陈旧版本返回 409，不覆盖另一处更新；超时后应先读取最新设置确认，不能盲目重复写入。
- 非法档位、空值、非有限数、不支持字段或伪造用户 ID 返回 400；未登录为 401。
- `feedback_version` 维持独立，不随门槛保存改变。

## 只读预览

`GET /api/recommendations/profile/preview/?profile_min_score=7.0`

要求登录；只接受一个不重复的 `profile_min_score` 参数，不接受其他用户或算法参数。使用现有安全解析和特征提取，返回必要摘要，不查询完整推荐库、不执行排序，不返回来源作品 ID、标题、长评或逐作品评分。

核心响应：`contract_version=recommendations.profile-preview.v1`、`is_preview=true`、`saved=false`、`preference_version`、`sample_policy`、可分析/合格样本数、冷启动状态、待关联等诊断计数。`sample_policy.source=unsaved_preview`，明确不是已保存/实际生效画像。

预览也受现有推荐读取限流约束，并设置 `Cache-Control: private, no-store`。数据库失败返回 503，不能伪装为空手账或零样本。即使服务端开关关闭，也可只读预览拟选门槛，但不影响实际推荐。

## 统一生效与上下文隔离

`profile_policy.py` 为统一规则入口。当前用户的有效门槛用于：画像提取、冷启动、已有样本权重、默认身份核对列表及证据合同。历史全站严格关联管理命令仍保留原服务端基线，不因本次设置而自动扩大扫描或执行写入。

画像 `sample_policy` 增加：规则版本、实际 `minimum_score`、已配置值、开关状态及来源。证据版本增加 `profile_policy_version` 和 `profile_min_score`；真实来源个人评分仍完整返回。

用户设置变化或开关切换会改变画像/偏好上下文，即使恰巧入选样本相同，旧分页、浏览游标和详情依据仍不能混用。公共目录特征缓存不包含用户门槛或私人样本，不为此清空全局缓存。

## 保留固定评测基线

原质量评测、V1/V2 对照和固定留出工具显式使用 8.5 分，不读取用户自定义门槛来增加合格测试样本；比较上下文声明 `fixed_evaluation_baseline`。自然线上浏览性能检查仍可读取实际用户上下文，不冒充固定留出评测。

本次没有运行真实账号评测，没有扫描全站私人手账，也没有制造或修改真实评分。

## P1 开关与部署顺序

新增独立环境开关：`RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED`，**默认 false**。

原因：现有前端会将低于 8.5 分的来源判断为无效证据。P1 不修改前端，因此生产必须继续保持关闭，待 P2 完成门槛草稿、预览、动态文案及证据校验后，才在 P3 验收中开启。

关闭时：实际取样仍使用服务器默认 8.5；已保存的自定义值不会删除。API 同时返回已保存值和实际生效值，不能把保存 7.0 误报为正在按 7.0 推荐。P1 后端可存储参数并供隔离测试验证，不代表页面功能已开放。

部署步骤：

1. 备份并确认迁移目标，由正常部署流程在生产执行 `python manage.py migrate recommendations`；本任务不代为运行生产迁移。
2. 部署兼容后端；保持开关 false。旧前端无须新增字段即可维持原行为。
3. 通过授权账号做 GET 只读核验：默认/有效门槛 8.5，预览标记清晰，V1 默认不变。
4. P2/P3 完成前不在线上开启非默认门槛。

回退优先将开关设为 false，并按部署平台要求重启进程、让页面重新读取。需要回退代码时保持新增列和数据库默认值，不反向迁移删除已保存门槛；不清空手账、明确偏好或反馈，不切换 V2。

## 隔离验证

仅在专用本机 PostgreSQL 测试库运行。新增测试覆盖九档校验、模型约束、未评分/0 分、安全与状态过滤、原字段保护、用户隔离、预览零写入、标签和门槛独立保存、冲突/no-op、真实多连接竞争、旧上下文失效、默认 V1 同条件一致性、固定评测基线及迁移/旧后端插入兼容。

```powershell
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.venv/Scripts/python.exe -X utf8 manage.py test recommendations.tests.test_profile_sampling recommendations.tests.test_profile_sampling_concurrency --settings=config.recommendation_pg_test_settings --noinput --keepdb
```

### 执行结果

- 独立 PostgreSQL 全量回归：752 项，751 通过、1 项可选真实样本导入测试跳过。
- P1 新增 23 项测试通过，包括 3 项真实多连接行锁竞争及 1 项迁移/旧后端插入兼容验证。
- 最终补验预览不调用排序或公共索引、固定基线报告的政策版本及精确 AST 参数适配白名单；原 `evaluation/v2a-engine-baseline.v1.json` 保持不变，不重置历史排名基线。
- 前端现有 207 项测试通过，生产构建成功；保留既有主包约 877 KB 的体积警告。
- Django 系统检查无问题，`makemigrations --check --dry-run` 无遗漏模型变化。
- 自动化仅使用专用 `test_recommendation_isolated_tests` 本机 PostgreSQL；生产迁移、线上参数写入和浏览器验收均未执行。
