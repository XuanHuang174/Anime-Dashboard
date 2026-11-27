# D1：年度回顾统计与月份归属接口

## 交付边界

D1 扩展现有 [C1 私人观看回顾](watch-review-c1.md) 的只读统计接口，复用观看轮次与事件。支持自然年度、十二个月分布、月份待补充明细；不新增数据表、迁移、依赖、永久年度报告或后台任务。

[D2 已接入年度选择、总览、月份分布、月度往返和推荐页批次操作区留白](watch-review-d2.md)。[D3 已完成年度长图导出与完整页面验收](watch-review-d3.md)，年度功能已交付。

## 请求与兼容性

| 请求 | 用途 |
| --- | --- |
| `GET /api/watch-review/?year=2026` | 全年摘要、日期覆盖、十二个月分布与月份待补充摘要 |
| `GET /api/watch-review/entries/?year=2026&section=works` | 年度作品清单 |
| `GET /api/watch-review/entries/?year=2026&section=completed` | 年度完成轮次 |
| `GET /api/watch-review/entries/?year=2026&section=month_unknown` | 属于本年但无法确定月份的事实 |
| `GET /api/watch-review/entries/?year=2026&section=month_unknown&page=2&page_size=20&snapshot=...` | 沿用同一读取凭据翻页 |

`year` 为 1000—9999 的四位年份，与 `month`、自选 `start/end` 互斥。空年份、重复参数、未知参数、外层手账筛选和用户 ID 均不接受。省略时间参数仍默认上海当前自然月，保持 C1 行为。

年度区间为 `YYYY-01-01` 至 `YYYY-12-31`，含两个端点，闰年覆盖 366 天。`period.mode` 为 `year`，时区仍为 `Asia/Shanghai`。当前年度也使用完整自然年；“年度进行中”由后续页面展示，不改变既有实际日期归属规则。

保留 `journal.watch-review.v1` 契约、`actual-date-contained.v1` 口径及原有字段。仅年度响应新增 `annual`；原月度／自选响应不增加该对象。原有分区均可用于年度查询；新分区 `month_unknown` 只接受年度模式。摘要接口仍不接受分页或分区参数。

## 年度响应扩展

年度摘要和每一页明细都携带相同结构的 `annual`，各页数据来自其签名凭据约束的同一资料版本。

| 字段 | 含义 |
| --- | --- |
| `annual.year` | 四位年份字符串，例如 `2026` |
| `annual.basis` | 固定 `effective_facts_by_actual_month` |
| `annual.months` | 按一月至十二月升序，固定 12 项；无记录月份仍返回零值摘要 |
| `annual.months[].month` | `YYYY-MM` |
| `annual.months[].facts` | 该月明确归属的事实条数 |
| `annual.months[].summary` | 与 C1 完整 `summary` 相同的字段和统计定义 |
| `annual.month_unknown.facts` | 本年明确归属、但只有年份精度的事实条数 |
| `annual.month_unknown.works` | 上述事实涉及的私人记录去重数 |
| `annual.month_unknown.summary` | 这组事实的完整摘要，包含开始、完成、重看、感想及完成轮次评分 |

`facts` 是开始／完成里程碑、有效观看足迹以及随记／补录的事实数，不是作品数或集数。各月事实与月份待补充事实互不重叠，合计等于年度 `summary.activity_facts + summary.notes`。作品可能同时出现在多个桶，因此去重作品数不可相加。

`month_unknown` 明细仍是原始事实，返回 `attribution=definite`、`date_precision=year` 和原始 `actual_date=YYYY`，`count_unit=facts`；不会补成一月一日或年末。它与年度全部作品／完成轮次等分区可有重叠，不能把分区计数相加当作年度总量。

## 日期与汇总规则

- 日、月精度的本年事实进入相应月份。
- 只知道年份且与所选年度相同：进入年度确定统计及月份待补充桶，不分配月份。
- 日期完全未知：不进入年度确定统计，也不进入月份待补充。`coverage.undated_scope=owner_all_history` 继续表示本人全部手账的日期待补充资料，不能称为本年度遗漏。
- 非本年的事实不进入月份分布。跨年观看分别按实际开始与完成里程碑归属，不推断中间月份的观看。
- 年度总览对全年有效事实直接汇总，不拼接或累加月度作品数。相同私人记录在两个月完成两轮，为年度 1 部作品、2 个轮次。
- 年度均分对有评分的有效完成轮次等权计算，返回样本数及缺失数；不平均各月均分，不用当前综合评分补缺。
- 作废轮次、已撤销事件不参与；未完成轮次遗留的完成日期不构成完成。资料更正不新增观看活动，日期更正会重新归属年份／月份；清空、删除和备份恢复后实时重新计算。
- 计划时间、作品开播时间、添加时间、系统记录时间都不能反推实际观看日期。

## 一致性与资源边界

同一次数据库只读快照中只读取一次本人记录、轮次和事件，构造一次事实集合。年度摘要与月份桶均从该集合计算，不发起十二组月度数据库查询。`annual.months[].summary` 与相同资料版本下对应月度查询的确定摘要一致。

继续使用数据库强制只读与 PostgreSQL `REPEATABLE READ`，不降级到可写读取。签名凭据绑定本人、年度区间及模式、资料指纹和统计口径，15 分钟过期；同日期范围的自选模式不能混用年度凭据。更正、恢复、删除或过期后返回 `409 watch_review_changed`，不返回混合资料。

沿用本人最多 10000 条记录、50000 个轮次、100000 条事件的完整读取边界，超限或数据库错误返回 503，不返回部分年度统计。GET 不物化虚拟轮次、不修改资料，接口仅本人认证可用并保持私有禁止缓存响应头。

## 验证

`anime.test_watch_review_annual` 覆盖空年、参数校验、自然年与闰日、全年去重、轮次加权评分、年份精度、跨年观看、月份与独立月度接口一致、日期／集数更正、撤销／作废／清空、稳定分页、账号与凭据隔离、恢复／删除、查询数量、超限／数据库失败、强制只读、计划隔离和真实 PostgreSQL 并发读取。

年度专项 17 项在 PostgreSQL 全部通过；与 C1 回顾、观看历程、A3、计划及 B3 合并回归共 118 项通过。SQLite 专项通过，跳过 1 项仅 PostgreSQL 可验证的并发测试。Django 系统检查与迁移检查通过，无新增迁移。

仅使用 `config.recommendation_test_settings` 的内存 SQLite 或 `config.recommendation_pg_test_settings` 的显式独立本机 PostgreSQL。没有操作正式账号或数据库。
