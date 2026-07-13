-- DROP TABLE public.t_users;

CREATE TABLE public.t_users (
	id bigserial NOT NULL,
	user_name varchar(255) NULL,
	user_email varchar(255) NULL,
	created_at timestamptz NULL,
	updated_at timestamptz NULL,
	active bool DEFAULT false NULL,
	vip bool DEFAULT false NULL,
	CONSTRAINT t_users_pkey PRIMARY KEY (id),
	CONSTRAINT t_users_user_email_key UNIQUE (user_email),
	CONSTRAINT t_users_user_name_key UNIQUE (user_name)
);

CREATE INDEX idx_t_users_user_email ON public.t_users USING btree (user_email);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_users IS '用户基础信息表：存储核心账户状态及认证凭证';
COMMENT ON COLUMN public.t_users.id IS '[主键] 用户唯一自增ID';
COMMENT ON COLUMN public.t_users.user_name IS '用户全局唯一登录名/昵称';
COMMENT ON COLUMN public.t_users.user_email IS '用户绑定邮箱（全局唯一，用于登录/接收验证码）';
COMMENT ON COLUMN public.t_users.created_at IS '账号创建时间（带时区）';
COMMENT ON COLUMN public.t_users.updated_at IS '账号资料最后更新时间（带时区）';
COMMENT ON COLUMN public.t_users.active IS '账号激活状态：true=已激活，false=未激活';
COMMENT ON COLUMN public.t_users.vip IS '会员状态：true=VIP会员，false=普通用户';


-- DROP TABLE public.t_base_connect;

CREATE TABLE public.t_base_connect (
	id bigserial NOT NULL,
	user_id int4 NOT NULL,
	source_type varchar(20) NOT NULL,
	account varchar(255) NULL,
	guid varchar(255) NULL,
	encrypted_password text NULL,
	region varchar(50) NULL,
	total_count int4 NULL,
	access_token text NULL,
	oauth_token varchar(255) NULL,
	oauth_token_secret varchar(255) NULL,
	access_token_expires_at timestamptz(6) NULL,
	refresh_token text NULL,
	refresh_token_expires_at timestamptz(6) NULL,
	secret_string text NULL,
	created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
	updated_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
	is_active bool DEFAULT true NULL,
	last_synced_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
	CONSTRAINT t_base_connect_pkey PRIMARY KEY (id),
	CONSTRAINT fk_base_user_id FOREIGN KEY (user_id) REFERENCES public.t_users(id)
);

CREATE INDEX idx_base_user_source ON public.t_base_connect USING btree (user_id, source_type);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_base_connect IS '三方渠道授权连接表：管理用户与外部运动健康平台的 OAuth 凭证及同步状态';
COMMENT ON COLUMN public.t_base_connect.id IS '[主键] 授权连接唯一自增ID';
COMMENT ON COLUMN public.t_base_connect.user_id IS '[外键] 关联本系统的用户ID (t_users.id)';
COMMENT ON COLUMN public.t_base_connect.source_type IS '第三方数据源类型 (示例: garmin=佳明, strava=Keep等)';
COMMENT ON COLUMN public.t_base_connect.account IS '第三方平台的登录账号/用户名';
COMMENT ON COLUMN public.t_base_connect.guid IS '第三方平台分配给该用户的全局唯一标识符(OpenID/UUID)';
COMMENT ON COLUMN public.t_base_connect.encrypted_password IS '加密后的第三方平台密码（用于非OAuth直登，需高强度加密）';
COMMENT ON COLUMN public.t_base_connect.region IS '第三方平台所属区域/服务器节点 (示例: CN=中国区, US=国际区)';
COMMENT ON COLUMN public.t_base_connect.total_count IS '累计从该平台同步的运动活动总件数';
COMMENT ON COLUMN public.t_base_connect.access_token IS 'OAuth2 访问令牌（用于调用第三方API）';
COMMENT ON COLUMN public.t_base_connect.oauth_token IS 'OAuth1.0 专用 Token';
COMMENT ON COLUMN public.t_base_connect.oauth_token_secret IS 'OAuth1.0 专用 Token 密钥';
COMMENT ON COLUMN public.t_base_connect.access_token_expires_at IS 'Access Token 的绝对过期时间（带时区）';
COMMENT ON COLUMN public.t_base_connect.refresh_token IS 'OAuth2 刷新令牌（用于刷新过期的 Access Token）';
COMMENT ON COLUMN public.t_base_connect.refresh_token_expires_at IS 'Refresh Token 的绝对过期时间（带时区）';
COMMENT ON COLUMN public.t_base_connect.secret_string IS '备用扩展加密串/签名密钥';
COMMENT ON COLUMN public.t_base_connect.created_at IS '授权关系绑定时间';
COMMENT ON COLUMN public.t_base_connect.updated_at IS '授权信息（如同步令牌）最后更新时间';
COMMENT ON COLUMN public.t_base_connect.is_active IS '授权当前有效状态：true=正常同步，false=授权失效/暂停同步';
COMMENT ON COLUMN public.t_base_connect.last_synced_at IS '最近一次成功触发数据同步的时间（带时区）';


