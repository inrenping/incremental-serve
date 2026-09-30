# Spec - 一键同步（Quick Sync）v1.0

> 生成日期：2026-09-29
> 基于：用户已确认的口头方案（纯内存 diff / 一段式 / 单向 / 父子两表日志）
> 状态：已确认，开发中

---

## 1. 产品定义

- **一句话描述**：一键把源平台最新 N 条运动中「目标平台没有」的那些，直接搬运到目标平台，全程不写活动表。
- **目标用户**：incremental.icu 上绑定了多个运动平台账号的用户。
- **核心问题**：现有一键同步对数据库读写过多，第三方 API 请求数也过多。

## 2. MVP 范围（锁定）

| 优先级 | 功能 | 验收标准摘要 |
|--------|------|-------------|
| P0 | 纯内存拉取双端最新 N 条并 diff | 对 `t_base_activity` **零读写** |
| P0 | 差异项下载 FIT → 上传目标平台 | 单条失败不影响其他条 |
| P0 | 每次执行落一条批次记录 + K 条明细 | 批次含：用户、源账号、目标账号、条数、耗时、状态 |
| P0 | `GET /syncRuns` 批次列表 | 返回最近 N 次同步概览 |
| P0 | `GET /syncRuns/{run_id}/items` 明细 | 返回运动类型/开始时间/距离/源 ID→目标 ID/每条状态 |
| P0 | 修复 Garmin 凭证串号 | 每个 BaseConnect 独立 garth 会话 |
| P1 | 前端 `sync-runs.tsx` 展示 | 复用现有 sync-logs 卡片风格 |

## 3. 明确不做（Out-of-Scope）

| 不做的功能 | 原因 | 何时考虑 |
|------------|------|----------|
| dry_run / preview-confirm 两段式 | 用户明确要求一段式，不想多点一次 | 出现"我只想同步其中几条"的真实诉求时 |
| 双向同步 | 下载+上传请求翻倍，与效率优先冲突 | 有明确需求时加 direction 入参 |
| FIT 缓存到 Supabase | 常态路径纯内存；失败重试成本低于缓存运维成本 | 需要批量重放时 |
| 改造 `GARMIN_FIT_DIR` 本地落盘逻辑 | 磁盘泄漏隐患，但与本次目标无关 | 单独立项（已记入 OPEN-DECISIONS） |
| 收敛三套存储客户端 | 技术债，混入本次会失控 | 单独立项 |
| 废弃 `/pullFullActivities` 与 `/uploadActivity2Target` | 其他页面仍在使用 | 前端全面切换后 |

## 4. 技术架构（锁定）

| 层 | 技术 | 实际版本 | 锁定原因 |
|----|------|----------|----------|
| 后端 | FastAPI | 现有 | 项目既定 |
| ORM | SQLAlchemy | 现有 | 项目既定 |
| 数据库 | PostgreSQL | 现有 | 项目既定 |
| Garmin SDK | garth | 0.8.0 | 已安装；**已实测确认 `garth.Client` 可实例化**，具备 `configure/loads/connectapi/download/refresh_oauth2/dumps` |
| 迁移 | 手工 SQL | - | 项目无 alembic，DDL 维护在 `__init__.sql` |

## 5. API 端点清单（锁定）

| Method | Path | 功能 | 认证 | 请求体 | 响应体 |
|--------|------|------|------|--------|--------|
| POST | `/api/v1/base/execute2` | 一段式执行同步 | JWT | `{source_id, target_id, count=10}`（**契约不变**） | `{status, message, data:{source_count,target_count,diff_count,uploaded[],failed[],run_id}}` |
| GET | `/api/v1/base/syncRuns?limit=10` | 批次列表 | JWT | - | `{status, message, data:[...]}` |
| GET | `/api/v1/base/syncRuns/{run_id}/items` | 批次明细 | JWT | - | `{status, message, data:[...]}` |

**向后兼容约束**：`TaskRequest` 三个字段一个字不改；`execute2` 原有返回字段全部保留，只**新增** `data.run_id`。

## 6. 数据库表清单（锁定）

### t_sync_run（批次）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | bigserial PK | 批次主键 |
| user_id | int FK t_users | 用户 |
| source_connect_id | int FK t_base_connect | 源账号连接 |
| target_connect_id | int FK t_base_connect | 目标账号连接 |
| window_size | int | 每侧拉取条数 N |
| source_platform / target_platform | varchar(32) | 冗余平台标识（garmin/garmin_cn/coros） |
| source_account / target_account | varchar(255) | 冗余账号，便于展示"A→B" |
| fetched_source / fetched_target / diff_count | int | 拉取与差异统计 |
| uploaded_count / skipped_count / failed_count | int | 结果汇总 |
| status | varchar(32) | success / partial / failed / no_diff / cancelled / error |
| error_message | text | 批次级异常 |
| started_at / finished_at | timestamptz | 起止时间 |
| duration_ms | int | 耗时 |
| created_at | timestamptz | 记录创建时间 |

索引：`idx_t_sync_run_user_created (user_id, created_at DESC)`

### t_sync_run_item（明细）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | bigserial PK | |
| run_id | int FK t_sync_run | 所属批次 |
| activity_id | varchar(64) | 源平台活动 ID |
| activity_name | varchar(512) | 活动名 |
| sport_type_raw | varchar(64) | 运动类型原始值 |
| start_time_local | timestamptz | 运动开始时间（本地） |
| start_time_gmt | timestamptz | 运动开始时间（UTC） |
| distance_meters | numeric(12,2) | 距离（米） |
| filename | varchar(512) | 上传文件名 |
| status | varchar(32) | synced / duplicate / failed |
| message | text | 失败原因或平台返回摘要 |
| target_activity_id | varchar(64) | 目标平台回执 ID（有则填） |
| synced_at | timestamptz | 本条同步完成时间 |

