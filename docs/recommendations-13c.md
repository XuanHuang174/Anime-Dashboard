# 扩展 13C：人物与制作探索只读后端

日期：2026-10-27。承接 [13A 来源合同](recommendations-13a.md)、[13B 数据基础](recommendations-13b.md)和[扩展 13 方案](recommendations-extension-13-plan.md)。本轮实现登录态接口，未增加前端入口，未连接生产数据库、导入正式数据或修改 Render。声优与制作公司开关默认关闭；现有三种推荐、系列和提醒继续使用自己的开关与合同。

## HTTP 合同

均要求当前登录账号，响应 `Cache-Control: private, no-store`，Vary 包含 Authorization、Cookie。仅新增 GET，不增加公开匿名端点或写接口。冻结文件为 `recommendations/tests/fixtures/credits_http_contract.v1.json`；测试使用真实 Python HTTP 响应核对版本、公共字段和证据字段。

所有端点都接受已有的 `content_scope=safe|include_adult`；省略为 safe，既有 `X-Recommendation-Scope` 前置条件继续有效。未知、重复、空参数或错类型值返回 400；ID 必须为 1–2147483647。

| 路径，前缀 /api/recommendations/credits/ | 查询与结果 |
| --- | --- |
| search/ | 必填 kind=voice_actor\|studio、q；最多 15 项，另有 has_more |
| subjects/{bangumi_id}/ | 公司／声优 sections；默认每类最多 50 人，可 `expanded=true` 按需读取每类最多 5000 人 |
| record/{record_id}/ | 本人记录的 identity_state、已确认 bangumi_id；不会猜测身份或建立绑定 |
| entities/{kind}/{person_id}/ | entity 名称、当前内容范围内已确认的目录关联作品数、资料范围说明 |
| entities/{kind}/{person_id}/works/ | 筛选后的精确 count、每页 12 部的 results、context、next_cursor、expires_at |

搜索接受标准名、已收录别名、`person:ID` 或完整 Bangumi person 链接。NFKC、大小写与空白规范化只参与检索，不合并重名 ID；裸数字、subject 链接及任意外站 URL 拒绝。服务不会访问用户提供的网址。

作品摘要明确给出每类 status、last_attempt_status、count、limit 和 has_more；count 是当前可展示的去重实体数，而非本次返回数组长度。展开清单仍受来源及响应限额保护；超限返回不可用，不能把截断结果称为完整清单。`observed`／`empty_observed` 表示本次来源观察状态，不证明完整出演年表或“作品没有声优”。

## 筛选、计数与证据

作品列表支持 view=all|completed、sort=score_desc|release_desc|release_asc、type、year_from/year_to、min_score/max_score/max_score_exclusive、hide_recorded 和 cursor。

- 默认 all、score_desc、hide_recorded=true。“我看过的”必须显式使用 view=completed&hide_recorded=false；只采信本人已确认且已看完的记录，拒绝冲突条件。
- 评分规则复用既有模块，严格低分上限排除未知的 0 分。年份复用已验证的目录放送时期，不从作品名猜测。
- 日期排序标识为 `credits.catalog-period.v1`，输出 release_period 和 `release_precision=catalog_period|unknown`。它按目录中已知的年／月／日片段排序，不声称季度是确切发行日，不依赖系列开关；未知年放最后，未知月／日放在已知父时期内的最后，平分以 Bangumi ID 固定排序。
- `entity.work_count` 是当前内容范围内、具有有效参与证据的目录作品总数，不随列表的年份／评分／隐藏已有记录条件变化。列表 count 则在全部条件、隐藏反馈和身份冲突排除后精确计算，两者含义不同；不得拿当前一页的长度当总数。
- 一人多角、联合制作均保留证据，分页和计数先按作品去重。证据包含 subject_id、person_id、kind、position_or_cast_type、characters、来源备注、归档版本与时间。角色按已知定位 1/2/3、来源 order、固定 ID 排序；未知定位保留原码与警告，不能编造主配角标签。
- 章节备注只显示为来源文字，不解析成未经核验的参与集数。公司只接受 type=2 且 position=67；演员只接受 type=1 的原声 CV type=0 与具体作品角色绑定，不把译配、真人演员或全局同一角色扩散到其他作品。

## 内容、来源与隐私边界

每条展示证据必须同时满足：当前完整发布指针指向其自身作品和 kind、使用同一快照的实体／角色、初版职务／出演类型、受支持的来源映射与别名版本、45 天内且非未来的来源时间、动画来源身份、来源分级与当前合格目录一致。

姓名与别名只从这些当前可见观察实际引用的人物版本取出。新版 partial 不覆盖旧版完整观察；旧版仍为当前观察时继续使用其自己的姓名与别名。旧观察完全退出后，其历史别名不能再命中。原声来源 incomplete 不会牵连完整的公司来源；资料未加载、空观察、partial、failed、stale、关闭分别呈现。

