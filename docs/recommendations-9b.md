# 增强 9B：页面控制、批次与异常恢复闭环

后续补充：9C 在明确的能力合同/回退信号下增加自动恢复旧 V1 分页；9B 阶段“固定 browse、关闭时明确失败”的历史结论由 [9C 部署与回退说明](recommendations-9c-deployment.md) 补充，不代表普通错误也可静默回退。

## 范围

本阶段接入已部署的 9A `recommendations.browse.v1`。线上算法仍为 `content.v1`，没有修改 Django 排序、风格系数、目录、安全过滤或业务表。未引入 V2、每日精选、长期浏览历史、仪表盘或第二套资源搜索。

主推卡/网格布局及“查看详情 + 加入想看”双按钮保留。控制区增加原生风格 select、年份筛选和条件摘要；筛选继续使用现有 `RecommendationDialog.showModal()`，手机端沿用同一个 Dialog 的底部面板样式。

## 先行 9A 线上只读验收

2026-10-17，在用户明确授权的既有测试账号上正常登录，只执行 GET（普通登录是唯一 POST），没有隐藏/撤销/加入/偏好保存，没有直连生产数据库。

- 最终验收运行 24 次有界请求，53 项断言通过。
- 未登录 401；新浏览合同和 V1 默认均衡排序/首屏证据与旧列表一致。
- 本批分页无重复；相同游标重试位置/内容稳定；下一批与上一批不重叠。
- 单条详情的证据与浏览卡片一致；三档风格和年份参数正常。
- 逆序年份、用户/算法覆盖、非法风格/游标返回 400；旧游标附加不同条件返回 409。
- 画像、明确偏好、反馈摘要前后不变。
- 机器报告位于忽略目录 `.tools/deployment-9a/20261017T061411551276Z.json`。不包含账号密码、访问令牌、游标或来源作品/个人评分内容，不进入 Git。

边界：这里只核验授权账号的真实接口，不把它当作跨用户写入、实时重分类或压力验收。NSFW/未知分级及多用户并发仍由隔离 PostgreSQL 测试验证。

本次跨网 HTTP 观测：旧列表约 13.9s，新均衡首屏约 13.3s，两档风格约 16.5–16.9s，年份首屏约 14.8s，三个游标请求约 1.2–1.4s。不是服务器纯计算时间，也不能归因于单一瓶颈；现有生产请求超时 30s 未调整。将首屏等待与部署性能列入 9C，不能因此宣称已通过生产性能门槛或顺带切换 V2。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `frontend/src/components/recommendations/RecommendationBrowseControls.jsx` | 风格/类别控制、可移除摘要、筛选草稿、批次操作区 |
| `frontend/src/utils/recommendationBrowse.js` | 独立新合同校验、条件规范化、跨页/批快照一致性检查 |
| `frontend/src/utils/recommendationBrowseSession.js` | 当前用户工作区的请求代次、AbortController、游标推进和局部失败 |
| `frontend/src/hooks/useRecommendations.js` | React 生命周期桥接；依赖已应用的条件，不依赖草稿 |
| `frontend/src/api/recommendationClient.js` | 首次显式 browse=true；后续只传服务端 cursor，不混用 page/state_token |
| `frontend/src/utils/recommendationSnapshot.js` | 同时读取真实 profile/preferences/browse，并校验保存确认版本 |
| `frontend/src/pages/Recommendations.jsx` | 复用现有详情、资源、加入、反馈与偏好闭环 |

原来的 `client.list` 和旧分页工具仅保留给旧合同测试/调用者；正式推荐页面固定使用 `client.browse`。后端关闭新能力或返回旧响应时会明确失败，**不会悄悄回退旧协议或 Mock**。测试夹具也升级为 9A 合同，不把合成适配器导入生产代码。

## 用户操作

- 风格默认均衡，切换立即启动新会话；重复选中同档或同类别不发请求。仅页面内存，不写明确偏好/本地长期存储。
- 筛选在面板内部维护草稿。编辑、关闭、面板内恢复默认均不发请求；应用且条件确有变化才请求第一批。
- 四位年份及起止关系先本地校验，再由后端严格校验；空年份不发送参数。
- 摘要统计类型、年份区间、最低 Bangumi 评分、隐藏已有记录四类条件。年份上下限作为一个条件；隐藏记录默认计 1。单项移除发起新会话。
- 顶部“恢复默认”重置类别/风格/筛选；空结果的“清除本次筛选”只恢复默认筛选，保留类别和风格。
- 加载更多只追加本批；换一批只替换当前批。成功换批后焦点和滚动返回结果区，尊重 prefers-reduced-motion。
- 本轮结束显示指定文案及“调整筛选 / 重新开始浏览”，不会自动循环或无限拼接。

