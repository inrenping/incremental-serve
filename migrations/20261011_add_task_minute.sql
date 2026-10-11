-- 迁移：定时任务错峰执行（20261011）
--
-- 背景：
--   原先调度器全局只在每小时整点 tick 一次（minute=0），所有任务在同一秒被顺序执行，
--   外部 API（Garmin/Coros）速率上限容易被打满、串行同步互相拖累导致失败。
--   本迁移引入「分钟」维度：每个任务创建时定死一个随机分钟(0-59)，调度器改为每分钟
--   tick、只在该任务的「小时:分钟」命中时才执行，从而把任务自然错峰到不同分钟。
--
-- 同时新建 t_task_execution 幂等表：以 (task_id, run_slot) 唯一约束，确保「应用内调度器」
--   与「外部无鉴权 /cron-execute 接口」以及多 worker 实例之间不会重复执行同一个任务槽位。
--
-- 幂等：全部使用 IF NOT EXISTS / ON CONFLICT，可重复执行。

-- ============================================================================
-- 1. t_task 增加 minute 列（0-59，创建时随机定死）
-- ============================================================================

ALTER TABLE public.t_task ADD COLUMN IF NOT EXISTS minute integer;
COMMENT ON COLUMN public.t_task.minute IS '执行分钟(0-59)，创建时随机定死，配合 hours 实现错峰执行；NULL 视为 0（整点）';

-- 既有任务按 id 确定性打散到 0-59 分钟，避免上线后全部仍挤在整点。
-- 公式 (id*37)%60 在 id 连续区间内分布较均匀，且可重复执行幂等（已赋值的不再更新）。
UPDATE public.t_task SET minute = (id * 37) % 60 WHERE minute IS NULL;

-- ============================================================================
-- 2. 执行幂等表：同一 (task_id, run_slot) 只执行一次
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.t_task_execution (
    id          bigserial   PRIMARY KEY,
    task_id     integer     NOT NULL,
    run_slot    varchar(20) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- 唯一约束即幂等闸：调度器 / 外部接口 / 多实例同时尝试插入同一槽位时，只有一个成功。
ALTER TABLE public.t_task_execution
    DROP CONSTRAINT IF EXISTS uk_task_execution_slot;
ALTER TABLE public.t_task_execution
    ADD CONSTRAINT uk_task_execution_slot UNIQUE (task_id, run_slot);

CREATE INDEX IF NOT EXISTS idx_t_task_execution_slot
    ON public.t_task_execution (task_id, run_slot);
