-- 任务模型升级：一任务 × 多同步对 × 多触发小时（2026-10-02）
-- 1) t_task 增加 hours jsonb 列（触发小时列表，如 [8,20]），旧 hour 列保留但废弃
-- 2) 新增 t_task_item 子表存「源 -> 目标」同步对
-- 3) 幂等合并旧数据：同 user + 同源 + 同目标 的多条任务合并为一条
--    （hours 合并去重升序，其余任务 is_active 置为 false）
-- 生产库手动执行：psql "$DATABASE_URL" -f migrations/20261002_task_multi_items.sql

BEGIN;

-- 1) hours 列；旧 hour 允许 NULL（新任务不再写 hour）
ALTER TABLE public.t_task ADD COLUMN IF NOT EXISTS hours jsonb;
ALTER TABLE public.t_task ALTER COLUMN hour DROP NOT NULL;

-- 2) 同步对子表
CREATE TABLE IF NOT EXISTS public.t_task_item (
    id bigserial NOT NULL,
    task_id integer NOT NULL,
    connect_source_id integer NOT NULL,
    connect_target_id integer NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT t_task_item_pkey PRIMARY KEY (id),
    CONSTRAINT t_task_item_task_fkey FOREIGN KEY (task_id) REFERENCES public.t_task (id) ON DELETE CASCADE,
    CONSTRAINT t_task_item_source_fkey FOREIGN KEY (connect_source_id) REFERENCES public.t_base_connect (id),
    CONSTRAINT t_task_item_target_fkey FOREIGN KEY (connect_target_id) REFERENCES public.t_base_connect (id)
);
CREATE INDEX IF NOT EXISTS idx_t_task_item_task_id ON public.t_task_item (task_id);

-- 3) 旧数据合并（仅在子表为空时执行一次，保证幂等）
DO $$
DECLARE
    rec RECORD;
    keep_id integer;
BEGIN
    IF EXISTS (SELECT 1 FROM public.t_task_item) THEN
        RAISE NOTICE 't_task_item 已有数据，跳过旧任务合并';
        RETURN;
    END IF;

    -- 3.1) 按 user + 源 + 目标 分组，hours 合并去重升序写入保留任务
    FOR rec IN
        SELECT user_id, connect_source_id, connect_target_id, min(id) AS keep_id
        FROM public.t_task
        WHERE hour IS NOT NULL
        GROUP BY user_id, connect_source_id, connect_target_id
    LOOP
        keep_id := rec.keep_id;

        UPDATE public.t_task t
        SET hours = sub.sorted_hours,
            hour = sub.min_hour
        FROM (
            SELECT jsonb_agg(h ORDER BY h) AS sorted_hours, min(h) AS min_hour
            FROM (
                SELECT DISTINCT t2.hour AS h
                FROM public.t_task t2
                WHERE t2.user_id = rec.user_id
                  AND t2.connect_source_id = rec.connect_source_id
                  AND t2.connect_target_id = rec.connect_target_id
                  AND t2.hour IS NOT NULL
            ) d
        ) sub
        WHERE t.id = keep_id;

        -- 3.2) 为保留任务写入同步对
        INSERT INTO public.t_task_item (task_id, connect_source_id, connect_target_id)
        VALUES (keep_id, rec.connect_source_id, rec.connect_target_id);

        -- 3.3) 同组其余任务停用（保留 created_at 历史，不物理删除）
        UPDATE public.t_task t
        SET is_active = false
        WHERE t.user_id = rec.user_id
          AND t.connect_source_id = rec.connect_source_id
          AND t.connect_target_id = rec.connect_target_id
          AND t.id <> keep_id;
    END LOOP;

    RAISE NOTICE '旧任务合并完成';
END $$;

COMMIT;
