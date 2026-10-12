# 严格关联任务实时监控（仅本机）

这是独立的运维监控工具，不修改推荐页面，不提供新的公开 API，也不重启关联任务。

## 启动

先确认任务的私有报告目录和批次 UUID，再在项目根目录执行：

```powershell
.venv/Scripts/python.exe -m recommendations.progress_monitor --run-dir .tools/<本次任务目录> --batch-id <本次批次UUID> --port 8769
```

打开 `http://127.0.0.1:8769/`。关闭前台监控进程可以按 Ctrl+C；不会停止独立运行的关联任务。

读取正常 Django 配置中的正式库连接，但数据库查询事务强制只读。只监听 `127.0.0.1`，不允许外部 Host/Origin，不设置跨域许可，不输出凭据、用户身份、片名、评分、长评或记录明细。

## 两种进度不可混用

- **本轮扫描**：解析当前续跑的批量日志；每次重试从头扫描，会重新检查并跳过既有身份关联。数字重置不代表丢失已提交数据。
- **累计成功关联**：查询同一批次 UUID 的 `linked` 审计事件数，跨续跑累计。事务提交后才可见；前面的重复扫描期间可能保持不变。
- **候选数量**：来自执行前的严格匹配审计，不包括需要用户确认及无安全候选的记录。正式库若有用户并行修改，最终验收应解释与预审计的差异。

页面每 3 秒读取日志状态，数据库每 30 秒采样一次（`--db-interval` 允许 15–300 秒）。显示页面刷新、扫描日志、成功关联事件和数据库成功采样的独立时间戳。数据库采样失败保留最后可信值并提示异常；五分钟无扫描/提交显示排查提醒，不直接认定进程死亡。

`status.json` 原先仅在阶段切换时更新，不能把它的修改时间当作任务心跳。监控结合日志和数据库，而非只显示该文件。

`apply.json` 完整生成仅代表关联执行结束；只有最终状态明确为 `complete` 才显示验收通过。`needs_attention` 表示仍需检查。此监控不会自动撤销、修改或补写任何数据。

私有目录中的 `live-progress.json` 是最近一次只读采样摘要，不能作为离线实时数据使用。报告和运行日志应始终放在忽略的 `.tools/`，不提交 Git。

## 测试

```powershell
.venv/Scripts/python.exe -m unittest recommendations.tests.test_progress_monitor -v
.venv/Scripts/python.exe manage.py test recommendations --settings=config.recommendation_test_settings
```

第一项不连接数据库；第二项使用隔离的本地测试配置，绝不用于迁移正式库。测试生成的私有样例保留在 `.tools/progress-monitor-tests/`，不执行批量删除。
