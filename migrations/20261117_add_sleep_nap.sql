-- 迁移：新增小睡明细表 t_sleep_nap（20261117）
--
-- 背景：Garmin dailySleepDTO.dailyNapDTOS 提供逐条小睡的起止时间，
--       此前只落了 nap_time_seconds 汇总秒数，逐条数据被丢弃。
--       本表与 t_sleep_detail 同构：靠 daily_id 关联 t_sleep_daily，自身不带 user_id。
--
-- 幂等：全部使用 IF NOT EXISTS / DO $$，可重复执行。

CREATE TABLE IF NOT EXISTS public.t_sleep_nap (
    id               bigserial PRIMARY KEY,
    daily_id         bigint      NOT NULL REFERENCES public.t_sleep_daily (id) ON DELETE CASCADE,
    start_at         timestamptz NOT NULL,
    end_at           timestamptz NOT NULL,
    duration_seconds integer     NOT NULL,
    nap_feedback     varchar(128),
    created_at       timestamptz,
    updated_at       timestamptz
);

COMMENT ON TABLE public.t_sleep_nap IS '小睡明细（佳明 dailyNapDTOS，逐条起止时间）';
COMMENT ON COLUMN public.t_sleep_nap.start_at IS '小睡开始时刻(UTC)';
COMMENT ON COLUMN public.t_sleep_nap.end_at IS '小睡结束时刻(UTC)';
COMMENT ON COLUMN public.t_sleep_nap.nap_feedback IS '佳明小睡反馈枚举，如 LATE_TIMING_LONG_DURATION_LOW_NEED';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uk_t_sleep_nap_daily_start'
    ) THEN
        ALTER TABLE public.t_sleep_nap
            ADD CONSTRAINT uk_t_sleep_nap_daily_start UNIQUE (daily_id, start_at);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_t_sleep_nap_daily_id
    ON public.t_sleep_nap (daily_id);
CREATE INDEX IF NOT EXISTS idx_t_sleep_nap_start_at
    ON public.t_sleep_nap (start_at);
