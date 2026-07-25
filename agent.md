# 项目 Agent 协作指南

## 项目概述

这是一个用于记录与汇总番剧的个人手账网站，采用 Django 提供后端、数据管理与管理后台，React 提供用户前台。

- 后端：Python 3.13、Django 6.0.7
- 管理后台：django-simpleui 2026.1.13
- 前端：React 19.2.7、Vite 6.4.3
- 开发数据库：SQLite
- 默认语言：简体中文（`zh-hans`）
- 默认时区：`Asia/Shanghai`

## 目录职责

```text
.
├── anime/                 # 番剧领域 Django 应用
│   ├── models.py          # 数据模型
│   ├── views.py           # 后端视图与未来 API
│   ├── admin.py           # Django Admin 注册与配置
│   └── migrations/        # 数据库迁移
├── config/                # Django 项目配置
│   ├── settings.py        # 应用、模板、静态资源和本地化配置
│   └── urls.py            # 根路由、管理后台和前台入口
├── frontend/              # React 前端工程
│   ├── src/               # React 源代码与样式
│   ├── dist/              # Vite 构建产物，不直接编辑
│   ├── package.json       # 前端依赖与脚本
│   └── vite.config.js     # Vite 构建、静态路径和 API 代理配置
├── manage.py              # Django 管理入口
└── requirements.txt       # Python 依赖版本
```

## 环境与常用命令

项目使用 PowerShell，Python 虚拟环境位于 `.venv`。如果 PowerShell 禁止执行激活脚本，只对当前终端临时放宽策略：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1
```

不依赖激活状态时，优先直接调用项目解释器：

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

前端命令需要在 `frontend` 目录运行：

```powershell
cd frontend
npm install
npm run dev
npm run build
```

- Django 与构建后的 React 前台：`http://127.0.0.1:8000/`
- Django 管理后台：`http://127.0.0.1:8000/admin/`
- Vite 热更新开发服务器：`http://127.0.0.1:5173/`

## 前后端接入约定

1. React 是用户前台，Django Admin 是内容管理后台，不要用 Django 模板重新实现用户前台。
2. Vite 的 `base` 固定为 `/static/frontend/`。Django 通过 `STATICFILES_DIRS` 提供 `frontend/dist` 内的构建资源。
3. Django 根路径渲染 `frontend/dist/index.html`，`/admin/` 必须继续指向 Django Admin。
4. 修改 `frontend/src` 后必须执行 `npm run build`，否则 8000 端口仍会显示旧构建。
5. Vite 开发服务器将 `/api` 代理至 `http://127.0.0.1:8000`。新增前后端接口时统一使用 `/api/` 前缀。
6. 不要直接编辑 `frontend/dist` 或 `frontend/node_modules`。

## Django 开发约定

1. 番剧业务代码放在 `anime` 应用中；新增独立业务域时再创建新应用。
2. 修改模型后依次运行：

   ```powershell
   .\.venv\Scripts\python.exe manage.py makemigrations
   .\.venv\Scripts\python.exe manage.py migrate
   ```

3. 需要后台维护的模型应在 `anime/admin.py` 注册，并兼容 Simple UI。
4. `simpleui` 必须保持在 `INSTALLED_APPS` 的最前面，且位于 `django.contrib.admin` 之前。
5. 用户可见文本优先使用中文；代码标识符、模块名和 API 字段使用清晰的英文命名。
6. 数据库查询注意避免 N+1；列表接口按需使用 `select_related`、`prefetch_related` 和分页。

## 修改后的最低验证要求

根据改动范围执行以下检查：

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
cd frontend
npm run build
```

涉及页面或路由时，还应确认：

- `/` 返回 React 前台且静态资源加载成功；
- `/admin/` 返回中文 Simple UI 后台；
- 新增的 `/api/` 接口状态码和响应结构符合预期。

## 安全与仓库卫生

- 不要在代码、文档或提交记录中保存账号密码、令牌或生产密钥。
- 当前 Django `SECRET_KEY` 和 SQLite 配置仅适用于本地开发，部署前必须改用环境变量和正式数据库。
- 不要提交 `.venv/`、`.tools/`、`frontend/node_modules/`、`frontend/dist/`、`db.sqlite3` 或运行日志。
- 保留用户已有修改，不要覆盖与当前任务无关的文件。
- 禁止批量或递归删除文件、目录，不要使用 `del /s`、`rd /s`、`rmdir /s`、`Remove-Item -Recurse` 或 `rm -rf`。
- 如确需删除，只能一次删除一个经过确认的明确文件路径；需要批量删除时停止操作并请用户手动处理。

## 完成标准

改动应保持 Django 检查通过、前端构建成功、根路径和管理后台可访问，并同步更新必要的迁移、依赖清单与项目说明。不要把生成目录或本地数据当作源代码提交。