搜索及实体计数先通过内容范围过滤，再返回实体；没有可见关联时返回通用 404，不提供跨范围作品或角色证据。NSFW 继续要求现有总开关、账号／本次浏览范围、有效逐作品审核及来源分级一致；未知分级始终排除。即使具备成人内容资格，封面仍沿用既有序列化规则，不输出成人来源封面。

私人记录只按当前用户读取身份字段与观看状态，不选择个人评分、评价或偏好标签，不读取其他用户记录。作品列表排除隐藏反馈及身份冲突候选；GET 不写手账、不修改偏好、不自动附加身份、不产生反馈操作。

## 稳定分页与响应边界

初次请求及续页都在独立 PostgreSQL REPEATABLE READ、READ ONLY 事务中读取；不回退到普通可写事务。每条查询超时为 10 秒，锁等待最多 3 秒。响应发送前再次检查开关、目录／公共关系修订、内容范围；涉及本人状态时同时复核本人身份／观看状态／反馈，避免返回计算期间已经失效的结果。

游标签名并绑定账号、认证会话、账号认证哈希、内容范围、参与关系修订、全目录指纹、本人身份／状态、隐藏反馈、实体、完整条件、排序版本和独立放行修订。游标只含条件及不透明修订／结果顺序摘要，不包含原始 token、密码、个人评分或评价。签名不等于加密，客户端不得把游标当作不可信条件编辑器。

有效期最多 900 秒，且不晚于当前内容范围到期时间。续页不会延长原始 expires_at。只传 cursor 时沿用签名条件；显式提供的条件必须整体匹配。先验证所有修订及结果顺序摘要，再按固定顺序取下一页；不重新运行推荐排名，也不保留服务端私有结果缓存。条件、目录、身份、反馈、范围、放行批次变化或过期均要求重新读取。

错误区分：503 `credits_disabled`、503 `credits_data_unavailable`、409 `credits_snapshot_changed`、既有内容范围错误，以及 400／通用 404。前端不能因为 credits_disabled 就重置用户 NSFW 选择。

读取上限：目录／本人记录各 50000、单作品单类原始展示事实 5000、单页事实 10000、响应紧凑 UTF-8 JSON 预算 4 MiB、游标 4096 字符、搜索 15 项、作品页 12 部。超限明确失败，不出具部分成功的精确总数。所有浏览只读本地索引，无上游请求。

## 独立开关

新增配置，13C 保持默认值即可，无需本阶段开放：

```text
RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED=False
RECOMMENDATION_CREDITS_STUDIO_ENABLED=False
RECOMMENDATION_CREDITS_CANARY_USERS=
RECOMMENDATION_CREDITS_EPOCH=1
```

两个 enabled 独立控制；灰度名单按精确用户名匹配，格式错误关闭服务。名单为空只表示在对应开关开启后向所有登录用户开放，并非自动启用。后续回退／重新发布时须更换 EPOCH，使旧分页不能跨放行批次恢复。能力端点新增独立 credits 字段和 voice_actor／studio 布尔值，不改变 exploration 字段或三个主导航。

本轮无需新迁移，仍依赖 13B 的 `0016_credits_foundation`。缺表或数据库故障返回受控不可用；能力标记不等于资料覆盖已通过上线门槛。13F 才进行真实环境覆盖、性能、灰度和多账号验收。

## 验证与 13D 交接

测试在项目专用的本机隔离 PostgreSQL 运行，使用合成账号／归档／目录；没有接触生产数据。覆盖身份与会话隔离、成人核验与撤回、独立灰度、空／失败／过期来源、同名多 ID、历史别名退出、源端点异常、展开清单、精确去重计数、跨页与筛选绑定、响应期间并发变化、数据库强制只读和输出／查询限额。

组合回归 191 项通过，包含 13A／13B／13C，以及既有系列后端、本人工作区、探索浏览／基础合同、一般成人内容范围、系列／探索放行与回退测试。最后补齐范围响应预算和关闭状态字段后，13C 的 21 项测试再次全部通过。Django check 无问题；makemigrations --check --dry-run 无新增迁移。

另有 50000 条合成目录、10000 部单公司作品、10000 部单声优作品各两角的 HTTP 基线：新增 30000 条参与事实，两类冷读／重复读／续页各三次，最终测得 1.1848–1.3472 秒、每次 33 条 SQL。仅为本机无负载基线；未测 Render 网络、服务器内存、并发负载或真实全库覆盖。

13D 对接上述 GET、独立 capability 和准确的空／partial 提示。预览详情首次展开时请求 expanded=true；编辑窗口不接入。正文姓名、角色名和备注作为纯文本渲染。列表返回 watch_status，completed 读条件已就绪；13E 再完成对应前端与原有详情／幂等加入想看闭环，不可把本次 context 当成其他接口的 evidence_token 使用。
