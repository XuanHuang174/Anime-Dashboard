# 核心 6A：证据合同与一致性审计

## 1. 路径审计清单

| 路径/项目 | 原状及风险 | 本次处理与验收 |
| --- | --- | --- |
| 主推卡、网格卡 | 都消费列表 results，不存在独立“卡片 API” | 两者共享相同每作品证据合同，不新增接口或重做布局 |
| GET /api/recommendations/ | 直接暴露排序器的 reason/evidence | 排序结果产生后，由请求内 EvidencePresenter 统一生成对外解释并用专用 Serializer 输出 |
| GET /api/recommendations/{bangumi_id}/ | 独立调用非确定性证据路径，无点击时版本校验 | 同一个 Presenter、同一个确定性选择函数；支持 evidence_token 校验，不调用完整推荐排序 |
| V1 内部证据估分 | 关联度参与熟悉类评分，改求和方式可能改变排序 | 原算术、评分公式、候选选择和重排均保留；展示结果绝不回写排序分 |
| 版本标识 | 高分来源只有特征规则信息，明确偏好来源缺少统一版本 | 补充合同、算法、题材、特征、解释选择规则、画像和偏好版本 |
| 状态变化 | 旧卡片可能打开基于新画像的详情 | 新前端传递卡片令牌；不一致返回 409，关闭旧详情并重载推荐，不重试旧令牌 |
| 来源安全 | 列表/详情的来源安全复核不统一 | 每次响应使用一次批量安全查询，同时复核页内作品和全部有效画像种子，发现变更拒绝响应 |
| 身份边界 | 目录/公共库/私人记录的主键不同 | 对外来源只包含 Bangumi ID，不输出 anime_id/record_id；不按标题合并身份 |
| 隐私与副作用 | 不允许读取长评推断口味或回写真实资料 | 复用只读画像提取；GET 无 INSERT/UPDATE/DELETE，不查询 review 列，无外部模型调用 |

`evidence.py` 不是第二套排序器。它调用既有 `engine.evidence_for(..., deterministic=True)`，继承现有来源搜索范围和关联依据，并稳定求和/并列选择。
V1 内部 `deterministic=False` 路径冻结；浮点边界下对外展示可能选择另一条同样真实的关联证据，但不会改变排序内部的分数或顺序，也不宣称展示来源是全部排序因素。

## 2. 增量合同

列表保留 `recommendations.3b.v1`，现有 `kind/source/shared_tags/reason` 字段兼容。每个结果及详情补充：

```json
{
  "evidence": {
    "contract_version": "recommendations.evidence.v1",
    "kind": "rated_work",
    "source": {"bangumi_id": 101, "title": "示例来源作品", "personal_score": "9.0"},
    "shared_tags": ["音乐", "日常"],
    "feature_rule_version": "journal-genres.v3",
    "shared_tag_sources": {"音乐": ["private_base_tags"], "日常": ["private_base_tags"]},
    "versions": {
      "algorithm_version": "content.v1",
      "ranking_policy_version": "frozen-v1",
      "taxonomy_version": "genres.v4",
      "feature_version": "genres-idf.v4",
      "feature_rule_version": "journal-genres.v3",
      "selection_rule_version": "association-evidence.v1",
      "profile_version": "<64位不透明HMAC>",
      "preference_version": 0,
      "catalog_feature_version": "<公共IDF权重SHA256>"
    },
    "interpretation": "shared_genre_association",
    "is_probability": false,
    "is_exhaustive_ranking_explanation": false
  },
  "evidence_context": "与 evidence.versions 相同的版本对象",
  "evidence_token": "<64位用户/作品绑定HMAC>",
  "evidence_disclaimer": "共有题材是关联证据，不代表唯一排序原因，也不代表你喜欢这部作品的概率。"
}
```

此处为隔离测试结构示例，非真实用户评分。

- `source.personal_score` 只取有效来源作品的真实个人评分，格式为一位小数；不是候选作品公共评分，也不是内部排序值。
- 明确偏好使用 `kind=explicit_preferences`、`source=null`、`shared_tag_sources={}`，不虚构作品或个人评分。
- 无有效依据时 `evidence=null`、`reason=""`；仍返回上下文和令牌，不伪装成有推荐理由。
- 列表顶层也返回 `evidence_contract_version/evidence_context/evidence_disclaimer`，空结果及冷启动不丢失版本语义。
- `profile_version` 是有效输入的用户绑定 HMAC 内容指纹，不是数据库版本计数，不暴露评分摘要或私人记录主键。
- `catalog_feature_version` 是本次实际 IDF 权重的指纹，不是“目录任意字段更新”的全局计数。作品自身原始标签/标题/分数等另由证据令牌绑定。
- 公共评分、个人评分、内部排序分含义不变；不引入投票人数或评分可信度推断。

