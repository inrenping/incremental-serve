-- 一键同步批次与明细记录（2026-09-29）
-- 纯内存 diff 一键同步，活动不落库，仅记录每次执行的批次与明细。
-- 生产库手动执行：psql "$DATABASE_URL" -f migrations/20260929_quick_sync.sql

CREATE TABLE IF NOT EXISTS public.t_sync_run (
    id bigserial NOT NULL,
    user_id integer NOT NULL,
    source_connect_id integer NOT NULL,
    target_connect_id integer NOT NULL,
    window_size integer NOT NULL DEFAULT 10,
    source_platform varchar(32) NULL,
    target_platform varchar(32) NULL,
    source_account varchar(255) NULL,
    target_account varchar(255) NULL,
    fetched_source integer NOT NULL DEFAULT 0,
    fetched_target integer NOT NULL DEFAULT 0,
    diff_count integer NOT NULL DEFAULT 0,
    uploaded_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    status varchar(32) NOT NULL DEFAULT 'error',
    error_message text NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NULL,
    duration_ms integer NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT t_sync_run_pkey PRIMARY KEY (id),
    CONSTRAINT t_sync_run_user_fkey FOREIGN KEY (user_id) REFERENCES public.t_users (id),
    CONSTRAINT t_sync_run_source_fkey FOREIGN KEY (source_connect_id) REFERENCES public.t_base_connect (id),
    CONSTRAINT t_sync_run_target_fkey FOREIGN KEY (target_connect_id) REFERENCES public.t_base_connect (id)
);

CREATE INDEX IF NOT EXISTS idx_t_sync_run_user_created
ON public.t_sync_run USING btree (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.t_sync_run_item (
    id bigserial NOT NULL,
    run_id bigint NOT NULL,
    activity_id varchar(64) NULL,
    activity_name varchar(512) NULL,
    sport_type_raw varchar(64) NULL,
    start_time_local timestamptz NULL,
    start_time_gmt timestamptz NULL,
    distance_meters numeric(12,2) NULL,
    filename varchar(512) NULL,
    status varchar(32) NOT NULL DEFAULT 'failed',
    message text NULL,
    target_activity_id varchar(64) NULL,
    synced_at timestamptz NULL,
    CONSTRAINT t_sync_run_item_pkey PRIMARY KEY (id),
    CONSTRAINT t_sync_run_item_run_fkey FOREIGN KEY (run_id) REFERENCES public.t_sync_run (id)
);

CREATE INDEX IF NOT EXISTS idx_t_sync_run_item_run
ON public.t_sync_run_item USING btree (run_id);
