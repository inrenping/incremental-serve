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

    # 3. 只测某个 FIT 文件（会真实写入账号，建议用测试账号）
    python3 scripts/probe_suunto_upload.py --session-key <key> --fit /path/to/activity.fit

    # 4. 【推荐先做这个】拉一份服务端自己生成的 SML 当结构基准（只读，不上传）
    python3 scripts/probe_suunto_upload.py --session-key <key> --dump-ref <activityKey>

第4 步是解决 523 的最短路径：读接口 ``GET /v1/workouts/{key}/sml`` 返回的
字段名与层级就是服务端内部模型本身，拿它跟我们生成的 XML 逐层对比，
缺什么立刻可见——不必去猜那份没公开的 legacy SML spec。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 这两个模块都只在真正需要时才导入：
#   - ``suunto_sml``       依赖 fitparse（FIT 解析），只有生成 XML 时才用得上
#   - ``suunto_service``   连带拉起 requests/fastapi 等一串依赖，只有发请求时才用得上
# 这样 ``--help`` / ``--dump-ref`` 在裸环境（没装 fitparse/requests）下也能跑。

# 上传成功后把服务端返回的 payload 存到这个目录（main 里按 --outdir 设置）
_DUMP_DIR: str | None = None


def _probe(session_key: str, region: str, label: str, filename: str,
           data: bytes, ctype: str = "application/octet-stream") -> dict:
    """发一次上传，打印并返回结果（不抛异常）。"""
    import requests

    from app.services import suunto_service  # 延迟导入：只在真发请求时拉依赖

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
        if _DUMP_DIR:
            os.makedirs(_DUMP_DIR, exist_ok=True)
            path = os.path.join(_DUMP_DIR, f"uploaded_{label}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            print(f"{'':>26}✅ 载荷已保存 {path}（用它反查服务端认出的结构）")

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


def _dump_reference_sml(session_key: str, region: str, activity_key: str, outdir: str) -> bool:
    """把**服务端自己生成的 SML** 拉下来存到本地，当结构基准。

    这是解决 523 最快的路：``GET /v1/workouts/{key}/sml`` 返回的是服务端认可的
    权威结构（读接口返回 JSON，但里面的字段名/层级就是服务端内部模型）。
    拿它跟我们生成的 XML 逐层对比，缺什么一目了然——不用再猜私有 spec。
    """
    import requests

    from app.services import suunto_service  # 延迟导入

    base_url = suunto_service._base_url(region)
    print(f"拉取服务端 SML 基准（activity_key={activity_key}）…")
    try:
        resp = requests.get(
            base_url + f"workouts/{activity_key}/sml",
            headers=suunto_service._headers(session_key),
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 请求异常: {e}")
        return False

    print(f"  HTTP {resp.status_code}")
    if resp.status_code >= 400:
        print(f"  {resp.text[:300]}")
        return False

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"reference_{activity_key}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"  ✅ 已保存 {path}（{len(resp.text)} 字符）")

    # 顺手把结构骨架打出来，方便和我们的 XML 对照
    try:
        data = resp.json()
        payload = data.get("payload", data) if isinstance(data, dict) else data
        print("\n  服务端 SML 骨架（key → 类型）：")
        _print_skeleton(payload, indent=4)
    except Exception as e:  # noqa: BLE001
        print(f"  （骨架打印失败: {e}）")
    return True


def _print_skeleton(node, indent: int = 0, max_depth: int = 6) -> None:
    """递归打印 JSON 的骨架（只打 key 和标量类型，不打数组内容）。

    注意 ``suunto/sml`` 这种**带斜杠的 key**：它的下一层才是 ``Sample``，
    不能因为 key 长得像标量名就当叶子跳过 —— 那恰好是要对比的核心字段。
    """
    pad = " " * indent
    if indent > max_depth * 2:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, dict):
                print(f"{pad}{k}/")
                _print_skeleton(v, indent + 2, max_depth)
            elif isinstance(v, list):
                first = v[0] if v else None
                print(f"{pad}{k}[] ({len(v)} 项)")
                if isinstance(first, (dict, list)):
                    _print_skeleton(first, indent + 2, max_depth)
            elif v is None or isinstance(v, (str, int, float, bool)):
                # 含斜杠的 key 一律展开（哪怕当前值是标量，
                # 上层多半是{"suunto/sml": {...}}这种带命名空间的包装）
                print(f"{pad}{k} = {type(v).__name__}: {str(v)[:40]}")
            else:
                print(f"{pad}{k} = {type(v).__name__}")


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
    ap.add_argument("--fit", help="要测试的 FIT 文件（与 --dump-ref 二选一）")
    ap.add_argument("--activity-id", type=int, default=1,
                    help="SML 里的 ActivityType，默认 1(running)")
    ap.add_argument("--dump-ref", metavar="ACTIVITY_KEY",
                    help="只拉取服务端自己生成的 SML（GET /workouts/{key}/sml）"
                         "存成结构基准，不做任何上传")
    ap.add_argument("--outdir", default="/tmp/suunto-probe",
                    help="基准文件保存目录，默认 /tmp/suunto-probe")
    ap.add_argument("--out", help="把JSON 结果写到文件")
    args = ap.parse_args()

    global _DUMP_DIR
    _DUMP_DIR = args.outdir

    session_key = args.session_key
    if not session_key:
        if not (args.username and args.password):
            ap.error("需要 --session-key，或同时给 --username 和 --password")
        session_key = _try_login(args.username, args.password, args.region)
        if not session_key:
            ap.error("拿不到 sessionKey")
    print(f"sessionKey: {session_key[:8]}…（长度 {len(session_key)}）\n")

    # 只拉基准模式：不上传任何东西，安全
    if args.dump_ref:
        sys.exit(0 if _dump_reference_sml(
            session_key, args.region, args.dump_ref, args.outdir) else 1)

    if not args.fit:
        ap.error("需要 --fit，或用 --dump-ref <ACTIVITY_KEY> 只拉基准")
    with open(args.fit, "rb") as f:
        fit = f.read()
    print(f"FIT: {args.fit}（{len(fit)} 字节）\n")

    print("逐个探测：")
    results = []

    # ① 先试 legacy SML XML —— suuntool 明确说 filePart 就是 SML XML，
    #    这条最可能是正解，先试它可以尽快拿到成功信号。
    try:
        from app.services import suunto_sml  # 延迟导入：只有生成 XML 时才需要 fitparse

        sml = suunto_sml.fit_bytes_to_sml_xml(
            fit, device_source="suunto-probe", activity_id=args.activity_id)
        results.append(_probe(session_key, args.region, "sml-xml-as-.sml", "activity.sml", sml,
                              ctype="application/xml"))
        # 服务端如果靠 filename 后缀判断类型，.fit 后缀 + XML 内容是个有用的对照
        results.append(_probe(session_key, args.region, "sml-xml-as-.fit", "activity.fit", sml,
                              ctype="application/octet-stream"))
        # 真实 SML 是 UTF-8 开头的 <?xml 声明；确认声明本身不影响解析
        if not sml.lstrip().startswith(b"<?xml"):
            results.append(_probe(session_key, args.region, "sml-xml+decl",
                                  "activity.sml",
                                  b'<?xml version="1.0" encoding="UTF-8"?>\n' + sml,
                                  ctype="application/xml"))
    except Exception as e:  # noqa: BLE001
        print(f"  [sml-xml] 生成失败: {e}")
        results.append({"label": "sml-xml", "ok": False, "error": str(e)})

    # ② 原始 FIT 二进制 —— 服务端错误文案提到 "binary"，
    #    且它自己有 exportFit 能力，导入端大概率也收 FIT。
    results.append(_probe(session_key, args.region, "raw-FIT-binary", "activity.fit", fit))

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

    print("\n下一步建议：")
    print("  1. 先用 --dump-ref <已有活动的 key> 拉一份服务端自己的 SML 下来，")
    print("     那是权威结构基准，比猜 spec 快得多：")
    print(f"     python3 {os.path.relpath(__file__)} --session-key <key> \\")
    print(f"       --dump-ref <activityKey> --outdir {args.outdir}")
    print("  2. 拿到基准后跟 fit_bytes_to_sml_xml 的输出逐层对比，缺什么补什么。")


if __name__ == "__main__":
    main()