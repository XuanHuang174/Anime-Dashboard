# 核心 4A：只读偏好提取与数据合同

## 边界

复用受登录保护的 `GET /api/recommendations/profile/` 和独立的
`GET/PATCH /api/recommendations/preferences/`。用户只能由 `request.user` 决定，
不接受查询中的用户 ID、关闭安全过滤开关或任意提取规则。

4A 不新增页面或复杂排序算法，不新增模型/数据库迁移，不重跑全站身份关联。
画像 GET 不写入偏好、标签、评分、长评、时间戳或身份关联，也不读取长评字段。
所有特征在本进程提取，不调用外部模型、HTTP 服务或图片服务。

## 版本与兼容

| 版本 | 值 | 范围 |
| --- | --- | --- |
| 外层画像/偏好合同 | `recommendations.3a.v1` | 保留当前页面需要的字段，添加扩展字段 |
| 新增提取合同 | `recommendations.extraction.v1` | `profile.extraction` 内三类信息及诊断 |
| 标签词表 | `genres.v2` | 加入真百/真百合到百合的确定映射 |
| 提取规则 | `journal-genres.v1` | 基础/自定义题材、副本清洗、来源与计数 |
| 推荐特征版本 | `genres-idf.v2` | 与新词表配套；排序公式不变 |

公共特征缓存升级独立键，私人画像和手动偏好不进入公共缓存。分页 HMAC
加入提取版本和样本题材字段来源；题材相同但来源变化时也会要求重读第一页。
未确认常见题材不参与排序或推荐理由。

## 三类信息

### 明确偏好

`extraction.explicit_preferences` 复用偏好接口的完整合同，包含规范题材、版本、
更新时间及可选题材。独立保存在 `RecommendationPreference`；自动画像只读取，
绝不覆盖。空手账也可通过用户显式 PATCH 题材进入现有 `preference_based` 模式。

### 高可信画像

只取看过、个人评分达到默认 8.5、身份已明确且推荐目录 `nsfw is False` 的作品。
不使用 Bangumi 评分、观看进度、管理标签或长评替代个人评分。

- `eligible_sample_count`：按 Bangumi ID 去重的安全已关联高分作品数，保留原含义。
- `usable_sample_count`：其中至少有一个有效题材的作品数。
- `cold_start`：可分析样本未达到默认 3 部；缺题材不再被算成完整个性化画像。
- `readiness`：保留 `needs_identity / needs_more_samples / needs_tags / ready`。
- `sample_policy.requires_usable_tags`：新增，明确有效样本需要可解释题材。

同一标准作品的多条高分旧记录沿用既有规则：最高个人评分记录代表该作品，
评分相同则取最小私人记录 PK；不合并旧记录、不改评分。代表记录的私人题材
清空后，不从另一份旧记录、公共目录题材或同名作品补回。不同季度/续作不同 ID
分别计数，绝不按片名推断身份。

### 待确认的常见题材

`extraction.common_topics` 仅观察安全已关联、未进入高可信样本集合的作品：
未评分、低分、在看、想看、搁置等均不自动代表喜欢。

- `requires_confirmation: true`
- `used_for_recommendation: false`
- 按 Bangumi ID 去重；同作品多份观察记录取最小 PK，避免重复放大统计。
- 已作为高可信样本的同 ID 不再进入常见题材集合。

未关联或冲突、失效身份只输出既有计数诊断，不尝试自动关联或猜测题材。
成人和未知分级不进入任何题材提取、画像样本或常见题材集合。

## 标签清洗与来源

读取有效私人基础标签和 `custom_tags`。尚未初始化私人快照的旧记录沿用现有
共享基础资料；已经初始化但为空的私人标签保持为空，不从目录补齐。

