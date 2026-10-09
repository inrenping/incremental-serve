# Suunto 非官方 FIT 上传 · 排查报告（HTTP 523）

> 仓库：`incremental-serve`（本地名 `blunt-serv`）
> 日期：2026-10-09
> 状态：**调查 + 一处已验证的结构修复（未提交）**
> 核心结论：523 是「内容嗅探失败」，最可能的结构性根因是生成的 SML 缺 `<Activity>` 元素；原始 FIT 直传被拒是预期行为，suuntool 根本不支持 FIT 上传。

---

## 1. 已确认的事实（均有源码/样本证据）

1. **suuntool 上传只收 SML，不收 FIT。**
   - `internal/api/endpoints/upload.go`：`UploadWorkout(ctx, c, smlPath, extensionsPath)` —— 参数就是 `.sml` 文件路径。
   - `internal/api/multipart.go`：`WorkoutMultipart` 只写两个 part：`filePart`（用户文件，`application/octet-stream`）+ 可选 `workoutExtensionsPart`（`application/json`）。
   - `upload_test.go`：用 `<sml>fake</sml>` 作为 `filePart` 内容做断言。**suuntool 本身不生成 SML**，只负责把你提供的 `.sml` 文件 POST 出去。
   - → 团队代码 `upload_sml`（`part-filePart-octet`）的**请求结构（multipart / 字段名 `filePart` / 分片 Content-Type `application/octet-stream`）与 suuntool 逐字一致，不是问题所在。**

2. **读接口（GET /sml）返回 JSON，写接口要 legacy SML XML。** 团队早期发 JSON 报 500、改 XML 后报 523，这个演进是对的方向。`workouts.go` 的 `FetchSML` 注释明确写「`/v1/workouts/{key}/sml` 返回 application/json（~5MB）」。

3. **真实服务端 SML 的权威结构已拿到**：`real.sml`（服务端导出的真实活动）。根元素是 `<sml>`，`DeviceLog` 是其子节点；元素顺序与团队生成器一致。

4. **JAXB 模型（`suunto-sml-model`）是第三方逆向，不是服务端真实 schema。** 它的字段类型声明（`Header.Distance=Integer`、`Sample.Altitude=Integer` 等）与服务端真实输出（`real.sml` 里这些是**浮点**）不完全一致，只能作为「宽松参考」，不能当作上传校验的硬标准。

5. **团队生成器的元素顺序、命名空间、根结构已经正确**（与 `real.sml` 逐项比对通过，团队自身的 `verify.py` 在修复后 `ALL CHECKS PASSED`）。

---

## 2. 尚未确认的假设

- **523 的根因尚未被「上传成功」最终证实。** 当前最强候选是「缺 `<Activity>`」，但缺少一次真实上传成功的证据。下一步需要你在真实账号上跑一次（见 §7）。
- **`Header.Distance` / `Sample.Altitude` / `Sample.Distance` / `Sample.GPSAltitude` 该用 int 还是 float？** 服务端真实输出是浮点，JAXB 把前三者标成 Integer、GPSAltitude 标成 Double。两种口径都满足：Integer 字段收 int 安全，Double 字段收 int/float 都安全。**当前保守策略：保持 int（避免触发 JAXB 的 NumberFormatException），待真实上传验证后再决定是否改 float。**

---

## 3. 523 最可能的排查方向 + 证据

### 方向 A（最强候选，已修复）：生成的 SML 缺少 `<Activity>` 元素
- 证据：当活动名为空时，生成器 `_sub(hdr, "Activity", activity_name or "")` 因为 `_sub` 在 text 为空时**直接跳过**，导致 Header 没有 `<Activity>`。
- 对照：真实 `real.sml` **始终**包含 `<Activity />`（即使为空）；团队 `verify.py` 也断言该元素必须存在（修复前正是因此断言失败）。
- 推论：服务端 SML 解析/嗅探很可能要求 Header 含此元素，缺失即判「没拿到 SML」→ 报 523。
- 已修复：`suunto_sml.py` 改为**始终**输出 `<Activity>`（有名写名、无名写空）。修复后 `verify.py` 通过，且生成器输出与 `real.sml` 的元素集合/顺序一致（除测试数据缺的字段外）。

