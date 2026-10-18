# V2 未使用目标补充验证协议

这是 `recommendations.unused-holdout.v1` 的离线回溯验证，不是未见用户、时间切分或严格统计独立的盲测。现有画像及目录曾用于调试，无法追溯证明不存在未归档实验。结果报告必须保留这些限制，不把新选目标包装成全新用户样本。

本阶段不修改算法：固定比较线上 V1、原 V2 准备路径和已冻结的 `association-novelty-explore.v3` 候选；不改权重、评分或标签、不启用 V2。新目标一旦用于排名就视为已消耗，不能在它上面调参后继续声称独立验证。

## 1. 排除旧目标与预登记选取

配置固定在 `evaluation/v2-unused-holdout.v1.json`，冻结前不执行新排名：

- 验证原全站 D3 签名锁及原 V2-A 历史签名锁，原数据/目录/账号集合变化则停止。
- 读取已完成的 5A／5B-3／V2 诊断报告的配置头与完成清单，校验 SHA；若档案缺失、损坏或存在未计入的更大留出预算，停止。
- 原历史目标句柄匹配到的调试账号整户排除，避免旧升序目标随历史手账变更后无法逐条复原；另保守排除每个账号当前最前 5 个 ID。
- 排除原 D3 全站及原历史集已使用的账号—Bangumi ID 对，不跨用户混用身份或按相似标题推断系列。
- 对其余目标按固定、带命名空间的 HMAC 顺序选取每账号 4 部，不按评分高低、题材、公共评分或命中结果择优。
- 保留自然训练样本至少 3 部；可用未使用目标不足 4 部时整账号标记不足，不减目标数或制造评分。

不是声明这些作品从未在推荐候选、来源画像或目录里出现，而是按可核验档案排除已被当作评测目标的账号—作品对。账号本身仍可能属于已见账号，未知未归档实验无法被程序排除。

## 2. 整组移除与固定分母

4 个目标一次性从训练 seeds 和已有记录排除表中移除，构建新的最小训练上下文，不复制完整 profile/extraction、旧解释来源或请求缓存，防止整组目标的标签/评分残留。

只保留剩余真实样本、明确偏好、反馈、其余手账身份和所有者。明确偏好可能含目标的先验知识，保持真实输入但在报告中披露；反馈排除不撤销。公共目录特征和 IDF 仍可包含目标的公开元数据，不包含目标私人评分标签。

每个账号只有一个整组留出上下文；重复两次用于验证确定性，不计作两份独立样本。每分类的 Hit/MRR/平均目标 binary-DCG 均保留全部 4 个目标作分母，类别资格不符、无关联、被反馈排除或未入池的目标不替换。

本轮与旧“逐一留出”难度不同，**不得把新旧集命中差值直接当作算法提升**；只比较本轮三个引擎的同条件结果。

## 3. 冻结、签名与一次消耗

1. 固定独立本机 PostgreSQL 回归及 16 项并发测试，实际通过后由正常应用密钥签发当前代码的 `unused-holdout-tests`，不手填通过。
2. `--freeze-only` 只读读取、选择并签发 `unused-holdout-cohort`，不调用排名。锁绑定全部输入句柄、原档案摘要、目录、候选配方和当前代码指纹。
3. 评测前逐项验证所有输入，创建 `.tools/unused-holdout-attempts/<目标集摘要>.json`。摘要不依赖文件名、时间、配方或代码版本；复制锁或重新签发相同目标集不能恢复其“未使用”资格。文件以 exclusive create 防并发竞争。
4. 才开始三臂排名。控制/观测两次排序、证据摘要及 SQL 数必须相等；禁止目标自身成为训练证据来源。结果中只保留匿名计数/位置，不导出真实账号、目标 ID 或评分。

评测失败也保留消耗标记，不自动重试、重签、换样或删除。若需要故障恢复，必须另行审查同代码/同数据条件，且不再称重新抽出的独立试验。该本地防误用机制不是针对能改文件/密钥的系统所有者的安全沙箱。

本协议只进行一次封存试验。目标集消耗标记不是跨任意未来协议的全局逐目标注册表；未来试验必须把本次冻结锁加入历史排除，不能只替换部分目标或协议名便宣称全新验证。本次实际结论见 [补充验证报告](recommendations-unused-holdout-report.md)。

## 4. 判定边界

沿用既有相关性、留出不退步、题材重复度、类别定位及本地 5000ms／6 SQL 预算，另要求至少 2 个账号、8 个目标。安全及证据须无错误。

- 两个主要比较臂在所有类别的留出命中都为 0：`insufficient_evidence`，不是质量通过。
- 请求异常、输入漂移或代码变化：停止，不能转为空结果或减少分母。
- 类别等门槛无法计算：证据不足，不能自动通过。
- 独立用户泛化、部署性能及服务游标回退不是本命令的测量范围，明确标记未验证。
- 阈值通过至多为 `pass_on_target_disjoint_supplement`；不签发生产许可，不能替代 D3 发布凭据链。

## 5. 执行顺序

```powershell
.\scripts\setup-recommendation-postgres.ps1
$env:RECOMMENDATION_TEST_DATABASE_URL='<本机独立 PostgreSQL 连接，不是 DATABASE_URL>'
.\.venv\Scripts\python.exe manage.py verify_unused_holdout_validation `
  --output .tools/v2-unused/新的测试目录/tests.json

# 以下为正常应用环境，只读生产数据。不要使用测试 settings，不执行迁移或业务写入。
.\.venv\Scripts\python.exe manage.py validate_recommendation_holdouts `
  --confirm-sitewide-readonly --freeze-only `
  --prior-site-lock .tools/v2d3/20261018-expanded-freeze/cohort.lock.json `
  --prior-history-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json `
  --output-dir .tools/v2-unused/新的冻结目录

.\.venv\Scripts\python.exe manage.py validate_recommendation_holdouts `
  --confirm-sitewide-readonly `
  --prior-site-lock .tools/v2d3/20261018-expanded-freeze/cohort.lock.json `
  --prior-history-lock .tools/v2a-recall/20261017-freeze-a/cohort.lock.json `
  --cohort-lock .tools/v2-unused/新的冻结目录/cohort.lock.json `
  --test-report .tools/v2-unused/新的测试目录/tests.json `
  --output-dir .tools/v2-unused/新的结果目录

.\scripts\setup-recommendation-postgres.ps1 -Stop
```

每个输出目录必须全新；测试、锁和完整评测只保留在忽略的 `.tools`。真实数据库强制 READ ONLY / REPEATABLE READ；不读取长评、不调用外部模型或资源搜索，不进行 Magnet/剪贴板操作。

配置和验证脚本已纳入源码指纹。冻结后不再改代码；原锁过期或输入漂移时需报告阻塞，不能静默重新冻结目标。线上继续 `content.v1` 且 V2 浏览开关关闭，无新增迁移、业务表、前端布局或算法开关。