采用 NFKC、首尾空白处理、固定词表和明确别名，例如搞笑→喜剧、真百/轻百→百合。
年份、国家、作品自身中文/原名/私人名称、二刷/年度最佳/神作等管理标签不产生题材。
未知标签只在推荐副本中忽略，数据库中的原值保持不变；不作子串/模糊语义猜测。
历史异常 JSON 容器、非字符串元素安全忽略，不将字典键解释为题材。

每部作品每个规范题材计一次；同义词和基础/自定义字段重复不增加权重。
`source_work_counts` 标识 `private_base_tags / shared_base_tags / custom_tags` 来源。
同一作品同题材可以同时出现在基础和自定义来源，所以来源计数之和可能大于
`sample_count`，但单个来源计数不得超过样本数。

高分推荐结构化证据增加 `feature_rule_version` 和 `shared_tag_sources`，只输出
规范题材及来源字段名；私人记录 PK/未知原标签/长评不输出、不放入公共缓存。
内部样本保留对应记录 PK 供本进程追溯，推荐理由仍依据实际来源作品和个人评分。

## 提取合同示意

以下片段说明字段结构，不是生产数据或备用 Mock 返回：

```json
{
  "contract_version": "recommendations.3a.v1",
  "taxonomy_version": "genres.v2",
  "eligible_sample_count": 3,
  "usable_sample_count": 3,
  "cold_start": false,
  "readiness": "ready",
  "extraction": {
    "contract_version": "recommendations.extraction.v1",
    "rule_version": "journal-genres.v1",
    "taxonomy_version": "genres.v2",
    "explicit_preferences": {
      "contract_version": "recommendations.3a.v1",
      "taxonomy_version": "genres.v2",
      "tags": ["科幻"],
      "version": 1,
      "updated_at": "2026-10-13T08:00:00+08:00",
      "available_tags": ["日常", "科幻", "音乐"]
    },
    "trusted_profile": {
      "eligible_work_count": 3,
      "usable_work_count": 3,
      "ready": true,
      "tag_summary": [{
        "tag": "音乐",
        "sample_count": 3,
        "source_work_counts": {
          "private_base_tags": 2,
          "shared_base_tags": 0,
          "custom_tags": 1
        }
      }]
    },
    "common_topics": {
      "work_count": 1,
      "feature_work_count": 1,
      "requires_confirmation": true,
      "used_for_recommendation": false,
      "tag_summary": [{
        "tag": "日常",
        "sample_count": 1,
        "source_work_counts": {
          "private_base_tags": 1,
          "shared_base_tags": 0,
          "custom_tags": 0
        }
      }]
    },
    "record_stats": {
      "journal_record_count": 4,
      "rated_record_count": 3,
      "unrated_record_count": 1,
      "completed_record_count": 4,
      "safe_linked_work_count": 4
    },
    "ignored_tag_counts": {
      "invalid": 0, "year": 0, "country": 0,
      "management": 1, "work_title": 0, "unknown": 0
    }
  }
}
```

实际响应还保留旧 `tag_summary / has_usable_tags / sample_policy / diagnostics /
identity_service_ready`，偏好更新时间为真实值、可选题材为完整词表。`record_stats`
和旧诊断是当前用户手账的总量计数，可能包含未计入特征的记录；它们不是安全样本数。
未评分只用 `personal_score is None` 判断，用户保存的 0 分属于已评分低分，不是未评分。
忽略统计只统计已选取安全代表记录的标签条目，不返回未知标签原文。

## 验证与上线

```powershell
.venv/Scripts/python.exe manage.py test recommendations --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
cd frontend
npm test
npm run build
```

测试配置使用独立内存 SQLite，不连接 `.env` 的 Neon 正式库，也不连接 R2。
覆盖三类分离、空评分与 0 分、词表映射、私人空标签、重复身份、真实 JWT 隔离、
即时安全分级变化、原始字段完整保留、无长评查询/外部调用，以及来源变更的分页失效。

部署后无需 migrate 或批量更新。现有前端可兼容读取，细分冷启动文案与界面在 4B
接入；当前前端对新增常见题材不会自动选择或保存偏好。