### 方向 B（已排除）：凭据 / sessionKey 问题
- 代码里曾假设「SML 和原始 FIT 都报 523 ⇒ 凭据问题」。这个推理**不成立**：
  - 原始 FIT 经 `filePart` 直传被拒是**预期**的——suuntool 从不传 FIT，服务端不会把 `filePart` 里的裸 FIT 当 binary 识别。
  - 所以「FIT 也 523」不能作为凭据无效的证据；它只说明 FIT 直传这条路本来就不通。
- 凭据是否有效的正确判据是只读端点（GET `user` / `workouts/count` / `activitytypes`）：200 即有效，401/403 才无效。脚本里已有 `_check_session` / `check_session` 自检，应作为上传前置门槛（已具备）。

### 方向 C（低概率）：`User-Agent` / 端点细节
- suuntool 与团队读操作都用 `User-Agent: com.stt.android.suunto/6008013`。上传大概率同款；若 A 修复后仍 523，再排查 UA 与是否有额外头（如 `x-totp`，suuntool 注释说上传不带 totp）。

---

## 4. suuntool 上传协议源码分析结论

| 项 | suuntool 源码事实 | 对本题的意义 |
|---|---|---|
| 上传端点 | `POST /v1/workout` | 团队一致 |
| 请求形态 | multipart/form-data，字段 `filePart`，分片 CT `application/octet-stream`；可选 `workoutExtensionsPart` | 团队一致 |
| 鉴权 | `STTAuthorization: <sessionKey>`（与读操作同款，无 x-totp） | 团队一致 |
| 收的内容 | 用户提供的 `.sml` 文件（**不是 FIT**） | **FIT 直传不在 suuntool 能力内** |
| 是否生成 SML | 否 | 团队必须自己造 SML（已实现 `fit_bytes_to_sml_xml`） |
| 字段格式 | 测试用 `<sml>fake</sml>`，无任何 SML 内容格式指引 | SML 内容格式只能靠 `real.sml` + `polar_training2sml` + JAXB 模型比对 |

---

## 5. 三条路线可行性比较

| 路线 | 可行性 | 依据 |
|---|---|---|
| **① 原始 FIT 直传** | ❌ 不被非官方接口支持 | suuntool 源码只上传 SML；实测裸 FIT 经 `filePart` 报 523（预期）。服务端虽有 `exportFit` 导出 FIT，但导入端不认 `filePart` 里的裸 FIT。 |
| **② FIT → SML XML → 上传** | ✅ 唯一可行路线 | suuntool 上传的就是 SML；团队转换器结构已对齐 `real.sml`；缺 `<Activity>` 修复后待真实账号验证。功率（power）暂未写入（SML 里要走 `Sample/AppsData/AppData`，需先定 AppNumber）。 |
| **③ 官方 `/v2/upload` API** | ⚠️ 需 Partner 资质 + 订阅密钥 | 与本项目「免审核」诉求冲突，不作为默认。仅作兜底。 |

---

## 6. 实际修改的文件清单

| 文件 | 修改 | 原因 | 是否提交 |
|---|---|---|---|
| `app/services/suunto_sml.py` | 修复 `<Activity>`：Header 中**始终**输出该元素（空则 `<Activity />`） | 真实 `real.sml` 始终含 `<Activity>`；缺失是 523 最强候选根因；团队 `verify.py` 也断言其存在 | **未提交**（等你确认诊断） |

> 说明：未改动 `upload_sml` / `upload_workout` 的请求结构（已与 suuntool 一致，无需改）。类型字段（Distance/Altitude 等）保持 int 的保守策略，待真实上传验证后再决定是否改 float。

---

## 7. 可复制执行的本地测试命令

### 7.1 离线校验（不需要网络 / 账号）
```bash
# 1. 准备受管 venv（已装 fitparse 1.2.0）
VENV=/home/inrenping/.workbuddy/binaries/python/envs/default/bin/python

# 2. 生成 SML 并与真实样本逐项比对（元素集合 / 顺序 / 类型）
$VENV /home/inrenping/.cache/work/sml_check/compare_real.py

# 3. 跑团队自带的结构校验（修复后应通过）
$VENV /home/inrenping/.cache/work/sml_check/verify.py
```

