# 目录摘要读取优化与原目标开发输入重冻

本阶段只做显式离线 V2 开发，线上默认 `content.v1` 和所有生产开关不变。

## 精确读取优化：public-manifest.v1

上一轮发现目录指纹读取占 V2 调用总耗时约 81%。新路径不是延长 TTL 或只依赖 `updated_at`，而是每次仍执行当前安全目录 SELECT，在数据库内将有序、完整行内容计算为 SHA256 摘要，只传回计数与摘要：

- 行包含 Bangumi ID、公共评分、标签/中日文名指纹、放送字段和类型。安全目录成员变化会改变计数或摘要；直接 `QuerySet.update()` 不更新时间戳也可被检测。
- 摘要相同才复用不可变的公共元数据行；不同则在**同一只读重复读事务**里重新读取。仍使用原候选水合、来源安全复核与当前特征校验，不缓存用户画像、证据或结果。
- 数据库仍计算整目录内容，没有消除服务端扫描。优化主要减少未变化目录的重复传输，并不消除首次冷读。冷读比旧路径多一次摘要查询（通常 6 SQL），热读仍为 5 SQL。不能把热读收益冒充冷启动首屏收益。
- 若公共特征索引本身也已过期，原引擎仍会另行补读变化行，叠加摘要冷读可能达到 7 SQL；必须如实报告并按原 6 SQL 门槛判定，不能为了压低统计而跳过补读。这是后续首读路径优化的边界，不在本阶段放宽规则。
- 每个显式 reader 最多保留四个公开查询作用域，生命周期由离线命令控制；不进入生产公共索引或用户结果缓存。查询作用域由实际 SQL、绑定值和行数上限区分，年份等仍按既有可靠字段过滤规则执行。
- `execute_v2` 显式传入 reader 才能启用；生产 serving 拒绝此参数。配方记录独立读取版本，排名、分数、证据必须与未优化路径逐项一致。

## 原目标重冻（不是新独立验证）

`refreeze_recommendation_discovery` 只读取原已消费锁中选定的账号，不重新执行选目标算法：

1. 按原账号的签名句柄定位当前账号；按原四个目标的用户—作品签名定位身份，顺序与数量完全保留。
2. 若账号不再有效、原目标不再属于自然有效样本或移出目标后不足三部训练作品，停止，不替补、不改分。
3. 将当前输入指纹与目录版本冻结为**新的开发快照**，链接旧锁和旧结果摘要，旧文件保持不动；此步骤不计算推荐。
4. 后续评测必须再次核对新快照，不接受新的输入漂移。原目标已经揭晓，所有收益仅为开发回归，不能重新宣称独立盲验证。
5. 五臂（V1、参考 V2、保护型 V2、整体画像候选、双视图候选）在同一当前快照下比较，四目标共同移出训练。所有 V2 臂使用相同精确读取优化，避免把网络等待差异当作算法收益。
6. 第一个固定账号还会在全部 V2 臂、三类别中比较优化前后输出摘要，共 12 对，另有隔离 PostgreSQL 更新/安全/过滤矩阵。观察重复运行只允许冷读多一次 SELECT，排名与证据必须相同。

保留原 1,200 秒总预算、5,000ms/6 SQL 门槛。冷读额外查询、热读、目录变化后重读分别记录；真实服务部署、风格游标、独立验证不由本命令代替。

## 执行顺序

1. 启动已有本机隔离 PostgreSQL；设置测试库环境变量；运行固定凭据命令（不向生产库运行测试）：

```powershell
.\.venv\Scripts\python.exe manage.py verify_unused_holdout_validation --output .tools/v2-manifest/20261018-tests/tests.json
```

2. 下列命令使用正常数据库配置，但会强制只读事务；绝不带测试数据库 settings，也不执行迁移：

```powershell
.\.venv\Scripts\python.exe manage.py refreeze_recommendation_discovery --confirm-sitewide-readonly --consumed-cohort .tools/v2-unused/20261018-freeze/cohort.lock.json --consumed-result .tools/v2-unused/20261018-evaluation/evaluation.json --test-report .tools/v2-manifest/20261018-tests/tests.json --freeze-only --output-dir .tools/v2-manifest/20261018-freeze

.\.venv\Scripts\python.exe manage.py refreeze_recommendation_discovery --confirm-sitewide-readonly --consumed-cohort .tools/v2-unused/20261018-freeze/cohort.lock.json --consumed-result .tools/v2-unused/20261018-evaluation/evaluation.json --test-report .tools/v2-manifest/20261018-tests/tests.json --cohort-lock .tools/v2-manifest/20261018-freeze/cohort.lock.json --output-dir .tools/v2-manifest/20261018-comparison
```

3. 独立首读/复用性能诊断（不计算质量）可使用：

```powershell
.\.venv\Scripts\python.exe manage.py profile_recommendation_discovery --optimized-public-read --confirm-sitewide-readonly --prior-site-lock .tools/v2d3/20261018-expanded-freeze/cohort.lock.json --output .tools/v2-manifest/20261018-profile.json
```

输出目录/文件必须全新。所有 `.tools` 凭据、签名输入、报告保持本地忽略，不提交。停止命令即可停止实验，无数据库迁移或线上缓存删除需要。