索引：`idx_t_sync_run_item_run (run_id)`

## 7. 页面清单（锁定）

| 页面 | 路由 | 组件 | 对应 API |
|------|------|------|----------|
| 仪表盘 | `/dash` | `sync-runs.tsx`（新增） | syncRuns + syncRuns/items |

## 8. 设计 Token（前端）

沿用 incremental 现有 Card + Badge 样式；图标统一使用项目已锁定的 `@tabler/icons-react`。**禁止 emoji 作功能图标**。

## 9. 验收标准（EARS）

| 编号 | 功能 | 验收标准 | 优先级 |
|------|------|----------|--------|
| AC-01 | diff | While 双端均成功拉取，系统**必须**按「开始时间差 ≤ 300 秒 且 距离差 ≤ 5%」判定重复 | P0 |
| AC-02 | 无差异 | If diff_count = 0，系统**必须**返回 status=success 且上传/下载请求数为 0 | P0 |
| AC-03 | 不入相册 | When 执行一条同步，系统**必须**对 `t_base_activity` 零读写 | P0 |
| AC-04 | 独立会话 | When source 与 target 都是 Garmin 且区域不同，系统**必须**各自持有独立 garth 凭证 | P0 |
| AC-05 | 幂等 | If 目标平台返回 DUPLICATE_ACTIVITY，该条**必须**标记为 duplicate 而非 failed | P0 |
| AC-06 | 日志 | When 批次结束，系统**必须**写 1 条 run 记录 + K 条 item 记录 | P0 |
| AC-07 | 隔离失败 | If 某条下载或上传抛异常，系统**必须**记录该条失败并继续处理剩余条目 | P0 |
| AC-08 | 契约 | 修改后 `execute2` 的旧字段**必须**全部保留 | P0 |

## 10. 边界与约束

- 每侧拉取条数 `count` 默认 10，上限 50。
- 下载上传并发：**3**（低于现状 5，降低风控概率）。
- 第三方请求预算：正常态 = 2 次列表 + K×(下载+上传) 次，K = diff 条数。
- token 恢复策略：**乐观调用**，401/鉴权失败才刷新，禁止每次先 test。
- Garmin 会话刷新后**必须**将新 secret_string 回写 `t_base_connect`。

## 11. 已知坑

| 坑 | 技术栈指纹 | 根因 | 修法 |
|----|------------|------|------|
| garth 全局单例串号 | garth-0.8.0 | 全程共用模块级 `garth.client` | 每个 BaseConnect 独立 `garth.Client()` |
| Coros ZIP 落盘不删除 | GARMIN_FIT_DIR | `_upload_fit_zip_to_coros` 写了临时文件无清理 | **本次不修**，记未决项 |
| perform_relogin 先 test 后做 | garmin/coros | test 本身是一次真实第三方请求 | 改为乐观调用 + 惰性恢复 |

## 12. 端到端验证步骤

```bash
# 1. 迁移
psql "$DATABASE_URL" -f migrations/20260929_quick_sync.sql

# 2. 启动
python -m uvicorn app.main:app --reload   # 等待 Application startup complete

# 3. 核心成功流（返回 diff_count >= 1 且有 uploaded 明细）
curl -X POST http://localhost:8000/api/v1/base/execute2 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source_id":1,"target_id":2,"count":10}'
# 断言：status=success，data 中含 run_id

# 4. 日志回查
curl "http://localhost:8000/api/v1/base/syncRuns?limit=5" -H "Authorization: Bearer $TOKEN"
curl "http://localhost:8000/api/v1/base/syncRuns/1/items" -H "Authorization: Bearer $TOKEN"
# 断言：run 的 diff_count == items 条数（failed 也计入）

# 5. 无差异幂等流：重复调用步骤 3
# 断言：diff_count = 0 或对端返回 DUPLICATE_ACTIVITY → status=duplicate
```

## 13. 端到端验证步骤

```bash
# 1. 迁移（生产库 / Supabase 手动执行）
psql "$DATABASE_URL" -f migrations/20260929_quick_sync.sql

# 2. 启动
python -m uvicorn app.main:app --reload   # 等待 Application startup complete

# 3. 核心成功流（返回 diff_count >= 1 且有 uploaded 明细）
curl -X POST http://localhost:8000/api/v1/base/execute2 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source_id":1,"target_id":2,"count":10}'
# 断言：status=success，data 中含 run_id；活动表 t_base_activity 零新增行

# 4. 日志回查
curl "http://localhost:8000/api/v1/base/syncRuns?limit=5" -H "Authorization: Bearer $TOKEN"
curl "http://localhost:8000/api/v1/base/syncRuns/1/items" -H "Authorization: Bearer $TOKEN"
# 断言：run 的 diff_count == items 条数（failed 也计入明细）

# 5. 无差异幂等流：重复调用步骤 3
# 断言：diff_count = 0 或对端返回 DUPLICATE_ACTIVITY → 明细 status=duplicate

# 6. Garmin↔Garmin 串号验证（推演结论，需真实账号）
# 用 CN 区源 + 国际区目标各一次，确认 diff 不再恒为 0
```

## 14. 变更记录

| 日期 | 变更内容 | 原因 | 影响范围 |
|------|----------|------|----------|
| 2026-09-29 | 初版 | 用户要求降低 DB 读写与第三方请求数 | 新增 5 个后端文件 + 2 张表 + 1 个前端组件 |
