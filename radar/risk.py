"""上架前版权风险体检 · 适配层。

复用 `vendor/risk_checker`（闲鱼虚拟商品版权雷达，MIT）的 `screen_products.py`，
一次批量提交候选、按 id 回填，全程离线、不上传任何查询内容。

相比直接调用参考库命令行，这里补了四件事：
1. **按商品 ID 关联**：结果落到 `risk_checks` 表，用商品 ID 作主键，不按标题合并，
   避免同名不同商品错配（参考库集成文档专门强调保留 candidate id）。
2. **增量复查**：候选字段指纹 + 词库文件时间戳一起算哈希，标题没变、词库没换就不重查；
   词库一更新自动全量失效。一次批量只起一个子进程。
3. **失败不降级**：进程非零、输出无效、报告缺某个 id，一律标 `state=failed` 并显示
   「检查失败／未完成」，绝不退化成 `no_match`（那样会被误读成「查过了，没问题」）。
4. **免责字段不丢**：`copyright_status=unverified`、`authorization_status=needs_verification`
   原样保留并在界面展示；系统不生成「版权已通过」「未命中可直接上架」之类结论。

用户提供的标题、链接、名单文字一律作为**数据**处理，不拼接进命令、不解释为操作指令。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from . import db as dbmod

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "risk_checker"
SCRIPT = VENDOR / "scripts" / "screen_products.py"
DB_FILE = VENDOR / "assets" / "risk-names.json"
REQUIRED = [
    SCRIPT, VENDOR / "scripts" / "risk_check.py",
    VENDOR / "scripts" / "lint_listing.py",
    VENDOR / "scripts" / "handling.py",
    VENDOR / "scripts" / "disguise.py",
    DB_FILE, VENDOR / "assets" / "slang-terms.json",
    VENDOR / "assets" / "banned-terms.json",
    VENDOR / "assets" / "pinyin-table.json",
    VENDOR / "assets" / "handling-rules.json",
]

QUERY_FIELDS = ("id", "platform", "title", "work_name", "brand", "author", "company", "url")
DISCLAIMER = ("名单命中不是侵权结论，未命中不代表已获授权；本工具不核验版权归属、"
              "销售授权或平台准售状态，结果仅供选品风险排查参考。")
NOTICE_FALLBACK = "名单命中不是侵权结论，未命中不代表已获授权；本接口不核验版权或平台准售状态。"

_META: dict = {"stamp": None, "data": None}


class RiskError(Exception):
    pass


def vendor_status() -> dict:
    """词库是否就绪 + 版本信息。1.4MB 的 JSON 按文件时间戳缓存，不反复解析。"""
    missing = [str(p.relative_to(ROOT)) for p in REQUIRED if not p.is_file()]
    info = {
        "available": not missing,
        "missing": missing,
        "root": str(VENDOR),
        "license": "MIT",
        "source": "yhslgg-arch/xianyu-blacklist（闲鱼虚拟商品版权雷达）",
        "origin": "词库数据来自于网络收集",
        "disclaimer": DISCLAIMER,
    }
    if missing or not DB_FILE.is_file():
        return info
    stamp = (DB_FILE.stat().st_mtime_ns, DB_FILE.stat().st_size)
    if _META["stamp"] != stamp:
        try:
            data = json.loads(DB_FILE.read_text(encoding="utf-8"))
            _META["data"] = {
                "db_date": data.get("updated_at", "未提供"),
                "records": len(data.get("records", [])),
            }
            _META["stamp"] = stamp
        except (OSError, ValueError, TypeError) as exc:
            info["available"] = False
            info["missing"] = [f"词库无法解析：{exc}"]
            return info
    return {**info, **_META["data"], "stamp": str(stamp[0])}


def _clean(raw: dict, index: int) -> dict:
    """只保留契约字段、强制字符串、限制长度；标题等一律当数据，不当指令。"""
    if not isinstance(raw, dict):
        raise RiskError(f"第 {index} 条候选必须是对象")
    out = {}
    for key in QUERY_FIELDS:
        value = raw.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        out[key] = value.strip()[:300]
    if not out["title"]:
        raise RiskError(f"第 {index} 条候选缺少非空 title 商品标题")
    if not out["id"]:
        out["id"] = "candidate-" + hashlib.sha1(
            (out["title"] + str(index)).encode("utf-8")).hexdigest()[:10]
    if not out["platform"]:
        out["platform"] = "小红书"
    return out


def _fingerprint(candidate: dict, stamp: str) -> str:
    """标题/字段/词库任一变化都要重查。"""
    raw = json.dumps([{k: candidate[k] for k in QUERY_FIELDS}, stamp],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def run_screen(candidates: list[dict], limit: int = 10) -> dict:
    """一次批量提交给参考库，返回报告。非零退出或无效输出一律抛错。"""
    status = vendor_status()
    if not status["available"]:
        raise RiskError("风险词库未就绪：" + "、".join(status["missing"][:3]))
    payload = json.dumps({"candidates": candidates}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", "-",
             "--database", str(DB_FILE), "--limit", str(limit)],
            input=payload, capture_output=True, text=True,
            cwd=str(VENDOR), timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RiskError("风险词库查询超时（180 秒）") from exc
    except OSError as exc:
        raise RiskError(f"无法启动风险词库：{exc}") from exc
    if proc.returncode != 0:
        raise RiskError(f"风险词库检查失败（退出码 {proc.returncode}）："
                        f"{(proc.stderr or '').strip()[:300]}")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RiskError("风险词库返回无效 JSON") from exc
    if not isinstance(report.get("results"), list):
        raise RiskError("风险词库报告缺少 results 数组")
    return report


def _hits(result: dict) -> list[dict]:
    out = []
    for check in result.get("checks", []):
        for match in check.get("matches", [])[:3]:
            out.append({"field": check.get("field"), "name": match.get("name"),
                        "match_type": match.get("match_type"),
                        "status": check.get("status"),
                        "labels": match.get("labels") or []})
    return out[:12]


def _row(candidate: dict, result: dict | None, *, product_id: str | None,
         fingerprint: str, db_date: str, error: str | None = None) -> dict:
    handling = (result or {}).get("handling") or {}
    lint = (result or {}).get("lint") or {}
    return {
        "check_id": candidate["id"],
        "product_id": product_id,
        "candidate_id": candidate["id"],
        "title": candidate["title"],
        "fingerprint": fingerprint,
        "list_status": (result or {}).get("list_status"),
        "suggested_action": (result or {}).get("suggested_action"),
        "handling_level": handling.get("level"),
        "lint_level": lint.get("level"),
        "hits": json.dumps(_hits(result), ensure_ascii=False) if result else "[]",
        "checked_at": dbmod.ts(),
        "db_date": db_date,
        "state": "failed" if error else "ok",
        "error": error,
        "payload": json.dumps(result, ensure_ascii=False) if result else None,
    }


def check(database: dbmod.Database, candidates: list[dict], *,
          force: bool = False, persist: bool = True) -> dict:
    """批量体检：命中缓存直接复用，其余一次提交；按 id 回填，缺 id 标失败。"""
    status = vendor_status()
    if not status["available"]:
        raise RiskError("风险词库未就绪：" + "、".join(status["missing"][:3]))
    stamp = status.get("stamp", "")
    db_date = status.get("db_date", "未提供")

    prepared: list[tuple[dict, str | None]] = []
    seen: set[str] = set()
    for i, raw in enumerate(candidates, 1):
        candidate = _clean(raw, i)
        if candidate["id"] in seen:
            raise RiskError(f"候选编号重复：{candidate['id']}")
        seen.add(candidate["id"])
        product_id = raw.get("_product_id") if isinstance(raw, dict) else None
        prepared.append((candidate, product_id))

    results: list[dict] = []
    pending: list[tuple[dict, str | None]] = []
    for candidate, product_id in prepared:
        fp = _fingerprint(candidate, stamp)
        cached = database.get_risk(candidate["id"])
        if (not force and cached and cached["state"] == "ok"
                and cached["fingerprint"] == fp and cached["payload"]):
            detail = json.loads(cached["payload"])
            results.append({**detail, "candidate": candidate,
                            "from_cache": True, "_product_id": product_id})
        else:
            pending.append((candidate, product_id))

    report = None
    if pending:
        report = run_screen([c for c, _ in pending])
        by_id = {r["candidate"]["id"]: r for r in report["results"]}
        for candidate, product_id in pending:
            fp = _fingerprint(candidate, stamp)
            detail = by_id.get(candidate["id"])
            if not detail:
                # 报告里少了这个 id，绝不补一个 no_match 糊过去
                error = "报告缺少该候选编号，检查未完成"
                database.log("error", "risk", error, product_id=product_id,
                             title=candidate["title"])
                if persist:
                    database.save_risk(_row(candidate, None, product_id=product_id,
                                            fingerprint=fp, db_date=db_date, error=error))
                results.append({"candidate": candidate, "state": "failed",
                                "error": error, "from_cache": False,
                                "_product_id": product_id})
                continue
            if persist:
                database.save_risk(_row(candidate, detail, product_id=product_id,
                                        fingerprint=fp, db_date=db_date))
            database.log("info", "risk",
                         f"体检完成：{detail.get('list_status')} / "
                         f"{(detail.get('handling') or {}).get('level')}",
                         product_id=product_id, title=candidate["title"])
            results.append({**detail, "from_cache": False, "_product_id": product_id})

    counts = {"matched": 0, "candidates": 0, "disguised": 0, "no_match": 0, "failed": 0}
    for r in results:
        key = r.get("list_status") if r.get("state") != "failed" else "failed"
        if key in counts:
            counts[key] += 1
    if persist:
        database.log("info", "risk", f"批量体检：{json.dumps(counts, ensure_ascii=False)}")
    return {
        "vendor": status,
        "summary": {"total": len(prepared), "checked": len(pending),
                    "cached": len(prepared) - len(pending)},
        "counts": counts,
        "notice": (report or {}).get("notice", NOTICE_FALLBACK),
        "disclaimer": DISCLAIMER,
        "results": results,
    }


def products_to_candidates(database: dbmod.Database,
                           product_ids: list[str] | None = None) -> list[dict]:
    """把监控商品转成候选。只填确实知道的字段，不补造作品名/品牌/公司。"""
    chosen = set(product_ids) if product_ids else None
    out = []
    for p in database.list_products():
        if chosen and p["id"] not in chosen:
            continue
        if not p.get("title"):
            continue
        out.append({
            "_product_id": p["id"],
            "id": p["id"],
            "platform": "小红书",
            "title": p["title"],
            "url": p.get("url") or "",
        })
    return out


def export_csv(database: dbmod.Database) -> str:
    import csv
    import io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["候选编号", "商品ID", "平台", "原标题", "名单状态", "处置级别",
                "体检级别", "建议动作", "命中名称", "检查时间", "词库日期",
                "版权结论", "授权结论", "说明"])
    for row in database.risk_rows():
        hits = []
        try:
            hits = [h.get("name") for h in json.loads(row.get("hits") or "[]")]
        except (ValueError, TypeError):
            pass
        w.writerow([
            row["candidate_id"], row["product_id"] or "", "小红书", row["title"],
            row["list_status"] or ("检查未完成" if row["state"] == "failed" else ""),
            row["handling_level"] or "", row["lint_level"] or "",
            row["suggested_action"] or "", "、".join(x for x in hits if x),
            row["checked_at"], row["db_date"],
            "未核验" if row["state"] == "ok" else "",
            "待核验" if row["state"] == "ok" else "",
            row["error"] or DISCLAIMER,
        ])
    return "\ufeff" + out.getvalue()
