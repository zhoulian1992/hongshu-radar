#!/usr/bin/env python3
"""只读 MCP 数据服务（stdio，零第三方依赖）。

给 AI 助手读本机监控数据用，与界面共用同一套统计函数，避免口径不一致。
不提供任何增删改工具，数据库以 SQLite 只读模式打开。

配置示例（见 /api/mcp 返回的说明文本）：
  {"mcpServers": {"xhs-radar": {"command": "python3",
    "args": ["/绝对路径/mcp_server.py"]}}}
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from radar import analytics, db as dbmod  # noqa: E402

TOOLS = [
    {"name": "get_overview", "description": "监控概览：商品数、今日总增量、模糊销售额、快照数",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_products", "description": "商品看板：累计已售、今日/昨日/上小时增量、爆品值、商品价值、连续出单天数、口径提示",
     "inputSchema": {"type": "object", "properties": {
         "keyword": {"type": "string", "description": "按名称/店铺/ID 过滤"},
         "sort_by": {"type": "string", "description": "goods_value|boom_value|today_delta|current_sold"}}}},
    {"name": "get_product_sales", "description": "指定商品的小时/每日销量序列",
     "inputSchema": {"type": "object", "properties": {
         "product_id": {"type": "string"}, "days": {"type": "integer"},
         "date": {"type": "string", "description": "YYYY-MM-DD，返回该日 24 小时"}},
         "required": ["product_id"]}},
    {"name": "get_ranking", "description": "按爆品值或商品价值排序，返回优先研究顺序",
     "inputSchema": {"type": "object", "properties": {
         "by": {"type": "string", "description": "boom_value|goods_value"},
         "limit": {"type": "integer"}}}},
    {"name": "search_products", "description": "搜索商品",
     "inputSchema": {"type": "object", "properties": {"keyword": {"type": "string"}},
                     "required": ["keyword"]}},
]


def _db() -> dbmod.Database:
    cfg_path = os.path.join(ROOT, "config.json")
    db_path = "data/radar.db"
    if os.path.isfile(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            db_path = json.load(f).get("db_path", db_path)
    if not os.path.isabs(db_path):
        db_path = os.path.join(ROOT, db_path)
    return dbmod.Database(db_path, read_only=True)


def call(name: str, args: dict) -> dict:
    db = _db()
    products = [analytics.compute_metrics(db, p) for p in db.list_products()]
    meta = {"口径": "销量均为相邻累计已售快照的差值；缺基线显示 —，不伪造 0",
            "时间": analytics._s(analytics.now()), "时区": "Asia/Shanghai"}

    if name == "get_overview":
        return {**meta, "商品数": len(products),
                "监控中": sum(1 for p in products if p["active"]),
                "快照数": db.snapshot_count(),
                "今日总增量": sum(p["today_delta"] or 0 for p in products),
                "模糊销售额": round(sum(p["fuzzy_revenue"] or 0 for p in products), 2)}

    if name == "list_products":
        kw = (args.get("keyword") or "").lower()
        rows = [p for p in products if not kw or kw in (
            (p["title"] or "") + (p["shop_name"] or "") + p["id"]).lower()]
        sort_by = args.get("sort_by") or "goods_value"
        if sort_by == "boom_value":
            # 爆品值越小越优先看；没有值的排最后
            rows.sort(key=lambda p: p["boom_value"] if p["boom_value"] is not None else 1e18)
        else:
            rows.sort(key=lambda p: (p.get(sort_by) if p.get(sort_by) is not None else -1),
                      reverse=True)
        return {**meta, "排序": sort_by, "products": [_slim(p) for p in rows]}

    if name == "get_product_sales":
        pid = args["product_id"]
        product = db.get_product(pid)
        if not product:
            return {"error": "商品不存在", "product_id": pid}
        return {**meta, "product": _slim(analytics.compute_metrics(db, product)),
                "daily": analytics.daily_series(db, pid, days=int(args.get("days") or 7)),
                "hourly": analytics.hourly_series(db, pid, args.get("date"))}

    if name == "get_ranking":
        by = args.get("by") or "goods_value"
        rows = [p for p in products if p.get(by) is not None]
        rows.sort(key=lambda p: p[by], reverse=(by == "goods_value"))
        return {**meta, "排序依据": by,
                "items": [{"rank": i, **_slim(p),
                           "提示": "样本过小，先看能否连续出单" if p["sample_small"] else ""}
                          for i, p in enumerate(rows[:int(args.get("limit") or 20)], 1)]}

    if name == "search_products":
        kw = (args.get("keyword") or "").lower()
        return {**meta, "products": [_slim(p) for p in products
                                    if kw in ((p["title"] or "") + p["id"]).lower()]}
    return {"error": f"未知工具：{name}"}


def _slim(p: dict) -> dict:
    keys = ("id", "title", "category", "price", "current_sold", "today_delta",
            "yesterday_delta", "hour_delta", "boom_value", "goods_value",
            "fuzzy_revenue", "streak", "partial", "sample_small", "note",
            "status", "imprecise", "imprecise_raw", "notes_count",
            "competitor_count", "season_tag", "research_done")
    return {k: p.get(k) for k in keys}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xhs-radar", "version": "1.0.0"}}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            params = req.get("params") or {}
            try:
                data = call(params.get("name", ""), params.get("arguments") or {})
                resp = {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False,
                                                                    default=str)}]}}
            except Exception as exc:
                resp = {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"查询失败：{exc}"}], "isError": True}}
        elif method and method.startswith("notifications/"):
            continue
        else:
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
