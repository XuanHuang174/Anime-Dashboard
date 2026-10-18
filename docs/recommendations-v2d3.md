# V2-D3：审批分派、自然只读评测与部署回退

日期：2026-10-18。D3交付受控服务入口和可重复验收工具，不意味着自动上线V2。**线上默认V1；只有当前完整配方的六门槛全部通过、凭据链有效、人工审核并统一启用后，增强浏览才使用V2。** 不修改用户评分、私人题材、长评或身份，不引入新模型/前端仪表盘。

## 1. 独立服务开关

默认值（示例，不会自动修改本地或Render的`.env`）：

```text
RECOMMENDATION_ALGORITHM_VERSION=content.v1
RECOMMENDATION_V2_BROWSE_ENABLED=False
RECOMMENDATION_V2_BROWSE_APPROVAL_FILE=
RECOMMENDATION_BROWSE_ENABLED=True
RECOMMENDATION_BROWSE_EPOCH=<所有worker一致的发布代次>
```

`deployment.py`分派增强浏览、能力和携带D2票据的详情。旧分页/无票据详情继续V1以兼容旧客户端；客户端不能通过算法、用户、配方或版本查询参数选择V2。新开关与旧 `RECOMMENDATION_V2_APPROVAL_FILE` 分离：旧冻结V2引擎审批不能授权修正召回/运行时/控制的新浏览配方。

开关关闭时无需读取审批文件，正常V1完全不依赖凭据。单独打开开关、凭据缺失/过期/篡改/与当前源码不符时返回明确503，不假装成功或私自绕过审批。配置旧全局算法为V2或同时关闭增强浏览也不能放行新配方。

准备配方 `serving_enabled=false` 与服务配方 `true` 拥有不同的完整配方摘要。V2服务、游标和详情证据使用同一服务配方，准备阶段的旧票据不能混用；其余计分/召回/风格规则与D1/D2一致。本阶段不调参或改变V1排序。

## 2. 凭据链与六门槛

审批目录内必须同时有：

```text
approval.json       browse-approval，绑定服务配方及各凭据摘要
evaluation.json     browse-evaluation，当前代码、冻结自然群体、历史基线及实际结果
tests.json          browse-tests，固定独立PostgreSQL/前端/浏览器/构建凭据
performance.json    browse-performance，部署服务进程影子性能凭据
```

全部签名绑定应用SECRET_KEY和当前前后端源码指纹，最多7天有效；过期必须复测，不通过延长日期或复制旧文件放行。审核同时重新计算六项门槛，不能只写一个 `gates=pass`：

1. 安全：后端硬过滤及隔离测试通过，真实只读样本无安全错误。
2. 证据：来源/个人评分/共有题材独立核对，实际检查非空且无证据错误。
3. 相关性：延续原0允许相关性/留出下降、至少0.001发现重复度改善；逐账号留出不降。至少两个自然留出账号；两版发现留出完全零命中不当作质量通过。
4. 类别：熟悉类集中、探索类保持关联且有至少两个新方向，维持原门槛。
5. 性能：原引擎5000ms/6条SQL门槛不变；另外对完整浏览服务进程使用独立预注册的8000ms/32条SQL预算，绝不把两个计量边界混为一谈。本地跨网Neon数据不能填充部署证据。
6. 回退：固定隔离测试验证V1恢复、游标/详情失效、多用户与已存记录保护；真实结果的用户/算法快照隔离也核对。

配置在 `evaluation/v2d-deployment.v1.json`，在新群体排名前固定。它不是为了观察结果后放宽旧质量门槛。SQL/墙钟记录不是私人喜欢概率，不能用于页面匹配百分比。

## 3. 固定独立验收凭据

只使用本机专用PostgreSQL，命令拒绝生产Neon和SQLite。先启动既有便携实例并提供连接，浏览器依赖使用已安装的Playwright：

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
$env:PLAYWRIGHT_MODULE='C:/path/to/existing/node_modules/playwright'
.\.venv\Scripts\python.exe manage.py verify_recommendation_browse_release --output .tools/v2d3/<新目录>/tests.json
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

父进程不读取正式手账；固定子进程依次执行D3 PostgreSQL全量/并发测试、前端全部测试、生产构建、真实React页面的合成浏览器验收，不能传自定义测试模块或手写通过状态。存在coverage模块时同时采集后端覆盖率，不自动安装依赖。只有全部成功且源码未变化才签发当前代码凭据。

浏览器为无头本地隔离实例，所有服务为合成响应，不使用用户浏览器登录态；外部HTTP、真实Magnet和剪贴板副作用被禁用。

## 4. 原历史集 + 新自然群体（互不替代）

