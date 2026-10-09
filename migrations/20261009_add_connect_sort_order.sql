-- 迁移：为 t_base_connect 增加 sort_order 字段，用于「应用程序账号」的展示排序
-- 背景：原先 getConnectConfigs 固定按 created_at DESC 排序（后加的账号排最前），
--       用户希望手动拖拽调整顺序，且所有页面的账号下拉框都按这个顺序展示。
-- 执行时间点：代码发布前或发布后均可（ADD COLUMN 带默认值，PG11+ 为 ONLINE 安全操作）
-- 执行方式：直接在 Neon / PostgreSQL 数据库里跑下面的语句即可
-- 幂等：使用 IF NOT EXISTS，可重复执行

BEGIN;

-- 1. 加字段。DEFAULT 0 + NOT NULL，既有行自动填 0。
ALTER TABLE public.t_base_connect
    ADD COLUMN IF NOT EXISTS sort_order integer NOT NULL DEFAULT 0;

-- 2. 给既有行初始化顺序：沿用原先的展示顺序（created_at DESC），
--    这样迁移前后用户看到的顺序一致，不会突然重排。
--    用 row_number() 按用户分组生成 0,1,2...（降序取反 -> 转为升序排名）。
WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at DESC, id DESC) AS rn
    FROM public.t_base_connect
    WHERE sort_order = 0
)
UPDATE public.t_base_connect bc
SET sort_order = ranked.rn - 1
FROM ranked
WHERE bc.id = ranked.id;

COMMIT;

-- 备注：
-- * 之后新增的账号（登录流程 insert 时）会取 DEFAULT 0，即排在最前。
--   如需「新增账号排最后」，在 base_connect_service 的创建逻辑里显式写入
--   max(sort_order)+1。当前保持 0（排最前），符合直觉：刚加的账号最显眼。
-- * 排序查询：ORDER BY sort_order ASC, created_at DESC
--   （sort_order 相同时用 created_at 兜底，保证顺序稳定）