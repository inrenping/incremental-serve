"""探测 Sports Tracker 上传端点POST /v1/workout 到底认什么载荷。

背景
----
我们生成的 legacy SML XML 被服务端拒绝：
    HTTP 500, code=523,
    "Workout was not saved because neither binary or SML was provided"

这条错误说明服务端在**自己嗅探载荷类型**，而不是靠 filename 或 Content-Type。
它同时检查两种格式：
    - ``binary``：原始 FIT 二进制（服务端有 GET /v1/workout/exportFit/{key} 导出 FIT，
      说明它内部就有 FIT 的解析能力，所以导入大概率也收 FIT）
    - ``SML``    ：legacy SML XML

本脚本按顺序试多种载荷，把服务端真实判定结果打出来。

用法
----
    # 1. 先登录拿 sessionKey（凭据用你的账号）
    python3 scripts/probe_suunto_upload.py --username <用户> --password <密码>

    # 2. 或者直接传已有的 sessionKey
    python3 scripts/probe_suunto_upload.py --session-key <sessionKey>

    # 只测某个 FIT 文件
    python3 scripts/probe_suunto_upload.py --fit /path/to/activity.fit
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import suunto_service  # noqa: E402
from app.services import suunto_sml  # noqa: E402


def _probe(session_key: str, region: str, label: str, filename: str,
           data: bytes, ctype: str = "application/octet-stream") -> dict:
    """发一次上传，打印并返回结果（不抛异常）。"""
    import requests

    base_url = suunto_service._base_url(region)
    files = {"filePart": (filename, data, ctype)}
    try:
        resp = requests.post(
            base_url + "workout",
            files=files,
            headers=suunto_service._headers(session_key),
            timeout=90,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [{label:<22}] 请求异常: {e}")
        return {"label": label, "ok": False, "error": str(e)}

    body = (resp.text or "").strip()
    parsed = None
    try:
        parsed = resp.json()
    except Exception:  # noqa: BLE001
        pass

    # Asko 信封：{"error":..., "payload":..., "metadata":...}
    err = parsed.get("error") if isinstance(parsed, dict) else None
    payload = parsed.get("payload") if isinstance(parsed, dict) else None
    ok = resp.status_code < 400 and not err

    verdict = "✅ 成功" if ok else "❌ 失败"
    detail = json.dumps(err, ensure_ascii=False) if err else (body[:200] or "-")
    print(f"  [{label:<22}] {verdict} HTTP {resp.status_code} :: {detail}")
    if ok and isinstance(payload, dict):
        print(f"{'':>26}返回 key={payload.get('key')} "
              f"activityId={payload.get('activityId')} "
              f"distance={payload.get('totalDistance')} "
              f"time={payload.get('totalTime')}")

    return {
        "label": label,
        "filename": filename,
        "content_type": ctype,
        "status_code": resp.status_code,
        "ok": ok,
        "error": err,
        "body": body[:500],
        "payload": payload,
    }


def _try_login(username: str, password: str, region: str) -> str | None:
    """尝试登录拿 sessionKey。失败返回 None。"""
    from app.schemas.base_connect import SuuntoLoginRequest

    class _Conn:
        """最小 BaseConnect 替身，够 perform_login 用。"""
        id = 0
        user_id = 0
        account = username
        access_token = None
        refresh_token = None
        region = region
        extra = None
        status = "active"

        def __init__(self):
            self.source_type = "suunto"

    try:
        cfg = suunto_service.perform_login(
            _Conn(), SuuntoLoginRequest(username=username, password=password)
        )
        return (cfg or {}).get("access_token") if isinstance(cfg, dict) else (
            getattr(cfg, "access_token", None)
        )
    except Exception as e:  # noqa: BLE001
        print(f"登录失败: {e}")
        return None


def main():
    ap = argparse.ArgumentParser(description="探测颂拓上传端点接受哪种载荷")
    ap.add_argument("--session-key", help="已有的 sessionKey（优先用这个）")
    ap.add_argument("--username", help="颂拓账号（用于登录换sessionKey）")
    ap.add_argument("--password", help="颂拓密码")
    ap.add_argument("--region", default="intl", choices=["intl", "cn"])
    ap.add_argument("--fit", required=True, help="要测试的 FIT 文件")
    ap.add_argument("--activity-id", type=int, default=1,
                    help="SML 里的 ActivityType，默认 1(running)")
    ap.add_argument("--out", help="把JSON 结果写到文件")
    args = ap.parse_args()

    session_key = args.session_key
    if not session_key:
        if not (args.username and args.password):
            ap.error("需要 --session-key，或同时给 --username 和 --password")
        session_key = _try_login(args.username, args.password, args.region)
        if not session_key:
            ap.error("拿不到 sessionKey")
    print(f"sessionKey: {session_key[:8]}…（长度 {len(session_key)}）\n")

    with open(args.fit, "rb") as f:
        fit = f.read()
    print(f"FIT: {args.fit}（{len(fit)} 字节）\n")

    print("逐个探测：")
    results = []

    # ① 原始 FIT 二进制 —— 服务端明确提到 "binary"，且它自己有 exportFit 能力
    results.append(_probe(session_key, args.region, "raw-FIT-binary", "activity.fit", fit))

    # ② 生成的 legacy SML XML
    try:
        sml = suunto_sml.fit_bytes_to_sml_xml(
            fit, device_source="suunto-probe", activity_id=args.activity_id)
        results.append(_probe(session_key, args.region, "sml-xml-default", "activity.sml", sml))
        results.append(_probe(session_key, args.region, "sml-xml-as-.fit", "activity.fit", sml,
                              ctype="application/octet-stream"))
    except Exception as e:  # noqa: BLE001
        print(f"  [sml-xml]生成失败: {e}")
        results.append({"label": "sml-xml", "ok": False, "error": str(e)})

    # ③ 空载荷 / 垃圾内容 —— 用于确认 523 的触发条件（不发垃圾，避免污染账号）
    print("\n注意：空/垃圾载荷会往账号里写脏数据，已跳过。")

    print("\n" + "=" * 70)
    ok_labels = [r["label"] for r in results if r.get("ok")]
    if ok_labels:
        print("✅ 可用的载荷形态：", ", ".join(ok_labels))
        print("→ 改 upload_fit_to_suunto 用这个形态即可")
    else:
        print("❌ 所有形态都被拒。逐条错误：")
        for r in results:
            print(f"   {r['label']}: {r.get('error') or r.get('body')}")
        print("\n→ 说明 SML 结构还缺东西（如 service header / legacy workout sections），")
        print("  或 FIT 二进制也需特定包装。")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n结果已写入 {args.out}")


if __name__ == "__main__":
    main()