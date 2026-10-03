-- 迁移：新增睡眠数据表 t_sleep_daily / t_sleep_detail（20261003）
--
-- 结构对称心率那套：
--   t_sleep_daily  每日睡眠汇总（user_id + calendar_date 唯一）
--   t_sleep_detail 睡眠阶段片段（靠 daily_id 关联，自身不带 user_id）
--
-- 口径：calendar_date 采用佳明口径，即「起床那天」。
--       10/2 23:30 睡到 10/3 07:00，记为 10/3，与佳明 App 一致。
--
-- 幂等：全部使用 IF NOT EXISTS / ON CONFLICT DO NOTHING，可重复执行。

CREATE TABLE IF NOT EXISTS public.t_sleep_daily (
    id                          bigserial PRIMARY KEY,
    user_id                     bigint      NOT NULL REFERENCES public.t_users (id),
    calendar_date               date        NOT NULL,
    sleep_start_at              timestamptz,
    sleep_end_at                timestamptz,
    local_offset_minutes        integer,
    sleep_time_seconds          integer,
    nap_time_seconds            integer,
    deep_sleep_seconds          integer,
    light_sleep_seconds         integer,
    rem_sleep_seconds           integer,
    awake_sleep_seconds         integer,
    unmeasurable_sleep_seconds  integer,
    awake_count                 integer,
    sleep_score                 integer,
    average_sp_o2_value         double precision,
    lowest_sp_o2_value          integer,
    average_respiration_value   double precision,
    avg_sleep_stress            double precision,
    sleep_window_confirmed      boolean,
    created_at                  timestamptz,
    updated_at                  timestamptz
);

COMMENT ON TABLE public.t_sleep_daily IS '用户每日睡眠汇总（佳明口径：calendar_date 为起床那天）';
COMMENT ON COLUMN public.t_sleep_daily.sleep_time_seconds IS '睡眠总时长(秒, 不含小睡)';
COMMENT ON COLUMN public.t_sleep_daily.calendar_date IS '统计日期(佳明口径: 起床那天)';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uk_t_sleep_daily_user_date'
    ) THEN
        ALTER TABLE public.t_sleep_daily
            ADD CONSTRAINT uk_t_sleep_daily_user_date UNIQUE (user_id, calendar_date);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_t_sleep_daily_user_id
    ON public.t_sleep_daily (user_id);
CREATE INDEX IF NOT EXISTS idx_t_sleep_daily_calendar_date
    ON public.t_sleep_daily (calendar_date);

CREATE TABLE IF NOT EXISTS public.t_sleep_detail (
    id               bigserial PRIMARY KEY,
    daily_id         bigint      NOT NULL REFERENCES public.t_sleep_daily (id) ON DELETE CASCADE,
    start_at         timestamptz NOT NULL,
    end_at           timestamptz NOT NULL,
    duration_seconds integer     NOT NULL,
    activity_level   integer     NOT NULL,
    created_at       timestamptz,
    updated_at       timestamptz
);

COMMENT ON TABLE public.t_sleep_detail IS '用户睡眠阶段片段';
COMMENT ON COLUMN public.t_sleep_detail.activity_level IS '0=深睡 1=浅睡 2=REM 3=清醒';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uk_t_sleep_detail_daily_start'
    ) THEN
        ALTER TABLE public.t_sleep_detail
            ADD CONSTRAINT uk_t_sleep_detail_daily_start UNIQUE (daily_id, start_at);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_t_sleep_detail_daily_id
    ON public.t_sleep_detail (daily_id);
CREATE INDEX IF NOT EXISTS idx_t_sleep_detail_start_at
    ON public.t_sleep_detail (start_at);
