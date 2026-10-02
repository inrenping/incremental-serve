-- 修复：t_task 旧字段仍为 NOT NULL，导致（未写入旧字段时）新建任务报 500
-- 旧字段（connect_source_id / connect_target_id / hour）已废弃，新逻辑只写 hours + t_task_item，
-- 因此这里把它们的 NOT NULL 约束去掉；重复执行无副作用。
-- 生产库手动执行：psql "$DATABASE_URL" -f migrations/20261002_task_legacy_cols_nullable.sql

BEGIN;

ALTER TABLE public.t_task ALTER COLUMN connect_source_id DROP NOT NULL;
ALTER TABLE public.t_task ALTER COLUMN connect_target_id DROP NOT NULL;
ALTER TABLE public.t_task ALTER COLUMN hour DROP NOT NULL;

COMMIT;
