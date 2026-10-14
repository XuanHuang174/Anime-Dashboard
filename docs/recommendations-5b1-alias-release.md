# 5B-1：11 项同义映射批准发布

## 授权范围与版本

用户在审阅 5B-1 清单后明确要求：“先启动 11 项的同义映射，然后再对新增的题材确认”。本次只启用下表 11 项；初次草案中的其他映射及 12 项新题材都未启用，不包含 5B-2 排序改造。

| 原标签（英文大小写归一） | 规范题材 |
| --- | --- |
| 治愈系 | 治愈 |
| 治癒 | 治愈 |
| Comedy | 喜剧 |
| 日常系 | 日常 |
| 戀愛 | 恋爱 |
| MUSIC | 音乐 |
| 戰鬥 | 战斗 |
| 旅行系 | 旅行 |
| 校園 | 校园 |
| 百合向 | 百合 |
| 職場 | 职场 |

- 批准发布标识：`aliases.2026-10-14.approved-11`。
- 题材规则：`genres.v2` → `genres.v3`；既有 26 个规范题材不变。
- 提取规则：`journal-genres.v1` → `journal-genres.v2`，明确标识特征含义变化。
- 公共特征：`genres-idf.v2` → `genres-idf.v3`。
- 公共缓存使用独立 `recommendations.public-features.v3`；签名包含目录版本、题材版本、特征版本及提取规则版本。旧键自然过期，不全局清缓存。
- 排序算法仍为 `content.v1`：公式、系数、400 候选池、120 结果上限、同分顺序和多样性逻辑不变。

## 行为与数据保护

同一规范表用于公共候选和高分手账特征，避免两侧对同一标签理解不一致。规范化只发生在请求/统计的派生副本中：同作品的 `MUSIC、音乐、music` 只计一个题材，仍可保留基础/自定义标签的证据来源。

不改原目录标签、私人基础/自定义标签、评分、长评、手账状态、身份或用户明确偏好。私人基础标签主动保存为空时，不回退读取目录或共享标签。无有效题材仍不进候选池。成人与未知分级继续在后端过滤，不从新标签推断作品身份、系列或内容分级。

仅支持精确归一，不识别 `不喜欢MUSIC` 等子串；原标签或映射后的规范标签等于作品自身名称时继续排除。题材偏好写接口仍只接受规范名称，不把英文/繁体别名写回偏好表。

映射可使部分已关联、高分作品获得有效题材，从而改变派生样本数、画像和推荐顺序；这属于特征修正，不是排序权重调整，也不会覆盖手动偏好。未获批的草案（包括未在目录出现的文字变体）继续不生效。

## 分页、部署与回归

推荐分页 HMAC 显式包含题材版本，并保留特征/提取规则版本。旧快照的第二页请求返回现有 JSON 409，由现有页面重新加载第一页，避免新旧分页混用。新版本第一、第二页仍正常连续翻页。

部署新后端进程后规则随代码生效，无需重新导入、数据库迁移或扫描全站私人手账。画像请求按当前用户现有数据重新提取，不批量更新私人记录。代码验证和当前真实目录审计不等于已完成线上部署后验收。

```powershell
.venv/Scripts/python.exe manage.py test recommendations.tests.test_alias_release recommendations.tests.test_tag_coverage recommendations.tests.test_quality_baseline recommendations.tests.test_engine_api recommendations.tests.test_extraction_api --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py test anime recommendations --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py check --settings=config.recommendation_test_settings
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.recommendation_test_settings
```

以上测试使用隔离 SQLite，禁止在默认 Neon 正式库运行测试。测试覆盖：准确的 11 项批准集、未批准规则仍排除、大小写/NFKC、同作品去重和来源、空标签保护、原评分/长评/偏好不变、用户隔离、安全过滤、旧缓存拒用、版本变化后旧分页失效，以及没有特征变化时 V1 冻结排序不变。

## 实际覆盖验收

沿用只读命令，对 `.env` 配置的公共目录重新运行，并比较初次报告：

```powershell
.venv/Scripts/python.exe manage.py audit_recommendation_tags --compare-before-dir .tools/recommendation-tags/5b1-review-20261014-final --output-dir .tools/recommendation-tags/5b1-approved-11-final
```

PostgreSQL 强制只读一致性快照；命令不访问私人手账、不调用 HTTP/外部模型、不写缓存或原数据。旧报告和当前目录指纹必须一致，才输出 `approved_release_comparison` 的实际覆盖差异；不一致即停止并要求重新建立可比基线，不把底层数据变化当作规则收益。

输出仍为本地忽略目录中的 `report.md`、`coverage.json`、待审核清单和完整未识别统计，最后由 `manifest.json` 标记完成。初次快照的预期是新增覆盖 16 部（11,900 → 11,916），实际结果以本次核验报告为准。它不是推荐准确率/用户喜欢概率，也不是正式数据库记录被修改的数量。

下一步：单独讨论 12 项新题材的定义与批准范围，不能把本次 11 项授权扩大成新增题材授权。
