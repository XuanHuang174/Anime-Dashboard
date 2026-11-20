# 代理切换、DNS 故障与数据恢复

## 这次证据与处理范围

PC 控制台中的多个 API 请求报 `net::ERR_NAME_NOT_RESOLVED`，目标都是 `anime-dashboard-api-dv0s.onrender.com`。这些请求尚未进入 Django，不属于应用返回的 429；“无法加载响应数据”也不是 HTTP 响应。手机在切换前后都能保存，说明应优先检查 PC 的系统/浏览器 DNS、代理规则及网络路径，不能据此认定所有用户无法直连或账号/IP 被锁。

浏览器脚本通常只能得到 Axios `ERR_NETWORK`，不能可靠区分 DNS、证书、CORS 或扩展拦截。因此界面统一提示连接失败；精确 DNS 原因以控制台为准。Cloudflare 分析脚本被扩展拦截、Permissions-Policy 警告和 Tailwind CDN 提示，不是截图中所有 API 域名解析失败的直接证据。

本轮不修改服务端可信代理列表、限流额度或登录验证，也不清空账号、缓存和令牌。

## 前端行为

- 对明确允许的普通读取，在无 HTTP 响应的网络错误/超时后最多补试两次，等待约 1 秒/3 秒并加入少量随机延迟。并发故障共用等待，重试请求最多两个并行。
- 保存、删除、导入、邮件、令牌刷新、下载及消费式通知 GET 不自动重放。若写入断网，不能仅因未收到响应就断言写入失败；用户应先核对状态再决定是否重做。
- 有 HTTP 响应的 401/403/429/5xx 不套用网络自动重试。现有刷新令牌单飞、429 Retry-After 与通知退避继续生效。
- 同一连接故障用一个固定提示展示。手账、资料、专栏及题材能力的失败读取在重新连接后补读；已成功的资源不因普通 focus 反复刷新。
- `online`、返回前台和手动按钮只触发合并的恢复尝试，以真实 API 读取判断连通性。`navigator.onLine` 不作为网站可达性保证。持续 DNS 失败时停止短间隔补试，等待网络变化或用户重试。
- 手账已加载内容保留，读取新分页/筛选失败时注明仍是上次成功结果；输入框、编辑弹窗和未保存修改不重挂载。资料未读到时显示“我的番剧手账/个人资料暂未读取”，不伪装为其他昵称。
- 取消、离开页面、切换账号后旧请求不能重放或把旧账号响应交给新页面。其他模块仍保留各自的错误/重试入口，本轮不批量重挂载全站页面。

## 根因改善：使用自己的 API 域名

重试不能修复某台 PC 持续无法解析 `onrender.com` 的 DNS 环境。推荐给现有 Render 服务绑定 `api.xh-anime.com`，验证后让前端只使用该入口。不硬编码未经配置的地址，不在浏览器里自动切换多个携带凭据的后端。

当前仓库没有 Render/DNS 管理凭据连接。以下是需在控制台完成的配置，不是代码推送后自动生效：

1. Render 当前后端服务 → Settings → Custom Domains，添加 `api.xh-anime.com`。
2. 在该域名的 DNS 服务商添加 CNAME：`api` → `anime-dashboard-api-dv0s.onrender.com`。按 Render 控制台提示完成验证和证书签发，先确认新域名健康接口与 API 正常。
3. 若 DNS 托管在 Cloudflare，为减少客户端对 `onrender.com` CNAME 解析链的依赖，完成验证后按平台要求启用代理（橙云）并用 Full (strict) 回源；不能用 Flexible。代理不是“永不受网络影响”的保证。核对缓存规则，不缓存 `/api/*`、认证响应和私人数据。
4. Render 环境变量 `ALLOWED_HOSTS` 在原值基础上加入 `api.xh-anime.com`（不带协议），保留原 Render 域名以及其他已有主机。`CORS_ALLOWED_ORIGINS` 保留实际前端 `https://xh-anime.com` 等精确来源；不是把 API 地址当作前端来源。原有 CSRF、可信代理及系列/Credits 开关保留。
5. 先部署后端配置并验证 API 新域名。再在前端托管平台将 `VITE_API_BASE_URL` 改为 `https://api.xh-anime.com`（不要加 `/api`），**重新构建部署前端**；只改本机或后端的 VITE 变量不会改变已发布 JS。
6. 原域名暂时保留，用于旧前端和回滚。不要关闭它或删除已有 DNS。新入口仍回源同一个服务/数据库，不创建第二份用户数据。

自定义域名/代理会改变真实回源链路；切换前还应沿用 `docs/rate-limit-stability.md` 的管理员只读脚本验证新入口的来源指纹与伪造前缀隔离（使用该脚本的 `--help` 确认地址参数），防止又把不同访客合并成一个 IP 额度。

官方配置依据：[Render 自定义域名](https://render.com/docs/custom-domains)、[Render DNS 配置](https://render.com/docs/configure-other-dns)、[Cloudflare 代理状态](https://developers.cloudflare.com/dns/proxy-status/)、[Cloudflare Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)。

## PC 与手机验收

在有问题的 PC 上，分别开代理/关代理运行一次只读脚本：

```powershell
.\.venv\Scripts\python.exe scripts/verify_api_connectivity.py
```

新域名配置完成、前端也切换后：

```powershell
.\.venv\Scripts\python.exe scripts/verify_api_connectivity.py --api-origin https://api.xh-anime.com
```

脚本检查系统 DNS、HTTPS 健康、公共 API、跨域预检和线上 JS 中的 API 入口。无需密码、Render Shell 或令牌，不修改业务数据，只输出状态；它不能替代浏览器 Secure DNS 和代理扩展的验证。请勿反复运行造成不必要请求。

浏览器继续检查 Network 中实际请求域名；开/关代理、网络恢复后无需重新登录；首次失败只有统一连接提示，恢复后昵称与列表补回；已有筛选/分页和编辑输入保留；保存请求不自动重复。手机同样检查一次。若仅 PC 系统 DNS 失败，继续核对代理软件退出后的 DNS 接管、系统 DNS、Edge Secure DNS 配置；不要通过关闭证书验证或放开 CORS 解决 DNS 问题。

## 开发回归

前端单元测试包含断网重试上限、重试并发、取消/跨账号迟到响应、429、刷新令牌遇到断网和通知恢复等待。浏览器回归脚本为 `frontend/scripts/test-network-switch.mjs`：先启动本机 Vite（默认端口 5189），通过 `PLAYWRIGHT_MODULE` 指定已有 Playwright 模块位置，再运行脚本。所有 API 都被合成数据拦截，桌面/手机均验证首次 DNS 失败、online 自动恢复、手动恢复、原数据/筛选/分页保留以及保存仅发送一次；它不替代真实 PC 开关代理后的线上验收。

诊断脚本自身可运行 `python scripts/test_api_connectivity.py`，仅使用模拟网络数据。