-- DROP TABLE public.t_base_activity;

CREATE TABLE public.t_base_activity (
	id bigserial NOT NULL,
	user_id int4 NOT NULL,
	source_type varchar(20) NOT NULL,
	base_connect_id int4 NOT NULL,
	activity_id varchar(64) NOT NULL,
	activity_name varchar(255) NULL,
	sport_type_raw varchar(50) NULL,
	sport_mode_raw int4 NULL,
	start_time_gmt timestamptz(6) NULL,
	start_time_local timestamp(6) NULL,
	end_time_gmt timestamptz(6) NULL,
	distance_meters numeric(12, 2) NULL,
	duration_seconds numeric(10, 2) NULL,
	moving_duration_seconds numeric(10, 2) NULL,
	calories numeric(10, 2) NULL,
	average_hr int4 NULL,
	max_hr int4 NULL,
	average_cadence int4 NULL,
	max_cadence int4 NULL,
	average_speed numeric(8, 3) NULL,
	max_speed numeric(8, 3) NULL,
	start_lat float8 NULL,
	start_lon float8 NULL,
	location_name varchar(255) NULL,
	device_id varchar(100) NULL,
	elevation_gain numeric(10, 2) NULL,
	elevation_loss numeric(10, 2) NULL,
	created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
	updated_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
	CONSTRAINT t_base_activities_pkey PRIMARY KEY (id),
	CONSTRAINT uq_base_act_source_origin UNIQUE (source_type, activity_id)
);

