# M1：人工访问申请与超级管理员审批

日期：2026-10-21。替代原 N2-1 的成年依据签发方案，复用既有资格表、审计表和后台入口。**人工访问授权不等于年龄核验；本阶段没有开放 NSFW 推荐。**

## 1. 范围与实际呈现

- 推荐页现有筛选 Dialog 内新增“受限内容访问”小区块，打开时才读取本人申请；不阻塞首屏推荐，不增加新弹窗或仪表盘。
- 无申请：阅读提示、勾选确认、点击“申请开启”。该按钮是独立操作，不是筛选“应用”，更不会开启 NSFW。
- 待审批：显示等待超级管理员审批，不能重复点击申请；“刷新审批状态”仅 GET。
- 已批准：显示批准和有效期，同时明确“NSFW 推荐尚未开放，暂不能开启”。无可用 NSFW 开关。
- 已拒绝：显示给本人的简短提示，可重新申请；内部审批原因不返回用户。
- 已撤销/到期：可重新申请，不自动续期。历史记录提示重新申请，不自动承认旧资格。
- 状态加载失败不显示成“未申请”；提交结果不确定时可用同 UUID 确认原操作。409 需要重新读取，不覆盖新决定。
- 关闭面板不会自动申请；已主动提交的请求即使关闭面板，也可能已在服务端完成，重新打开以 GET 确认。取消网络请求不承诺撤销已经提交的事务。

普通筛选、V1 排序、三类推荐、资源服务、私人评分/长评/标签及公共作品展示规则不变。申请成功只标记现有浏览上下文已变化，不强制重载整页或丢掉批次；重新应用筛选/重载时读取最新上下文。没有新增焦点触发的全量推荐刷新。

## 2. 增量模型与历史保护

`0009_manual_access_application` 在现有表上增加：

- authorization_source：legacy / manual；所有旧行默认 legacy，不自动转成人工授权。
- requested_at、decided_at：申请及决定时间。
- decision_message：明确面向本人的拒绝提示，与内部审计 reason 分开。
- rejected 状态和 apply / approve / reject 审计动作；原有动作值继续允许留在历史审计中。

旧记录、原核验字段和审计均保留，没有回填批准、自动授权、扫描私人手账或赋予权限。迁移只扩展结构和约束，不修改用户手账。显式重新申请时才将本人当前记录切换为 manual，清理已不适用的当前核验字段；历史审计不改写。

manual 分支的约束要求真实申请时间、有效状态/版本及批准期限，核验字段必须为空/0，防止将人工审批伪装成 verified。旧 active 行保留但有效状态为 legacy_unavailable；不会继续依赖模拟年龄证明。

## 3. 合同与接口

合同 `recommendations.eligibility.manual.m1.v1`；策略 `eligibility.manual.closed.v1`。

| 接口 | 行为 |
| --- | --- |
| GET /api/recommendations/eligibility/ | 本人状态读取，不建行，不修改过期记录 |
| POST /api/recommendations/eligibility/apply/ | 本人显式申请，不能指定其他账号/批准状态/期限/来源 |
| POST /api/recommendations/eligibility/revoke/ | 保留本人主动停用，不能替他人操作 |

两个本人写入接口接受且只接受：expected_version（整数）、operation_id（UUID）、confirmed（必须为 JSON true）。查询参数、未定义字段、外部 user_id、客户端状态/来源/期限、未明确确认均拒绝。沿用小请求体限制和推荐写入限流。

GET 及成功写入的 eligibility 返回：administrative_status、effective_status、authorization_source、manual_authorization_valid、eligibility_version、requested_at、valid_until、decision_message、can_apply，以及主体/上下文指纹。

兼容字段 administrative_qualification_valid 表示人工授权当前是否有效，不代表成年。age_assurance_status 固定 not_performed；adult_access_allowed=false、serving_integration=false、effective_scope=safe 固定。没有公开的批准/拒绝/续期接口。

未登录 401；无审批权限 403；非法输入 400；版本/状态冲突 409；存储/限流故障沿用 503/429，不假装读取成功。所有个性化响应 private,no-store。

## 4. 超级管理员审批

后台入口仍为 `/admin/recommendations/recommendationcontenteligibility/`，显示名改为“受限内容访问申请”。按状态/来源筛选，展示申请时间、账号、版本、有效期和单账号管理入口。

- 只允许启用、staff 且 is_superuser 的账号审批和读取申请/审计；旧 manage_content_eligibility 权限不再足以访问。
- 批准/拒绝要求先有用户实际提交的 manual/pending 申请，不能凭账号 ID 直接授权；不允许自审批。
- 批准默认 30 天、最多 30 天，到期重新申请；没有自动续期或永久有效选项。
- 拒绝可填写“给申请人的简短拒绝提示”；内部原因仅审计可读，不自动复制到用户响应。
- 单作品审核台账保持独立；批准用户不批准作品，不自动改变公共库可见性。
- 保留 CSRF、15 分钟签名确认、操作者/目标/版本/UUID 绑定及审计；旧后台签名盐失效。
- 无默认新增、修改、删除和批量批准操作。旧 prepare/grant/renew 服务动作明确停用，不以别名绕过新合同。

