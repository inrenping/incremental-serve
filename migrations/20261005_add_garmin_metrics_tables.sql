-- 迁移：新增佳明体能指标表（20261005）
--
-- 四张表，全部是「每人一份最新快照」，不记录历史变化，重复同步即覆盖：
--   t_garmin_training_status  训练状态与训练负荷（含 VO2max），一人一行
--   t_garmin_fitness_age      体能年龄，一人一行
--   t_garmin_race_prediction  比赛成绩预测，一人每个项目一行（5K/10K/半马/全马）
--   t_garmin_personal_record  个人纪录 PR，一人每个项目一行
--
-- 设计约定：
--   1. 粒度是「人」(user_id 唯一)，不是「连接」。一个人可能同时绑了国际区和中国区
--      两个 Garmin 账号，这里只保留最新同步到的那份；connect_id 列仅用于溯源。
--   2. 每张表都带 raw jsonb 保存佳明原始响应。这些接口都是逆向出来的私有接口，
--      字段随时会变，raw 保证加字段时不用再改表。
--   3. 写入一律用 ON CONFLICT (user_id[, ...]) DO UPDATE，天然幂等。
--
-- 幂等：全部使用 IF NOT EXISTS / DO $$ 判重，可重复执行。

-- ============================================================================
-- 1. 训练状态与训练负荷
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.t_garmin_training_status (
    id                              bigserial PRIMARY KEY,
    user_id                         bigint      NOT NULL REFERENCES public.t_users (id),
    connect_id                      bigint      REFERENCES public.t_base_connect (id),
    calendar_date                   date,
    -- 训练状态
    training_status                 varchar(32),
    training_status_feedback_phrase text,
    training_paused                 boolean,
    since_date                      date,
    -- 训练负荷
    weekly_training_load            numeric(10, 2),
    daily_training_load_acute       numeric(10, 2),
    daily_training_load_chronic     numeric(10, 2),
    acute_chronic_workload_ratio    numeric(6, 3),
    acwr_status                     varchar(32),
    acwr_percent                    numeric(6, 2),
    load_tunnel_min                 numeric(10, 2),
    load_tunnel_max                 numeric(10, 2),
    load_level_trend                varchar(32),
    fitness_trend                   varchar(32),
    -- 最大摄氧量
    vo2_max_value                   numeric(5, 2),
    vo2_max_precise_value           numeric(6, 3),
    vo2_max_calendar_date           date,
    vo2_max_running                 numeric(5, 2),
    vo2_max_cycling                 numeric(5, 2),
    -- 原始响应与时间戳
    raw                             jsonb,
    synced_at                       timestamptz,
    created_at                      timestamptz,
    updated_at                      timestamptz
);

COMMENT ON TABLE public.t_garmin_training_status IS '佳明训练状态与训练负荷最新快照（一人一行，覆盖式更新）';
COMMENT ON COLUMN public.t_garmin_training_status.training_status IS '训练状态：PRODUCTIVE/MAINTAINING/PEAKING/OVERREACHING/DETRAINING/UNPRODUCTIVE/NO_STATUS';
COMMENT ON COLUMN public.t_garmin_training_status.weekly_training_load IS '7 日训练负荷';
COMMENT ON COLUMN public.t_garmin_training_status.acute_chronic_workload_ratio IS 'ACWR 急慢性负荷比，>1.5 为危险区';
COMMENT ON COLUMN public.t_garmin_training_status.load_tunnel_min IS '当前建议负荷区间下限';
COMMENT ON COLUMN public.t_garmin_training_status.load_tunnel_max IS '当前建议负荷区间上限';
COMMENT ON COLUMN public.t_garmin_training_status.raw IS '佳明 /metrics-service/metrics/trainingstatus/aggregated 原始响应';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uk_t_garmin_training_status_user'
    ) THEN
        ALTER TABLE public.t_garmin_training_status
            ADD CONSTRAINT uk_t_garmin_training_status_user UNIQUE (user_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_t_garmin_training_status_date
    ON public.t_garmin_training_status (calendar_date);

-- ============================================================================
-- 2. 体能年龄
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.t_garmin_fitness_age (
    id            bigserial PRIMARY KEY,
    user_id       bigint NOT NULL REFERENCES public.t_users (id),
    connect_id    bigint REFERENCES public.t_base_connect (id),
    calendar_date date,
    fitness_age   numeric(5, 1),
    vo2_max_value numeric(5, 2),
    max_met       numeric(5, 2),
    raw           jsonb,
    synced_at     timestamptz,
    created_at    timestamptz,
    updated_at    timestamptz
);

COMMENT ON TABLE public.t_garmin_fitness_age IS '佳明体能年龄最新快照（一人一行，覆盖式更新）';
COMMENT ON COLUMN public.t_garmin_fitness_age.fitness_age IS '体能年龄（岁），由 VO2max 推算';
COMMENT ON COLUMN public.t_garmin_fitness_age.max_met IS '最大代谢当量 MET';
COMMENT ON COLUMN public.t_garmin_fitness_age.raw IS '佳明 /metrics-service/metrics/maxmet/daily 原始响应';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uk_t_garmin_fitness_age_user'
    ) THEN
        ALTER TABLE public.t_garmin_fitness_age
            ADD CONSTRAINT uk_t_garmin_fitness_age_user UNIQUE (user_id);
    END IF;
