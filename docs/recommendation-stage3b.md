# 基础 3B：真实推荐 V1

> 这是 3B 阶段的历史基线。加入想看已由 [3C](recommendations-3c.md) 接通；
> [核心 4A](recommendations-4a.md) 将题材词表升级为 `genres.v2`、特征为 `genres-idf.v2`，
> 增加只读自定义题材、来源追溯和可分析样本门槛，排序公式不变。常见题材不自动参与推荐。

本阶段提供推荐计算、只读详情、真实画像/偏好接入；不提供手账写入、反馈训练或复杂协同推荐。正式页面没有 Mock 回退。加入想看按钮明确禁用，留待 3C 完成安全幂等写入。

## 算法和身份

- `content.v1` / `genres-idf.v1` / `genres.v1`：只使用 26 个核心题材与明确同义词（沿用 3A taxonomy）；年份、国家、制作方、作品名、二刷/年度最佳等未定义标签不作为特征。作品标题恰好与题材同名时，不把该词作为该作品特征。
- 喜好来源：当前用户已看过且个人评分 ≥ 配置阈值（默认 8.5）的、安全 Bangumi 身份对齐的记录；使用有效基础标签，不读取长评或自定义管理标签。至少 3 部才形成画像。有明确偏好但样本不足时返回 `preference_based`，文案只引用明确题材，不伪装为高分作品画像。没有任何有效依据返回 `cold_start` 和空数组。
- 同义词合并后，`idf = min(3, 1 + log((N+1)/(df+1)))`，降低常见特征的相对权重。个人高分作品权重按评分、题材数归一化；明确偏好在已有画像下占 25% 的输入权重。
- 为你发现：相关性、弱公共评分及新题材综合分，进行滚动 MMR 重排（相关分 0.5、与近 5 条最大标签重叠惩罚 0.5）。
- 熟悉的心动：实际来源作品的加权共有题材重叠优先；有画像时必须有真实高分作品证据。
- 换一种口味：必须保留共有题材，同时至少有一个不在当前喜好向量中的题材；新题材加权排序。不承诺总有结果。
- `bangumi_id` 是作品唯一身份，绝不把它当作手账记录主键。默认隐藏所有已记录作品，高分种子始终不重复推荐。`watch_status` 只读取当前用户；不返回其他用户信息。
- 后端 `nsfw=False` 且身份非空是硬条件。列表、详情、种子选择均 fail-closed。公共缓存仅保存公开题材/评分/类型，输出前重读当前分级和标签，并重新生成证据。封面只返回验证后的 R2 地址，外部源地址不会进入响应，缺图不排除作品。

## 接口

要求现有 JWT/Session 登录，只使用 `request.user`。拒绝未定义/重复查询参数，不接受用户 ID 或 NSFW 开关。

```text
GET /api/recommendations/?category=all&type=all&min_score=0&hide_recorded=true&page=1&page_size=12
GET /api/recommendations/{bangumi_id}/
GET /api/recommendations/profile/
GET/PATCH /api/recommendations/preferences/
```

分类：`all` / `familiar` / `explore`。类型：all、TV、OVA、剧场版、WEB、短片、动态漫画。`page_size` 1–24，`page` 1–1000。首屏前端固定 12 条，首条为主推；点击加载下一页追加，不更换主推。

响应列表包含 `contract_version`、`algorithm_version`、`feature_version`、`taxonomy_version`、`mode`、`readiness`、`count`、`page`、`page_size`、`state_token`、`next_page`、`results`。作品字段是 3A 公共详情字段（列表不含简介），增加：

```json
{
  "content_rating": "non_adult",
  "watch_status": null,
  "reason": "因为你给《来源作品》打了 9.0 分，这部作品同样包含‘音乐’标签。",
  "evidence": {
    "kind": "rated_work",
    "source": {"bangumi_id": 123, "title": "来源作品", "personal_score": "9.0"},
    "shared_tags": ["音乐"]
  }
}
```

明确偏好证据使用 `kind=explicit_preferences`、`source=null`。没有证据的详情可以返回 `evidence=null`、空理由，前端明确说明资料不足；不会捏造叙述。不输出匹配百分比或把公共评分当作个人评分。

下一页必须携带第一页 `state_token`。该 HMAC 标记绑定用户、评分种子、明确偏好版本、手账身份/状态、筛选、当前排序结果与公开元数据。条件发生变化返回 JSON 409，前端重新加载第一页，避免串页；它是状态校验，不是持久化快照。

错误：401 未登录；400 非法参数；404 不安全/不存在的详情；409 分页或偏好版本冲突；429 限流；503 数据库不可用。限流使用已有用户总限额，加推荐列表/详情共享的 60 次/分钟额度。数据库异常不输出连接信息。

## 资源边界和降级

V1 最多读取 50,000 条公开特征（当前库在范围内），候选精排 400 条，可浏览结果最多 120 条。`count` 是本次可浏览结果数量，不是数据库总数；这是有界推荐集合，不承诺全库穷举排序。类型/最低评分先过滤再截断候选。说明来源检查覆盖全部题材，再选高分前 64 部；画像向量仍使用全部有效种子。

公共特征缓存单槽、60 秒 TTL，并检查公开数量/最大更新时间。常规 save 更新可立即使缓存失效；绕过 updated_at 的 bulk update 特征最多延迟到 TTL。输出前安全门仍即时复查。数据量超过安全预算返回 503；不进行后台网络抓取或 R2 上传。未来 3C/性能阶段可扩展物化特征、任务队列与持久化推荐快照。

页面使用独立请求客户端复用既有 JWT 刷新机制。类别/筛选变更、详情关闭和页面卸载：AbortController + 请求序号双重保护，旧响应不能提交。下一页出错保留已加载结果并重试，409 自动从第一页刷新。偏好 PATCH 携带 expected_version，冲突不覆盖其他页面修改。不会把个人推荐或偏好写入浏览器持久存储。

## 安全执行与验收

**本地 .env 指向正式 Neon；不得直接运行默认 migrate/test 写入测试数据。**

```powershell
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
cd frontend
npm.cmd test
npm.cmd run build
```

后端覆盖：权限/查询合同、真实共有标签、三类区别、冷热启动、手账隐藏、NSFW/未知过滤、跨用户状态标记拒绝、评分/偏好/安全变化的分页冲突、当前缓存安全门、无列表 N+1、外部图片过滤、数据库 503。前端覆盖：响应合同校验、迟到请求隔离、分页去重、固定 API 路径和信号透传、Mock 仅位于 tests/fixtures、路由保护和样式隔离。

界面手动验收：登录打开 Dashboard Banner → 核对真实样本数 → 三类切换 → 类型/评分筛选 → 加载下一页 → 详情 → 保存偏好 → 故障重试与空结果恢复。手机 390px 两列、桌面四列，中间平板三列；缺图统一占位海报。

部署前必须确认 3A 的 `0003_recommendationpreference` 迁移已应用；本阶段没有新增迁移。本轮测试只使用 SQLite 内存数据库，没有修改 Neon/R2。代码推送不等于线上部署验收完成。