## 5. 并发、零副作用及闭锁链路

事务锁序仍为目标用户行 → 当前资格行 → 审计，与加入/反馈的用户锁顺序兼容。等待用户锁后再次读取操作者权限。相同 UUID/内容只重放回执并返回当前状态，不重放旧申请/批准；同 UUID 不同内容、不同 UUID 的旧版本为 409。

待审批状态下以当前版本再申请是 no-op，不更改申请时间或增长状态版本；保留操作回执。拒绝、到期、撤销后以新 UUID/当前版本显式重新申请；旧批准/拒绝请求不能覆盖新一轮申请。状态变更递增独立 eligibility_version，不修改偏好/反馈版本。审计失败连同状态变更全部回滚。

内容范围策略更新为 `content-scope.closed.v3`，绑定人工来源和新策略语义，旧快照/证据重新读取。仍在任何目录查询前 403 拒绝 include_adult；即使人工批准有效，成人/未知详情、加入和反馈也不放行。本阶段不提供可以打开真实成人服务的配置开关。

## 6. 验证与部署

写入、并发及迁移验证只使用本机独立 PostgreSQL `test_recommendation_isolated_tests`，不用 SQLite/生产 Neon。前端使用合成合同，未调用线上申请或审批。

测试覆盖：默认无写入、严格本人参数、待审批幂等、超级管理员限制与自审批、批准期限、内部/公开提示隔离、过期只读、停用账号、重放不复活、版本上限、数据库唯一/状态约束、审计回滚、手账/偏好/反馈及其他账号不变、签名/CSRF/旧盐、迁移保留旧行与审计、NSFW/未知继续拒绝。

七项 PostgreSQL 并发：同 UUID 申请、不同 UUID 旧版本申请、重复批准、批准与拒绝、批准与本人停用、撤销与已有作品加入、等待用户行锁期间撤销操作者超级管理员身份。最后一项通过 pg_stat_activity 确认真实 Lock 等待后，再验证 403 及无审批写入。前端覆盖快速点击合并、超时使用原 UUID、409 后仅 GET 恢复、关闭/重开迟到响应、错误局部恢复及所有状态的组件渲染。

部署顺序：

1. 先备份，执行正常部署迁移 `python manage.py migrate --noinput`，确认 recommendations.0009_manual_access_application。
2. 完成新后端切换、停止旧实例承接流量后，再部署新前端并允许提交人工申请/审批；不要在新旧资格服务混跑时批准 manual 记录。
3. 不为测试账号自动授权，不运行生产申请/审批作为只读验收。部署后使用更新的 verify_recommendation_scope_deployment.py 验证新人工合同和 closed.v3；后台先做只读核验，真实申请/批准另获明确授权。
4. 用户手动验收：筛选内申请确认、关闭取消、待审批状态；超级管理员后台批准/拒绝/撤销；用户刷新读取结果。批准后仍没有可用 NSFW 开关，成人直接访问仍被拒绝。

回退：可先回退/隐藏前端申请入口，保留本阶段兼容后端及关闭式推荐链路。**产生 manual 数据后不能直接把资格服务回退到旧 N2-1**：旧服务假定 active 必有年龄证明，且不识别人工状态。若必须回退后端，先提供保留 M1 状态读取兼容的修复版本；不反向执行 0009、不删表、不删除或伪造历史记录。下一阶段的范围总开关与灰度回退另在 M2/M3 实现。

## 7. 本轮执行结果

- 初始 M1/关闭式范围专项 49 项通过。
- 推荐模块首次完整 851 项：848 通过、1 项既有跳过、2 项 Django/Node 跨端测试因沙箱 EPERM 无法读取路径失败。没有修改相关业务断言；获准正常文件访问后重跑全项目。
- 全项目独立 PostgreSQL 回归 1,064 项：1,063 通过、1 项既有真实样本跳过，362.029 秒。
- 最终补测申请、关闭式范围、作品审核、部署脚本保护和上下文探针共 79 项全通过，32.587 秒，包含最后新增的真实行锁撤权测试。该测试首次观测时被事务内缓存的统计快照影响，添加 pg_stat_clear_snapshot 后确认真实 Lock 等待，再断言撤权后的 403；没有去掉锁竞争断言。
- 前端全部 261 项通过；最终生产构建通过，仅有既有大包体积提示。Django 系统检查、迁移漂移检查及 git diff --check 均通过。
- 原始日志在忽略目录 `.tools/m1-backend-regression.log`、`.tools/m1-full-regression.log`、`.tools/m1-final-targeted.log`、`.tools/m1-final-targeted-fixed.log`、`.tools/m1-frontend-regression.log`，保留初次失败，不用生产数据制造样本。

本轮未主动执行生产迁移、申请/审批或线上写入验收；未进行浏览器视觉/焦点验收。部署后的只读核验及真实申请/审批交互仍待用户确认环境后分别执行。
