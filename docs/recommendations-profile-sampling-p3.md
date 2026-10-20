# P3：门槛设置部署验收与受控开放

## 当前结论（2026-10-20）

**部署前置检查通过，允许由用户手动执行受控开启；尚未宣称生产开关已开启或 P3 全部结案。**

线上默认仍为 `content.v1`，门槛功能开关读取为 false。P1/P2 的线上接口可读取完整政策字段，推荐页 JS/CSS SHA256 与本次本地生产构建逐文件一致。

本轮没有生产 PATCH、加入、反馈、身份关联或数据库写入，没有改 Render 设置、没有调用生产数据库，也没有修改两账号的评分/长评/私人标签。唯一 POST 是正常账号登录。特殊写入和锁竞争全部使用独立本机 PostgreSQL；浏览器合成测试拦截全部 HTTP，不允许外网访问。

## 交付与自动化覆盖

1. `scripts/verify_recommendation_sampling_deployment.py`：有请求上限的两账号只读审计，默认不连接 Django 或 `.env`，只允许普通登录 POST；要求明确部署确认、指定账号和预期的开关状态。失败不打印密码、令牌、来源作品或游标。
2. `scripts/prepare_recommendation_sampling_rollout.py`：仅打印统一配置，`applied=false`。始终固定 V1，关闭 V2 浏览；不触碰增强 9 的浏览开关或数据库。
3. `recommendations/tests/test_sampling_rollout.py`：开关往返、提升/降低门槛、旧证据/浏览游标失效、跨用户/旧重试保护、真实评分 0/未评分逐字段保留、审计器拒绝业务写入；将实际 Django API 响应交给只读审计器验证开启/关闭两态。
4. `frontend/scripts/test-recommendation-sampling.mjs`：完整生产页面、客户端和原生 dialog 的隔离浏览器闭环，测试九档、预览与当前画像分离、取消、失败保留草稿、连击合并、原子保存、低分来源证据、保存成功刷新失败只 GET 恢复、多标签页冲突、回退保留门槛、窄屏/短横屏和焦点返回。
5. 复跑原宽版工作台 12 组浏览器检查，沿用原区域的新名称，不删减既有覆盖。

前端 229 项合同/组件测试通过，生产构建通过；既有主包体积警告保留，不扩展为打包重构。P3 新浏览器脚本 6 组闭环及原工作台 12 组检查全部通过；已检查桌面和 320px 窄屏截图，无横向溢出、保存栏可见。门槛专项 PostgreSQL 首轮 31 项通过，包含原有 3 项真实多连接行锁竞争与增量迁移/旧后端插入兼容。

完整 PostgreSQL 回归：761 项，760 项通过、1 项可选真实样本测试跳过，耗时 189.289 秒；Django 系统检查无问题，迁移 dry-run 无模型差异。不以真实库或 SQLite 替代。

## 线上只读结果

两个明确授权账号均通过关闭状态的只读基线，最终各 27 次请求，报告仅在忽略目录 `.tools/deployment-sampling-p3/` 保存。以下为只读聚合计数，不代表已经保存或正式采用较低门槛。

| 授权账号场景 | 6.0 预览可分析 | 当前 8.5 可分析 | 10.0 预览可分析 | 当前推荐依据 |
| --- | ---: | ---: | ---: | --- |
| 多样本测试账号 | 72 | 34 | 0 | 作品画像 |
| 少样本测试账号 | 3 | 2 | 0 | 明确选择题材 |

通过项：

- 前端静态构建一致；未登录接口 401；实际账号身份符合授权。
- 九档参数合同、当前生效政策、画像内保存设置一致；未知档位、重复参数、用户 ID 越权均 400。
- 6.0/8.5/10.0 预览正确标记未保存，缓存为 private/no-store；预览前后偏好与正式画像摘要不变。
- 三类推荐全部 V1，首屏各核验 12 条证据及安全分级；普通列表/详情解释一致。
- 同一游标重试：作品顺序、证据、快照、过期时间、批次/页和总数一致，跨页无重复。续批游标本身由 `signing.dumps` 带时间戳签发，字符串可能不同，不把这种差异等同于排序跳位；报告记录变化字段名而不记录游标内容。

