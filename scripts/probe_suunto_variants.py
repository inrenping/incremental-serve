#!/usr/bin/env python3
"""颂拓上传「形态矩阵」探针 —— 自包含版（只依赖 requests + app.services.suunto_sml）。

用途：在**能拿到有效 sessionKey** 的机器上跑一次，直接看出
``POST /v1/workout`` 到底接受哪种请求形态，从而定位 523
（``Workout was not saved because neither binary or SML was provided``）。

为什么需要它：
    2026-10-09 线上 SML XML 与原始 FIT 报完全相同的 523。本地用**假**
    sessionKey 打同一端点得到的是 **403 Forbidden**，说明 403 = 凭据无效、
    523 = 凭据有效但服务端没在它期望的位置找到载荷。凭据可以先排除，
    剩下的只有「请求形态」和「载荷内容」两类，这个脚本一次全试完。

脚本刻意**不 import `suunto_service`**：这样在生产服务器上只要把这一个文件
抓下来（`git checkout origin/dev -- scripts/probe_suunto_variants.py`）就能跑，
不用先合 master、不用重启服务。

用法::

    # 1) 会话自检（只读，不写任何数据，建议先跑）
    python3 scripts/probe_suunto_variants.py --session-key <sk> --check-only

    # 2) 形态矩阵（会真实尝试创建活动，成功即停，最多创建 1 条）
    python3 scripts/probe_suunto_variants.py \\
        --session-key <sk> --fit /path/to/xxx.fit

    # 3) 拉服务端自己生成的 SML 作字段级基准（只读）
    python3 scripts/probe_suunto_variants.py --session-key <sk> --dump-ref <key>

sessionKey 也可以从环境变量 ``SUUNTO_SESSION_KEY`` 读，避免留在命令行历史里。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from app.services import suunto_sml  # noqa: E402

USER_AGENT = "com.stt.android.suunto/6008013"
INTL_BASE = "https://api.sports-tracker.com/apiserver/v1/"

# 与 suunto_service.UPLOAD_VARIANTS 保持一致（此处内联以便独立运行）
# (label, 载荷类型, 是否 raw body, multipart 字段名, filename, Content-Type)
UPLOAD_VARIANTS = [
    ("raw-octet-sml", "sml", True, None, None, "application/octet-stream"),
    ("raw-xml-sml", "sml", True, None, None, "text/xml"),
    ("part-filePart-octet", "sml", False, "filePart", "workout.sml",
     "application/octet-stream"),
    ("part-filePart-xml", "sml", False, "filePart", "workout.sml",
     "application/xml"),
    ("part-filePart-textxml", "sml", False, "filePart", "workout.sml", "text/xml"),
    ("part-file-sml", "sml", False, "file", "workout.sml",
     "application/octet-stream"),
    ("part-sml-sml", "sml", False, "sml", "workout.sml",
     "application/octet-stream"),
    ("raw-octet-fit", "fit", True, None, None, "application/octet-stream"),
    ("part-binary-fit", "fit", False, "binary", "activity.fit",
     "application/octet-stream"),
    ("part-filePart-fit", "fit", False, "filePart", "activity.fit",
     "application/octet-stream"),
]


def base_url(region: str) -> str:
    region = (region or "intl").lower()
    if region == "cn":
        url = os.getenv("SUUNTO_CN_BASE_URL", "")
        if not url:
            raise SystemExit("国内版需设置环境变量 SUUNTO_CN_BASE_URL")
        return url.rstrip("/") + "/"
    return os.getenv("SUUNTO_INTL_BASE_URL", INTL_BASE).rstrip("/") + "/"


def headers(session_key: str, content_type: str = None) -> dict:
    h = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    if session_key:
        h["STTAuthorization"] = session_key
    if content_type:
        h["Content-Type"] = content_type
    return h


def check_session(session_key: str, region: str) -> bool:
    """只读自检：GET v1/workouts / v1/user。任何一步 401/403 即判凭据无效。"""
    base = base_url(region)
    ok = True
    for name, path in (("workouts", "workouts?since=0&limit=1&offset=0"),
                       ("user", "user")):
        try:
            r = requests.get(base + path, headers=headers(session_key), timeout=25)
            if r.status_code in (401, 403):
                ok = False
            print(f"  GET /v1/{name:9s} -> HTTP {r.status_code} "
                  f"{'OK' if r.status_code == 200 else 'FAIL'}  {(r.text or '')[:100]}")
        except Exception as e:  # noqa: BLE001
            print(f"  GET /v1/{name:9s} -> ERROR {e}")
    print("  提示：401/403 = 凭据无效；能拿到 523 才说明凭据有效。")
    return ok


def dump_ref(session_key: str, key: str, region: str) -> None:
    base = base_url(region)
    r = requests.get(base + f"workouts/{key}/sml", headers=headers(session_key),
                     timeout=60)
    print(f"HTTP {r.status_code}")
    text = r.text or ""
    print(text[:4000])
    out = f"/tmp/suunto_ref_{key}.json"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n完整响应已写入 {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session-key", default=os.getenv("SUUNTO_SESSION_KEY", ""))
    ap.add_argument("--region", default=os.getenv("SUUNTO_REGION", "intl"))
    ap.add_argument("--fit", help="用于构造载荷的 FIT 文件")
    ap.add_argument("--check-only", action="store_true", help="只做会话自检，不发上传请求")
    ap.add_argument("--dump-ref", metavar="WORKOUT_KEY",
                    help="拉取该活动服务端生成的 SML（只读）")
    ap.add_argument("--activity-id", type=int, default=None, help="覆盖 SML ActivityType")
    args = ap.parse_args()

    if not args.session_key:
        print("缺少 sessionKey：用 --session-key 或环境变量 SUUNTO_SESSION_KEY",
              file=sys.stderr)
        return 2

    print(f"region = {args.region}\nbase   = {base_url(args.region)}\n")

    print("== 会话自检 ==")
    if not check_session(args.session_key, args.region):
        print("\n凭据无效（401/403）。先重新绑定/登录颂拓账号再排查格式，"
              "否则改再多代码也是白改。")
        return 1
    print("凭据有效。\n")

    if args.dump_ref:
        print(f"== 拉取参考 SML ({args.dump_ref}) ==")
        dump_ref(args.session_key, args.dump_ref, args.region)
        return 0

    if args.check_only:
        return 0

    if not args.fit:
        print("需要 --fit <FIT 文件> 才能构造载荷", file=sys.stderr)
        return 2

    fit_bytes = open(args.fit, "rb").read()
    print(f"FIT: {args.fit} ({len(fit_bytes)} bytes)")

    payloads = {"fit": fit_bytes}
    try:
        payloads["sml"] = suunto_sml.fit_bytes_to_sml_xml(
            fit_bytes, device_source="suunto-probe", activity_id=args.activity_id
        )
        print(f"SML XML 生成成功 ({len(payloads['sml'])} bytes)")
    except Exception as e:  # noqa: BLE001
        print(f"SML XML 生成失败: {e}")

    print("\n== 形态矩阵 ==")
    base = base_url(args.region)
    winner = None
    for label, kind, raw, field, filename, ctype in UPLOAD_VARIANTS:
        data = payloads.get(kind)
        if data is None:
            continue
        shape = (f"raw body, CT={ctype}") if raw else \
                (f"multipart field={field} filename={filename} CT={ctype}")
        try:
            if raw:
                r = requests.post(base + "workout", data=data,
                                  headers=headers(args.session_key, ctype), timeout=60)
            else:
                r = requests.post(base + "workout",
                                  files={field: (filename, data, ctype)},
                                  headers=headers(args.session_key), timeout=60)
            body = (r.text or "")[:220]
            if 200 <= r.status_code < 300:
                print(f"[成功] {label:22s} {shape}")
                print(f"       -> HTTP {r.status_code} {body}")
                winner = label
                break
            print(f"[失败] {label:22s} {shape}")
            print(f"       -> HTTP {r.status_code} {body}")
        except Exception as e:  # noqa: BLE001
            print(f"[失败] {label:22s} {shape}")
            print(f"       -> ERROR {e}")

    print()
    if winner:
        print(f"结论：可用形态 = {winner}")
        print("回填 suunto_service.UPLOAD_VARIANTS，只留这一条即可。")
        return 0
    print("结论：所有形态都失败。问题在载荷内容（SML 结构 / FIT 合规性），"
          "不在请求形态。下一步用 --dump-ref 拉真实 SML 做字段级 diff。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
