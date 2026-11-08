"""统计口径与选品指标。

严格遵循两条底线：
1. 销量一律由「累计已售快照的差值」得到，绝不把累计值当日销。
2. 缺少基线显示 —，绝不伪造 0；采集失败不落快照，所以不会凭空多算。

文章里的两个自定指标：
  爆品值   = 累计已售 ÷ 今日销量增量           （越小越优先看）
  商品价值 = 客单价 × 今日增量 × (今日增量 ÷ 累计已售)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db as dbmod

TZ = ZoneInfo("Asia/Shanghai")

MIN_DELTA = 3  # 低于这个当日增量，标记「样本过小」，不直接下结论


def now() -> datetime:
    return datetime.now(TZ)


def day_floor(dt: datetime) -> datetime:
    return dt.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def hour_floor(dt: datetime) -> datetime:
    return dt.astimezone(TZ).replace(minute=0, second=0, microsecond=0)


def _s(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _baseline(database: dbmod.Database, pid: str, boundary: str) -> dict | None:
    """boundary 时刻（含）之前的最后一条可用快照。"""
    rows = database.snapshots(pid, end=boundary, usable_only=True)
    return rows[-1] if rows else None


def current_sold(database: dbmod.Database, pid: str) -> int | None:
    """当前可信累计已售 = 最新可用快照与历史高水位取大者（防偶发回退）。"""
    rows = database.snapshots(pid, usable_only=True)
    latest = rows[-1]["sold"] if rows else None
    hw = database.high_water(pid)
    vals = [v for v in (latest, hw) if v is not None]
    return max(vals) if vals else None


def today_delta(database: dbmod.Database, pid: str, at: datetime | None = None) -> dict:
    """今日销量增量。返回 delta / partial / note，缺基线时 delta=None。"""
    at = at or now()
    today0 = day_floor(at)
    cur = current_sold(database, pid)
    if cur is None:
        return {"delta": None, "partial": True, "note": "无可用快照（待采集或读数不精确）"}

    base = _baseline(database, pid, _s(today0))
    if base and base["captured_at"] == _s(today0):
        return {"delta": max(0, cur - base["sold"]), "partial": False, "note": "0 点基线完整"}

    if base:
        return {"delta": max(0, cur - base["sold"]), "partial": True,
                "note": f"缺 0 点基线，用 {base['captured_at'][:16]} 近似（勿与完整日对比）"}

    # 当天中途才开始监控：用当天第一条真实成功快照当起点，明确标记不完整
    rows = database.snapshots(pid, start=_s(today0), usable_only=True)
    if rows:
        return {"delta": max(0, cur - rows[0]["sold"]), "partial": True,
                "note": f"当天 {rows[0]['captured_at'][11:16]} 才开始监控，半日口径"}
    return {"delta": None, "partial": True, "note": "今日无基线"}


def hour_delta(database: dbmod.Database, pid: str, at: datetime | None = None) -> int | None:
    """上一个完整整点到当前整点的增量。"""
    at = at or now()
    this_hour = hour_floor(at)
    prev_hour = this_hour - timedelta(hours=1)
    cur = _baseline(database, pid, _s(this_hour))
    base = _baseline(database, pid, _s(prev_hour))
    if not cur or not base or cur["captured_at"] == base["captured_at"]:
        return None
    return max(0, cur["sold"] - base["sold"])


def yesterday_delta(database: dbmod.Database, pid: str, at: datetime | None = None) -> int | None:
    at = at or now()
    today0 = day_floor(at)
    y0 = today0 - timedelta(days=1)
    end = _baseline(database, pid, _s(today0))
    start = _baseline(database, pid, _s(y0))
    if not end or not start or end["captured_at"] == start["captured_at"]:
        return None
    return max(0, end["sold"] - start["sold"])


def daily_series(database: dbmod.Database, pid: str, days: int = 7,
                 at: datetime | None = None) -> list[dict]:
    """近 N 个自然日的每日增量；缺基线的日子 delta=None（前端画断点，不补 0）。"""
    at = at or now()
    today0 = day_floor(at)
    out = []
    for i in range(days - 1, -1, -1):
        d0 = today0 - timedelta(days=i)
        next0 = d0 + timedelta(days=1)
        end_rows = database.snapshots(pid, end=_s(next0 - timedelta(seconds=1)),
                                      usable_only=True)
        end_rows = [r for r in end_rows if r["captured_at"] >= _s(d0)]
        base = _baseline(database, pid, _s(d0))
        label = d0.strftime("%m-%d")
        if not end_rows or not base or base["captured_at"] > _s(d0):
            # 没有 0 点基线时，允许用当天首条做起点，但标记为不完整
            first = end_rows[0] if end_rows else None
            last = end_rows[-1] if end_rows else None
            delta = max(0, last["sold"] - first["sold"]) if first and last else None
            out.append({"date": label, "delta": delta, "partial": True})
        else:
            delta = max(0, end_rows[-1]["sold"] - base["sold"])
            out.append({"date": label, "delta": delta, "partial": False})
    return out


def hourly_series(database: dbmod.Database, pid: str, date: str | None = None) -> list[dict]:
    """某天每小时增量，用于折线图。缺失小时为 None，不伪造 0。"""
    if date is None:
        date = now().strftime("%Y-%m-%d")
    d0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=TZ)
    rows = database.snapshots(pid, start=_s(d0),
                              end=_s(d0 + timedelta(days=1) - timedelta(seconds=1)),
                              usable_only=True)
    by_hour: dict[int, int] = {}
    for r in rows:
        by_hour[int(r["captured_at"][11:13])] = r["sold"]
    out = []
    prev = None
    for h in range(24):
        if h in by_hour and prev is not None:
            out.append({"hour": f"{h:02d}", "delta": max(0, by_hour[h] - prev)})
        else:
            out.append({"hour": f"{h:02d}", "delta": None})
        if h in by_hour:
            prev = by_hour[h]
    return out


def streak_days(database: dbmod.Database, pid: str) -> int:
    """连续出单天数——文章强调「还要看能不能连续几天都卖」。"""
    series = daily_series(database, pid, days=14)
    count = 0
    for item in reversed(series):
        if item["delta"] and item["delta"] > 0:
            count += 1
        else:
            break
    return count


def boom_value(sold: int | None, delta: int | None) -> float | None:
    """爆品值 = 累计已售 ÷ 今日增量。今日没增量就不该算出一个好看的数。"""
    if not sold or not delta or delta <= 0:
        return None
    return round(sold / delta, 2)


def goods_value(price: float | None, sold: int | None, delta: int | None) -> float | None:
    """商品价值 = 客单价 × 今日增量 × (今日增量 ÷ 累计已售)。不是利润。"""
    if price is None or not sold or not delta or delta <= 0:
        return None
    return round(price * delta * (delta / sold), 2)


def trial(price: float, sold: int, delta: int, *, partial: bool = False) -> dict:
    """试算器：不落库，纯计算，并把口径陷阱一起返回给界面。"""
    warnings = []
    if delta <= 0:
        warnings.append("今日增量为 0，爆品值/商品价值不参与排序")
    elif delta < MIN_DELTA:
        warnings.append(f"今日只增加 {delta} 单，样本过小，比例再好看也别直接下结论")
    if not sold:
        warnings.append("累计已售为 0 或缺失，无法计算占比")
    if partial:
        warnings.append("半日口径，不要和完整一天的数据对比")
    return {
        "price": price, "sold": sold, "delta": delta,
        "boom_value": boom_value(sold, delta),
        "goods_value": goods_value(price, sold, delta),
        "fuzzy_revenue": round(price * delta, 2) if delta and delta > 0 else 0,
        "warnings": warnings,
        "note": "模糊销售额 = 客单价 × 销量增量，不代表真实成交额；商品价值也不是利润",
    }


def compute_metrics(database: dbmod.Database, product: dict,
                    at: datetime | None = None) -> dict:
    """把商品、统计口径、两个选品指标、研判进度合成一行看板数据。"""
    pid = product["id"]
    td = today_delta(database, pid, at)
    cur = current_sold(database, pid)
    delta = td["delta"]
    price = product.get("price")
    snaps = database.snapshots(pid, usable_only=True)
    last = snaps[-1]["captured_at"] if snaps else None
    all_snaps = database.snapshots(pid, usable_only=False)
    last_any = all_snaps[-1] if all_snaps else None
    # 最近一次读数如果是「1万+」这类模糊值，看板上必须看得见
    imprecise = bool(last_any and not last_any["usable"])
    imprecise_raw = last_any["sold_raw"] if imprecise else None

    try:
        research = __import__("json").loads(product.get("research_json") or "{}")
    except (TypeError, ValueError):
        research = {}
    done = sum(1 for v in research.values() if v)

    if product.get("delisted_at"):
        status = "已下架"
    elif cur is None:
        status = "待采集" if not product.get("last_error") else "异常"
    elif product.get("last_error"):
        status = "异常"
    elif td["partial"]:
        status = "半日口径"
    else:
        status = "正常"

    return {
        **product,
        "current_sold": cur,
        "today_delta": delta,
        "yesterday_delta": yesterday_delta(database, pid, at),
        "hour_delta": hour_delta(database, pid, at),
        "boom_value": boom_value(cur, delta),
        "goods_value": goods_value(price, cur, delta),
        "fuzzy_revenue": round(price * delta, 2) if price is not None and delta else None,
        "streak": streak_days(database, pid),
        "sample_small": bool(delta is not None and 0 < delta < MIN_DELTA),
        "partial": td["partial"],
        "note": td["note"],
        "last_snapshot": last,
        "snapshot_count": len(snaps),
        "imprecise": imprecise,
        "imprecise_raw": imprecise_raw,
        "research_done": done,
        "status": status,
    }
