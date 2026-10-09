# Incremental Serv — 后端 API

`incremental.icu` 健身数据聚合平台的**后端服务**（仓库 `blunt-serv`）。

它负责把用户绑定的多个运动平台账号（Garmin / COROS / Suunto）的数据统一拉取、存储、并在平台之间**单向同步**，同时对外提供心率、睡眠、训练指标等分析接口，以及供 AI 客户端（ChatGPT GPT Actions / MCP）接入的 OAuth 2.1 授权服务。

> 前端仓库：`github.com/inrenping/incremental.icu`（Next.js，部署在 Vercel，通过 `vercel.json` 的 rewrite 把 `/api/*` 转发到本服务）。

---

## 架构概览

```
                         ┌─────────────────────────────┐
   浏览器 / Next.js  ───► │  Vercel (i.incremental.icu)  │
   (Clerk 登录态)         │  /api/*  → rewrite 到后端     │
                         └──────────────┬──────────────┘
                                        │ HTTPS
                                        ▼
                         ┌─────────────────────────────┐
                         │   Incremental Serv (本仓库)   │
                         │   FastAPI · uvicorn · 8000   │
                         │   systemd: incremental-serve │
                         └───┬───────┬───────┬──────┬───┘
              ┌──────────────┘       │       └──────────────┐
              ▼                      ▼                      ▼
        ┌──────────┐          ┌──────────┐          ┌──────────────┐
        │ Garmin   │          │  COROS   │          │  Suunto       │
        │(CN/intl) │          │ Team API │          │(SportsTracker)│
        └──────────┘          └──────────┘          └──────────────┘
              │                      │                      │
              └──────────┬───────────┴───────────┬──────────┘
                         ▼                       ▼
                  ┌──────────────┐        ┌──────────────────┐
                  │ PostgreSQL   │        │ Supabase Storage │ (FIT 缓存)
                  └──────────────┘        └──────────────────┘
```

- **鉴权**：用户态走 **Clerk**（前端校验 JWT，后端用 JWKS 验签，失败回退 HS256 自签 JWT）。
- **定时同步**：由 GitHub Actions 工作流驱动，用 `X-Sync-Token` 共享密钥鉴权（见环境配置）。
- **AI 接入**：内置 OAuth 2.1 授权服务器（`/oauth/*` + `/.well-known/*`），支持 RFC 7591 动态客户端注册，供 ChatGPT / MCP 以 `read` scope 访问数据。

---

## 技术栈

| 层 | 选型 |
|---|---|
| Web 框架 | FastAPI `0.135` + Uvicorn |
| ORM / 数据库 | SQLAlchemy `2.0` + PostgreSQL |
| 后台调度 | APScheduler（进程内，随 lifespan 启停） |
| 用户鉴权 | Clerk（JWKS 验签）+ 兼容旧 HS256 JWT |
| 第三方鉴权 | OAuth 2.1 + PKCE（授权码流程，用于 AI 接入） |
| 邮件 | Resend（邮箱验证码） |
| FIT 解析 | `garmin-fit-sdk` / `fitparse` |
| Garmin SDK | `garth 0.8` |
| 对象存储 | Supabase Storage（S3 兼容）/ 阿里云 OSS / AWS S3 |
| 部署 | GitHub Actions → SSH → 远程服务器 systemd |

---

## 核心功能

- **多平台账号连接**：用户登录 Garmin（国内 `connectapi.garmin.cn` / 国际 `garmin.com`）、COROS、Suunto，凭证加密后存入 `t_base_connect`。
- **运动记录同步（核心）**：把源平台最新 N 条运动中「目标平台没有」的条目，下载 FIT 后上传到目标平台。
  - 旧链路：`POST /base/pullFullActivities` + `POST /base/uploadActivity2Target/{id}/{target}`（两步入库，activities/files/compare 页面仍在使用）。
  - 新链路（Quick Sync）：`POST /base/execute2` —— 纯内存 diff，对活动表零读写，并落 `t_sync_run` / `t_sync_run_item` 批次日志。
- **健康数据分析**：
  - 心率：日级 + 明细（`/garmin/getDailyHeartRate*`）。
  - 睡眠：日级 + 月报环形图（`/garmin/getDailySleep`、`/garmin/getMonthlySleep`，佳明口径 `calendar_date`=起床日）。
  - 训练指标：体能年龄、个人纪录、竞赛预测、训练状态（`/garmin/getFitnessMetrics*`）。
  - 跑量统计：年/月累计、目标完成度（`/base/getRunningTotal`）。
- **定时同步任务**：用户可建「源 → 目标」同步任务，指定每日触发小时，由 GitHub Actions 定时触发。
- **OAuth 资源服务器**：暴露受保护资源给 AI 客户端（GPT Actions / MCP），支持动态客户端注册与令牌续期。
- **FIT 文件缓存**：把 FIT 批量备份到 Supabase Storage（`/base/batchUploadFitToStorage`）。

---

## 目录结构