## 3. 快照与兼容规则

```text
读取列表 -> 保存该作品 evidence_token
打开详情 -> GET /api/recommendations/{bangumi_id}/?evidence_token=<原令牌>
  一致：200，列表与详情的证据字段完全一致
  画像/偏好/规则/算法/有效权重/作品输入改变：409，不返回新证据混入旧卡片
  作品成人、未知分级或不存在：404
  非法/重复令牌、外部 user_id、record_id 或算法覆盖参数：400
```

令牌绑定当前登录用户、作品 Bangumi ID、有效画像与偏好、算法与解释规则、权重及当前作品输入。跨用户、跨作品复用均失败；所有响应继续 `private, no-store` 并按 Authorization/Cookie 区分。

评分/观看状态/私人有效题材改变后，重新提取当前用户画像，不读取旧画像缓存。私人长评改变不属于画像输入，也不为它查询长评。
相同有效输入恢复为相同内容指纹是允许的，它不是严格递增的修改日志。

为兼容既有直接详情消费者，无参数详情仍可读取当前独立版本；它不承诺与某个未提供令牌的旧卡片一致。新页面已完成最小传输层接入，带令牌时还检查响应回显；409 提供“重新加载推荐”，不绕过校验偷偷取新证据。

分页状态另绑定解释合同/选择规则与实际权重版本。旧分页状态需要重新加载第一页，不更改排名计算。

## 4. 性能与零副作用

- 详情仅查询一个作品、复用当前画像及公共 IDF 特征索引、生成一份解释，不调用 `rank_candidates/rank_context` 或重排候选池。
- 公共索引冷缓存可能仍需构建目录特征，这是现有索引路径，不等于重算用户推荐列表；不宣称详情变成零数据库开销。
- 每页一次安全集合复核，不按卡片逐个查询来源；同题材解释只在该次 Presenter 内复用，不共享私人证据缓存。
- 本次增加证据安全复核和签名开销，不能把旧 V2 性能报告当作新版本性能已通过。
- 不运行生产迁移、全站扫描或数据修复；不写评分、标签、长评、偏好、身份绑定或观看状态。
- V2 仍未启用；新证据服务和测试纳入发布指纹，旧发布凭据不能直接批准新代码。

## 5. 一致性与回归验证

```powershell
.\.venv\Scripts\python.exe manage.py test recommendations.tests.test_evidence_contract recommendations.tests.test_quality_baseline recommendations.tests.test_engine_api recommendations.tests.test_ranking_v2 recommendations.tests.test_comparison --settings=config.recommendation_test_settings --noinput
.\.venv\Scripts\python.exe manage.py test --settings=config.recommendation_test_settings --noinput
.\.venv\Scripts\python.exe manage.py check --settings=config.recommendation_test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings

# frontend 目录
npm test
npm run build
```

验收清单：

- [x] 三类列表与带令牌详情的类型、来源、个人分、共有题材、版本和文案一致。
- [x] 明确偏好与无依据分支不冒充高分画像，字段类型和 null 语义明确。
- [x] 同请求重复运行、倒序种子输入、3 个不同 PYTHONHASHSEED 的对外证据完全一致。
- [x] 评分、观看状态、私有题材、偏好版本、目录权重、作品输入、规则和 V1/V2 变更均拒绝旧令牌。
- [x] 不接受用户/记录身份注入；跨用户/跨作品令牌不可复用；成人及未知分级硬过滤。
- [x] 详情不调用排序引擎；每页查询数量不随卡片数增加。
- [x] GET 未写库、未查询 review；评分、标签、长评及偏好前后完全保留。
- [x] 既有 V1 固定排序基线保持不变，对外解释不回写排名对象。
- [ ] 部署后的真实账号与浏览器联调：尚未执行，后续使用正常授权的内置 Browser 或明确批准的只读接口验证。

自动化测试仅使用隔离 SQLite 与合成样本，禁止为测试在正式 Neon 中补分或改标签。上线先部署兼容的新后端，再加载新前端；旧页面/分页出现 409 时重新加载即可，不进行静默降级。

本次检查结果：新增 15 项后端一致性测试；后端全量 553 项无失败、5 项环境条件跳过，前端 82 项全部通过，生产构建通过。Django 检查通过、无新增迁移；既有约 871 KB 主包体积提示仍存在。本次未访问正式库执行测试，也未进行线上交互验收。