CREATE INDEX idx_base_activities_connect ON public.t_base_activity USING btree (base_connect_id);
CREATE INDEX idx_base_activities_user_start ON public.t_base_activity USING btree (user_id, start_time_gmt DESC);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_base_activity IS '运动活动主表：存储清洗后的核心运动摘要及核心指标数据';
COMMENT ON COLUMN public.t_base_activity.id IS '[主键] 运动活动唯一自增ID';
COMMENT ON COLUMN public.t_base_activity.user_id IS '[外键] 归属的用户ID (t_users.id)';
COMMENT ON COLUMN public.t_base_activity.source_type IS '数据来源渠道 (示例: garmin, strava)';
COMMENT ON COLUMN public.t_base_activity.base_connect_id IS '[外键] 关联的授权连接ID (t_base_connect.id)';
COMMENT ON COLUMN public.t_base_activity.activity_id IS '第三方平台该条运动记录的原始唯一ID';
COMMENT ON COLUMN public.t_base_activity.activity_name IS '用户自定义或系统默认生成的活动标题/名称';
COMMENT ON COLUMN public.t_base_activity.sport_type_raw IS '第三方平台原始运动类型文本 (示例: RUNNING, CYCLING)';
COMMENT ON COLUMN public.t_base_activity.sport_mode_raw IS '第三方平台原始运动子模式编码';
COMMENT ON COLUMN public.t_base_activity.start_time_gmt IS '运动开始的标准时间（GMT/UTC时区）';
COMMENT ON COLUMN public.t_base_activity.start_time_local IS '运动开始的当地时间（无时区，反映手表记录时的本地显示时间）';
COMMENT ON COLUMN public.t_base_activity.end_time_gmt IS '运动结束的标准时间（GMT/UTC时区）';
COMMENT ON COLUMN public.t_base_activity.distance_meters IS '运动总距离，单位：米 (m)';
COMMENT ON COLUMN public.t_base_activity.duration_seconds IS '运动总持续时间（含暂停），单位：秒 (s)';
COMMENT ON COLUMN public.t_base_activity.moving_duration_seconds IS '实际移动持续时间（扣除暂停），单位：秒 (s)';
COMMENT ON COLUMN public.t_base_activity.calories IS '本次运动消耗的热量，单位：千卡 (kcal)';
COMMENT ON COLUMN public.t_base_activity.average_hr IS '运动全程平均心率，单位：次/分 (bpm)';
COMMENT ON COLUMN public.t_base_activity.max_hr IS '运动全程最大心率，单位：次/分 (bpm)';
COMMENT ON COLUMN public.t_base_activity.average_cadence IS '全程平均步频/踏频 (跑步为spm，骑行为rpm)';
COMMENT ON COLUMN public.t_base_activity.max_cadence IS '全程最大步频/踏频';
COMMENT ON COLUMN public.t_base_activity.average_speed IS '全程平均速度，单位：米/秒 (m/s)';
COMMENT ON COLUMN public.t_base_activity.max_speed IS '全程最大速度，单位：米/秒 (m/s)';
COMMENT ON COLUMN public.t_base_activity.start_lat IS '运动起点的地理纬度（WGS-84标准，十进制度数）';
COMMENT ON COLUMN public.t_base_activity.start_lon IS '运动起点的地理经度（WGS-84标准，十进制度数）';
COMMENT ON COLUMN public.t_base_activity.location_name IS '基于地理位置逆编码生成的地点名称 (示例: 北京市朝阳区)';
COMMENT ON COLUMN public.t_base_activity.device_id IS '记录该运动的硬件设备唯一序列号/ID (如佳明手表序列号)';
COMMENT ON COLUMN public.t_base_activity.elevation_gain IS '运动累计爬升高度，单位：米 (m)';
COMMENT ON COLUMN public.t_base_activity.elevation_loss IS '运动累计下降高度，单位：米 (m)';
COMMENT ON COLUMN public.t_base_activity.created_at IS '记录首次同步入库时间（带时区）';
COMMENT ON COLUMN public.t_base_activity.updated_at IS '记录在本地系统最后更新时间（带时区）';


-- DROP TABLE public.t_user_refresh_tokens;

CREATE TABLE public.t_user_refresh_tokens (
	id bigserial NOT NULL,
	user_id int4 NOT NULL,
	refresh_token text NOT NULL,
	expires_time timestamptz NOT NULL,
	created_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
	expires_ip varchar(45) NULL,
	user_agent text NULL,
	revoked bool DEFAULT false NOT NULL,
	CONSTRAINT t_user_refresh_tokens_pkey PRIMARY KEY (id),
	CONSTRAINT fk_refresh_tokens_user FOREIGN KEY (user_id) REFERENCES public.t_users(id) ON DELETE CASCADE
);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_user_refresh_tokens IS '用户登录刷新凭证表：管理长效登录状态(Refresh Token)的安全校验与无感刷新';
COMMENT ON COLUMN public.t_user_refresh_tokens.id IS '[主键] Token记录自增ID';
COMMENT ON COLUMN public.t_user_refresh_tokens.user_id IS '[外键] 领用该Token的用户ID (t_users.id)';
COMMENT ON COLUMN public.t_user_refresh_tokens.refresh_token IS '刷新令牌内容（用于延长JWT访问令牌寿命）';
COMMENT ON COLUMN public.t_user_refresh_tokens.expires_time IS '该条 Refresh Token 的绝对过期时间点';
COMMENT ON COLUMN public.t_user_refresh_tokens.created_at IS '登录/签发 Token 的时间';
COMMENT ON COLUMN public.t_user_refresh_tokens.expires_ip IS '签发该 Token 时的客户端 IP 地址（支持 IPv4/IPv6）';
COMMENT ON COLUMN public.t_user_refresh_tokens.user_agent IS '签发该 Token 时客户端的浏览器/APP设备标识环境';
COMMENT ON COLUMN public.t_user_refresh_tokens.revoked IS '是否已被强制吊销（true=已作废，用于主动登出或安全风险拦截）';