```
blunt-serv/
├── app/
│   ├── main.py                  # FastAPI 入口（lifespan 启停调度器）
│   ├── api/v1/endpoints/        # 路由层（auth/user/garmin/coros/base/
│   │                            #   main/task/google/supabase/oauth/webhook…）
│   ├── core/                    # 配置(config) / 安全(security: Clerk+JWT) / 调度器
│   ├── db/                      # SQLAlchemy session
│   ├── models/                  # 数据模型（含 t_base_connect/t_base_activity/心率/睡眠/任务…）
│   ├── services/                # 业务逻辑层
│   │   ├── garmin_service.py    # 佳明接入 + 心率/睡眠/指标同步
│   │   ├── coros_service.py     # 高驰接入
│   │   ├── coros_upload.py      # 高驰 OSS STS v2 上传通道
│   │   ├── suunto_service.py    # 颂拓(SportsTracker) 私有 API 接入
│   │   ├── suunto_sml.py        # FIT → legacy SML XML 生成
│   │   ├── base_connect_service.py / base_activity_service.py
│   │   ├── quick_sync_service.py# 一键同步(纯内存 diff)
│   │   ├── platform_session.py  # 各平台会话管理
│   │   └── oss/                 # Supabase / Ali / AWS 存储客户端
│   └── utils/                   # 加解密、日志、运动类型配置
├── scripts/                     # 离线探针（suunto/coros/garmin 排查用，可独立运行）
├── migrations/                  # 手工 DDL（项目无 alembic）
├── docs/                        # 架构决策(OPEN-DECISIONS.md) / 规格(spec/)
├── tests/                       # pytest
├── .github/workflows/           # CI 测试 + SSH 部署 + 多定时同步工作流
├── __init__.sql                 # 建表初始化脚本
└── requirements.txt
```

---

## 数据模型要点

- **`t_users`**：用户（邮箱/手机号，`user_email` 为唯一标识字段，**没有 `username` 列**）。
- **`t_base_connect`**：第三方账号连接（平台类型 `source_type`、区域 `region`、加密 `access_token`、`master` 标记、各平台专有字段）。
- **`t_base_activity`**：拉取入库的运动记录（按 `master` 账号聚合）。
- **心率**：`t_heart_rate_daily`（含 `user_id`）→ `t_heart_rate_detail`（无 `user_id`，经 `daily_id` 关联）。
- **睡眠**：`t_sleep_daily`（含 `user_id`，`calendar_date`=起床日）→ `t_sleep_detail`（阶段片段，`activity_level`：0 深睡/1 浅睡/2 REM/3 清醒）。
- **同步任务**：`t_task`（每用户最多 10 个）/ `t_task_item`（每任务 1 条「源→目标」）/ `t_task_result`。
- **批次日志**：`t_sync_run` / `t_sync_run_item`（Quick Sync 执行记录）。

---

## 鉴权说明

服务同时存在三套凭证体系：

| 体系 | 用途 | 有效期 |
|---|---|---|
| 用户 JWT（`Authorization: Bearer`） | 前端调用 API | Access 60 min / Refresh 7 天 |
| OAuth 2.1 授权码（AI 接入） | ChatGPT / MCP | 授权码 10 min / Access 365 天 / Refresh 400 天 |
| `X-Sync-Token` | GitHub Actions 定时任务 | 长期随机串（环境变量 `CRON_SYNC_TOKEN`） |

后端 `get_current_user` 优先校验 Clerk JWT（JWKS），失败回退 HS256 自签。不带 `Authorization` 头返回 **401**（不是 403），无效 token 返回 `无效的认证凭据`。

---

## 平台接入现状

| 平台 | 接入方式 | 状态 |
|---|---|---|
| **Garmin** | 官方 `garth` SDK + OAuth1/2 | 完整：拉取、FIT 下载、心率/睡眠/指标同步、上传到别家 |
| **COROS** | 私有 Team API + OSS STS v2 上传通道（逆向实现） | 完整：登录、下载、FIT 上传 |
| **Suunto / Sports Tracker** | 私有 API（逆向自官方 APK，TOTP 签名，与开源 `suuntool` 一致） | **上传链路攻坚中**：服务端仅接受 legacy SML XML，`suunto_sml.py` 负责 FIT→SML 生成，当前在调通 `POST /v1/workout` 的载荷格式 |

> ⚠️ COROS 与 Suunto 均依赖非官方接口，可能随时无通知变更，且涉及各平台服务条款。属于已知技术风险，已被接受。

---

## 定时同步任务规则

- 每用户最多 **10 个任务**（`MAX_TASKS_PER_USER`）。
- 每个任务只允许 **1 条**「源 → 目标」同步对（`MAX_TASK_ITEMS=1`），不同任务的「源→目标」不可重复。
- 每个任务每日最多触发 **3 个时间点**（`MAX_TASK_HOURS=3`，即每天最多执行 3 次）。
- 端点：`POST /task`（创建/修改）、`POST /task/cron-execute`（GitHub Actions 调用）。

---

## 配置（环境变量）

