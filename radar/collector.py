"""采集层：可插拔适配器 + 失败分类 + 下架识别。

原版是「轻量接口 → 静默浏览器 → 可见浏览器」三级兜底（Windows/Playwright）。
Mac 上不预装 Playwright，这里改成同构但更自由的三条路：
  1. direct   轻量接口直采（标准库 urllib）
  2. command  你自己写的采集命令/浏览器脚本，输出 JSON 即可（等价于浏览器兜底）
  3. plugin   浏览器插件/外部程序主动 POST 数据进来，本服务不主动抓
另加 demo 演示源，用于在没有登录态的情况下先把全链路跑通验证。

不变的底线：失败不写快照、模糊值不计算、明确业务特征才判下架、461 触发冷却。
"""
from __future__ import annotations

import json
import random
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta

from . import analytics, db as dbmod
from .parser import (DETAIL_API, UA, SoldReading, detect_delisted, parse_detail,
                     parse_sold_text)

SOLD_LIMIT = 10000          # 超过 1 万后页面只给模糊值，原版直接移出监控
COOLDOWN_MINUTES = 15       # 461 风控后的冷却时间


class CollectError(Exception):
    def __init__(self, kind: str, message: str, detail: str | None = None):
        super().__init__(message)
        self.kind = kind        # temporary | blocked | delisted | invalid
        self.message = message
        self.detail = detail


@dataclass
class Result:
    ok: bool
    source: str
    sold: SoldReading | None = None
    title: str | None = None
    shop_name: str | None = None
    shop_id: str | None = None
    cover: str | None = None
    price: float | None = None
    shop_sold: int | None = None
    fans: int | None = None
    stock_status: int | None = None
    deliverable: int | None = None
    delisted_reason: str | None = None
    error_kind: str | None = None
    error_msg: str | None = None
    error_detail: str | None = None
    raw_text: str = ""


def _from_payload(payload, source: str) -> Result:
    """把接口原始响应或「已解析好的扁平 JSON」统一转成 Result。

    扁平 JSON 形如：
      {"item_id":"...", "title":"...", "sold":"已售2210", "price":9.9, "shop_name":"..."}
    """
    if not isinstance(payload, dict):
        raise CollectError("invalid", "采集返回的不是 JSON 对象")
    data = payload.get("data")
    is_api_response = isinstance(data, dict) and "template_data" in data
    if is_api_response or (payload.get("success") is not None and "data" in payload):
        detail = parse_detail(payload)
        if not detail.ok:
            raise CollectError("temporary",
                               detail.sold.reason or "接口数据不完整",
                               json.dumps(payload, ensure_ascii=False)[:800])
        return Result(
            ok=True, source=source, sold=detail.sold, title=detail.title,
            shop_name=detail.shop_name, shop_id=detail.shop_id, cover=detail.cover,
            price=detail.price, shop_sold=detail.shop_sold, fans=detail.fans,
            stock_status=detail.stock_status, deliverable=detail.deliverable,
            delisted_reason=detail.delisted_reason,
        )

    # 扁平结构（插件 / 自定义脚本 / 手动注入）
    sold = parse_sold_text(payload.get("sold", payload.get("sold_raw")))
    reason = detect_delisted(payload, payload.get("page_text"))
    return Result(
        ok=True, source=source, sold=sold,
        title=payload.get("title"), shop_name=payload.get("shop_name"),
        shop_id=payload.get("shop_id"), cover=payload.get("cover"),
        price=payload.get("price"), shop_sold=payload.get("shop_sold"),
        fans=payload.get("fans"), stock_status=payload.get("stock_status"),
        deliverable=payload.get("deliverable"),
        delisted_reason=reason,
        raw_text=str(payload.get("page_text") or ""),
    )