-- DROP TABLE public.t_user_social;

CREATE TABLE public.t_user_social (
	id bigserial NOT NULL,
	user_id int4 NOT NULL,
	provider varchar(20) NOT NULL,
	provider_user_id varchar(100) NOT NULL,
	access_token text NULL,
	created_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
	CONSTRAINT t_user_social_pkey PRIMARY KEY (id),
	CONSTRAINT fk_user_social_user FOREIGN KEY (user_id) REFERENCES public.t_users(id) ON DELETE CASCADE
);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_user_social IS '用户社交账号映射表：维护三方快捷登录（Google、Github等）与本系统账户的绑定关系';
COMMENT ON COLUMN public.t_user_social.id IS '[主键] 社交绑定自增ID';
COMMENT ON COLUMN public.t_user_social.user_id IS '[外键] 关联的本系统用户ID (t_users.id)';
COMMENT ON COLUMN public.t_user_social.provider IS '社交登录服务商';
COMMENT ON COLUMN public.t_user_social.provider_user_id IS '社交平台上的用户唯一标识 (如微信的 OpenID 或 UnionID)';
COMMENT ON COLUMN public.t_user_social.access_token IS '社交平台当前有效的临时第三方访问令牌';
COMMENT ON COLUMN public.t_user_social.created_at IS '首次绑定该社交账号的时间';


-- DROP TABLE public.t_user_verify_codes;

CREATE TABLE public.t_user_verify_codes (
	id bigserial NOT NULL,
	email varchar(100) NOT NULL,
	code varchar(10) NOT NULL,
	purpose varchar(20) NOT NULL,
	expires_at timestamptz NOT NULL,
	created_at timestamptz NULL,
	used bool DEFAULT false NULL,
	ip_address inet NULL,
	CONSTRAINT t_user_verify_codes_pkey PRIMARY KEY (id)
);

CREATE INDEX idx_t_user_verify_codes_email ON public.t_user_verify_codes USING btree (email);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_user_verify_codes IS '安全验证码记录表：短期临时凭证，用于注册、登录及敏感操作的安全核验';
COMMENT ON COLUMN public.t_user_verify_codes.id IS '[主键] 验证码流水分支ID';
COMMENT ON COLUMN public.t_user_verify_codes.email IS '接收验证码的电子邮箱地址';
COMMENT ON COLUMN public.t_user_verify_codes.code IS '验证码明文内容（如 6 位数字或字母）';
COMMENT ON COLUMN public.t_user_verify_codes.purpose IS '业务使用场景 (示例: register=注册, login=登录, reset_pwd=改密)';
COMMENT ON COLUMN public.t_user_verify_codes.expires_at IS '验证码失效的绝对截止时间点';
COMMENT ON COLUMN public.t_user_verify_codes.created_at IS '验证码触发发送的时间';
COMMENT ON COLUMN public.t_user_verify_codes.used IS '核验使用状态：true=已被核验核销，false=尚未使用';
COMMENT ON COLUMN public.t_user_verify_codes.ip_address IS '请求发送该验证码的客户端 IP 地址';


-- DROP TABLE public.t_log_api;

