# 扩展 12A：关系来源、分类合同与样本核验

日期：2026-10-25。状态：离线基础交付。无数据库表、迁移、HTTP 路由、环境变量或前端改动；无生产库/R2 访问，不改现有三种推荐结果。

## 1. 官方来源核验

| 来源 | 本轮验证 | 用途及限制 |
| --- | --- | --- |
| [Archive 说明](https://github.com/bangumi/Archive/blob/master/README.md) | 官方文档 HTTP 200；本地 ZIP 原文实读 | `subject-relations.jsonlines` 包含 subject_id、related_subject_id、relation_type、order；subject.jsonlines 提供对应条目 type |
| [关系常量](https://github.com/bangumi/common/blob/master/subject_relations.yml) | 官方文件 HTTP 200，固定文件 SHA256 | 仅在两端均确认是动画时应用动画关系映射 |
| [API schema](https://github.com/bangumi/api/blob/master/open-api/v0.yaml) | 官方文件 HTTP 200，固定文件 SHA256 | 正确关系路径是 `/v0/subjects/{subject_id}/subjects`，返回目标 id/type 和文字 relation；没有数字 relation_type 或观看顺序 |
| API 实际关系响应 | 对 328609、436738、1424、3774、265、10639 的六次只读 GET 均 ConnectTimeout | 未取得真实 API 样本，不宣称实时方向、权限、NSFW 可见性已验收；API 适配器仅基于官方 schema 和合成测试，未启用 |

采用已下载归档 `dump-2026-10-06.210359Z.zip`；本轮重新计算 ZIP SHA256，与此前官方 manifest 一致：`2f4273f1aa5d6932c8aa2c448f91771fe0d07ec69c1ef64b4c5098f0760f8d99`。这不是 10 月 25 日的最新线上数据。

完整关系成员实读 **937153 行**，成员 SHA256 为 `88ff5fa3bfd547a0d32e115d4310f321e043f44a1d36125ec46d2006fe76d60c`。扫描只统计全成员行数并抽取预选来源，未对全库关系质量作审计。

来源文档 SHA256、ZIP 摘要、成员摘要和抽样时间记录在 `recommendations/tests/fixtures/series_source_sample.v1.json`。摘要用于复核本轮内容，不把可变 master 链接当永久版本。更新映射时需重新核验并升映射版本。

## 2. 真实样本范围及结果

来源 ID 共 13 个：1424、3774、328609、436738、459642、265、6048、6049、10639、9717、25833、44693、334105。选择依据为直线续作、总集篇链、分支、不同演绎、故事/发行顺序不同，不用于估计目录整体覆盖率。

完整保留这 13 个源条目的 **415 条出向关系**；350 个端点从同一 ZIP 的 subject.jsonlines 核验媒介，未用推荐目录中“查不到”推断媒介。样本仅保留必要事实字段，未复制简介、封面、用户资料或长评。

- 动画间关系 64 条，跨媒介 351 条；规范化行错误 0、未知关系 0、重复 0。
- 抽样图未发现故事前后关系循环，未发现相反方向已返回却不符合检查配对的情况。
- 12 条配对未在**本样本来源集合**取得反向断言，目标均未作为本次抽样源；不能说官方缺失、单向或错误，更不能自动修复。
- 数据缺失、真正单向、冲突、循环及未知枚举另用合成测试验证，不伪称已在真实样本发现。

机器结果：[12A 样本审计](recommendations-12a-source-audit.json)。其中 `anime_assertions` 仍是来源观察，未变成线上可展示作品或合格推荐候选。

| 场景 | 本次归档观察 | 分类决策 |
| --- | --- | --- |
| 直线续作 | 1424 → 3774 是续集；3774 → 1424 是前传 | 确认目标相对源的方向，但不直接生成必看顺序 |
| 总集篇仍有后续关系 | 328609 → 436738/459642 均为总集篇；436738 → 459642 又是续集 | 保留多条关系；不能把“续集”和“总集篇”设成作品互斥全局类型 |
| 完整故事反向 | 436738 → 328609 为全集 | 不能倒置成原 TV 是总集篇 |
| 剧场版并非一类关系 | 9717 → 25833 为总集篇；9717 → 44693 为续集；3774 → 12426 为番外篇 | 平台剧场版与关系分类独立 |
| 不同演绎 | 265 → 772 为不同演绎 | 不自动合并成一条连续剧情链 |
| 分支及弱关联 | 10639 同时有多个续集、不同世界观、相同世界观及角色出演 | 保留分支；不由这些边推出全系列归属 |
| 故事与发行顺序 | 10639（2011）→ 290（2006）为续集 | API/Archive relation 和日期都不是自动观看指南 |
| 未定档 | 328609 → 537409 是续集，目标 date 为空 | 保留关系，不能虚构日期或“现在可以观看” |

以上仅陈述本次归档中的标注，不保证标注完备或等同于专家观看攻略。包括《死与新生》等含混合内容的作品，只说“来源标为总集篇”，不自动建议跳过整部。

## 3. 合同与代码

`recommendations/series_contract.py` 不加载 Django，不联网或读写资料。

- 合同：`recommendations.series.assertion.v1`。
- 映射：`bangumi.anime-relations.v1`。
- 支持 14 个动画关系种类，按 chronology / editions / branches / related 提供展示分组提示。分组提示不是排序、系列归属或连续剧情证明。
- 原始 archive 数字值、API 文字值及来源排序单独保留。API 使用精确中文源标签；其他语言/新标签不会猜测翻译。
- 只有动画对使用动画映射。归档即使源是动画，也会带 3003、1004 等音乐/书籍关系码，已验证非动画目标统一作为跨媒介断言保留；未知目标媒介进入待核验，不假设它是动画。
- 自环、非法 ID、布尔数字、错类型、归档字段漂移拒绝；未知关系保留原值，置 unknown/requires_review，不归入续集。
- 来源 envelope 必须含 source_kind、source_reference、snapshot_version、observed_at（带时区）、payload_sha256。API 快照版本仅是本地观察版本，不能冒充上游修订号。
- `validate_provenance` 只校验形状与允许的官方来源格式；实际字节校验、访问权限、审核与来源真实性由 12B 导入服务完成。单行规范化器不接收或签发可信凭证。

`inspect_assertions` 检查重复、多种关系同对、选定配对的反向不符和故事前后环。诊断不删边、不补反向边、不产生系列合并。前传/续集、总集篇/全集仅用于检查已取得的反向观察；番外/主线/衍生不自动建立逆关系。

合同列出的覆盖状态供 12B 存储设计使用，12A 未接入状态服务：

| 状态 | 含义 |
| --- | --- |
| not_loaded | 未读取 |
| observed | 已取得该范围的来源观察，不代表完整系列 |
| empty_observed | 成功观察中无关系，不证明独立作品或没有被权限过滤 |
| partial | 限额、范围或资料不全 |
| failed | 获取/解析失败，不能覆盖成空 |
| stale | 超过刷新策略，等待重新核验 |

发行形式属于节点，关系属于断言；已有 season 字段是季度化资料，后续需要精确发行日期时应保留官方原日期及精度，不用季度首日冒充实际首播日。

## 4. 可复核的离线审计

```powershell
.venv/Scripts/python.exe -m recommendations.series_audit --input recommendations/tests/fixtures/series_source_sample.v1.json --output .tools/12a/recheck.json
.venv/Scripts/python.exe -m unittest recommendations.tests.test_source_pipeline recommendations.tests.test_series_contract -v
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

审计仅接受本地、最多 2 MiB / 每集合 5000 行的策划样本，输出输入样本摘要、计数、诊断及动画断言。不载入 .env、Django 或数据库；默认拒绝覆盖已有输出。重复执行须换一个明确输出路径。`seed_ids_without_normalized_assertions` 只描述本次规范化结果，不能当作已确认无系列。

本轮 43 项来源/合同测试通过，其中新增 16 项；Django check 与无新增迁移检查通过。覆盖双格式映射、跨媒介、方向、总集篇复合关系、日期先后差异、未知/自环/循环/分支/冲突、来源结构、CLI 不覆盖和无网络依赖。前端未改，无需重新构建；未跑全量 PostgreSQL 推荐回归，因本阶段尚未接入任何在线调用点。

## 5. 下一阶段

按[扩展 12 整体方案](recommendations-extension-12-plan.md)推进 12B。此次 API 实际响应未验收应保留为明确门槛；归档基线已经可用，不以网络失败为理由改用标题推断，不在此阶段偷偷放行系列过滤或自动观看顺序。
