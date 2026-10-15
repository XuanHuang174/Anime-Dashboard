# 核心 7B：复用资源抽屉与会话隔离

## 先行只读验收（已部署的 7A）

2026-10-15 使用原有 `scripts/verify_recommendation_deployment.py` 对明确授权的单个测试账号执行只读联调。

- 线上推荐 JS/CSS 与本地 7A 正式构建 SHA256 一致。
- 三类推荐、列表/详情证据合同、分页稳定性、伪造令牌 409、越权参数 400、未登录 401 检查通过。
- 当前线上引擎为 `content.v1`；画像与明确偏好前后读取指纹不变。
- 共 25 次请求；只有正常登录使用 POST，没有调用加入想看、修改偏好等写接口。
- 第一次下载前端入口时发生 `ChunkedEncodingError`，在登录前停止；一次重试通过。没有改变服务端配置或绕过安全校验。
- 本地报告：`.tools/deployment-6c/20261015T133345195053Z.json`，结果 `passed_scoped_readonly`。报告目录被 Git 忽略，不包含密码、访问令牌。
- 没有提供成人/未知分级生产测试样本，所以这两项线上探针为未验证，不能将本次只读通过理解成所有生产路径全覆盖。

## 实现边界

只复用 `AnimeResourceDrawer` 与 `GET /api/utils/anime-search/`；没有新搜索 API、业务表、身份推断、推荐排序变化或 V2 切换。

- `RecommendationDialog` 保留一个原生 `dialog.showModal()`，资源面板是它内部的同级布局槽，不使用 body portal、不打开第二个模态框。
- >=1024px：保留左侧详情，右侧 400px 资源区；<1024px：资源面板在同一个 dialog 内滑入，详情 `inert`，可通过“返回详情”恢复。
- 原生 dialog 提供顶层与背景 inert，另在 dialog 内处理 Tab 首尾循环（排除隐藏或 inert 元素）；资源组件新增可选 `trapFocus`，原手账调用默认值仍为 true，推荐弹窗设置 false，避免无法到达外层“返回详情”。
- Escape 由 dialog 的 cancel 事件统一处理：优先关闭资源，恢复资源入口焦点，第二次才关闭详情。顶部关闭按钮关闭整个详情。
- 卡片继续只有“查看详情 / 加入想看”；资源入口仅出现在详情操作区。
- 展示完整标题、原放送季度，声明检索不保证正确季度或版本。清洗后的关键词不能用于身份判断。

## 会话与副作用合同

| 状态域 | 归属 | 失效规则 |
| --- | --- | --- |
| 详情 | 原 `createRecommendationDetailController` | 作品、证据或已确认加入上下文变化，整份详情重新读取 |
| 加入 | 原按 Bangumi ID 的 want 控制器 | 保留原幂等与旧字段保护；不混入资源请求 |
| 资源 | 共享抽屉的 `createResourceSearchSession` | 每次打开独立 epoch；关闭、换作品、卸载、改词或重搜取消旧请求 |
| 复制 | 当前资源 epoch + 独立 copySequence | 旧成功、旧失败、被新复制覆盖的回调均不能更新提示或 toast |

共享 session 服务是原组件内部搜索逻辑的抽取，不是第二套搜索系统。没有结果缓存，不保存搜索词或结果到 localStorage。只沿用原有登录会话存在性检查。

- 仅打开详情不调用资源服务；首次点击入口后才自动请求。开发 StrictMode 重放不会重复发出自动搜索。
- 每个响应、异常和 finally 均校验 active、epoch 和 AbortSignal；即使底层无视 abort，也不能写入新会话。
- 输入编辑取消当前请求并清空旧结果/复制提示，不边输入边查询；回车或搜索按钮发起新请求。
- 关闭或换作品重置所有资源字段。资源失败只在资源区展示，不使推荐页进入全局错误。
- 保留骨架、空结果、超时提示、网页兜底、复制失败手动复制及安全 Magnet 校验；打开/复制还需验证链接确属当前结果。
- 已发起的系统剪贴板操作无法撤销，但其完成回调不会污染新会话。自动化测试使用模拟剪贴板，不实际复制或唤醒 BT 客户端。
- Django 代理继续执行登录校验、用户级限流、固定上游和字段白名单，不传递私人评分、标签或长评。

## 测试与复现

前端新加 6 项 session 合同测试（并调整现有 JSX/样式合同），覆盖 StrictMode、迟到成功/错误/finally、复制竞态、输入编辑、非法链接、空结果、超时、访客与实例隔离。

后端增加 1 项代理合同测试：两个合成用户限流独立、请求不共用结果、恶意上游的私人字段被白名单剔除。现有身份、安全过滤、幂等保护继续由原回归测试验证。

```powershell
# 根目录：必须使用隔离配置，不能省略后误连正式 Neon
.\.venv\Scripts\python.exe manage.py test --settings=config.recommendation_test_settings --noinput
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings

# frontend 目录
npm test
npm run build
# 若本机未在 frontend 安装 playwright，指向已配置的运行时包；不需要修改项目依赖
$env:PLAYWRIGHT_MODULE='C:/path/to/node_modules/playwright'
node scripts/test-recommendation-resource.mjs
```

浏览器脚本启动临时 localhost Vite 与独立无头 Edge；使用真实组件、StrictMode、原生 modal、合成详情及可控请求，阻断所有非当前 localhost 的网络访问，不访问用户浏览器配置或真实账号。可通过 `PLAYWRIGHT_CHANNEL` 指定已安装的其他 Chromium 浏览器。

浏览器验证：详情零搜索、首次点击仅一次请求、桌面 400px 并排、320/375/768/1024px 不溢出、移动端隐藏详情不可聚焦、Tab 留在 dialog、Escape 分层关闭与焦点归还、切作品及关闭详情取消请求、迟到响应/复制失败不串场、局部错误与网页兜底。截图位于被忽略的 `.tools/resource-drawer-7b/`。

本次结果：前端全量 114 项通过；真实浏览器集成脚本通过；后端隔离测试 571 项中 566 项通过、5 项环境条件跳过（4 项 PostgreSQL 行锁竞争、1 项可选归档样本），无失败；Django 检查通过，无新增迁移。正式构建通过，既有主包约 872 KB 的体积提示仍在，没有借此扩展范围进行无关打包重构。

## 发布与回退

本次不需要迁移、不更改线上环境变量、缓存命名空间或 V1 默认开关。正常发布 React 构建即可，Django 新增内容仅为测试。

7B 的本地浏览器验收不能冒充部署后的 7B 验收；本次先行线上测试对应旧 7A 构建。发布后应核对新静态资产，再以授权账号进行页面只读打开/搜索验证；不实际加入或覆盖记录。资源查询会消耗原有限流配额。

需要回退时重新发布上一个已验收的 7A 前端构建即可；没有新数据需要回滚或删除。继续保持 V1，不清空用户偏好或手账。