CREATE TABLE public.t_log_api (
	id bigserial NOT NULL,
	user_id varchar(100) NULL,
	user_name varchar(100) NULL,
	log_type varchar(50) NOT NULL,
	module_name varchar(100) NULL,
	op_desc text NULL,
	req_url text NULL,
	req_method varchar(10) NULL,
	req_params jsonb NULL,
	ip_address inet NULL,
	user_agent text NULL,
	duration_ms int4 NOT NULL,
	created_at timestamptz NOT NULL,
	resp_data text NULL,
	CONSTRAINT t_sys_log_pkey PRIMARY KEY (id)
);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_log_api IS '系统级接口审计日志表';
COMMENT ON COLUMN public.t_log_api.id IS '[主键] 系统API日志全局自增大整型ID';
COMMENT ON COLUMN public.t_log_api.user_id IS '发起操作的用户标识（使用字符串以兼容未登录游客或外部临时ID）';
COMMENT ON COLUMN public.t_log_api.user_name IS '触发请求的用户账户名（冗余字段，方便快速排查）';
COMMENT ON COLUMN public.t_log_api.log_type IS '日志分级/分类 (示例: ACCESS=正常访问, EXCEPTION=异常报错)';
COMMENT ON COLUMN public.t_log_api.module_name IS '归属系统业务模块 (示例: Auth=权限认证, Sync=同步服务)';
COMMENT ON COLUMN public.t_log_api.op_desc IS '该 API 接口的行为含义概要描述';
COMMENT ON COLUMN public.t_log_api.req_url IS '请求的完整 HTTP URL 路径';
COMMENT ON COLUMN public.t_log_api.req_method IS '请求的 HTTP 方法类型 (示例: GET, POST, PUT, DELETE)';
COMMENT ON COLUMN public.t_log_api.req_params IS '请求传入的参数报文（含 URL 参数或 Body 体，采用 JSONB 方便检索）';
COMMENT ON COLUMN public.t_log_api.ip_address IS '发起请求的客户端物理 IP 地址';
COMMENT ON COLUMN public.t_log_api.user_agent IS '客户端浏览器或 APP 的 User-Agent 头环境文本';
COMMENT ON COLUMN public.t_log_api.duration_ms IS '接口服务端内部代码逻辑响应耗时，单位：毫秒 (ms)';
COMMENT ON COLUMN public.t_log_api.created_at IS '请求发生的绝对时间（带时区）';
COMMENT ON COLUMN public.t_log_api.resp_data IS '接口返回的响应报文摘要/全文';


-- DROP TABLE public.t_log_operation;

CREATE TABLE public.t_log_operation (
	id bigserial NOT NULL,
	user_id int4 NULL,
	log_type varchar(50) NOT NULL,
	module_name varchar(100) NULL,
	op_desc text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT t_operation_log_pkey PRIMARY KEY (id)
);

-- 表与字段的属性元数据注释
COMMENT ON TABLE public.t_log_operation IS '业务操作审计日志表';
COMMENT ON COLUMN public.t_log_operation.id IS '[主键] 业务操作日志全局自增大整型ID';
COMMENT ON COLUMN public.t_log_operation.user_id IS '[外键] 执行敏感操作的用户系统ID (t_users.id)';
COMMENT ON COLUMN public.t_log_operation.log_type IS '业务操作动作分类 (示例: CREATE=新建, DELETE=删除, EXPORT=导出)';
COMMENT ON COLUMN public.t_log_operation.module_name IS '关联的功能模块名称 (示例: Device=设备关联, Activity=运动管理)';
COMMENT ON COLUMN public.t_log_operation.op_desc IS '操作行为的具体业务语义描述 (示例: 用户解绑了佳明渠道授权)';
COMMENT ON COLUMN public.t_log_operation.created_at IS '业务操作发生的时间（带时区）';


