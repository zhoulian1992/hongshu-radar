"""本地 HTTP 服务：只监听 127.0.0.1，数据不出本机。

除了看板接口，还专门留了 /api/ingest —— 这是文章最后那段「浏览器插件点一下
加入监控队列，商品链接同步到飞书，软件每小时同步一次」的直接落地：
插件/书签可以把商品链接 POST 进来，只加入队列；也可以连页面上的销量一起推过来，
由服务端按同样的口径落快照。
"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import analytics, collector, db as dbmod, risk

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

# 文章里的 7 条判断维度，界面上做成可勾选的研判清单
RESEARCH_ITEMS = [
    {"key": "duration", "label": "① 做了多久", "hint": "翻最早笔记/评价/链接历史，关注短期起量且最近还在出单"},
    {"key": "notes", "label": "② 发了几篇笔记", "hint": "同样销量，笔记更少的优先研究；留意删笔记/投流/站外引流"},
    {"key": "boom", "label": "③ 爆品值", "hint": "累计已售 ÷ 今日增量，越小越先看；一天一单也会很好看"},
    {"key": "value", "label": "④ 商品价值", "hint": "价格 × 今日增量 × 占比；别把它当利润，看能不能持续卖"},
    {"key": "timing", "label": "⑤ 跟着时间走", "hint": "开学/考试/毕业/节日/年终汇报，先问为什么是现在卖"},
    {"key": "competitors", "label": "⑥ 多少人在做", "hint": "搜关键词，多找几家对比价格、内容、最近动销"},
    {"key": "blue_ocean", "label": "⑦ 别把没人做当蓝海", "hint": "先模仿再超越；没竞品也可能是真没需求"},
]


class AppState:
    def __init__(self, database: dbmod.Database, cfg: dict, config_path: str,
                 scheduler=None):
        self.db = database
        self.cfg = cfg
        self.config_path = config_path
        self.scheduler = scheduler

    def save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)


def _read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if not length:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def _csv_items(text: str) -> list[dict]:
    """飞书多维表导出 CSV → 商品字段。表头见 samples/选品表模板.csv。"""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    alias = {
        "商品名称": "title", "标题": "title", "title": "title",
        "商品链接": "url", "链接": "url", "商品ID": "id", "商品 id": "id",
        "分类": "category", "客单价": "price", "价格": "price",
        "笔记数": "notes_count", "最早笔记": "first_note_at",
        "竞争家数": "competitor_count", "时间节点": "season_tag",
        "店铺": "shop_name", "备注": "note",
    }
    items = []
    for row in reader:
        item = {}
        for k, v in row.items():
            if not k or v is None:
                continue
            key = alias.get(k.strip())
            if key:
                item[key] = v.strip() if isinstance(v, str) else v
        if item.get("title") or item.get("url") or item.get("id"):
            items.append(item)
    return items


def _resolve_id(item: dict) -> str | None:
    from .parser import ID_IN_PATH, HEX24
    raw = str(item.get("id") or item.get("url") or "")
    m = ID_IN_PATH.search(raw) or HEX24.search(raw)
    return m.group(1).lower() if m else None


def add_items(state: AppState, items: list[dict], *, source: str | None = None) -> dict:
    """统一的新增入口：解析 ID、跳过已删除、已存在只补空字段。"""
    db = state.db
    stat = {"added": 0, "duplicate": 0, "skipped": 0, "invalid": 0}
    for item in items:
        pid = _resolve_id(item)
        if not pid:
            # 没有 24 位 ID 时，用标题或链接的短哈希生成 ID，保证选品表也能导入
            import hashlib
            key = str(item.get("title") or item.get("url") or "").strip()
            if not key:
                stat["invalid"] += 1
                continue
            pid = "ref_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        if db.is_deleted(pid):
            stat["skipped"] += 1
            continue
        fields = {
            "id": pid,
            "title": (item.get("title") or item.get("name") or f"待采集 {pid[:8]}").strip(),
            "url": item.get("url"),
            "shop_name": item.get("shop_name"),
            "category": item.get("category"),
            "price": _num(item.get("price")),
            "notes_count": _num(item.get("notes_count"), as_int=True),
            "first_note_at": item.get("first_note_at"),
            "competitor_count": _num(item.get("competitor_count"), as_int=True),
            "season_tag": item.get("season_tag"),
            "source": source or item.get("source") or state.cfg.get("default_source", "demo"),
            "active": 1,
        }
        if db.upsert_product(fields):
            stat["added"] += 1
        else:
            stat["duplicate"] += 1
    db.log("info", "products", f"新增商品：{json.dumps(stat, ensure_ascii=False)}")
    return stat


def _num(value, as_int: bool = False):
    if value in (None, "", "—", "-"):
        return None
    try:
        return int(float(value)) if as_int else float(value)
    except (TypeError, ValueError):
        return None


def make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HongshuRadar/1.0"

        def log_message(self, fmt, *args):
            pass  # 静默，日志进 SQLite

        # ---------- helpers ----------
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")

        def _json(self, data, status: int = 200):
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _text(self, text, content_type="text/plain; charset=utf-8", status=200):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path: str):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.abspath(os.path.join(WEB_DIR, rel))
            if not full.startswith(WEB_DIR) or not os.path.isfile(full):
                return self._text("404", status=404)
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        # ---------- router ----------
        def _dispatch(self, method: str):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if not path.startswith("/api/"):
                    if method == "GET":
                        return self._static(path)
                    return self._json({"error": "not found"}, 404)
                return self._api(method, path, query)
            except Exception as exc:
                state.db.log("error", "api", f"{method} {path} 异常：{exc}",
                             detail=traceback.format_exc()[:2000])
                return self._json({"error": str(exc), "trace": traceback.format_exc()[-800:]},
                                  500)

        def _api(self, method: str, path: str, query: dict):
            db = state.db
            body = _read_json(self) if method == "POST" else {}

            if path == "/api/state" and method == "GET":
                products = [analytics.compute_metrics(db, p)
                            for p in db.list_products()]
                # 风险体检结果按商品 ID 关联，不按标题合并
                risk_map = db.risk_for_products()
                for p in products:
                    p["risk"] = risk_map.get(p["id"])
                today = sum(p["today_delta"] or 0 for p in products)
                return self._json({
                    "overview": {
                        "products": len(products),
                        "active": sum(1 for p in products if p["active"]),
                        "snapshots": db.snapshot_count(),
                        "today_total": today,
                        "fuzzy_revenue": round(sum(p["fuzzy_revenue"] or 0 for p in products), 2),
                        "alerts": sum(1 for p in products if p["status"] in ("异常", "已下架")),
                        "db_size": db.db_size(),
                        "demo_mode": bool(state.cfg.get("demo_mode")),
                    },
                    "products": products,
                    "scheduler": state.scheduler.status() if state.scheduler else {},
                    "research_items": RESEARCH_ITEMS,
                    "logs": db.logs(limit=8),
                })

            if path == "/api/products" and method == "POST":
                items = body.get("items")
                if items is None:
                    from .parser import parse_product_input
                    parsed = parse_product_input(body.get("text") or "")
                    items = []
                    invalid = 0
                    for p in parsed:
                        if p.error:
                            invalid += 1
                            db.log("warn", "products", f"输入未识别：{p.error}",
                                   detail=p.raw[:300])
                        else:
                            items.append({"id": p.product_id, "url": p.url,
                                          "title": f"待采集 {p.product_id[:8]}",
                                          "category": body.get("category"),
                                          "price": _num(body.get("price"))})
                    # probe：只解析 + 试采预览，绝不落库（原版要求先展示信息再确认）
                    if body.get("probe"):
                        preview = []
                        probe_source = (body.get("source")
                                        or state.cfg.get("default_source", "demo"))
                        # 试采的目的是看能不能读到数据；plugin 是被动接收型，这里改用直采
                        if probe_source == "plugin":
                            probe_source = "direct"
                        for it in items[:5]:
                            fake = {"id": it["id"], "url": it.get("url"),
                                    "title": it.get("title"), "price": it.get("price"),
                                    "source": probe_source}
                            try:
                                r = collector.collect_product(db, fake, state.cfg)
                                preview.append({
                                    "id": fake["id"], "ok": r.ok,
                                    "title": r.title, "price": r.price,
                                    "sold_raw": r.sold.raw if r.sold else None,
                                    "precise": r.sold.precise if r.sold else False,
                                    "reason": (r.sold.reason if r.sold and r.sold.reason
                                               else r.error_msg),
                                })
                            except collector.CollectError as exc:
                                preview.append({"id": fake["id"], "ok": False,
                                                "reason": exc.message})
                        return self._json({"probe": True, "parsed": len(parsed),
                                           "invalid": invalid, "preview": preview})
                    stat = add_items(state, items, source=body.get("source"))
                    stat["invalid"] += invalid
                    return self._json(stat)
                return self._json(add_items(state, items, source=body.get("source")))

            if path == "/api/import" and method == "POST":
                items = _csv_items(body.get("csv") or "")
                return self._json({"parsed": len(items), **add_items(
                    state, items, source=body.get("source") or "manual")})

            if path == "/api/ingest" and method == "POST":
                return self._json(self._ingest(body))

            if path == "/api/collect" and method == "POST":
                if not state.scheduler:
                    return self._json({"error": "调度器未启动"}, 503)
                return self._json(state.scheduler.run_round(product_ids=body.get("product_ids")))

            if path == "/api/trial" and method == "POST":
                return self._json(analytics.trial(
                    float(body.get("price") or 0), int(body.get("sold") or 0),
                    int(body.get("delta") or 0), partial=bool(body.get("partial"))))

            if path == "/api/config" and method == "GET":
                safe = dict(state.cfg)
                if isinstance(safe.get("notify"), dict):
                    safe["notify"] = {**safe["notify"],
                                      "webhook": "***" if safe["notify"].get("webhook") else ""}
                return self._json(safe)

            if path == "/api/config" and method == "POST":
                allowed = ("interval_minutes", "default_source", "fetch_command",
                           "min_wait", "max_wait", "demo_mode", "notify")
                for key in allowed:
                    if key in body:
                        if key == "notify" and isinstance(body[key], dict):
                            merged = dict(state.cfg.get("notify") or {})
                            for k, v in body[key].items():
                                if not (k == "webhook" and v == "***"):
                                    merged[k] = v
                            state.cfg[key] = merged
                        else:
                            state.cfg[key] = body[key]
                state.save_config()
                db.log("info", "config", "配置已更新")
                return self._json({"ok": True, "config": state.cfg})

            if path == "/api/logs" and method == "GET":
                return self._json({"logs": db.logs(
                    level=(query.get("level") or ["all"])[0],
                    limit=int((query.get("limit") or ["300"])[0]))})

            if path == "/api/logs" and method == "DELETE":
                db.clear_logs()
                return self._json({"ok": True})

            if path == "/api/export" and method == "GET":
                return self._text(self._export_csv(),
                                  "text/csv; charset=utf-8")

            if path == "/api/demo" and method == "POST":
                from seed_demo import seed
                return self._json(seed(db, int(body.get("count") or 8),
                                       reset=bool(body.get("reset"))))

            if path == "/api/cleanup" and method == "POST":
                return self._json(db.cleanup(
                    keep_days=int(body.get("keep_days") or 90),
                    log_days=int(body.get("log_days") or 30)))

            if path == "/api/mcp" and method == "GET":
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                return self._text(
                    "请帮我配置一个本地 MCP 服务器，名称设为 xhs-radar。\n"
                    "服务器使用 stdio 通信，命令是：python3 " + os.path.join(root, "mcp_server.py") + "\n"
                    "这个 MCP 只读本机红薯雷达数据库（商品、小时销量、每日销量、爆品值、商品价值），"
                    "不允许修改任何数据。配置完成后请调用 tools/list 验证。")

            # ---------- 上架前版权风险体检 ----------
            if path == "/api/risk/status" and method == "GET":
                return self._json({**risk.vendor_status(), "cache": {
                    "count": len(db.risk_rows()), "counts": db.risk_counts(),
                }})

            if path == "/api/risk/check" and method == "POST":
                if body.get("candidates"):
                    candidates = body["candidates"]
                elif body.get("product_ids") is not None:
                    candidates = risk.products_to_candidates(db, body["product_ids"])
                else:
                    candidates = risk.products_to_candidates(db)
                if not candidates:
                    return self._json({"error": "没有可体检的候选"}, 400)
                try:
                    return self._json(risk.check(
                        db, candidates, force=bool(body.get("force")),
                        persist=body.get("persist", True)))
                except risk.RiskError as exc:
                    db.log("error", "risk", f"体检未完成：{exc}")
                    return self._json({"error": str(exc),
                                       "vendor": risk.vendor_status()}, 503)

            if path.startswith("/api/risk/product/") and method == "GET":
                cid = urllib.parse.unquote(path[len("/api/risk/product/"):])
                row = db.get_risk(cid)
                if not row:
                    return self._json({"row": None, "result": None})
                try:
                    payload = json.loads(row["payload"]) if row["payload"] else None
                except (ValueError, TypeError):
                    payload = None
                return self._json({"row": row, "result": payload})

            if path == "/api/risk/rows" and method == "GET":
                return self._json({"rows": db.risk_rows(),
                                   "counts": db.risk_counts(),
                                   "notice": risk.DISCLAIMER})

            if path == "/api/risk/export" and method == "GET":
                return self._text(risk.export_csv(db), "text/csv; charset=utf-8")

            if path == "/api/risk/cache" and method == "DELETE":
                db.clear_risk()
                db.log("info", "risk", "体检缓存已清空")
                return self._json({"ok": True})

            if path.startswith("/api/product/"):
                pid = urllib.parse.unquote(path[len("/api/product/"):])
                product = db.get_product(pid)
                if not product:
                    return self._json({"error": "商品不存在"}, 404)
                if method == "GET":
                    date = (query.get("date") or [None])[0]
                    return self._json({
                        "product": analytics.compute_metrics(db, product),
                        "hourly": analytics.hourly_series(db, pid, date),
                        "daily": analytics.daily_series(db, pid, days=7),
                        "snapshots": db.snapshots(pid, usable_only=False)[-60:],
                        "research_items": RESEARCH_ITEMS,
                    })
                if method == "POST":
                    allowed = ("title", "price", "category", "shop_name", "url",
                               "notes_count", "first_note_at", "competitor_count",
                               "season_tag", "active", "source", "research_json")
                    patch = {k: v for k, v in body.items() if k in allowed}
                    if "research_json" in patch and not isinstance(patch["research_json"], str):
                        patch["research_json"] = json.dumps(patch["research_json"],
                                                            ensure_ascii=False)
                    db.update_product(pid, patch)
                    return self._json({"ok": True, "product": db.get_product(pid)})
                if method == "DELETE":
                    db.delete_product(pid)
                    return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)

        def _ingest(self, body: dict) -> dict:
            """浏览器插件 / 外部程序推送：只给链接 = 加入监控队列；带销量 = 顺手写快照。"""
            db = state.db
            pid = _resolve_id(body) or body.get("item_id")
            url = body.get("url") or body.get("link")
            if not pid and url:
                from .parser import parse_product_input
                parsed = parse_product_input(url)
                valid = [p for p in parsed if not p.error]
                if not valid:
                    return {"ok": False, "reason": "无法从链接解析商品 ID"}
                pid, url = valid[0].product_id, valid[0].url
            if not pid:
                return {"ok": False, "reason": "缺少 item_id 或商品链接"}
            pid = str(pid).lower()
            if db.is_deleted(pid):
                return {"ok": False, "reason": "该商品已被主动删除，不再自动加入"}

            has_sold = any(k in body for k in ("sold", "sold_raw", "cum_sold"))
            item = {
                "id": pid, "url": url,
                "title": body.get("title") or f"待采集 {pid[:8]}",
                "shop_name": body.get("shop_name"), "price": _num(body.get("price")),
                "source": body.get("source") or "plugin",
            }
            stat = add_items(state, [item], source="plugin")
            if not has_sold:
                db.log("info", "plugin", f"已加入监控队列：{item['title']}", product_id=pid)
                return {"ok": True, "action": "queued", "id": pid, **stat}

            sold_key = next((k for k in ("sold", "sold_raw", "cum_sold") if k in body), "sold")
            product = db.get_product(pid)
            result = collector._from_payload({**body, "sold": body[sold_key]}, "plugin")
            from .scheduler import current_slot
            slot = current_slot(state.cfg)
            outcome = collector.apply_result(db, product, result, slot)
            return {"ok": True, "action": "snapshot", "id": pid, "slot": slot,
                    **outcome}

        def _export_csv(self) -> str:
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["商品名称", "商品ID", "分类", "店铺", "客单价", "累计已售",
                             "今日增量", "昨日增量", "连续出单天数", "爆品值", "商品价值",
                             "模糊销售额", "笔记数", "竞争家数", "时间节点", "研判进度",
                             "名单状态", "风险级别", "口径", "状态"])
            risk_map = state.db.risk_for_products()
            for p in state.db.list_products():
                m = analytics.compute_metrics(state.db, p)
                rk = risk_map.get(m["id"]) or {}
                writer.writerow([m["title"], m["id"], m["category"] or "", m["shop_name"] or "",
                                 m["price"] if m["price"] is not None else "",
                                 m["current_sold"] if m["current_sold"] is not None else "—",
                                 m["today_delta"] if m["today_delta"] is not None else "—",
                                 m["yesterday_delta"] if m["yesterday_delta"] is not None else "—",
                                 m["streak"], m["boom_value"] if m["boom_value"] is not None else "—",
                                 m["goods_value"] if m["goods_value"] is not None else "—",
                                 m["fuzzy_revenue"] if m["fuzzy_revenue"] is not None else "—",
                                 m["notes_count"] or "", m["competitor_count"] or "",
                                 m["season_tag"] or "", f"{m['research_done']}/7",
                                 rk.get("list_status") or ("未完成" if rk.get("state") == "failed" else "未体检"),
                                 rk.get("handling_level") or "",
                                 m["note"], m["status"]])
            return "\ufeff" + out.getvalue()

    return Handler


def create_server(state: AppState, host: str = "127.0.0.1", port: int = 8777):
    handler = make_handler(state)
    return ThreadingHTTPServer((host, port), handler), handler
