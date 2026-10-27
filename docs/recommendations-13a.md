# 扩展 13A：人物、公司与出演关系合同

日期：2026-10-27。状态：离线基础交付。遵循[扩展 13 方案](recommendations-extension-13-plan.md)。本轮没有数据库表、迁移、在线路由、环境变量或前端变化；没有连接生产数据库、修改私人手账、同步生产资料或调整推荐排序。

## 官方来源核验

| 来源 | 本轮取得的证据 |
| --- | --- |
| [Archive 说明](https://github.com/bangumi/Archive/blob/master/README.md) | 官方 GitHub 内容 API 200；确认人物／公司、角色、职务和具体作品出演关联 |
| [职务常量](https://github.com/bangumi/common/blob/2ee87b9995353f77cf02e390e8635bf97dbc1fa2/subject_staffs.yml) | 固定 common 提交，文件 SHA256 `d12e575c0f28f2b9793027bced2ecc6d3857ecd080839d5917023f7eca9d8547`；67=动画制作、42=製作、63=制作 |
| [人物与角色关系常量](https://github.com/bangumi/common/blob/2ee87b9995353f77cf02e390e8635bf97dbc1fa2/person_relations.yml) | SHA256 `d9c56dc55836440010fec5eb384aaa2eeb2bae78063e6a41cf047890d800108e`；0=CV、1=译配、2=演员、3/4/5/6=中/日/英/韩配 |
| [官方导出实现](https://github.com/bangumi/server/blob/master/cmd/archive/main.go) | 内容 blob `16f22a840faeddea7e3a0e769ca9e289c94033cc`，文件 SHA256 `c0f714dfea9730d575da3b223e73465cd8184e8e02c098e0f8afd4e0b4182fca`；确认 person-characters.type 是 Cast.RltType，不能和角色实体类型混用 |
| [API schema](https://github.com/bangumi/api/blob/65d29cff2331e08c0110d30d515f7f1b6488f845/open-api/v0.yaml) | 固定提交，文件 SHA256 `5a7ddb7ddec132293b1aa08102e6ac63e31b2925574658e927d6f931df2519da`；核验 /subjects/{id}/persons、/subjects/{id}/characters 和 /persons/{id}/characters 的不同结构 |
| 实际 API 响应 | 对 328609 的 persons、characters 两次无凭据只读 GET 均 ConnectTimeout。未取得真实响应，不宣称实时数据、权限或配音语种可见性已验收 |

原始文件域名部分请求超时，改用同一官方 GitHub 内容 API 取得常量和说明；未换用第三方映射。文件及 blob 摘要均保留在[机器审计摘要](recommendations-13a-source-audit.json)。master 链接仅方便阅读，不视为永久版本；复核以所记内容 hash 为准。常量定义是数据合同参考，没有复制上游应用实现。

特别注意：API `RelatedCharacter.type` 表示角色／机体等实体种类，`relation` 才是主角等定位；`actors` 列表没有归档中的原声／译配关系类型。`PersonCharacter.type` 同样是角色实体类型。不能把这些 API type 当成归档 person-characters.type，也不能把 actors 全部断言为 type=0。此次不交付或启用 API 出演转换器。

## 真实归档样本

使用本机已下载的 `dump-2026-10-20.210332Z.zip`，SHA256 为 `9fe8de272398ff4bd12994b73e25aac37cca631372383f6aadc503528623aa93`，与既有官方 manifest 一致；在提取前后重新校验。它不是 10 月 27 日的最新线上快照。

预先选定 8 个来源：265、1424、3774、8019、9717、10639、328609、436738，覆盖联合制作、相同公司跨作品、原版与总集篇、多个角色和不同配音版本及成人作品。策划样本不是随机抽样，不估计全库覆盖率。

全成员实读并计算 SHA256：subject 697,963 行、person 104,055 行、character 224,679 行、subject-persons 2,232,175 行、subject-characters 452,377 行、person-characters 292,429 行。只是完整扫描并选取指定来源，不是全成员质量审核。

提交样本保留八部作品的全部 1,697 条职务、336 条出演、275 条作品角色绑定，以及这些关系引用的最小实体投影：1,372 个人物／公司和 234 个角色。未复制简介、头像、个人履历、出生资料、用户信息、凭据或私人记录。另保留四条从同一归档明确核对的简体名称及其字段行；不代表完整别名覆盖。

| 观察结果 | 决策 |
| --- | --- |
| 9 条动画制作关系，涉及 7 家公司 | 仅 position=67 且 type=2 满足初版来源条件；不自动成为线上候选 |
| 265 同时关联 GAINAX（93）与タツノコプロ（696） | 保留联合制作，不合并成第三个实体 |
| 京都动画（2481）、CloverWorks（32356）各出现在多个样本作品 | 使用稳定 person ID，不依赖手账 studio 文本 |
| 原声 CV 246 条、译配 1 条、中配 80 条、英配 9 条 | 不混并；初版仅原声 CV |
| 其中人物 41073 的职业含 seiyu，但实体 type=2 | 该条不按个人出演放行，不凭名称或职业自动纠正公共来源 |
| 245 条出演符合初版来源条件 | 已确认动画＋个人＋角色端点＋同一作品角色绑定＋CV；与作品审核、目录纳入分开 |
| 69 条出演涉及角色定位 4/5（含非初版配音） | Archive README 仅说明 1/2/3；其余保留原值、标记 unmapped_character_role，不虚构定位，具体出演仍可独立成立 |
| 2,033 条规范化成功，解析错误／重复／同身份冲突均为 0 | 缺失、冲突、未知值、同名消歧和跨版本误联接用独立合成测试验证，不伪称样本已出现所有异常 |

`source_eligible` 只表示符合初版来源规则，不代表 safe、审核通过、可见、没有剧透或完整作品履历。原始归档 nsfw 只用于源分类审计，不能替代线上资格。

## 代码与冻结合同

- `recommendations/credits_contract.py`：独立命名空间、最小实体投影、只检索不合并的别名规范化、人物链接解析、职务与出演规范化、归档成员来源 envelope 校验。
- `recommendations/credits_audit.py`：最多 2 MiB、每集合最多 5,000 行的离线样本审计，记录不支持范围、角色注记、重复、冲突和行错误。保留原始数值，不删改源数据或构造缺失绑定。
- `credits_contract.v1.json`：来源合同冻结为 `recommendations.credits.assertion.v1`，映射为 `bangumi.anime-credits.v1`。
- `credits_http_contract.v1.json`：只冻结未来检索、摘要、本人记录身份、实体头部、作品列表的路径及上下文。明确 planned_not_implemented；不添加主导航，不部署 GET 或写接口。
- `credits_source_sample.v1.json`：公开事实投影、各成员 hash、来源观察及失败 API 探针，供后续重复核验。

来源 envelope 校验只能检查形状、允许来源、时间与摘要格式；13B 仍须核对真实 ZIP／成员字节、完整观察和范围，不能把自行填写 hash 的样本当作可信导入凭证。规范化函数不读磁盘、不联网、不初始化 Django；CLI 只读取明确输入并输出新报告，默认不覆盖。

```powershell
.\.venv\Scripts\python.exe -m recommendations.credits_audit --input recommendations/tests/fixtures/credits_source_sample.v1.json --output .tools/13a/NEW-report.json
.\.venv\Scripts\python.exe -m unittest recommendations.tests.test_credits_contract recommendations.tests.test_series_contract recommendations.tests.test_source_pipeline -v
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

测试共 58 项通过，其中新增 15 项：原声／译配／演员隔离、职务和实体类型、跨媒介、缺失端点、同角色跨作品、同名 ID 不合并、未知角色定位、固定来源、重复／冲突、上限、离线运行及 CLI 不覆盖。Django 检查及迁移一致性检查通过。未修改前端或在线服务，因此不重跑页面构建或全量 PostgreSQL 业务回归。

## 13B 交接

按[扩展 13 方案](recommendations-extension-13-plan.md)实现独立存储、完整观察指针、批量预检导入、失败保留和别名字段解析。将本次发现的实体类型冲突作为待核验事实保留；不能为了覆盖率自动纠正。正式覆盖需对真实目录分别审计声优和公司，并区分源完整性、实际关联与候选资格。语种扩展、复杂艺名关系、API 实时补全均不由 13A 的 schema 样本自动放行。