复制 `.env.example` 为 `.env` 并填写。生产由 `deploy.yml` 用 GitHub Secrets 注入覆盖。

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接串（**必填**，缺失即启动报错） |
| `SECRET_KEY` | HS256 JWT 签名密钥 |
| `CLERK_SECRET_KEY` / `CLERK_ISSUER` / `CLERK_WEBHOOK_SECRET` | Clerk 鉴权 |
| `RESEND_API_KEY` / `RESEND_EMAIL_FROM` | 邮箱验证码邮件 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google 登录 |
| `GOOGLE_SERVICE_ACCOUNT_B64` | Google 服务账号 JSON（Base64），用于服务端调 Calendar 等 |
| `GIT_HUB_CLIENT_ID` / `GIT_HUB_CLIENT_SECRET` | GitHub 登录（变量名避开 `GITHUB_` 前缀） |
| `SUPABASE_STORAGE_*` | Supabase Storage（S3 兼容）FIT 缓存 |
| `CRON_SYNC_TOKEN` / `CRON_SYNC_USER_EMAIL` | 定时任务共享密钥与同步账号 |
| `MCP_RESOURCE_URI` | OAuth 资源标识（需与 `incremental-mcp` 侧一致） |
| `CANONICAL_ORIGIN` | 站点 canonical 域名 |

---

## 本地开发

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # 填写 DATABASE_URL 等
uvicorn app.main:app --reload
```

- 开发环境自动开启 `/docs`（Swagger）；生产环境（`APP_ENV=production`）关闭 `docs_url`/`openapi_url`。
- 健康检查：`GET /api/v1/base/health`、`GET /`（返回 `{"status":"online"}`）。
- Python 版本：依赖 `garth` 不支持 3.14+，建议 **3.11 / 3.12**（可用 `uv` 管理）。

---

## 数据库

- 项目**不使用 alembic**，DDL 维护在 `__init__.sql` 与 `migrations/` 下的手工 SQL。
- 首次初始化：`psql "$DATABASE_URL" -f __init__.sql`
- 增量迁移（按日期命名，依次执行）：心率/睡眠/任务/训练指标等表结构变更。

---

## 部署

通过 **GitHub Actions** 自动部署：推送到 `master` 分支即触发 `deploy.yml`（先跑 pytest 矩阵 3.11/3.12，再 SSH 到远程服务器）。

部署脚本会：拉取 `master` → 用 Secrets 重写 `.env` → `pip install -r requirements.txt` → `systemctl restart incremental-serve.service`。

手动在服务器上跑（首次或调试）：

```bash
# 1. systemd 单元 /etc/systemd/system/incremental-serve.service
[Unit]
Description=incremental-serve deploy
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/incremental-serve
Environment="PYTHONPATH=/var/www/incremental-serve"
ExecStart=/var/www/incremental-serve/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

# 2. 启用
sudo systemctl daemon-reload
sudo systemctl enable --now incremental-serve.service
```

查看日志：

```bash
sudo journalctl -u incremental-serve.service -f
```

> 前端经 Vercel rewrite 转发到本服务；生产 `DATABASE_URL` 由 GitHub Secret 注入，本地 `.env` 不参与生产。

---

## 定时工作流（`.github/workflows/`）

| 文件 | 作用 | 频率 |
|---|---|---|
| `deploy.yml` | 测试 + 部署 | push 到 `master` |
| `sync_daily_heart_rate.yml` | 每日同步心率 | 定时 |
| `sync_daily_sleep.yml` | 每日同步睡眠 | 定时 |
| `sync_fitness_metrics.yml` | 同步训练指标 | 定时 |
| `sync_main_activity.yml` | 同步主运动记录 | 定时 |
| `refresh_garmin_count.yml` | 刷新佳明活动数 | 定时 |
| `backup_fit_to_storage.yml` | FIT 备份到 Storage | 定时 |

所有定时工作流均带 `X-Sync-Token` 头；能否跑通取决于仓库 Secrets 是否配置了 `CRON_SYNC_TOKEN` / `CRON_SYNC_USER_EMAIL`。

---

## 已知限制 / 注意事项

- **上传链路**：Suunto 上传仍依赖 legacy SML 生成，正在调通；COROS 上传依赖私有 OSS 通道，接口可能变更。
- **接口口径坑**（开发时注意）：
  - `getActivitiesByPage` 的 `end_date` 有 off-by-one，需传 `YYYY-MM-DD 23:59:59`。
  - `getRunningTotal` 仅认 `master` 账号，无 master 时静默返回 0。
  - 跑步类型口径：`indoor_running` 不在 `ACTIVITY_CONFIG` 中，过滤会少算。
- **用户隔离**：所有 `/api/v1/*` 端点通过 `get_current_user` 取用户，**禁止**硬编码 `get_user_by_username(db,"inrenping")`；读接口允许未登录回退到默认账号（`get_current_user_optional`），**写接口一律强制登录**。
- 调度器为进程内 APScheduler，单实例部署；多实例需注意重复触发。

---

## 相关文档

- `docs/decisions/OPEN-DECISIONS.md`：悬而未决问题登记册。
- `docs/spec/quick-sync-v1.md`：一键同步功能规格（已确认）。
- `AGENTS.md`：给 AI 编码助手的协作约定。

---

## License

见 `LICENSE`（项目采用其声明之开源协议）。
