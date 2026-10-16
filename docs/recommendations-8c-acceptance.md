# 增强 8C：隔离回归验收报告

验收日期：2026-10-16。结论：**隔离自动化与部署后线上只读接口验收通过；线上页面验收部分完成，剩余项受浏览器控制通道超时阻塞，不宣告全部结案。**

## 环境与安全边界

- PostgreSQL 17.11，独立本机实例 `127.0.0.1:55437`；Django 创建并销毁的库为 `test_recommendation_isolated_tests`。未借用生产 Neon，未使用 SQLite 替代。
- 测试入口在 Django 初始化前验证测试 URL，拒绝远程主机、正式库名与 SQLite；测试类再次验证 PostgreSQL、测试库名称与本机地址。缺少配置即停止，不回退到 `.env` 的 `DATABASE_URL`。
- 全部账号、目录与手账均为合成数据；独立连接通过不同 PostgreSQL backend PID 验证，并非在单连接中假装并发。
- 后端测试禁止外部 HTTP。浏览器使用独立无头 Edge、本机 Vite fixture 与模拟传输，阻断外网；不读取用户浏览器，不启动 BT 客户端，不写真实剪贴板。
- 保持 `content.v1`，未修改排序公式、数据清洗、评分或反馈语义。8C 不新增业务表或迁移。

## 实际执行结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| PostgreSQL 汇总回归 | 167 通过，0 失败，0 跳过 | `.tools/acceptance-8c/20261016T134203552834Z.json`，28.808 秒 |
| 其中 PostgreSQL 并发测试 | 16 通过：原反馈 6、原加入想看 4、新边界 6 | 同一报告的三个 concurrency 字段与完整 case ID |
| 前端 Node 合同/状态测试 | 137 通过，0 失败，0 跳过 | `npm test`；8C 新增 3 项 |
| 反馈页面浏览器回归 | 15 组通过 | `.tools/acceptance-8c-browser/report.json`，含原 8B 的 8 组与 8C 的 7 组 |
| 加入想看/详情/资源闭环浏览器回归 | 7 组通过 | `test-recommendation-loop.mjs` 的 PASS 输出 |
| 资源抽屉独立浏览器回归 | 通过 | `test-recommendation-resource.mjs`；本机截图在 `.tools/resource-drawer-7b/` |
| Django check | 0 issues | 隔离 PostgreSQL settings |
| makemigrations --check --dry-run | No changes detected | 无新迁移、无模型漂移 |
| React 生产构建 | 通过 | 既有主 bundle 超过 500 kB 的提示仍在，不是本次新增的构建错误 |

以上是**场景覆盖与通过情况**，不是代码行/分支覆盖率，不宣称 100% 代码覆盖或生产可用性。浏览器测试覆盖真实组件和请求协调层，但 HTTP 使用模拟响应；数据库集成与行锁在另一层真实 PostgreSQL 中验证，未将两者冒称为同一次真实 HTTP 全栈或线上写入验收。

## 需求覆盖矩阵

| 场景 | 后端与数据库 | 前端与浏览器 |
| --- | --- | --- |
| 同作品多入口快速操作 | 同 UUID 八连接合并；不同 UUID 旧版本只允许一个成功；唯一约束保持一条状态 | 主推/详情连击只有一次 POST；普通卡共享控制器合同；加入与反馈共用写入门闩 |
| 多用户隔离 | A 隐藏前后，B 三类完整响应快照、反馈版本与手账逐字段不变 | 跨标签页仅发送失效 nonce；账号变化销毁旧私有状态 |
| 手账零副作用 | 已有个人 0 分、超长长评、观看状态、显式空标签、私人标签及时间戳逐字段比较 | 写入体只含作品身份、版本与操作 UUID，不包含手账字段 |
| 加入想看与隐藏竞争 | 4 want + 4 hide，已有记录完整保留；新记录只创建一次，评分为空、长评为空 | 同作品 pending 时相互禁用，失败不关闭详情与资源 |
| 反馈版本与旧令牌 | hide → undo 后即使资格回到原状，旧分页/详情证据仍 409；画像与偏好版本不被误改 | 明确重读第一页；旧分类/分页/详情不回写；确认保存后只重试 GET |
| 服务端已提交但响应丢失 | UUID 回执只返回当前反馈状态；撤销后的旧 hide 重放不复活屏蔽 | 先显示“结果尚未确认”，使用完全相同 UUID/请求体重试，不重复增加版本 |
| NSFW/未知分级 | 撤销前或撤销后改为 True/None 均硬过滤；缓存不能绕过 | 不靠前端隐藏来提供安全保证 |
| 真实行锁与超时 | `pg_blocking_pids` 证明撤销等待目录锁，等待中重分级后 404；用户锁超时 JSON 503 且无部分写入 | 确认失败和刷新失败分开；后者明确提示反馈已保存 |
| 慢网/断网/资源独立 | 推荐、反馈与资源代理分别保留 JSON 错误边界 | 分类迟到结果、隐藏清单迟到分页、离开路由迟到回调、资源断网和重试均隔离 |
| 多端和键盘 | 不涉及数据库写入 | 桌面/390px 窄屏；44px 触控区；菜单 Escape 优先；抽屉内滚动；Tab/Shift+Tab 循环与返回焦点 |
| Toast | 不涉及额外写入 | 单条替换、12 秒自动消失、悬停/聚焦暂停；旧计时器不能删除新 Toast |
| 部署脚本安全性 | 离线测试证明未确认部署即停止 | 拒绝全部业务写入与非白名单路径，只有正常认证允许 POST |