def collect_direct(product: dict, timeout: float = 10.0) -> Result:
    url = DETAIL_API.format(item_id=product["id"])
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.xiaohongshu.com/goods-detail/{product['id']}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 461:
            raise CollectError("blocked", "HTTP 461：请求环境受限，进入冷却") from exc
        raise CollectError("temporary", f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise CollectError("temporary", f"网络异常：{type(exc).__name__}: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CollectError("temporary", "返回体不是合法 JSON", body[:500]) from exc
    result = _from_payload(payload, "direct")
    result.raw_text = body[:3000]
    return result


def collect_command(product: dict, command: str, timeout: float = 60.0) -> Result:
    """自定义采集命令。命令里可用 {item_id} / {url} 占位，stdout 必须是 JSON。"""
    if not command:
        raise CollectError("invalid", "未配置 fetch_command")
    url = product.get("url") or f"https://www.xiaohongshu.com/goods-detail/{product['id']}"
    cmd = command.replace("{item_id}", product["id"]).replace("{url}", url)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CollectError("temporary", "采集命令超时") from exc
    if proc.returncode != 0:
        raise CollectError("temporary", f"采集命令退出码 {proc.returncode}",
                           (proc.stderr or "")[:800])
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CollectError("temporary", "采集命令输出不是 JSON", proc.stdout[:500]) from exc
    return _from_payload(payload, "command")


def collect_demo(product: dict, database: dbmod.Database,
                 slot: str | None = None) -> Result:
    """演示源：在历史高水位上继续单调递增，且同一整点幂等。

    不能从哈希重新起算——那会让累计值突然回退，虽然高水位能兜住，
    但小时序列会变得不可读。
    """
    import hashlib
    pid = product["id"]
    seed = int(hashlib.md5(pid.encode()).hexdigest()[:6], 16)
    if slot is None:
        from .scheduler import current_slot
        slot = current_slot()
    existing = database.snapshots(pid, start=slot, end=slot, usable_only=False)
    if existing and existing[0]["sold"] is not None:
        row = existing[0]
        return Result(
            ok=True, source="demo",
            sold=SoldReading(row["sold"], bool(row["usable"]),
                             "exact" if row["usable"] else "approximate",
                             row["sold_raw"] or f"已售{row['sold']}"),
            title=product.get("title"),
            shop_name=product.get("shop_name") or "演示店铺",
            price=product.get("price") or row["price"],
        )
    hw = database.high_water(pid)
    base = hw if hw is not None else 180 + seed % 900
    sold = base + (seed % 7 + 1)
    return Result(
        ok=True, source="demo",
        sold=SoldReading(sold, True, "exact", f"已售{sold}"),
        title=product.get("title"), shop_name=product.get("shop_name") or "演示店铺",
        price=product.get("price"),
    )


def collect_product(database: dbmod.Database, product: dict, cfg: dict,
                    slot: str | None = None) -> Result:
    """单个商品走完它的采集链再返回，失败分类明确。"""
    source = product.get("source") or cfg.get("default_source", "direct")
    errors: list[str] = []

    if source == "plugin":
        # 被动接收型：本轮不主动抓，插件推来的数据已由 /api/ingest 落库
        return Result(ok=False, source="plugin", error_kind="skip",
                      error_msg="由浏览器插件/外部程序推送，本轮不主动采集")

    if source == "demo":
        try:
            return collect_demo(product, database, slot)
        except Exception as exc:
            raise CollectError("temporary", f"演示源异常：{exc}") from exc

    chain: list[tuple[str, object]] = [("direct", collect_direct)]
    command = cfg.get("fetch_command") or ""
    if command:
        chain.append(("command", lambda p: collect_command(p, command)))

    for name, fn in chain:
        try:
            return fn(product)  # type: ignore[operator]
        except CollectError as exc:
            errors.append(f"[{name}] {exc.message}")
            if exc.kind == "blocked":
                raise
        except Exception as exc:  # 任何未预期异常都不能让整轮崩掉
            errors.append(f"[{name}] {type(exc).__name__}: {exc}")
    raise CollectError("temporary", "；".join(errors))


def apply_result(database: dbmod.Database, product: dict, result: Result,
                 slot: str) -> dict:
    """把一次采集结果落到数据库。这里决定「写不写快照」，是防假销量的关键。"""
    pid = product["id"]

    if result.delisted_reason:
        database.update_product(pid, {
            "active": 0, "delisted_at": dbmod.ts(),
            "delisted_reason": result.delisted_reason, "last_error": None,
        })
        database.log("info", "collect", f"确认下架：{result.delisted_reason}",
                     product_id=pid, title=product.get("title"))
        return {"status": "delisted", "reason": result.delisted_reason}

    if not result.ok:
        if result.error_kind == "skip":
            return {"status": "skip", "reason": result.error_msg}
        database.update_product(pid, {"last_error": result.error_msg})
        database.log("error", "collect", result.error_msg or "采集失败",
                     product_id=pid, title=product.get("title"),
                     detail=result.error_detail)
        return {"status": "failed", "reason": result.error_msg}

    reading = result.sold
    if reading is None or reading.value is None or not reading.precise:
        # 关键：失败 / 模糊值都不写**可用**快照。
        # 只留一条 unusable 记录用于展示，绝不让它进入相邻差值计算。
        database.add_snapshot(pid, slot, reading.value if reading else None,
                              usable=False, sold_raw=reading.raw if reading else None,
                              price=result.price)
        database.log("warn", "collect",
                     reading.reason if reading else "销量字段缺失",
                     product_id=pid, title=product.get("title"),
                     detail=f"读数：{reading.raw if reading else '无'}｜已跳过增量计算")
        return {"status": "imprecise", "reason": reading.reason if reading else "字段缺失"}

    # 明确的业务特征齐全 + 精确读数，才写可用快照
    patch = {"last_error": None}
    for key, value in (("title", result.title), ("shop_name", result.shop_name),
                       ("shop_id", result.shop_id), ("cover", result.cover)):
        if value:
            patch[key] = value
    if result.price is not None and product.get("price") is None:
        patch["price"] = result.price
    if patch:
        database.update_product(pid, patch)

    database.add_snapshot(
        pid, slot, reading.value, usable=True, sold_raw=reading.raw,
        price=result.price if result.price is not None else product.get("price"),
        shop_sold=result.shop_sold, fans=result.fans,
        stock_status=result.stock_status, deliverable=result.deliverable,
    )
    database.log("success", "collect", f"已售 {reading.value}（{result.source}）",
                 product_id=pid, title=product.get("title"))

    if reading.value > SOLD_LIMIT:
        database.update_product(pid, {"active": 0})
        database.log("warn", "collect", f"累计已售 {reading.value} 超过 {SOLD_LIMIT}，移出监控",
                     product_id=pid, title=product.get("title"))
        return {"status": "over_limit", "sold": reading.value}
    return {"status": "ok", "sold": reading.value}


def run_round(database: dbmod.Database, cfg: dict, *, slot: str | None = None,
              product_ids: list[str] | None = None) -> dict:
    """采集一轮。单轮内按商品串行，一个失败不卡死整轮；461 触发全局冷却。"""
    from .scheduler import current_slot

    slot = slot or current_slot(cfg)
    cooldown = database.setting("cooldown_until")
    if cooldown and cooldown > dbmod.ts():
        return {"slot": slot, "skipped": True, "reason": f"风控冷却中，至 {cooldown}",
                "total": 0, "results": {}}

    products = database.list_products(include_inactive=False)
    if product_ids:
        products = [p for p in products if p["id"] in set(product_ids)]
    summary = {"ok": 0, "failed": 0, "delisted": 0, "imprecise": 0,
               "over_limit": 0, "skip": 0}
    details: dict[str, dict] = {}

    for product in products:
        pid = product["id"]
        try:
            result = collect_product(database, product, cfg, slot)
            outcome = apply_result(database, product, result, slot)
        except CollectError as exc:
            if exc.kind == "blocked":
                until = dbmod.ts(analytics.now() + timedelta(minutes=COOLDOWN_MINUTES))
                database.set_setting("cooldown_until", until)
                database.log("error", "collect", f"触发风控熔断，冷却至 {until}",
                             detail=exc.detail)
                summary["failed"] += 1
                details[pid] = {"status": "blocked", "reason": exc.message}
                break
            outcome = apply_result(database, product, Result(
                ok=False, source="collector", error_kind=exc.kind,
                error_msg=exc.message, error_detail=exc.detail), slot)
        except Exception as exc:
            database.log("error", "collect", f"未预期异常：{type(exc).__name__}: {exc}",
                         product_id=pid, title=product.get("title"))
            outcome = {"status": "failed", "reason": str(exc)}
        status = outcome["status"]
        if status in summary:
            summary[status] += 1
        elif status == "failed":
            summary["failed"] += 1
        details[pid] = outcome

        min_wait = float(cfg.get("min_wait", 0.1))
        max_wait = float(cfg.get("max_wait", 0.3))
        if max_wait > min_wait:
            time.sleep(random.uniform(min_wait, min(max_wait, 3.0)))

    database.log("info", "scheduler",
                 f"{slot} 采集完成：成功 {summary['ok']} / 失败 {summary['failed']} / 下架 {summary['delisted']}")
    return {"slot": slot, "total": len(products), "summary": summary, "results": details}
