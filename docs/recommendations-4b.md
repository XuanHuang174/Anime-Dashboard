# 核心 4B：冷启动与真实页面引导

不新增模型或迁移，不批量改写旧手账。沿用 4A 的个人评分、身份、标签副本和安全隔离规则。

## 数据合同

`GET /api/recommendations/profile/` 和 `GET /api/recommendations/` 增加相同的 `cold_start_state`，版本为 `recommendations.cold-start.v1`。保留旧版 `cold_start` / `readiness` 等字段兼容性。

| code | 条件 / 引导 |
| --- | --- |
| `empty_journal` | 空手账；没有明确偏好时选择 3–5 个题材，有偏好则直接按明确题材推荐 |
| `all_unrated` | 非空手账且个人评分全部为 null，显示未评分记录数；0 分是已评分 |
| `no_qualifying_high_scores` | 没有“看过”且个人评分达到阈值的记录；不要求用户人为调高评分 |
| `insufficient_samples` | 有可分析样本但少于配置的最低数量（默认 3），1–2 部不生成完整画像 |
| `pending_identity` | 存在阻碍画像形成的待关联高分记录，复用用户确认身份入口 |
| `missing_genres` | 已关联安全高分作品缺少有效题材；不覆盖主动清空的私人标签 |
| `unavailable_samples` | 高分记录未收录或分级不安全/未知，不误称为空手账，不允许绕过安全过滤 |
| `ready` | 可分析安全样本达到最低数量；即使还有其他待关联记录也不降级 |

`reasons` 保留多个阻碍原因，优先身份核对、再题材缺失、再样本数量。所有原因都只在画像未就绪时阻碍推荐；`ready` 优先。

计数包括 `journal_record_count`、`rated_record_count`、`unrated_record_count`、`high_score_record_count`、`eligible_sample_count`（已关联安全高分作品，按 Bangumi ID 去重）、`usable_sample_count`（还必须有有效题材）、`pending_identity_count`（记录数）、`missing_genre_count`（去重作品数）、`unavailable_sample_count`（记录数）、`explicit_preference_count`。

`recommendation_basis` 是独立轴：`personalized`、`explicit_preferences`、`none`。不足样本但有明确偏好时显示“根据你选择的题材推荐”，证据仍由原推荐引擎按明确偏好生成。弱观察题材不会被隐式写入偏好。

HTTP 503 / 网络错误没有画像状态，前端显示错误与重试。HTTP 200 空推荐列表且 mode 非 `cold_start` 是筛选/候选无结果，显示恢复筛选入口，不当作空手账。画像、偏好与推荐并发读取快照不一致时提示重新加载，不显示混合摘要。

## 前端与季度兼容

`RecommendationProfileGuide.jsx` 提供独立口味便签与淡黄色引导卡；CSS 全部限制在 `.memphis-rec-container`。加载/失败不显示伪造的 0 计数，手机压缩为三项真实指标，详细设置复用原偏好面板。

`getSeasonDetails` 共用函数兼容 `YYYY-1/4/7/10` 和 `YYYY-01/04/07/10`，同时覆盖列表、手机列表、海报墙，不修改存储季度与排序逻辑。

## 安全验证（禁止使用正式库跑测试）

```powershell
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
cd frontend
npm test
npm run build
```

新增 API 测试覆盖分类、复合阻碍、阈值、明确偏好降级、0/null、所有状态、多用户隔离、只读、成人/未知分级详情与加入拒绝、503 与筛选空结果分离。前端使用实际 JSX 的 SSR 验证分类文案、入口、失败占位、口味指标、明确偏好标注及合同校验；不是浏览器视觉截图验收。

部署后人工验收：用不同测试账号分别制造七类画像条件，核对便签与引导按钮；收窄筛选确认显示筛选空状态；断网确认摘要不显示两个 0；查看已通过推荐加入的 `YYYY-01/04/07/10` 作品在列表与海报墙的四季图标。不要在正式账号中为测试批量改写评分或身份。