**范围限制**：抽查返回作品不能替代全库安全证明；成人/未知分级绕过、跨用户攻击和写入零副作用由隔离数据库回归验证。HTTP 耗时不代表 SQL 次数或服务器计算时间。本次没有扫描全站私人手账。

## 用户手动开启（已获本轮确认）

在后端 Render 服务的 Environment 中，统一设置并部署到所有 worker：

```text
RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=True
RECOMMENDATION_ALGORITHM_VERSION=content.v1
RECOMMENDATION_V2_BROWSE_ENABLED=False
```

除门槛开关外，另两项是保持 V1 的安全核对值；不要改成 V2，不改变增强 9 的筛选/浏览开关。部署期间不同 worker 混跑可能返回明确的上下文冲突；等待部署一致后刷新推荐页，旧证据失效应重新读取，不尝试绕过 409。

可生成配置但不会自动应用：

```powershell
.venv/Scripts/python.exe scripts/prepare_recommendation_sampling_rollout.py --enable
```

开启不自动修改任何人的门槛。原默认 8.5 的用户仍为 8.5，只有用户保存不同档位后才按新值取样；历史已保存值将按明确展示的实际政策生效。

## 开启后的终验（尚待执行）

由用户完成 Render 部署后，对两个授权账号分别运行：

```powershell
.venv/Scripts/python.exe scripts/verify_recommendation_sampling_deployment.py --deployment-confirmed --expect-enabled --expected-account <授权账号> --expected-dist frontend/dist
```

密码交互输入，不写进报告或配置。与关闭基线不同，`--expect-enabled` 必须检测到实际 true 才通过，不能以旧服务、404、503 或禁用控件冒充完成。

真实页面补验：门槛控件可用、九档完整、未保存关闭不生效、预览与当前画像区分、保存刷新后便签和理由采用实际门槛、桌面/窄屏与键盘返回。**本次内置 Browser 虽能打开推荐页，但页面读取持续超时；computer-use 路径无法可靠完成线上页面操作，真实页面检查仍待补验，不能以本地 E2E 冒充。**

如需线上保存测试，限定这两个用户明确授权的个人测试账号，先记录原门槛/标签，完成后按版本校验恢复；不修改评分、不伪造样本，不对其他用户操作。本轮尚未执行线上保存。

## 安全回退

出现问题时，Render 统一设置：

```text
RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=False
RECOMMENDATION_ALGORITHM_VERSION=content.v1
RECOMMENDATION_V2_BROWSE_ENABLED=False
```

部署一致后刷新推荐页并执行 `--expect-disabled` 只读审计。实际取样回到 8.5，已保存门槛保留，页面明确展示已保存值/有效值并禁用编辑。不删除迁移列，不反向清空设置，不删除任何评分、标签、长评或反馈；不启用 V2。

用户内存中的浏览/详情上下文按既有版本校验失效并重读。不要运行 `cache.clear()`、`localStorage.clear()` 或删除所有会话。公共特征缓存不含私人门槛，不需要全站清库。

## 待办清单

- [x] P1/P2 线上静态构建与后端只读合同匹配。
- [x] 两自然账号关闭状态只读基线、预览零持久化。
- [x] 隔离 PostgreSQL 门槛/冲突/开关回退与并发验证。
- [x] 完整页面合成多端、失败恢复、键盘和旧工作台回归。
- [ ] 用户手动设置 true 并完成 Render 全量部署。
- [ ] 开启后两账号 `--expect-enabled` 只读终验。
- [ ] 真实线上页面交互补验（Browser 读取超时限制仍在）。