## 本次发现并修复的问题

已隐藏作品管理抽屉沿用原生 dialog，但原有自定义 Tab 边界只在详情带资源面板时启用。真实浏览器回归发现从隐藏清单最后一个按钮按 Tab 可能移出弹窗。

修复：共享 `RecommendationDialog` 的 Tab 边界应用到所有模态面板，仍排除 disabled、隐藏和 inert 元素；不改变 Escape 的资源优先关闭规则。修复后窄屏双向循环及原有资源/详情测试通过。

## 遗留项与验收限制

1. 指定授权账号的线上只读接口核验已完成；实际页面检查部分完成，剩余项见下方补验记录。未进行线上反馈、撤销或新增手账写入。
2. 真实外部资源服务速度/可用性、BT 客户端唤醒不由本次模拟测试保证；Magnet 与复制只验证模拟操作及合法性约束。
3. 未进行生产负载压测或多机故障注入；本机 PostgreSQL 时长不能当作跨网 Neon 的性能结论。
4. 构建的既有体积提示保留，未顺带拆包或变更业务。

## 复跑

项目根目录 PowerShell（已准备本机便携 PostgreSQL 的情况下）：

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts/verify_recommendation_8c.py
# 每一步应确认退出码为 0；不要换成默认 settings 或 DATABASE_URL。
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

前端目录，Node 及 Playwright/Edge 已配置：

```powershell
npm test
npm run build
# 若 Playwright 不在本地 node_modules，显式指定已配置的运行时包路径。
$env:PLAYWRIGHT_MODULE='C:/Users/XuanHuang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
node scripts/test-recommendation-feedback-closure.mjs
node scripts/test-recommendation-loop.mjs
node scripts/test-recommendation-resource.mjs
```

`.tools` 中包含本机报告/截图，受 Git 忽略，不作为生产资源发布，不提交数据库、日志、凭据或构建产物。

## 部署后只读补验（2026-10-16）

用户确认已推送并授权开始只读验收后，运行 `verify_recommendation_feedback_deployment.py`，结果 `passed_scoped_readonly`。机器报告：`.tools/deployment-8c/20261016T142240624875Z.json`。

- 35 次有界请求、80 项断言通过；唯一 POST 是正常登录，业务写入接口调用为 0。未连接生产数据库、改动部署配置或重启服务。
- 线上推荐 JS/CSS 与本地 8C 构建哈希一致。没有通过服务端发布元数据独立证明每个后端实例的 Git SHA。
- 三类推荐均为 `content.v1`；每类抽查 3 张卡片的结构化证据，列表和详情一致；重复读取与第二页上下文稳定，失效证据 409，用户/算法覆盖参数 400，匿名推荐与反馈路径 401。
- 当前授权账号隐藏清单：2 部、`feedback_version=4`；列表/详情的反馈版本一致；验收前后画像、明确偏好及反馈清单摘要不变。没有为补齐分页而制造生产数据，实际非空清单第二页仍未在线验证。
- 2 项线上安全样例未实测：未提供人工确认的成人/未知分级公共目录 ID。隔离 PostgreSQL 已验证这些分支，不能因此冒称线上样例也通过。
- 内置 Browser 中用户正常登录后，读到真实画像摘要：29 部可分析样本、12 个自动题材、0 个手动偏好。主推卡 `⋯` 仅包含“不感兴趣”；按 Escape 关闭后焦点回到该卡菜单触发器，没有点击反馈项。
- **页面剩余项待补验**：已隐藏清单实际展示与滚动、普通卡/详情菜单、详情与资源互斥、桌面/窄屏 Tab 循环及关闭回焦。本轮 Browser 的多个页面采集/点击调用持续超时，已停止重试；不能把未确认动作当成成功，也不能据此断言网站存在缺陷。
- 最慢单次 API 请求为 14.361 秒（首轮推荐计算）；后续推荐请求约 1–4 秒。这里只作诊断记录，不是负载测试、性能保证或 V2 上线批准。

本轮不产生线上 Toast，因为隐藏/撤销属于业务写入；其计时与恢复边界沿用隔离测试结果。没有真实剪贴板写入或 Magnet 唤醒。
