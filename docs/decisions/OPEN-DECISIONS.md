# OPEN-DECISIONS 悬而未决登记册

> 只追加 + 就地关闭。每次 Phase 开始时复现到工作上下文最前面。
> Status: OPEN → RESOLVED（关闭时补 Resolution 字段）

| Date | Source | Open Item | Related Constraints | Current Leaning | Blocked By | Resolves When | Status |
|------|--------|-----------|---------------------|-----------------|------------|---------------|--------|
| 2026-09-29 | Phase 1 | `GARMIN_FIT_DIR` 磁盘泄漏：`_upload_fit_zip_to_coros` 每次向 Coros 推送都把 ZIP 写到本地磁盘且**从不删除**，长期运行会持续增长 | 高并发下同名文件还可能互相覆盖；本次改造范围严格限定为「降低 DB 读写与第三方请求数」，磁盘治理不在其中 | 改成全内存：md5 用 hashlib 在内存算，直接用 bytes 调 put_object / oss2 上传，彻底干掉磁盘 IO | 等用户确认纳入某个迭代 | 立为独立小任务（改动集中在 `_upload_fit_zip_to_coros` 一个函数） | **RESOLVED** 2026-10-05：修复一键同步 STS v1/v2 漂移时一并落地。上传流水线收敛到 `app/services/coros_upload.py`，MD5 与大小直接在 `zip_bytes` 上算，`AliOssClient`/`AwsOssClient` 改为 `put_object(data, key)` / `multipart_upload(data, key)` 全内存接口，不再写 `GARMIN_FIT_DIR`；回归测试见 `tests/test_coros_upload.py::test_upload_fit_writes_no_temp_file` |
| 2026-09-29 | Phase 1 | Garmin↔Garmin 跨区同步的静默失败**尚未经真实账号验证** | 结论是按代码路径推演出来的（全局 garth 单例 + `test_garmin_token`/`fetch_latest_garmin_activities` 不装载目标凭证），把握约 95%；新方案已改用独立 `garth.Client()` 根治 | 推演成立，新方案可修复 | 需要用户用真实的 CN 区 + 国际区各一个账号跑一次 `/execute2` | 用户实跑一次并确认 diff 不再恒为 0 | OPEN |
| 2026-09-29 | Phase 1 | 项目并存三套存储客户端（`SupabaseStorageClient` / `AliOssClient` / `AwsOssClient`），职责边界容易误读 | 复盘后认定**不是技术债**：三者职责正交、互相不可替换。AliOss/AwsOss 是 Coros 协议强制中转（Coros 服务端只回拉自己的桶，换 Supabase 会推送失败）；Supabase 是我们的资产缓存层，与同步链路正交。用户明确判断「这不是问题」 | 维持三套，靠 `CorosSession` 里的职责注释拦住误改即可；不引入 StorageBackend 抽象（YAGNI，仅为观感加间接） | 已与用户对齐 | 无需处理 | RESOLVED |
| 2026-09-29 | Phase 1 | 旧链路 `/pullFullActivities` + `/uploadActivity2Target` 何时下线 | 前端 activities / files / compare 三个页面仍在使用旧的两步入库链路 | 保持并存，暂不标记 deprecated | 等前端全面切到 `/execute2` | 三页面迁移完成后 | OPEN |