-- ==========================================
-- 1. 创建任务表 (t_task) 及注释
-- ==========================================
CREATE TABLE public.t_task (
    id bigserial NOT NULL,
    user_id bigint NOT NULL,
    connect_source_id bigint NOT NULL,
    connect_target_id bigint NOT NULL,
    hour int4 NOT NULL,
    is_active bool DEFAULT true NULL,
    created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
    updated_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
    CONSTRAINT t_task_pkey PRIMARY KEY (id),
    CONSTRAINT fk_task_user_id FOREIGN KEY (user_id) REFERENCES public.t_users(id),
    CONSTRAINT fk_task_connect_source FOREIGN KEY (connect_source_id) REFERENCES public.t_base_connect(id),
    CONSTRAINT fk_task_connect_target FOREIGN KEY (connect_target_id) REFERENCES public.t_base_connect(id)
);

-- 创建索引
CREATE INDEX idx_t_task_user_active ON public.t_task USING btree (user_id, is_active);

-- 添加表和字段注释
COMMENT ON TABLE public.t_task IS '数据同步/推送任务主表';
COMMENT ON COLUMN public.t_task.id IS '自增主键';
COMMENT ON COLUMN public.t_task.user_id IS '用户ID，关联 t_users.id';
COMMENT ON COLUMN public.t_task.connect_source_id IS '源端连接配置ID，关联 t_base_connect.id';
COMMENT ON COLUMN public.t_task.connect_target_id IS '目标端连接配置ID，关联 t_base_connect.id';
COMMENT ON COLUMN public.t_task.hour IS '任务执行时间点（如：小时，0-23）或执行周期';
COMMENT ON COLUMN public.t_task.is_active IS '任务是否启用（true: 启用，false: 停用）';
COMMENT ON COLUMN public.t_task.created_at IS '任务创建时间';
COMMENT ON COLUMN public.t_task.updated_at IS '任务更新时间';


-- ==========================================
-- 2. 创建任务结果表 (t_task_result) 及注释
-- ==========================================
CREATE TABLE public.t_task_result (
    id bigserial NOT NULL,
    task_id bigint NOT NULL,
    type int4 NOT NULL,
    message text NULL,
    created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
    CONSTRAINT t_task_result_pkey PRIMARY KEY (id),
    CONSTRAINT fk_task_result_task_id FOREIGN KEY (task_id) REFERENCES public.t_task(id)
);

-- 创建索引
CREATE INDEX idx_t_task_result_task_id ON public.t_task_result USING btree (task_id);

-- 添加表和字段注释
COMMENT ON TABLE public.t_task_result IS '任务历史执行结果表';
COMMENT ON COLUMN public.t_task_result.id IS '自增主键';
COMMENT ON COLUMN public.t_task_result.task_id IS '任务ID，关联 t_task.id';
COMMENT ON COLUMN public.t_task_result.type IS '结果类型/状态码（例如：1-成功，2-部分成功，3-失败）';
COMMENT ON COLUMN public.t_task_result.message IS '执行总结信息或全局错误提示';
COMMENT ON COLUMN public.t_task_result.created_at IS '执行记录生成时间';


-- ==========================================
-- 3. 创建任务结果详情表 (t_task_result_detail) 及注释
-- ==========================================
CREATE TABLE public.t_task_result_detail (
    id bigserial NOT NULL,
    task_result_id bigint NOT NULL,
    source_activity_id bigint NOT NULL,
    target_activity_id bigint NULL,
    success bool DEFAULT false NOT NULL,
    result varchar(400) NULL,
    result_text text NULL,
    created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NULL,
    CONSTRAINT t_task_result_detail_pkey PRIMARY KEY (id),
    CONSTRAINT fk_detail_task_result_id FOREIGN KEY (task_result_id) REFERENCES public.t_task_result(id),
    CONSTRAINT fk_detail_source_activity FOREIGN KEY (source_activity_id) REFERENCES public.t_base_activity(id),
    CONSTRAINT fk_detail_target_activity FOREIGN KEY (target_activity_id) REFERENCES public.t_base_activity(id)
);

