# 增强 9C：部署清单与安全回退

## 固定边界

- 生产默认算法始终 `content.v1`；回退不涉及 V2、数据库回滚、删除或重新生成手账。
- 本阶段无数据库迁移。测试只能使用独立本机 PostgreSQL 设置，不能把 `.env` 的生产 `DATABASE_URL` 当测试库。
- 新旧浏览协议、公共缓存、私人画像和资源搜索互不混用。

## 隔离回归

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
.\.venv\Scripts\python.exe scripts/verify_recommendation_9c.py
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
# 用完停止本机实例；不删除目录或文件。
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

可选覆盖率：虚拟环境安装 coverage 后设置 `COVERAGE_FILE=.tools/acceptance-9c/.coverage`，用 `python -m coverage run --branch --source=recommendations scripts/verify_recommendation_9c.py`，随后 `python -m coverage json -o .tools/acceptance-9c/coverage.json`。测试创建/销毁的是明确限定的独立测试数据库；禁止任何生产 DB 测试。

前端在 `frontend` 中运行：

```powershell
node --test --experimental-test-coverage tests
npm run build
node scripts/test-recommendation-browse-closure.mjs
node scripts/test-recommendation-loop.mjs
node scripts/test-recommendation-feedback-closure.mjs
```

使用已安装 Playwright；需要时设置 `PLAYWRIGHT_MODULE`。所有写入、Magnet、剪贴板均为合成/模拟；不依赖真实资源服务制造异常。

## 发布顺序

1. 记录前置提交/构建，确认测试通过、生产算法 V1。保留业务数据备份，但本次不进行数据修改。
2. 先部署后端 9C（开关保持开启），再部署前端 9C。已有 9B 前端仍可正常读取增强浏览；9C 前端遇到尚未更新的服务器 capabilities 404 时继续尝试既有 browse，不猜测为旧分页。
3. 初次发布设置同一组值到所有 worker：

   ```text
   RECOMMENDATION_ALGORITHM_VERSION=content.v1
   RECOMMENDATION_BROWSE_ENABLED=True
   RECOMMENDATION_BROWSE_EPOCH=<本次发布的新随机代次>
   RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=True
   ```

4. 更新代次会令现有增强游标返回 409，页面要求重读第一批，这是预期失效，不是业务数据丢失。正常画像/目录变更仍走明确 409 恢复，不静默合并流。
5. 等所有 worker 和静态资源部署一致后，用正常授权账号执行只读审计：

   ```powershell
   .\.venv\Scripts\python.exe scripts/verify_recommendation_browse_closure_deployment.py --deployment-confirmed --expected-account <授权账号>
   ```

   密码仅交互输入，不写入文件/命令。每账号最多 40 次请求；唯一允许的 POST 是正常登录，业务写入在客户端发送前被拒绝。部署前基线可显式加 `--allow-pre9c`，但这不能放行 9C。

6. 对两个授权账号分别验收；核对前后端构建版本、capabilities 为 V1/已开启/private-no-store，不能只看首页 200。
7. 浏览器真实只读复验：桌面/窄屏控制栏、筛选草稿与关闭、摘要移除、加载更多/换批/耗尽、Tab/焦点/Escape、详情/资源返回。不要在生产人为断网写入或触发 Magnet/剪贴板；极端写入/网络场景保持隔离模拟。
8. 记录部署端冷/热首屏与游标性能到 [记录表](recommendations-9c-performance.md)。未取得服务端 telemetry 时不可声称已测 SQL 或生产缓存命中率。

## 一次发布安全关闭增强 9

生成需要由运维统一应用的配置；脚本只打印，不读取 `.env`，不调用 Render，不直接部署：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_recommendation_browse_rollout.py --disable
```

把输出的 `environment_patch` **一次性**应用到全部 worker：固定 V1、`BROWSE_ENABLED=False`、新 `BROWSE_EPOCH`。不要每个 worker 各自生成不同代次。

关闭后：首次增强请求返回 503、续传返回 409，均携带 `code=browse_disabled, fallback=legacy_v1, algorithm_version=content.v1`。9C 前端仅认可这个明确合同，丢弃旧游标，重新 GET 原有 V1 第一页；隐藏风格/年份控件，保留类型、最低分及隐藏手账筛选，恢复原 `page/state_token` 分页。

回退不会继续展示已失效的详情/资源会话，也不会提交想看、反馈或偏好写入。反馈、明确偏好、手账记录及已保存评分仍完整保留。普通 401/429/500/503、证据冲突或 V2 配置错误不会触发伪装回退。

**兼容限制**：已经加载的旧 9B 静态包尚不具备该自动回退代码，需要刷新到 9C；尚未升级的旧后端也不能识别新信号。滚动发布过渡期间可能看到明确错误/重载提示，不能保证所有旧客户端瞬时无感。应先完成 9C 全量部署，再做生产回退操作。

重新开启时使用 `--enable` 生成另一组**新代次**统一发布；前端重读后取得新第一批。不能复用旧代次，否则无状态签名游标在恢复相同上下文后可能再次有效。

## 缓存与失效

- 增强公共缓存命名空间 `recommendations.browse.public.v1`，不同于原引擎 `recommendations.public-features.v4:content.v1:...`。
- 新 key 含实时目录精确摘要、发布代次、题材/特征/清洗规则版本，TTL 60 秒；不写私人推荐缓存。
- 每请求仍验证实时目录及安全分级，直接 UPDATE 不会被缓存掩盖；续传不重新排序，也不依赖公共缓存。
- 改发布代次后新 namespace key 自然隔离，旧短期公共 key 到期即可；不需要 `cache.clear()`，更不能清空会话或业务数据。
- 前端只清当前内存流、请求代次与详情/资源会话，禁止 `localStorage.clear()`（会误删认证和其他设置）。
- 缓存异常自动建立最新公共特征；若有资源问题可单独设 `BROWSE_PUBLIC_CACHE_ENABLED=False`。默认仍是 V1，不能以缓存故障为由启用 V2。

## 放行清单

- [x] 独立 PG 回归、并发锁、零副作用与安全硬过滤。
- [x] 前端合同/竞态、桌面/窄屏合成浏览器与构建。
- [x] 两自然账号当前部署只读基线及本机只读性能抽查。
- [x] 9C 新前后端部署一致、capabilities 与静态构建核对（2026-10-17 两账号接口及公开 JS/CSS SHA256 核验通过）。
- [ ] 9C 部署后真实页面只读、多端、首屏性能复验。
- [ ] 实际回退如需演练，优先隔离环境；未经授权不切生产开关。

9C 代码已部署且接口范围终验通过。内置 Browser 在用户手动登录后仍读取超时，真实页面多端及焦点补验待完成；服务端内部性能数据也尚未取得，不能把本清单全部打勾。未切换生产开关。
