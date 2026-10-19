# 推荐与私人手账细节调整（2026-10-20）

## 变更

- “浏览说明”保留原生 details。桌面固定入口并预留空间，手机使用稳定的独立行；展开内容在正常文档流内，不覆盖卡片。加减号宽度固定，开关位置不变，也不遮挡“恢复默认”。
- 类别改名“电波契合”，说明同步更新；接口 ID 仍是 `familiar`，不改分类、排序或过滤规则。
- 页面及加载壳统一为“我的手账 / 个性化推荐”“番剧探索”；页脚为“我的手账 / 番剧探索”。
- 共用资源抽屉移除搜索框额外黑色 outline，保留聚焦背景及键盘可见的紫色原边框；不改搜索、取消、复制或 Magnet 行为。
- 资源提示按用户提供的完整文本替换，本次仅落实页面文案，不提供法律有效性判断。
- 私人手账推荐入口更新“发现下一部宝藏番剧，翻开番剧探索手账”，箭头放入独立方框避免重复。复用首页 `cta-spark-icon` 魔法棒动画、`memphis-pressable` 悬停反馈，箭头使用同款描边/硬阴影及位移反馈，支持减少动效和窄屏。
- “待定”统计：想看（`plan`）或搁置（`on_hold`）或放送字段含“待定/未定档”的并集，每条记录只计一次。不将未评分当待定，不改变评分与观看状态。前端本地计算与后端全量统计同步；共用公开手账统计也采用一致口径，不扩展任何数据访问权限。

## 验证

全部测试使用合成数据，不连接生产 Neon、不操作真实用户资料。

- 201 项前端测试通过（新增文案、组件及搁置去重合同）。
- 本机独立 PostgreSQL 上 `anime.tests.AnimeApiTests` 58 项通过；Django 检查和无迁移检查通过。
- R1–R3 浏览器回归 36 项通过，包含说明入口在 1440/1024/768/390/320px 下同坐标开关，以及资源搜索框焦点检查。
- 推荐 Banner 浏览器 4 项通过：共享图标动画、悬停和方框箭头、500/390/320px 无溢出、键盘及减少动效。
- 加载链路浏览器 10 项通过；生产构建通过。已有主包 >500 kB 警告不在本次处理范围。

命令：

```powershell
# PostgreSQL 按已有脚本准备，并使用专用 RECOMMENDATION_TEST_DATABASE_URL。
.venv/Scripts/python.exe manage.py test anime.tests.AnimeApiTests --settings=config.recommendation_pg_test_settings --noinput --keepdb
cd frontend
node --test --test-reporter=dot tests
node scripts/test-recommendation-motion.mjs
node scripts/test-journal-recommendation-banner.mjs
node scripts/test-recommendation-loading.mjs
node node_modules/vite/bin/vite.js build
```

浏览器使用本机 Edge；必要时设置 `PLAYWRIGHT_MODULE`。报告/截图位于忽略目录 `.tools/acceptance-r3-browser/`、`.tools/acceptance-journal-banner/`、`.tools/acceptance-loading-browser/`，不进入提交。

## 部署

需要部署后端统计代码与前端资源，无数据库迁移，不开启 V2。尚未做本轮生产部署验收；上线后只读核查文案、说明开关、Banner 动效及统计即可。回退使用正常 revert 和重新部署，不清空缓存或删除用户数据。
