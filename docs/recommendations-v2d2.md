# V2-D2：固定浏览快照、详情证据绑定与切换恢复

日期：2026-10-18。D2承接D1执行配方，**不启用线上V2、不改算法、不增加业务表或Migration，不新增前端算法切换入口**。生产URL及能力接口仍走V1。D3部署审批、自然群体扩展评测及性能门槛验收未执行。

## 1. 复用同一浏览服务

`v2_browsing.browse_page()` 是准备服务，调用现有 `browsing.browse_page(..., _recipe=...)` 内部适配分支；第一次计算直接使用D1 `execution.execute_v2()`，没有第二套候选、筛选或批次算法。内部配方必须与 `recipe_descriptor()` 完整一致，不接受HTTP参数选择算法。

- V1：`recommendations.browse.v1` / `browse-controls.v1`。
- D2准备路径：`recommendations.browse.v2` / `browse-controls.v2`；额外返回完整 `execution_recipe`。
- 当前配方仍是 `content.v2 + balanced-rerank.v3 + source-pattern-recall.v2 + pattern-prefix-runtime.v1`，风格参数沿用D1预先固定规则。
- 均衡档不附加新的计分；120结果、24批次、12默认页不变。后续页/批只水合固定ID顺序，不重算列表，不生成随机补位。

`v2_browsing.capabilities()` 仅供隔离适配与后续D3准备；**未接入生产 `RecommendationCapabilitiesView`**。当前生产列表仍调用无配方的原V1路径；把环境变量改为V2仍会被浏览服务拒绝。`serving_enabled=false` 是真实阶段状态，不是已上线标记。

## 2. 游标与缓存隔离

V2签名域为 `recommendations.browse.cursor.v2`，与V1分离。签名体含完整执行配方、当前用户的HMAC作用域、画像/偏好/反馈/手账/目录/筛选修订、固定结果ID、全目录公共IDF权重、批页位置与固定过期时间。

同样的数量不意味着同一配方；召回、计分、运行时、风格控制、配方摘要任一变化均使旧游标409。画像、私人标签、身份、已有手账、反馈、目录安全或放送数据变化同样409。续页和重试不延长原快照的900秒有效期；签名字符串的生成时间可不同，但位置、顺序、有效期及证据保持一致。

V1/V2签名不能互相消费。兼容服务识别另一版本的有效签名后返回409，要求重读第一批。公共缓存用不同命名空间：

```text
recommendations.browse.public.v1:...
recommendations.browse.public.v2:<SHA256(recipe_revision + original_public_key)>
```

V2键压缩为摘要，兼容Memcached的250字节限制。缓存只有公共目录行、公共特征索引和目录修订；从不缓存用户画像、结果列表、私人题材或资源搜索结果。即使命中缓存，仍先读取当前目录修订和安全状态。

## 3. 列表到详情：无需重排

D2证据版本在原合同上增补：`recipe_revision`、`recall_policy_version`、`runtime_version`、`control_policy_version`、`browse_context_version`。后者是绑定全部浏览条件和数据上下文的HMAC。既有V1响应不额外返回这些字段。

每张D2卡片另返回签名 `detail_context`，包含规范Bangumi ID、公开筛选条件、完整配方、过期时间和不透明修订；不含明文用户ID、来源作品、个人评分、私人标签、长评、整份结果或公共权重表。

`v2_browsing.detail(user, bangumi_id, validated)` 使用独立PostgreSQL READ ONLY REPEATABLE READ：

1. 验证签名、owner、规范作品身份、配方、有效期及rollout版本。
2. 读取当前画像/手账/目录修订；带卡片 `evidence_token` 时严格要求旧卡片上下文完全一致。
3. 获取该作品和全目录公共权重，复用 `EvidencePresenter`，不调用整个推荐引擎。
4. 重新验证候选及来源安全、反馈修订，比较证据令牌后返回完整详情。

普通409不能去掉令牌偷偷重试。只有前端收到已验证的加入成功回执后，才使用原签名身份/配方/筛选作用域、不带旧证据令牌发起有意的最新详情读取；后端整份重读并签发新详情上下文。若原作用域已过期或配方变化，仍返回409，由用户重载推荐。

生产V1详情遇到D2 `detail_context` 明确返回409，绝不忽略它再返回V1解释。D2没有新增生产详情路由；D3才处理受批准的服务端分派。

## 4. 前端状态与滚动部署

`recommendationExecution.js` 验证完整配方；`mapBrowsePage` 同时校验页面、每项证据、详情上下文与版本。控制器先读取服务端能力；即使结果结构合法，也必须与能力声明配方一致。未知或混合版本停止展示，而非静默回退Mock。

- 续页/换批严格核对配方、快照、条件、证据上下文、位置及已见ID。
- 配方/算法改变、画像或反馈刷新时丢弃旧游标，首批重读；旧回调受AbortController和代次检查阻止。
- 409清空旧证据及详情上下文，并关闭详情/资源会话，保留布局供明确重载。
- 老服务可能对无法识别的V2游标返回400；游标续读遇到400同样停止重试旧游标，要求首批重载。
- 只有已有显式 `browse_disabled + legacy_v1` 合同会自动恢复旧V1分页；一般网络失败不假装安全回滚。
- 加入成功后同步按钮、清空旧详情与资源态，整份读回详情；新版详情不能降级混入V1旧解释。
- 页面布局不变。若隔离环境声明V2，均衡风格说明正确标V2，不继续显示V1文案。

部署次序必须先保留V1的兼容前后端，再由D3批准算法入口；回退只改变服务端算法分派并首批重载，不清用户数据。D2本身没有上线开关；不是把旧V2游标提交给任意旧部署就能无感继续原分页。

## 5. 执行与验证

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='postgresql://recommendation_test:local-isolated-7c-only@127.0.0.1:55437/recommendation_isolated_tests'
$env:COVERAGE_FILE='.tools/.coverage-v2d2'
.\.venv\Scripts\python.exe -m coverage run --branch --source=recommendations scripts/verify_recommendation_v2d2.py
.\.venv\Scripts\python.exe -m coverage json -o .tools/acceptance-v2d2/coverage.json
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_pg_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_pg_test_settings
.\scripts\setup-recommendation-postgres.ps1 -Stop
```

在 `frontend` 目录执行：

```powershell
npm test
npm run build
$env:PLAYWRIGHT_MODULE='C:/path/to/existing/node_modules/playwright'
node scripts/test-recommendation-v2-browse.mjs
```

浏览器脚本复用现有生产页面、原生dialog和同一个资源组件；仅切换合成响应合同。所有HTTP拦截，外网禁止；模拟写入、资源失败、慢请求与回退，绝不进行线上写入或启动BT客户端。

## 6. 后续边界

本阶段未扫描真实全站手账，未更换原冻结集，未声明命中或性能提升。D1盘点的17个自然留出可用账号可以支持后续扩展，但应先独立冻结群体规则及目标，不按命中挑选，继续保留原历史基线。

D3还需部署级分派/审批与回退演练、自然数据和服务端端到端性能复测、最终生产只读验收。D2通过不等于V2已满足质量门槛，也不意味着现在可以修改线上默认算法。
