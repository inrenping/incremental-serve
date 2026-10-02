-- 迁移：每个用户只允许保留一个定时任务（20261002）
--
-- 背景：任务改造为「一个任务 = 多条同步对 × 多个触发小时」后，
-- 不再需要建多条任务。历史数据里同一用户可能仍有多条任务
-- （源于旧版「一小时一任务」或不同同步对），本脚本把每个用户的
-- 多条任务合并为一条：
--   - 同步对：所有任务的 t_task_item 去重合并（缺失时回退旧字段 connect_source_id/target_id）
--   - 触发小时：所有任务的 hours / hour 去重升序合并
--   - 受「同步对数 × 小时数 ≤ 8 次/天」约束，超出时按小时升序截断（至少保留 1 个）
--   - 保留 id 最小的任务作为主任务（其执行记录保留），其余任务连同其执行记录一并删除
--
-- 幂等：合并后每个用户只剩 1 条任务，再次运行不会命中任何用户。

DO $$
DECLARE
    rec          RECORD;
    base_id      bigint;
    item_count   int;
    max_hours    int;
    merged_hours int[];
    first_src    bigint;
    first_tgt    bigint;
BEGIN
    CREATE TEMP TABLE IF NOT EXISTS tmp_pairs (s bigint, t bigint) ON COMMIT DROP;

    FOR rec IN
        SELECT user_id
        FROM public.t_task
        GROUP BY user_id
        HAVING count(*) > 1
    LOOP
        TRUNCATE tmp_pairs;

        -- 1) 汇总同步对：优先取子表
        INSERT INTO tmp_pairs (s, t)
        SELECT DISTINCT ti.connect_source_id, ti.connect_target_id
        FROM public.t_task_item ti
        JOIN public.t_task tk ON tk.id = ti.task_id
        WHERE tk.user_id = rec.user_id
          AND ti.connect_source_id IS NOT NULL
          AND ti.connect_target_id IS NOT NULL
          AND ti.connect_source_id <> ti.connect_target_id;

        -- 2) 子表缺失时回退任务上的旧字段
        INSERT INTO tmp_pairs (s, t)
        SELECT DISTINCT tk.connect_source_id, tk.connect_target_id
        FROM public.t_task tk
        WHERE tk.user_id = rec.user_id
          AND tk.connect_source_id IS NOT NULL
          AND tk.connect_target_id IS NOT NULL
          AND tk.connect_source_id <> tk.connect_target_id
          AND NOT EXISTS (
              SELECT 1 FROM tmp_pairs p
              WHERE p.s = tk.connect_source_id AND p.t = tk.connect_target_id
          );

        SELECT count(*) INTO item_count FROM tmp_pairs;
        CONTINUE WHEN item_count = 0;

        SELECT min(id) INTO base_id FROM public.t_task WHERE user_id = rec.user_id;

        -- 3) 合并触发小时（去重升序）
        SELECT ARRAY(
            SELECT DISTINCT h
            FROM (
                SELECT jsonb_array_elements_text(tk.hours)::int AS h
                FROM public.t_task tk
                WHERE tk.user_id = rec.user_id
                  AND tk.hours IS NOT NULL
                  AND jsonb_typeof(tk.hours) = 'array'
                UNION
                SELECT tk.hour AS h
                FROM public.t_task tk
                WHERE tk.user_id = rec.user_id AND tk.hour IS NOT NULL
            ) x
            WHERE h BETWEEN 0 AND 23
            ORDER BY h
        ) INTO merged_hours;

        -- 4) 受每日执行次数上限约束，必要时截断小时
        max_hours := GREATEST(1, 8 / item_count);
        IF coalesce(array_length(merged_hours, 1), 0) > max_hours THEN
            merged_hours := merged_hours[1:max_hours];
        END IF;
        IF coalesce(array_length(merged_hours, 1), 0) = 0 THEN
            merged_hours := ARRAY[8];
        END IF;

        SELECT s, t INTO first_src, first_tgt FROM tmp_pairs ORDER BY s, t LIMIT 1;

        -- 5) 清理被合并掉的任务的执行记录（t_task_result 外键无级联）
        IF to_regclass('public.t_task_result_detail') IS NOT NULL THEN
            DELETE FROM public.t_task_result_detail
            WHERE task_result_id IN (
                SELECT id FROM public.t_task_result
                WHERE task_id IN (
                    SELECT id FROM public.t_task
                    WHERE user_id = rec.user_id AND id <> base_id
                )
            );
        END IF;

        DELETE FROM public.t_task_result
        WHERE task_id IN (
            SELECT id FROM public.t_task
            WHERE user_id = rec.user_id AND id <> base_id
        );

        -- 6) 重建主任务的同步对
        DELETE FROM public.t_task_item WHERE task_id = base_id;
        INSERT INTO public.t_task_item (task_id, connect_source_id, connect_target_id, created_at)
        SELECT base_id, s, t, CURRENT_TIMESTAMP FROM tmp_pairs ORDER BY s, t;

        -- 7) 主任务写回合并结果，旧字段同步为首条同步对
        UPDATE public.t_task
        SET hours = to_jsonb(merged_hours),
            hour = merged_hours[1],
            connect_source_id = first_src,
            connect_target_id = first_tgt,
            is_active = true,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = base_id;

        -- 8) 删除其余任务（子表有 ON DELETE CASCADE）
        DELETE FROM public.t_task WHERE user_id = rec.user_id AND id <> base_id;

        RAISE NOTICE 'merged tasks for user % into task % (% pairs, hours %)',
            rec.user_id, base_id, item_count, merged_hours;
    END LOOP;

    DROP TABLE IF EXISTS tmp_pairs;
END $$;