-- 创建索引
CREATE INDEX idx_detail_result_id ON public.t_task_result_detail USING btree (task_result_id);
CREATE INDEX idx_detail_source_act_id ON public.t_task_result_detail USING btree (source_activity_id);

-- 添加表和字段注释
COMMENT ON TABLE public.t_task_result_detail IS '任务执行明细表（单条运动数据同步详情）';
COMMENT ON COLUMN public.t_task_result_detail.id IS '自增主键';
COMMENT ON COLUMN public.t_task_result_detail.task_result_id IS '任务结果ID，关联 t_task_result.id';
COMMENT ON COLUMN public.t_task_result_detail.source_activity_id IS '源端运动原始ID，关联 t_base_activity.id';
COMMENT ON COLUMN public.t_task_result_detail.target_activity_id IS '目的端同步成功后的新运动ID，关联 t_base_activity.id（同步失败时为NULL）';
COMMENT ON COLUMN public.t_task_result_detail.success IS '单条数据是否同步成功（true: 成功，false: 失败）';
COMMENT ON COLUMN public.t_task_result_detail.result IS '执行结果状态简码/标签（如：SUCCESS, FAILED, SKIP）';
COMMENT ON COLUMN public.t_task_result_detail.result_text IS '详细的同步反馈、接口返回报文或错误堆栈信息';
COMMENT ON COLUMN public.t_task_result_detail.created_at IS '明细记录创建时间';

-- public.t_heart_rate_daily definition

CREATE TABLE public.t_heart_rate_daily (
    id bigserial NOT NULL,

    user_id bigint NOT NULL,

    calendar_date date NOT NULL,

    max_heart_rate integer NULL,
    min_heart_rate integer NULL,

    resting_heart_rate integer NULL,

    last_seven_days_avg_resting_heart_rate integer NULL,

    created_at timestamptz DEFAULT now() NULL,
    updated_at timestamptz DEFAULT now() NULL,

    CONSTRAINT t_heart_rate_daily_pkey PRIMARY KEY (id),

    CONSTRAINT uk_t_heart_rate_daily_user_date
        UNIQUE (user_id, calendar_date),

    CONSTRAINT fk_t_heart_rate_daily_user
        FOREIGN KEY (user_id)
        REFERENCES public.t_users(id)
);

-- 索引
CREATE INDEX idx_t_heart_rate_daily_user_id
ON public.t_heart_rate_daily USING btree (user_id);

CREATE INDEX idx_t_heart_rate_daily_calendar_date
ON public.t_heart_rate_daily USING btree (calendar_date);

-- 表注释
COMMENT ON TABLE public.t_heart_rate_daily IS '用户每日心率汇总数据';

-- 字段注释
COMMENT ON COLUMN public.t_heart_rate_daily.id IS '主键ID';

COMMENT ON COLUMN public.t_heart_rate_daily.user_id IS '用户ID';

COMMENT ON COLUMN public.t_heart_rate_daily.calendar_date IS '统计日期';

COMMENT ON COLUMN public.t_heart_rate_daily.max_heart_rate IS '当日最大心率(BPM)';

COMMENT ON COLUMN public.t_heart_rate_daily.min_heart_rate IS '当日最小心率(BPM)';

COMMENT ON COLUMN public.t_heart_rate_daily.resting_heart_rate IS '静息心率(BPM)';

COMMENT ON COLUMN public.t_heart_rate_daily.last_seven_days_avg_resting_heart_rate IS '最近7天平均静息心率(BPM)';

COMMENT ON COLUMN public.t_heart_rate_daily.created_at IS '创建时间';

COMMENT ON COLUMN public.t_heart_rate_daily.updated_at IS '更新时间';

-- 约束注释
COMMENT ON CONSTRAINT t_heart_rate_daily_pkey
ON public.t_heart_rate_daily
IS '主键约束';

COMMENT ON CONSTRAINT uk_t_heart_rate_daily_user_date
ON public.t_heart_rate_daily
IS '用户每日心率数据唯一约束';