### 7.2 真实上传验证（需要你的 Suunto 国际版账号 + 网络）
```bash
# 用浏览器登录 sports-tracker.com → F12 → Network → 复制 STTAuthorization 头的值
export SUUNTO_SESSION_KEY=<从请求头复制的 sessionKey>

# 优先验证 SML 上传（修复后）是否还报 523
python3 scripts/probe_suunto_upload.py \
  --session-key "$SUUNTO_SESSION_KEY" \
  --fit /path/to/your/valid.fit \
  --outdir /tmp/suunto-probe

# 只读自检（确认凭据有效，200 才说明不是凭据问题）
python3 scripts/probe_suunto_variants.py --session-key "$SUUNTO_SESSION_KEY" --check-only
```
- 若 SML 仍 523：抓完整响应体 + 响应头，回到方向 C 排查 UA / 额外头 / 是否缺其他必填段。
- 若 SML 成功（返回 `payload.key`）：用 `GET /v1/workouts/{key}/sml` 拉回服务端接受的样本，与生成器输出做字段级 diff，校正剩余偏差（如 power、Summary 是否要带）。

---

## 8. 必须配置的环境变量（不含真实凭据）

| 变量 | 用途 | 示例（占位，非真实） |
|---|---|---|
| `SUUNTO_SESSION_KEY` | 临时上传探测用 sessionKey（也可用 `--session-key` 传） | `xxxx-xxxx-xxxx` |
| `SUUNTO_INTL_BASE_URL` | 覆盖国际版 base（默认 `https://api.sports-tracker.com/apiserver/v1/`） | 一般不用改 |
| `SUUNTO_CN_BASE_URL` | 国内版私有后端域名（国内版上线前必填，待你抓包确认） | `https://<待确认>.suunto.cn/` |
| `SUUNTO_UPLOAD_VARIANTS` | 设 `1` 进入诊断模式，逐一尝试 10 种上传形态 | `0`（默认，只试 `part-filePart-octet`） |

> 账号密码走前端 AES 加密（`SECRET_KEY`），不在环境变量里放明文。登录签名密钥（TOTP/SHA256）已内置在 `suunto_service.py` / `suunto_probe.py`，逆向自官方 APK，与 suuntool 一致。

---

## 9. 下一步建议 / 仍需你在 Suunto App 中人工确认

1. **在真实账号跑 §7.2**，确认 `<Activity>` 修复后 523 是否消失——这是把「假设」变成「结论」的唯一一步。
2. **App 内核对**：上传成功后，必须在 Suunto App（不是只看 HTTP 200）确认活动真的出现、轨迹/心率/距离正确。HTTP 2xx 不等于活动落库。
3. **国内版 host**：仍待你抓包确认 `SUUNTO_CN_BASE_URL`；代码已预留开关，域名确认后填 env 即生效。
4. **功率字段**：当前 SML 未写 power，若需要可在确认 AppNumber 后补 `Sample/AppsData/AppData`。
5. **回归**：上传协议验证通过后，再接 Phase 5 的工程化（会话管理、幂等去重、批量限速、定时任务），不要在协议未验证前铺批量同步。
6. **测试加固（建议后续）**：把 `compare_real.py` + `make_fit.py` 沉淀为 `tests/test_suunto_sml.py`，锁住 `<Activity>` 与元素顺序，避免回归。

---

## 附：证据来源索引
- suuntool 上传：`.cache/rev/suuntool/internal/api/endpoints/upload.go`、`multipart.go`、`upload_test.go`
- 真实 SML 基准：`.cache/work/sml_check/real.sml`
- JAXB 模型（第三方逆向）：`.cache/rev/jaxb/header_Header.java`、`sample_Sample.java`、`DeviceLog.java`
- 团队代码：`app/services/suunto_sml.py`、`app/services/suunto_service.py`、`scripts/probe_suunto_upload.py`、`scripts/probe_suunto_variants.py`
