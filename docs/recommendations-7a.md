# 核心 7A：交互合同与状态边界

## 范围与身份

- 复用现有 Django `POST /api/recommendations/{bangumi_id}/want/` 与安全详情 GET，不新增接口、业务表或响应字段。
- 服务端仅接受登录用户和 URL 中的 Bangumi ID；want 请求体仍为 `{}`，不接受其他用户 ID、记录 ID、个人评分或长评。
- `bangumi_id` 是目录身份；`anime_id` 是共享资料主键；`record_id` 是私人记录主键，三者不混用。
- 前端资源标题/清洗后的关键词仅用于检索，不能替代任意作品身份或观看状态判断。
- V1 排序及评分、题材、长评保护逻辑不变，V2 不启用。新合同测试纳入发布校验指纹，不复用旧 V2 批准凭据。

## 交互合同

| 情况 | 卡片/加入 | 详情 | 资源 |
| --- | --- | --- | --- |
| 未加入 | 查看详情、加入想看，共两个操作按钮 | 加载安全详情后可操作 | 只在详情提供入口 |
| 已有记录 | 显示想看/在看/看过/搁置，不覆盖已有字段 | 正常查看 | 仍可检索 |
| 加入中 | 同一 Bangumi ID 的并发调用共用一个 POST promise，按钮禁用 | 不提前伪造成功 | 已加载详情可正常展开资源 |
| 加入被拒绝/未确认 | 重试、核对身份或对应限制说明 | 不因 want 错误转为详情错误 | 保持可用，局部搜索错误不传入推荐状态 |
| 收到有效成功确认（created 或 existing） | 一次回调同步按钮并刷新列表 | 同步清除整份旧 item，进入 refreshing，按当前选中作品重新 GET | 旧资源区卸载取消请求，完整详情返回后可重新打开 |
| 加入已确认但重读失败 | 仍为已加入；不能误报 POST 失败 | “加入操作已确认，但详情更新失败”；重试只发 GET | 不基于未加载的新详情打开资源 |
| 普通卡片 token 或上下文 409 | 不执行任何新增写入 | 丢弃旧依据，要求“重新加载推荐”，不自动无 token 重试 | 不从失效卡片直接打开 |
| 切作品/关闭/卸载 | 在途 POST 保留原有确认与卸载保护 | 取消旧 GET，迟到响应不更新当前会话，也不重新打开旧作品 | 跟随详情卸载，状态不跨会话保留 |

## 详情状态机

`closed → loading → ready / error`

只有 `createWantController` 收到经过 `validateWantResult` 校验的有效成功确认后，才会通知页面启动：

`ready/loading/error → refreshing（无 item、无旧依据）→ ready / error`

- 普通打开调用 `client.detail(id, signal, cardToken)`，同时验证返回身份及原卡片证据上下文。
- 已知加入成功导致 owned/profile 输入可能变化，因此显式建立**新的详情读取上下文**，使用原有独立详情 GET；这不是 409 的兼容兜底。重读后接收的是新的整份标题、季度、观看状态、证据和版本。
- `existing` 确认也会重读；若实际输入没变，新令牌可以与旧令牌相同，不人为伪造版本递增。
- 详情普通重试保留原 expectation；若已经是 snapshotChanged，控制器拒绝自动重试，由页面关闭旧详情并重载推荐。
- 另一个作品 A 的加入也会改变全局 owned 指纹：若此时已切到 B，刷新 B 而不是重新打开 A；若已关闭详情，则不再请求详情。
- 控制器只存当前会话，不写 localStorage，不保存私人评分或长评。状态由 `useSyncExternalStore` 接入 React。
- 同一个共享 POST 的多个调用者不能分别重复执行同步回调。刷新回调失败只记录同步警告，不覆盖已经确认的写入结果。

## 资源入口与 7B 边界

7A 提供可用的基础入口，不提供无法点击的占位按钮：

- 卡片仍只有两个操作按钮；详情新增紧凑的“寻找资源”按钮。
- 点击后在**同一个原生 dialog 内**展开内联资源区，复用 `AnimeResourceDrawer`，不把 body portal 放在 dialog 顶层下面。
- 显示完整作品名称与原季度；输入框可修改关键词，首次展开后沿用现有自动搜索、复制与网页兜底逻辑。
- 标明清洗词不保证季度/版本匹配；只有安全详情完成后才允许展开。
- 资源查询继续走已登录的 Django 代理。网络错误保留在原组件局部，意外渲染异常由独立 ErrorBoundary 提供“重试资源区”；不改变推荐、详情或 want 控制器状态，不影响原手账抽屉。

7B 再完成侧滑布局、窄屏面板、全套焦点/Escape/异步复制会话联动及真实浏览器验收；不将 SSR 与函数测试冒充这些交互已验收。

## 合同测试与执行

```powershell
# 仓库根目录：隔离 SQLite，不能省略 settings 后使用正式 Neon
.\.venv\Scripts\python.exe manage.py test recommendations.tests.test_interaction_contract recommendations.tests.test_state_loop --settings=config.recommendation_test_settings --noinput
.\.venv\Scripts\python.exe manage.py test --settings=config.recommendation_test_settings --noinput
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings

# frontend 目录
npm test
npm run build
```

新增后端 5 项合同测试，覆盖成功后旧令牌 409、作品被隐藏出列表后仍可新上下文读取、跨作品 owned 变化、existing 不改任何字段、失败/多用户隔离及安全分级硬过滤。
新增前端 10 项测试，覆盖真实 JSX 的按钮数量与资源上下文、一次确认回调、整份 item 作废、普通 409、重读失败、同步异常、身份冲突、迟到请求和资源入口独立性及局部错误边界。ErrorBoundary 的回退渲染测试不等于真实浏览器中的网络/布局验收。

本阶段未执行生产写入或线上 Magnet 唤醒。PostgreSQL 实际行锁竞争测试仍需独立 PostgreSQL 环境；不能拿 SQLite 结果替代。

本次检查：前端全量 108 项通过、生产构建通过；后端全量 570 项中 565 项通过、5 项环境条件跳过（4 项 PostgreSQL 行锁竞争、1 项可选归档样本），无失败。Django 检查通过、无新增迁移；既有约 871 KB 主包体积提示仍在。浏览器实际交互及部署后的只读联调另行验收。