END $$;

-- ============================================================================
-- 3. 比赛成绩预测
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.t_garmin_race_prediction (
    id                 bigserial PRIMARY KEY,
    user_id            bigint NOT NULL REFERENCES public.t_users (id),
    connect_id         bigint REFERENCES public.t_base_connect (id),
    calendar_date      date,
    race_type          varchar(24) NOT NULL,
    distance_meters    numeric(10, 2),
    predicted_seconds  numeric(12, 3),
    predicted_time_text varchar(32),
    raw                jsonb,
    synced_at          timestamptz,
    created_at         timestamptz,
    updated_at         timestamptz
);

COMMENT ON TABLE public.t_garmin_race_prediction IS '佳明比赛成绩预测最新快照（一人每个项目一行）';
COMMENT ON COLUMN public.t_garmin_race_prediction.race_type IS '项目：FIVE_K/TEN_K/HALF_MARATHON/MARATHON';
COMMENT ON COLUMN public.t_garmin_race_prediction.predicted_seconds IS '预测完赛时间（秒）';
COMMENT ON COLUMN public.t_garmin_race_prediction.predicted_time_text IS '接口直接给的文本成绩，如 21:17';
COMMENT ON COLUMN public.t_garmin_race_prediction.raw IS '佳明 /metrics-service/metrics/racepredictions 原始响应（2026-10 实测该端点 404，暂用 raw 兜底）';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uk_t_garmin_race_prediction_user_type'
    ) THEN
        ALTER TABLE public.t_garmin_race_prediction
            ADD CONSTRAINT uk_t_garmin_race_prediction_user_type UNIQUE (user_id, race_type);
    END IF;
END $$;

-- ============================================================================
-- 4. 个人纪录
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.t_garmin_personal_record (
    id                 bigserial PRIMARY KEY,
    user_id            bigint NOT NULL REFERENCES public.t_users (id),
    connect_id         bigint REFERENCES public.t_base_connect (id),
    type_id            integer NOT NULL,
    type_key           varchar(32),
    activity_type      varchar(32),
    unit               varchar(16),
    value              numeric(14, 3),
    value_seconds      numeric(12, 3),
    value_meters       numeric(14, 3),
    activity_id        bigint,
    activity_name      varchar(255),
    achieved_at        timestamptz,
    activity_start_at  timestamptz,
    raw                jsonb,
    synced_at          timestamptz,
    created_at         timestamptz,
    updated_at         timestamptz
);

COMMENT ON TABLE public.t_garmin_personal_record IS '佳明个人纪录 PR（一人每个项目一行，覆盖式更新）';
COMMENT ON COLUMN public.t_garmin_personal_record.type_id IS '佳明 PR 类型：跑步 1=1km 2=1mile 3=5km 4=10km 5=半马 6=全马 7=最长跑；骑行/游泳另有映射';
COMMENT ON COLUMN public.t_garmin_personal_record.type_key IS '本地语义键，如 fastest_5k / longest_run，便于代码里不写魔法数字';
COMMENT ON COLUMN public.t_garmin_personal_record.unit IS 'value 的单位：second 秒 / meter 米';
COMMENT ON COLUMN public.t_garmin_personal_record.value IS '纪录原始值，按 unit 解释';
COMMENT ON COLUMN public.t_garmin_personal_record.value_seconds IS 'unit=second 时的秒数，冗余便于排序与展示';
COMMENT ON COLUMN public.t_garmin_personal_record.value_meters IS 'unit=meter 时的米数，冗余便于排序与展示';
COMMENT ON COLUMN public.t_garmin_personal_record.achieved_at IS '创纪录时刻(UTC)，来自 prStartTimeGmt';
COMMENT ON COLUMN public.t_garmin_personal_record.raw IS '佳明 /personalrecord-service/personalrecord/prs/{displayName} 原始条目';

-- activity_type 可能为 NULL，唯一键里用 COALESCE 兜底，避免 NULL 不相等导致重复行
CREATE UNIQUE INDEX IF NOT EXISTS uk_t_garmin_personal_record_user_type
    ON public.t_garmin_personal_record (user_id, type_id, COALESCE(activity_type, ''));

CREATE INDEX IF NOT EXISTS idx_t_garmin_personal_record_activity
    ON public.t_garmin_personal_record (activity_type);
