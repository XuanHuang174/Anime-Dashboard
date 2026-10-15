# 6C 部署验证与回退运行手册

## 1. 部署前

在仓库根目录执行隔离测试，绝不能省略安全 settings：

```powershell
.\.venv\Scripts\python.exe manage.py test --settings=config.recommendation_test_settings --noinput
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

在 `frontend` 目录执行 `npm test` 和 `npm run build`，保留本次构建的 `dist` 用于 SHA256 对照，不提交生成目录。
本阶段无模型变更，不要求生产数据迁移、重新关联身份、重算评分或上传封面。

## 2. 发布次序

1. 后端部署含 6A 合同与 6C 测试版本的代码，**保留 `RECOMMENDATION_ALGORITHM_VERSION=content.v1`**。
2. 确认所有后端实例/worker 已替换，避免新旧证据合同同时服务。
3. 发布前端静态资源，刷新 HTML。不要提前移除旧指纹资源，避免仍打开的页面动态 import 失败。
4. 核对构建资源与接口，再执行正常授权账号的 GET 验收。不会自动切换算法或签发 V2 批准。

## 3. 只读部署验证脚本

仓库根目录，先做匿名资源与鉴权探测：

```powershell
.\.venv\Scripts\python.exe scripts/verify_recommendation_deployment.py --expected-dist frontend/dist
```

新版未部署时资源指纹检查失败属于“版本未对齐”，不能忽略后继续声称通过。无 `--expected-dist` 仅验证资源可访问，不证明是已测试构建。

明确得到账号所有者授权后，手动指定唯一预期账号（用实际授权账号替换占位符）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_recommendation_deployment.py --authenticated --expected-account "授权账号邮箱" --expected-dist frontend/dist
```

脚本会隐藏输入用户名与密码，用户名必须与指定账号完全对应；通过正常 `/api/token/` 登录，不读取浏览器 Cookie、不从 Django SECRET_KEY 伪造 JWT。
除正常登录 POST 外仅 GET，最多 40 个请求，不触碰数据库，不调用 want/PATCH/DELETE，不更改线上配置。账号、密码、JWT、证据令牌、作品来源标题、评分及长评不写入报告。

检查包括：前端 JS/CSS SHA256、未登录 401、真实服务端账号身份、三分类 V1 与证据合同、列表/详情逐字段一致、重复读取、分页、伪造令牌 409、身份/算法注入 400、偏好及画像前后相同。
如无推荐行，只能验收冷启动分支，报告 `nonempty_evidence_checked=false`，不能声称真实证据已覆盖。

若已有**人工确认分级的公共目录 ID**，可添加 `--adult-id <BangumiID>` / `--unknown-id <BangumiID>`。未提供时明确记为未验；404 仅证明给定 ID 不可访问，不证明一个未核实 ID 的实际分级。不得传私人记录 ID，也不得为造样改变线上分级。

结果位于忽略目录 `.tools/deployment-6c/<UTC时间>.json`。`passed_public_only` 不是完整验收；`passed_scoped_readonly` 也只证明该账号和所列 GET 范围。浏览器交互、多用户与写入竞争需要分别记录，不以空列表或跳过项充当通过。

## 4. 一键切回 V1（仅运维在需要回退时执行）

在 Render 后端服务 Environment 中把唯一算法开关设为：

```text
RECOMMENDATION_ALGORITHM_VERSION=content.v1
```

保存并重启/重新部署**全部实例及 worker**。V1 不需要 V2 批准文件；不要为了回退修改分数、题材规则、代码常量或生成新批准文件。保持当前 6A/6B/6C 兼容代码即可，不必做数据库回滚。

当前已是 V1 时不要为测试先切 V2；本阶段对照切换仅在隔离 settings 中完成。

## 5. 缓存及旧页面处理规范

- 公共索引键由算法、题材规则、特征、提取版本及 V2 策略组成；用户证据不进入共享缓存。
- 线上默认使用进程本地缓存时，新的 shell 进程清缓存无法清空其他 worker；**完整重启所有 worker**才是可靠的进程缓存刷新方式。公共特征索引现有 TTL 为 60 秒。
- 若以后改为共享缓存，确需清理时，只针对当前配置的准确版本命名空间，由运维执行：

```powershell
python manage.py shell -c "from recommendations.version_control import clear_version_cache; clear_version_cache('content.v2')"
```

这是可选的单一公共键删除，不等于改算法开关；不清 V1、会话、认证或其他业务缓存。禁止使用 `cache.clear()`、Redis `FLUSHALL`、通配批删或文件目录批删。
- 分页与证据令牌是无状态签名，切版本后旧令牌返回 409；无须清理用户记录。前端重新加载第一页并用新卡片的令牌打开详情。
- 回退复验需检查列表 `algorithm_version=content.v1`、正确证据上下文、旧 V2 令牌不可复用；不能通过忽略令牌强行展示新理由。

## 6. 验收边界

隔离测试与演练报告不批准 V2 上线，也不自动放宽性能门槛。6C 新测试纳入服务端发布指纹后，旧 V2 签名凭据会失效；以后如需切换，必须对届时完整代码重新执行既有六门槛评测和人工批准。
本阶段的遗留问题与人工检查项见 [验收报告](recommendations-6c-acceptance.md)。
