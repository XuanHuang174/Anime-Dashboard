# 5B-1：首批 7 项独立题材发布

## 已确认范围

在 11 项同义映射发布后，用户逐项确认了下表的定义及边界，并明确要求开始执行。本次仅增加这 7 个**精确标签**：

| 规范题材 | 定义 | 不自动等同于 |
| --- | --- | --- |
| 热血 | 强调拼搏、挑战、昂扬情绪的叙事气质 | 战斗、运动 |
| 后宫 | 围绕同一主角展开的多角色恋爱或暧昧关系结构 | 恋爱、成人内容、古代宫廷 |
| 玄幻 | 具有超自然力量、修炼体系等幻想设定的题材；具体作品只依据已有标签识别 | 奇幻、异世界 |
| 穿越 | 角色跨越时间或世界的叙事机制 | 异世界、转生 |
| 魔法少女 | 以魔法少女身份、能力及相关叙事为题材 | 魔法、变身 |
| 恐怖 | 以恐惧、惊骇体验为主要表达方向 | 悬疑、猎奇、成人内容 |
| 武侠 | 以武者、武学、江湖及侠义关系为题材 | 战斗、历史、修仙 |

定义仅用于解释既有标签，**不是让系统从标题、简介、长评推断题材的提示词或分类器**。`转生、修仙、惊悚、变身、后宮、魔法少女向` 等近义/相关/文字变体不在本次批准范围，不新增对应映射。既有 NFKC、首尾空白处理保留。

`BL、耽美、实验、超现实、猎奇` 这 5 项继续待审。所有未批准的草案别名保持不生效。前一阶段批准的 11 项映射完整保留，不新增其他别名。

## 版本与接口

- 规范词表 `genres.v3` → `genres.v4`：原 26 项顺序保持，尾部追加 7 项，共 33 项。
- 提取规则 `journal-genres.v2` → `journal-genres.v3`。
- 公共特征 `genres-idf.v3` → `genres-idf.v4`；缓存键切到 `recommendations.public-features.v4`。
- 题材发布标识 `genres.2026-10-14.approved-7`。
- 推荐算法仍为 `content.v1`：不改排序公式、系数、候选池上限、多样性策略或分页大小。

`GET /api/recommendations/preferences/` 的 `available_tags` 自动包含新题材。原 React 偏好面板直接遍历该字段，无需修改 JSX、CSS 或布局。选项总数增加不代表替用户勾选；每人最多选择 20 个题材的限制不变。

公共候选和私人画像沿用同一个规范词表：只提取原本存在的精确标签。同作品的重复项、基础与自定义字段中的相同题材只贡献一个特征，同时保留证据来源。

`魔法少女` 不会自动补 `魔法`，`后宫` 不会自动补 `恋爱`；原始标签同时包含两者时允许保留两个有证据的特征。旧手账的陌生标签原样不动，已经存在的新题材可在下一次读取画像时被识别，不进行批量写回。

## 数据与合规保护

- 不改原目录/私人标签、个人评分、长评、状态、身份和明确偏好。
- 用户主动清空私人标签时，不从共享资料或推荐目录回填。
- 未评分、主动保存的 0 分及其他观看状态只按现有规则统计，不当作高分喜好。
- 手账常见题材仍需用户确认，只进本地草稿，统一保存后生效。
- 成人与未知分级继续由后端过滤；“后宫/恐怖”等题材不是分级证据，不据题材修改 NSFW。
- 不读取长评、不推断系列/续作、不请求外部 AI 或资料服务。
- 个人画像仍需至少 3 部安全、已关联、看过且个人评分达标并有有效题材的作品；题材扩充不降低门槛。

## 验证与部署

```powershell
.venv/Scripts/python.exe manage.py test recommendations.tests.test_genre_release recommendations.tests.test_alias_release recommendations.tests.test_tag_coverage recommendations.tests.test_quality_baseline recommendations.tests.test_engine_api recommendations.tests.test_extraction_api recommendations.tests.test_contracts_api --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

前端在 `frontend` 中执行 `npm test`、`npm run build`。新增测试验证实际 JSX 能显示 33 项动态选项、不自动勾选、无父级推断以及 20 项上限；只新增测试，没有前端运行代码改版。

画像及列表请求自动使用新规则；旧缓存不会混用。分页快照包含题材/特征/提取版本，旧版本第二页返回原有 JSON 409，重新读取第一页后可正常翻页。原始题材未变化的冻结样例继续验证 V1 排序不变。

部署新后端进程后生效，无需迁移或重新入库；不扫描全站私人手账来“刷新”记录。推送完成不等同于线上部署后联调已通过。

## 真实目录只读验收

与 **11 项映射已经启用、7 项题材尚未启用** 的 `genres.v3` 快照比较，避免把两阶段收益混在一起：

```powershell
.venv/Scripts/python.exe manage.py audit_recommendation_tags --compare-before-dir .tools/recommendation-tags/5b1-approved-11-final --output-dir .tools/recommendation-tags/5b1-approved-7-final
.venv/Scripts/python.exe manage.py audit_recommendation_quality --include-probes --output-dir .tools/recommendation-quality/5b1-approved-7-probes
```

默认设置读取 `.env` 指定的数据库，本项目可能为正式 Neon；审计使用数据库强制只读一致性快照，不运行迁移或测试。仅扫描明确非成人公共目录，不访问私人用户表。

`--compare-before-dir` 校验旧报告完成标记、SHA-256、版本、统计一致性与目录指纹。当前 `genres.v4` 可比较 `genres.v3`（本阶段）或 `genres.v2`（累计变化），报告明确写出版本；不同目录快照拒绝归因。输出保留本地忽略目录，不提交原始标签/作品样例。

报告包含实际有效题材覆盖变化，以及 7 项新标签各自的作品频率；同作品可包含多个题材，不能直接累加频率当作新增覆盖数。覆盖率不是用户喜欢概率或推荐准确率。合成题材探针只检查排序管道和证据，不冒充真人满意度评测。