## 状态合同

控制、草稿、浏览、请求和详情/资源各自隔离。

1. 每个已认证工作区拥有自己的浏览 controller，无私人缓存、无跨账号游标、无资源结果共享。
2. 首屏请求和后续请求分开取消，但追加/换批共用同步请求锁，防止 React 重绘前连击绕过禁用。
3. 分类/风格/筛选变化立即取消旧请求、关闭旧详情及资源会话；请求代次再次阻断不遵守 abort 的迟到响应。
4. 追加要求相同快照、有效期、条件、版本、证据上下文和连续页码；换批要求下一批第一页。重复作品不静默去重掩盖协议问题，而是停止旧会话、要求重载。
5. 普通追加/换批错误只影响底部操作区，保留原批次和原目标游标，重试不会跳批。
6. 409 或合同/快照冲突：保留卡片基本布局，但撤下旧理由/证据令牌、禁用旧卡片操作，并显示“浏览依据已更新，请重新加载推荐。”。仅明确重载会获取新第一批。
7. 已确认的想看/反馈/撤销/偏好保存会清除旧批次并重新读取画像和第一批。加入成功仍使用 7C 的整份详情重读，不能只改按钮。
8. 保存成功但刷新失败继续显示既有确认成功/刷新失败提示；重试只读，不重复写入。
9. 资源失败仅限资源组件；关闭弹窗、切条件、离开路由继续取消原资源会话，保留 Escape 优先关闭资源区的规则。

## 本地验收

2026-10-17 最终结果：

| 验证 | 结果 |
| --- | --- |
| 前端 Node 单元/合同测试 | 149 项通过，其中新增浏览控制 12 项 |
| PostgreSQL 后端兼容/并发回归 | 196 项通过，0 失败/错误/跳过，含 16 项既有行锁并发测试 |
| 9B 实际 React 组件 + 原生 Dialog + 合成 HTTP | 11 组场景通过，含手机端、竞态、耗尽和局部失败 |
| 原有 7C 实际页面闭环 | 7 组通过，含加入后整份重读、资源防串场 |
| 原有 8C 反馈/撤销闭环 | 15 组通过，含跨标签页、UUID 重放、失败恢复 |
| Vite 生产构建 | 通过；仍有既有主包 >500kB 提醒，未为此扩大本阶段范围 |
| Django check / 增量迁移检查 | 无问题 / No changes detected |

测试覆盖范围不等于源代码行覆盖率；本轮没有测量或虚构覆盖率百分比。组件测试使用合成源，缺图显示真实占位组件；不会让测试图片或 Mock 进入生产。

本机报告/截图均在忽略的 `.tools/acceptance-9b/`、`.tools/acceptance-9b-browser/`，旧回归报告在原有验收目录中。浏览器限制外部网络，剪贴板/BT不产生真实外部副作用；模拟新增仅存在于合成服务状态，不触碰生产手账。

### 执行

```powershell
cd frontend
node --test tests
npm run build
# 使用已经安装的 Playwright；必要时将 PLAYWRIGHT_MODULE 设为本机包绝对路径。
node scripts/test-recommendation-browse.mjs
node scripts/test-recommendation-loop.mjs
node scripts/test-recommendation-feedback-closure.mjs
cd ..
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.\.venv\Scripts\python.exe scripts/verify_recommendation_9b.py
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

后端脚本只允许显式 loopback 专用测试库，不回退生产 DATABASE_URL 或 SQLite。复跑线上只读接口时：

```powershell
.\.venv\Scripts\python.exe scripts/verify_recommendation_browse_deployment.py --deployment-confirmed --expected-account <明确授权测试账号>
```

密码由掩码输入读取，不放在参数、源码、环境配置或报告里。该脚本拒绝业务写入及非允许路径，唯一 POST 是正常登录。

## 部署和后续

- 先部署 9A 后端且保持 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`、`RECOMMENDATION_BROWSE_ENABLED=True`，再发布本次前端产物；无需迁移。
- 新前端遇到后端关闭新能力会显示真实错误，不假装成功。如果回退增强 9，应先恢复前置前端构建，再关闭后端新浏览入口；旧 V1 入口仍保留。
- 不全局清空用户偏好、反馈、手账或缓存。新页刷新/账号变化会丢弃本地内存游标；版本不兼容走明确重载。
- 本轮完成的是 **9A 线上先行只读核验 + 9B 本地实现与回归**，没有宣称新 9B 页面已在线部署验收。发布后在 9C 继续完成真实多端、首屏性能、慢网与回退验收，不启动 V2。