COMMENT ON CONSTRAINT fk_t_heart_rate_daily_user
ON public.t_heart_rate_daily
IS '关联用户表';

-- public.t_heart_rate_detail definition

CREATE TABLE public.t_heart_rate_detail (
    id bigserial NOT NULL,

    daily_id bigint NOT NULL,

    sample_time timestamptz NOT NULL,

    heart_rate integer NOT NULL,

    created_at timestamptz DEFAULT now() NULL,

    CONSTRAINT t_heart_rate_detail_pkey PRIMARY KEY (id),

    CONSTRAINT uk_t_heart_rate_detail_daily_time
        UNIQUE (daily_id, sample_time),

    CONSTRAINT fk_t_heart_rate_detail_daily
        FOREIGN KEY (daily_id)
        REFERENCES public.t_heart_rate_daily(id)
        ON DELETE CASCADE
);


-- 索引

CREATE INDEX idx_t_heart_rate_detail_daily_id
ON public.t_heart_rate_detail USING btree (daily_id);


CREATE INDEX idx_t_heart_rate_detail_sample_time
ON public.t_heart_rate_detail USING btree (sample_time);



-- 表注释

COMMENT ON TABLE public.t_heart_rate_detail IS '用户心率采样明细数据';



-- 字段注释

COMMENT ON COLUMN public.t_heart_rate_detail.id IS '主键ID';

COMMENT ON COLUMN public.t_heart_rate_detail.daily_id IS '每日心率汇总ID';

COMMENT ON COLUMN public.t_heart_rate_detail.sample_time IS '心率采样时间';

COMMENT ON COLUMN public.t_heart_rate_detail.heart_rate IS '心率值(BPM)';

COMMENT ON COLUMN public.t_heart_rate_detail.created_at IS '创建时间';



-- 约束注释

COMMENT ON CONSTRAINT t_heart_rate_detail_pkey
ON public.t_heart_rate_detail
IS '主键约束';


COMMENT ON CONSTRAINT uk_t_heart_rate_detail_daily_time
ON public.t_heart_rate_detail
IS '同一日同一采样时间唯一约束';


COMMENT ON CONSTRAINT fk_t_heart_rate_detail_daily
ON public.t_heart_rate_detail
IS '关联每日心率汇总表';


-- ── OAuth 2.0 授权码表 ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.t_oauth_authorization_codes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES t_users(id) ON DELETE CASCADE,
    code VARCHAR(64) NOT NULL UNIQUE,
    client_id VARCHAR(50) NOT NULL,
    redirect_uri VARCHAR(500),
    scope VARCHAR(200),
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_t_oauth_code ON public.t_oauth_authorization_codes(code);

COMMENT ON TABLE public.t_oauth_authorization_codes IS 'OAuth 2.0 授权码表，存储 GPT Actions 等第三方应用的临时授权码';
COMMENT ON COLUMN public.t_oauth_authorization_codes.id IS '自增主键';
COMMENT ON COLUMN public.t_oauth_authorization_codes.user_id IS '用户 ID，关联 t_users 表';
COMMENT ON COLUMN public.t_oauth_authorization_codes.code IS '授权码，唯一标识，用于换取 Access Token';
COMMENT ON COLUMN public.t_oauth_authorization_codes.client_id IS '第三方客户端标识，如 gpt-actions';
COMMENT ON COLUMN public.t_oauth_authorization_codes.redirect_uri IS '授权后回调地址';
COMMENT ON COLUMN public.t_oauth_authorization_codes.scope IS '授权范围，如 read';
COMMENT ON COLUMN public.t_oauth_authorization_codes.expires_at IS '授权码过期时间（创建后 10 分钟有效）';
COMMENT ON COLUMN public.t_oauth_authorization_codes.used IS '是否已使用（一次性使用，用完标记）';
COMMENT ON COLUMN public.t_oauth_authorization_codes.created_at IS '记录创建时间';
