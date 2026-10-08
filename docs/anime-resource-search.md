# 番剧资源搜索：Django 代理与 Memphis 抽屉

## 部署配置

无需新增依赖或数据库迁移，复用已安装的 `requests`、Axios、React 和 GSAP。
在 **Django 所在的 Render 服务**中设置以下环境变量；本地开发可填写根目录 `.env`。
不要写进前端 `VITE_*`、Cloudflare 前端构建配置或 Git 仓库。

```dotenv
ANIME_SEARCH_API_URL=https://xh-anime-search.onrender.com
ANIME_SEARCH_API_KEY=replace-with-your-internal-api-key
```

密钥既支持原始值，也支持带一个 `Bearer ` 前缀的值。URL 必须是 HTTPS，
不要带 `/search`、查询参数、片段或用户凭据。

## 后端实现与响应契约

- 视图及结果清洗：`anime/resource_search.py`。
- 路由：`anime/urls.py` 中的 `GET /api/utils/anime-search/`。
- 配置读取：`config/settings.py`，示例：`.env.example`。

浏览器通过现有 JWT 登录流程调用 Django，Django 用独立的服务密钥请求上游：

```python
url = f"{base}/search?keyword={quote(keyword.strip(), safe='')}"
response = requests.get(
    url,
    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    timeout=10,
    allow_redirects=False,
    stream=True,
)
```

接口每用户每分钟最多 20 次，关键词 1–200 字。上游响应限制 2MB、最多返回 100 条，
只暴露标题、发布时间、大小、发布者及 Magnet；不会把内部 URL、密钥或异常堆栈交给前端。
Requests 的 `timeout=10` 是连接/读取等待超时，不是绝对总耗时保证；另外在流式读取中检查
10 秒时间预算，防止持续返回碎片数据而长期占用进程。
参见 [Requests 超时说明](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts)。

上游支持数组、`results`/`data`/`items` 包装及常见字段别名。
如果服务使用另一种结构，需在 `normalize_resource_results()` 增加映射；不要静默把结构错误当空数组。

```json
{
  "status": "success",
  "keyword": "孤独摇滚",
  "results": [{
    "title": "[字幕组] 孤独摇滚 01 [1080P]",
    "published_at": "2026-10-09 20:00",
    "size": "1.2 GB",
    "publisher": "字幕组",
    "magnet": "magnet:?xt=urn:btih:..."
  }]
}
```

空数组仍返回 200；缺配置、503/504、超时或连接失败返回 503 JSON；其他上游错误、
非 JSON、超限、重定向返回 502 JSON；关键词无效返回 400、频率超限返回 429。
未登录由 DRF 返回 401。错误响应为 `{"status":"error","message":"友好提示"}`。
未登录和方法不允许使用 DRF 原有的 JSON `detail` 响应。

## 前端实现

- `DetailModal.jsx`：新增寻找资源按钮、独立位移层、Esc 先关闭抽屉，原有
  编辑/删除、看过作品的结案报告及精选专栏按钮保持原条件。
- `AnimeResourceDrawer.jsx`：搜索框、自动请求、骨架屏、结果卡片、复制及手动复制
  兜底、空状态与网页搜索。仅请求 Django `utils/anime-search/`。
- `AnimeResourceDrawer.css`：完整的作用域样式，粗黑边框、亮色块、硬阴影。
- `resourceSearch.js`：NFKC、季数和标点/副标题清洗、Magnet 安全校验、网页 URL 编码。

已有 GSAP 只操作内层详情面板的出入场；新增 CSS Transition 只操作外层
`.detail-resource-stage` 和抽屉，不会争抢同一个 transform。
宽度 ≥1100px 时面板向左平移 208px，右侧展开 400px 抽屉；较窄屏幕使用覆盖模式。
支持系统减少动画设置，关闭隐藏层后 `inert` 防止键盘聚焦到不可见控件。

搜索通过 AbortController 和递增请求序号防止旧响应覆盖新结果。关闭抽屉或切换番剧会
取消请求；每次打开重新清洗并搜索当前番剧。未登录用户显示登录入口和网页搜索，不触发
会强制跳转登录页的 Axios 401 流程。

Magnet 仅接受 BT URI，点击使用 `window.location.href = result.magnet` 唤起客户端。
网页兜底地址使用 `encodeURIComponent`，新标签页 `noopener noreferrer`。
结果由 React 文本节点展示，不注入上游 HTML。

## 验证

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test anime.test_resource_search
cd frontend
npm test
npm run build
```

本次回归使用隔离的 SQLite 内存测试数据库及模拟上游，不访问生产数据、不发送邮件、
不调用真实 AnimeSearch。上线前设置两项环境变量后，需用已登录账号验证上游真实字段契约，
分别测试有效资源、空结果和 Render 冷启动。不要在截图或日志中展示密钥。
