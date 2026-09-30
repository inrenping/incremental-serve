-- 迁移：为 t_sync_run 增加 task_id 和 trigger_mode 字段，用于区分定时执行 / 手动执行
-- 执行时间点：代码发布前或发布后均可（ALTER 是 ONLINE 安全操作，大表加 nullable 列很快）
-- 执行方式：直接在 Neon / PostgreSQL 数据库里跑下面的语句即可

BEGIN;

-- 1. 加字段
ALTER TABLE public.t_sync_run
    ADD COLUMN IF NOT EXISTS task_id integer NULL;

ALTER TABLE public.t_sync_run
    ADD COLUMN IF NOT EXISTS trigger_mode varchar(32) NOT NULL DEFAULT 'manual';

-- 2. 加外键约束（task_id 指向 t_task.id，用户删除任务时历史 run 保留，task_id 置空）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 't_sync_run_task_fkey'
    ) THEN
        ALTER TABLE public.t_sync_run
            ADD CONSTRAINT t_sync_run_task_fkey
            FOREIGN KEY (task_id) REFERENCES public.t_task (id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- 3. 加索引（按任务 ID 查同步历史、过滤 trigger_mode 都常用）
CREATE INDEX IF NOT EXISTS idx_t_sync_run_task_id
    ON public.t_sync_run USING btree (task_id);

CREATE INDEX IF NOT EXISTS idx_t_sync_run_trigger_mode
    ON public.t_sync_run USING btree (trigger_mode);

-- 4. 注释
COMMENT ON COLUMN public.t_sync_run.task_id IS '关联的任务 ID（定时任务触发时非空，手动触发时为空，关联 t_task 表）';
COMMENT ON COLUMN public.t_sync_run.trigger_mode IS '触发方式：manual=用户手动点击，scheduled=定时任务自动执行';

COMMIT;
