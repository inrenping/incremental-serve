#!/usr/bin/env python3
"""颂拓上传「形态矩阵」探针。

用途：在**能拿到有效 sessionKey** 的机器上跑一次，直接看出
``POST /v1/workout`` 到底接受哪种请求形态，从而定位 523
（``Workout was not saved because neither binary or SML was provided``）。

为什么需要它：
    2026-10-09 线上 SML XML 与原始 FIT 报完全相同的 523。本地用**假**
    sessionKey 打同一端点得到的是 **403 Forbidden**，说明 403 = 凭据无效、
    523 = 凭据有效但服务端没在它期望的位置找到载荷。所以凭据可以先排除，
    剩下的只有「请求形态」和「载荷内容」两类，这个脚本一次全试完。

用法::

    # 1) 会话自检（只读，不写任何数据，建议先跑）
    python3 scripts/probe_suunto_variants.py --session-key <sk> --check-only

    # 2) 形态矩阵（会真实尝试创建活动）
    python3 scripts/probe_suunto_variants.py \\
        --session-key <sk> --fit /path/to/xxx.fit

    # 3) 拉一份服务端自己生成的 SML 作字段级基准（只读）
    python3 scripts/probe_suunto_variants.py --session-key <sk> --dump-ref <key>

sessionKey 也可以从环境变量 ``SUUNTO_SESSION_KEY`` 读，避免在命令行历史里留痕。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from app.services import suunto_sml  # noqa: E402
from app.services.suunto_service import (  # noqa: E402
    UPLOAD_VARIANTS,
    _base_url,
    _headers,
    upload_sml,
)


def check_session(session_key: str, region: str) -> bool:
    """只读自检：GET v1/user / v1/workouts。任何一步 401/403 即判凭据无效。"""
    base = _base_url(region)
    ok = True
    for name, path in (("user", "user"), ("workouts", "workouts?since=0&limit=1&offset=0")):
        try:
            r = requests.get(base + path, headers=_headers(session_key), timeout=20)
            status = "OK" if r.status_code == 200 else "FAIL"
            if r.status_code in (401, 403):
                ok = False
            print(f"  GET /v1/{name:10s} -> HTTP {r.status_code} [{status}] "
                  f"{(r.text or '')[:110]}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"  GET /v1/{name:10s} -> ERROR {e}")
    return ok


def dump_ref(session_key: str, key: str, region: str) -> None:
    """拉服务端自己生成的 SML，作为字段级基准。"""
    base = _base_url(region)
    r = requests.get(base + f"workouts/{key}/sml", headers=_headers(session_key), timeout=60)
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
    ap.add_argument("--activity-id", type=int, default=None, help="覆盖 SML 的 ActivityType")
    args = ap.parse_args()

    if not args.session_key:
        print("缺少 sessionKey：用 --session-key 或环境变量 SUUNTO_SESSION_KEY", file=sys.stderr)
        return 2

    print(f"region = {args.region}\nbase   = {_base_url(args.region)}\n")

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

    payloads = {}
    try:
        payloads["sml"] = suunto_sml.fit_bytes_to_sml_xml(
            fit_bytes, device_source="suunto-probe", activity_id=args.activity_id
        )
        print(f"SML XML 生成成功 ({len(payloads['sml'])} bytes)")
    except Exception as e:  # noqa: BLE001
        print(f"SML XML 生成失败: {e}")
    payloads["fit"] = fit_bytes

    print("\n== 形态矩阵 ==")
    winner = None
    for label, kind, raw, field, filename, ctype in UPLOAD_VARIANTS:
        data = payloads.get(kind)
        if data is None:
            continue
        shape = ("raw body, Content-Type=" + ctype) if raw else \
                (f"multipart, field={field}, filename={filename}, part CT={ctype}")
        try:
            res = upload_sml(
                args.session_key, data, args.region,
                filename=filename or "workout.sml",
                field=field or "filePart",
                content_type=ctype, raw=raw,
            )
            print(f"[成功] {label:22s} {shape}")
            print(f"       -> HTTP {res.get('status_code')} "
                  f"{(res.get('body') or '')[:200]}")
            winner = label
            break
        except Exception as e:  # noqa: BLE001
            detail = getattr(e, "detail", str(e))
            print(f"[失败] {label:22s} {shape}")
            print(f"       -> {detail[:180]}")

    print()
    if winner:
        print(f"结论：可用形态 = {winner}")
        print("请把这个结果回填到 suunto_service.UPLOAD_VARIANTS（只留这一条）。")
        return 0
    print("结论：所有形态都失败。问题在载荷内容（SML 结构 / FIT 合规性），"
          "不在请求形态。下一步用 --dump-ref 拉真实 SML 做字段级 diff。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
