from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from app.models.base_connect import BaseConnect
from app.models.user import User
from app.services import platform_session
from app.services.sync_run_service import finalize, record_item, start_run

MAX_WORKERS = 3  # 下载/上传并发上限


def _format_local_time(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _is_same_activity_dict(source: dict, target: dict) -> bool:
    """判定是否为同一条运动：开始时间差 ≤ 300 秒 且 距离差 ≤ 5%）。

    沿用旧实现规则，行为必须一致。
    """
    if not source.get("start_time_gmt") or not target.get("start_time_gmt"):
        return False
    time_diff = abs(
        (source["start_time_gmt"] - target["start_time_gmt"]).total_seconds()
    )
    if time_diff > 300:
        return False
    s_dist = float(source.get("distance_meters") or 0)
    t_dist = float(target.get("distance_meters") or 0)
    if s_dist > 0 or t_dist > 0:
        max_dist = max(s_dist, t_dist)
        if max_dist and abs(s_dist - t_dist) / max_dist > 0.05:
            return False
    return True


def _diff_activities(source_activities: list[dict], target_activities: list[dict]) -> list[dict]:
    """时间桶哈希剪枝：以目标侧 start_time_gmt 的分钟数建桶，

    源侧每条只比对 ±5 分钟（覆盖 ±300 秒窗口）命中的桶，替代 O(n*m) 嵌套循环。
    保留「一个目标条目仅匹配一次」的语义。
    """
    target_buckets: dict[int, list[dict]] = {}
    for item in target_activities:
        gmt = item.get("start_time_gmt")
        if not gmt:
            continue
        minute_bucket = int(gmt.timestamp() // 60)
        for offset in range(-5, 6):
            target_buckets.setdefault(minute_bucket + offset, []).append(item)

    matched_target_ids: set[str] = set()
    diff = []
    for item_a in source_activities:
        gmt_a = item_a.get("start_time_gmt")
        if not gmt_a:
            diff.append(item_a)
            continue
        bucket = int(gmt_a.timestamp() // 60)
        candidates = target_buckets.get(bucket, [])
        is_matched = False
        for item_b in candidates:
            if item_b["activity_id"] in matched_target_ids:
                continue
            if _is_same_activity_dict(item_a, item_b):
                matched_target_ids.add(item_b["activity_id"])
                is_matched = True
                break
        if not is_matched:
            diff.append(item_a)
    return diff


def _process_one(item: dict, source_session, target_session) -> dict:
    """单条流水线（仅在 worker 线程内做网络 IO，不碰 DB）：下载 → 上传 → 返回结果。"""
    try:
        file_data, filename = source_session.download_fit(item)
        item["_filename"] = filename
        result = target_session.upload_fit(file_data, filename)
        status = (result or {}).get("status")
        if status in ("SUCCESS", "success"):
            detail = (result or {}).get("data") or (result or {})
            target_activity_id = (
                str(detail.get("uploadId") or detail.get("id") or "")
                if isinstance(detail, dict)
                else ""
            )
            return {"status": "synced", "target_activity_id": target_activity_id or None}
        if status == "DUPLICATE_ACTIVITY":
            return {"status": "duplicate", "message": "目标平台已存在该活动"}
        return {"status": "failed", "message": (result or {}).get("message") or "上传失败"}
    except Exception as e:
        return {"status": "failed", "message": f"处理失败: {str(e)}"}


def run_quick_sync(
    db: Session,
    current_user: User,
    source_id: int,
    target_id: int,
    count: int,
) -> dict:
    """一段式一键同步：拉双端 TopN → 内存 diff → 推差异项。活动表零读写。"""
    if source_id == target_id:
        return {"status": "error", "message": "两个账号相同，不需要同步"}

    run = None
    try:
        source_connect = (
            db.query(BaseConnect)
            .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == source_id)
            .first()
        )
        target_connect = (
            db.query(BaseConnect)
            .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == target_id)
            .first()
        )
        if not source_connect:
            return {"status": "error", "message": f"源平台 {source_id} 鉴权失败"}
        if not target_connect:
            return {"status": "error", "message": f"目标平台 {target_id} 鉴权失败"}

        run = start_run(db, current_user, source_connect, target_connect, count)

        # 每个连接独立会话，不再共用全局 garth 单例
        source_session = platform_session.build_session(source_connect, db, current_user)
        target_session = platform_session.build_session(target_connect, db, current_user)

        # 列表拉取本身即是最强的 token 有效性验证，无需先 test
        source_activities = source_session.list_activities(count)
        target_activities = target_session.list_activities(count)
        run.fetched_source = len(source_activities)
        run.fetched_target = len(target_activities)
        db.add(run)
        db.commit()

        if not source_activities:
            finalize(db, run, error_message=f"源平台 {source_id} 获取最新 {count} 条数据失败")
            return {
                "status": "error",
                "message": f"源平台 {source_id} 获取最新 {count} 条数据失败",
            }
        # 目标账号为空（新账号首同步）时，源侧全部视为差异并继续上传，不再判失败
        diff_source_only = _diff_activities(source_activities, target_activities)
        run.diff_count = len(diff_source_only)
        db.add(run)
        db.commit()
        if not diff_source_only:
            finalize(db, run)
            return {
                "status": "success",
                "message": "源平台没有需要上传的数据，完成同步",
                "data": {
                    "source_count": len(source_activities),
                    "target_count": len(target_activities),
                    "diff_count": 0,
                    "uploaded": [],
                    "failed": [],
                    "run_id": run.id,
                },
            }

        # 并发（仅网络 IO）：每条「下载→上传」，结果在内存中收集
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(
            max_workers=min(len(diff_source_only), MAX_WORKERS)
        ) as executor:
            future_map = {
                executor.submit(_process_one, item, source_session, target_session): item
                for item in diff_source_only
            }
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    results[item["activity_id"]] = future.result()
                except Exception as e:
                    results[item["activity_id"]] = {
                        "status": "failed",
                        "message": f"线程异常: {str(e)}",
                    }

        # 主线程顺序落库（SQLAlchemy Session 非线程安全，禁止在线程内写）
        uploaded = []
        failures = []
        for item in diff_source_only:
            res = results.get(
                item["activity_id"], {"status": "failed", "message": "无执行结果"}
            )
            record_item(
                db,
                run.id,
                item,
                res["status"],
                res.get("message"),
                res.get("target_activity_id"),
            )
            entry = {
                "activity_id": item["activity_id"],
                "activity_name": item.get("activity_name"),
                "start_time_local": _format_local_time(item.get("start_time_local")),
                "filename": item.get("_filename"),
            }
            if res["status"] == "failed":
                failures.append({**entry, "error": res.get("message")})
            else:
                uploaded.append(
                    {
                        **entry,
                        "result": (
                            {"status": "DUPLICATE_ACTIVITY"}
                            if res["status"] == "duplicate"
                            else {"status": "SUCCESS", "uploadId": res.get("target_activity_id")}
                        ),
                    }
                )

        finalize(db, run)

        return {
            "status": "success",
            "message": (
                f"同步完成：需要同步 {len(diff_source_only)} 条，"
                f"成功 {len(uploaded)} 条，失败 {len(failures)} 条"
            ),
            "data": {
                "source_count": len(source_activities),
                "target_count": len(target_activities),
                "diff_count": len(diff_source_only),
                "uploaded": uploaded,
                "failed": failures,
                "run_id": run.id,
            },
        }
    except Exception as e:
        if run is not None:
            finalize(db, run, error_message=f"执行异常: {str(e)}")
        return {"status": "error", "message": f"执行异常: {str(e)}"}
