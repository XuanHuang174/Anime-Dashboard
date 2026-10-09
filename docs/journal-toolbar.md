# 私人手账顶部工具栏

## 组件与作用域

- `JournalHeaderActions.jsx`：编辑模式的数据管理菜单、预览模式的更多工具菜单，以及两种模式共用的资源搜索和移动端菜单。
- `MemphisDropdown.jsx` / `JournalToolbar.css`：通过 Portal 将固定定位面板挂载到 body，`z-index: 9999`，视口边界定位、点击外部收起、Escape 与方向键操作。面板关闭后保留组件状态，但使用 hidden/inert 防止隐藏项获取焦点。
- `JournalDataTransfer` 和 `PublicJournalShareButton` 新增可选渲染入口，旧调用方式仍可使用。导入和分享确认保持独立挂载，关闭菜单或改变屏幕宽度不会丢失确认状态。
- 添加番剧仍使用原有第二行按钮及样式。公共主页和他人的分享手账不显示私人工具栏或单行资源按钮。

## 当前列表 CSV

`journalCsv.js` 的 `buildJournalCsv(items)` 是纯函数，传入页面已经筛选、搜索、排序后的 `filteredAnime`，不请求服务器全量导出接口。

选择“全部”时导出所有当前匹配项；启用分页时仅导出当前页可见项（下载成功提示注明当前页）。如需一次导出全部筛选结果，请先将分页数量选择为“全部”。记录仍在加载、加载失败或搜索防抖尚未完成时禁止 CSV 导出。

采用 UTF-8 BOM、CRLF、标准双引号转义，保留完整个人长评；对可能被 Excel 解释为公式的文本添加前置单引号。文件名使用本地日期 `我的番剧手账_YYYYMMDD.csv`。JSON 导出仍然是完整备份，CSV 是当前可见列表，二者不混用。

## 搜索隔离

顶部入口传入空标题及 `autoSearch: false`，打开后仅展示引导。单行入口传入番剧标题及 `autoSearch: true`，复用关键词清洗逻辑。每次启动使用新 key 挂载 `JournalResourceSearch`，资源抽屉重置关键词、结果、错误、Loading、复制状态和登录提示；关闭或切换时 AbortController 中止请求，序号校验防止旧响应覆盖。

详情弹窗继续使用默认自动搜索行为。所有搜索仍调用已登录保护的 Django `/api/utils/anime-search/`，不向浏览器提供内部服务密钥或直接调用内部服务。

## 测试

```powershell
cd frontend
npm test
npm run build
```

本地浏览器回归脚本需要可用的 Playwright 与 Chromium/Edge（不新增生产依赖）：

```powershell
# 第一终端：使用本地代理基址，不访问生产接口。
$env:VITE_API_BASE_URL='http://127.0.0.1:5177'
npm run dev -- --host 127.0.0.1 --port 5177 --strictPort

# 第二终端：设置为本地可用 Playwright 模块路径，或已安装的模块名。
$env:PLAYWRIGHT_MODULE='playwright'
$env:PLAYWRIGHT_CHANNEL='msedge' # 不设置则使用已安装的 Playwright Chromium。
node scripts/test-journal-toolbar.mjs
```

浏览器脚本拦截全部业务 API，使用模拟记录，不登录真实账号、不执行导入/分享变更。验证筛选 CSV 下载及 BOM、JSON 下载、导入取消、分享确认、搜索关键词与结果隔离、下拉层级、点击外部收起，以及 320–1440px 响应式布局。
