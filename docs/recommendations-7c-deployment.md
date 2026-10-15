# 核心 7C：执行、部署与回退

## 1. 独立 PostgreSQL 验收（禁止换成 SQLite/Neon）

需要先获得机器所有者对下载本机便携包的授权。脚本在 `.tools/postgres-7c/` 下载/解压，不安装系统服务，不改 `.env`，不执行递归删除。中文 Windows 路径下会建立 ASCII 临时目录别名，合成数据存放在 `%TEMP%/anime-postgres-7c-data/`，避免 PostgreSQL 的路径编码问题；物理程序仍在 `.tools`。不会重指向已有的不相关别名，也不会覆盖非空的失败数据目录。

```powershell
# 仓库根目录；只在首次明确授权后加 -Download
./scripts/setup-recommendation-postgres.ps1 -Download

# 以下凭据仅用于本机临时合成测试实例，不是生产凭据
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.\.venv\Scripts\python.exe -X utf8 scripts/verify_recommendation_7c.py
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings

# 完成后停止，仅保留可复用的本机运行时/合成数据文件，不批量删除
./scripts/setup-recommendation-postgres.ps1 -Stop
```

固定核心 7 包覆盖 want（含 4 个 PostgreSQL 竞争）、7A 交互、V1 引擎、3A 数据合同、代理和 7C 闭环。不是全后端/全 V2 基准测试；脚本要求零跳过、4 项竞争全部通过才输出 passed。它不生成 V2 发布批准文件，不调用旧的 SQLite 发布凭据命令。

实例只监听本机；临时测试凭据仅适合这台隔离合成数据实例。脚本不停止其他 PostgreSQL、不重用正式库，不自行清理旧库；Django 只在该实例创建/删除固定名字的测试库。不要修改库名或端口去借用现有正式服务。脚本的临时 initdb 密码文件会作为单个明确文件移除；初始化使用 `--no-clean`，失败时不自动批删目录。停机后保留程序、ASCII 别名及合成数据，可供复用；如要整体清理，由用户手动处理。

## 2. 本地前端验收

```powershell
# frontend 目录
npm test
npm run build
$env:PLAYWRIGHT_MODULE='C:/path/to/node_modules/playwright'
node scripts/test-recommendation-resource.mjs
node scripts/test-recommendation-loop.mjs
```

两个浏览器脚本使用独立无头 Edge、本地 Vite 和合成数据，阻断外部网络。整页脚本使用生产 Recommendations 页面/Hook，控制慢请求、断网、确认加入和重读失败；它不是生产写入测试，也不宣称已覆盖真实网络的所有故障模式。

## 3. 发布与只读复验

- 本次无模型变化，无需迁移，不需要数据回填。
- 正常发布前后端代码。后端变化为测试/脚本，线上算法环境变量始终为 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`。
- 先发布，再确认新静态资源完整到达，避免 CDN/旧标签页混用。只有发布成功后才能使用新的本地 dist 作 SHA256 对照。

```powershell
# 根目录：只接受明确授权账号，交互式输入，不在命令行/文件保存密码或令牌
.\.venv\Scripts\python.exe -X utf8 scripts/verify_recommendation_deployment.py --authenticated --expected-account '<授权账号>' --expected-dist frontend/dist

# 使用正常登录拿到短期 access token，仅通过子进程 stdin 传给全新浏览器上下文
$env:PLAYWRIGHT_MODULE='C:/path/to/node_modules/playwright'
.\.venv\Scripts\python.exe -X utf8 scripts/verify_recommendation_browser.py --expected-account '<授权账号>'
```

第二个脚本只 GET/HEAD；拒绝业务写入并拦截统计 POST、剪贴板和 Magnet 副作用。验证已部署页面真实详情/资源、一次按需查询、桌面并排、窄屏切换、Escape/焦点/返回。真实资源返回非 200 但局部恢复 UI 可用时，必须记为服务降级，不可冒充资源成功。

两个脚本均不直接访问数据库，不自动选择其他账号，不扫描全站手账。真实新增只允许在上述隔离 PostgreSQL 环境。生产不执行 POST want 或 PATCH preferences。

## 4. 回退与缓存规范

- UI 回退：重新部署前一个已验收的 `536e70e`（7B）构建；无需回滚数据。使用正常部署历史版本或审批后的 `git revert`，禁止 reset --hard、强推或删用户记录。
- 算法安全开关：本次从未开启 V2。若以后需要从其他版本回退，在 Render 将 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`，重启/重新部署所有实例与 worker。不要为了测试先切 V2；V1 不要求生成新的 V2 批准文件。
- 本地内存公共缓存由所有 worker 重启刷新；单独 shell 的清理不会影响其他进程。不要 `cache.clear()`、FLUSHALL 或通配批删。
- 共享缓存确需回收 V2 时只调用准确版本键：

```powershell
python manage.py shell -c "from recommendations.version_control import clear_version_cache; clear_version_cache('content.v2')"
```

这是运维可选指令，本次没有执行。资源结果从不写推荐缓存；分页和证据 token 不可跨上下文混用，409 后明确重新加载，不剥离 token 偷渡兼容。

详细测试结果与未完成项见 [7C 验收报告](recommendations-7c-acceptance.md)。