先用原两个授权账号和原V2-A签名锁，执行当前代码的 `audit_recommendation_execution`。保留历史4目标和扩展12目标；不重新冻结它们。本次D3评估必须引用该完整历史报告，验证manifest/配置/代码指纹和D1等价检查。

新群体使用用户已明确授予的全站只读授权：**全部活跃且自然形成画像、至少4个有效样本的账号**，不取邮箱/用户名/密码/长评。上限200个活跃账号/100个合格账号，超过停止而非择优截取。每账号按Bangumi ID升序固定前4个目标；不根据命中率、题材、作品流行度或V2输出选账号/目标。原阈值和有效作品规则不变。

```powershell
# 此步只冻结，不运行任何排名。
.\.venv\Scripts\python.exe manage.py evaluate_recommendation_deployment --confirm-sitewide-readonly --freeze-only --output-dir .tools/v2d3/<新冻结目录>

# 数据/代码漂移就停止，不自动重选。原历史报告和固定测试凭据缺一不可。
.\.venv\Scripts\python.exe manage.py evaluate_recommendation_deployment --confirm-sitewide-readonly --cohort-lock .tools/v2d3/<冻结目录>/cohort.lock.json --historical-report .tools/v2d3/<历史目录>/recall-audit.json --test-attestation .tools/v2d3/<测试目录>/tests.json --output-dir .tools/v2d3/<新评测目录>
```

每次数据库强制 `READ ONLY REPEATABLE READ`；1200秒预算，超时/漂移不生成部分成功报告。留出仅移除内存训练样本及该规范ID的已有排除，不修改数据库、私人分数或反馈。原始不合格/缺题材目标不事后换掉。只输出匿名统计、指标与HMAC，不输出账号、来源作品、个人评分、私人题材或长评。

两类结果必须分开解释：新的全群体指标不是历史难目标被命中的证明；样本不足账号不冒充留出准确率，原两个账号的小样本/明确偏好路径仍在历史及隔离测试中保留。

## 5. 部署服务进程影子性能记录

在 **Render已部署当前D3代码、V1/新开关关闭** 的Shell中，对原两个明确授权账号运行：

```text
python manage.py profile_recommendation_deployment --user-email <多样本授权账号> --user-email <少样本授权账号> --confirm-authorized-samples --output-dir .tools/v2d3/<全新性能目录>
```

仅读取这两个账号；V1/V2准备适配器都走完整浏览服务，记录冷/热公共缓存实际状态、类别、风格、SQL和墙钟。它是服务进程影子调用，不向用户提供V2，也不写手账；不测HTTP网络TTFB、不当作负载测试。

只有运行环境同时提供Render服务ID和部署commit时，凭据才标部署服务进程，服务ID仅留摘要。本机执行会自动标记本地，不能靠命令参数声称远端性能。该环境标记是运维可信边界，不是对掌握服务器/SECRET_KEY的操作者的防伪硬件证明。当前未取得Render Shell执行结果时，性能门槛必须为证据不足，即使本机耗时很好也不能批准。

## 6. 人工批准与统一切换（本任务不会自动执行）

审核自然评测、当前测试和部署性能后，再调用：

```text
python manage.py approve_recommendation_browse --evaluation <evaluation.json> --tests <tests.json> --performance <performance.json> --output-dir .tools/v2d3/<新审批目录> --confirm-reviewed
```

任何门槛不通过都拒绝生成审批。通过也只生成凭据包，绝不修改环境变量。批准文件不得进入Git、静态目录或公开附件；使用Render安全文件/持久私有目录提供四份文件，审批路径仅服务端配置。部署必须包含版本指纹涉及的前后端源码，不能删除缺失源码来绕过核验。

后续经人工确认才将所有worker统一设新开关True及同一审批文件/新发布代次。旧全局算法始终保持 `content.v1`。先部署兼容前端和后端，待所有worker一致，再做授权账号只读核验；不要通过线上写评分、反馈或加入想看来制造验收数据。

## 7. 一键安全回退

统一设置 `RECOMMENDATION_V2_BROWSE_ENABLED=False`，同时换一个所有worker一致的新 `RECOMMENDATION_BROWSE_EPOCH`；旧全局算法保持V1，增强浏览保持开启。无需有效审批即可恢复V1。

旧V2游标/详情票据409，前端清空旧证据/详情/资源态，明确重读第一批；老服务400游标也有首批恢复入口。V1/V2的公共缓存和签名命名空间分离，旧公共缓存自然过期，不执行 `cache.clear()` 或 `localStorage.clear()`。绝不删除手账、偏好、反馈、评分、长评、数据库或上传文件。

没有代码和开关已部署一致的证据，就不称为线上D3终验；没有六门槛和部署性能凭据，就不称为V2已获上线许可。
