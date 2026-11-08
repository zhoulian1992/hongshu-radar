"""演示数据：把文章里讲到的几种典型形态各造一个，用来验证口径和排序。

这不只是假数据，它同时是一份「口径测试用例」：
  A 短期起量（爆品值小、商品价值高）      B 高累计但当天增量一般（爆品值大）
  C 一天只出 1 单（比例好看但样本过小）    D 累计高、最近卖不动
  E 页面只给「1万+」模糊值（不参与计算）  F 采集失败不写快照（不产生假销量）
  G 当天中途才开始监控（半日口径）        H 稳定连续出单

生成后请到界面点「采集一次」，观察相邻快照差值如何变成今日增量。
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from radar import analytics, db as dbmod

SPECS = [
    # id, 名称, 分类, 客单价, 起始累计, 近4天每日增量(含今日), 备注
    ("demo_a", "假期安全教育 PPT 模板包", "教师资料", 9.9, 1900, [12, 18, 26, 100],
     "短期起量：爆品值 ≈ 20"),
    ("demo_b", "健身入门知识库（可编辑）", "健身", 19.9, 9300, [110, 100, 100, 100],
     "高累计：爆品值 ≈ 100"),
    ("demo_c", "小众城市旅行攻略合集", "旅游", 6.0, 420, [1, 0, 1, 1],
     "一天一单：比例好看但样本过小"),
    ("demo_d", "年终汇报模板 300 套", "职场", 29.0, 6800, [60, 20, 3, 0],
     "累计高，最近卖不动"),
    ("demo_e", "高中通用试题库", "教师资料", 15.0, 9800, [40, 30, 20, 10],
     "页面只显示 1万+，不硬算精确日销"),
    ("demo_f", "家常菜食谱电子版", "食谱", 5.0, 1600, [30, 25, 20, 0],
     "采集失败：不写快照，不凭空算销量"),
    ("demo_g", "毕业答辩 PPT 模板", "职场", 12.0, 760, [8, 12, 16, 45],
     "今天中午才开始监控，半日口径"),
    ("demo_h", "考研复试自我介绍模板", "考试", 8.0, 2300, [22, 28, 34, 38],
     "连续出单，形态稳定"),
]


def _write_day(db: dbmod.Database, pid: str, day0, base: int, increment: int,
               *, today: bool, half_day: bool = False, skip_baseline: bool = False):
    """写某一天 0 点基线 + 若干整点快照，累计单调递增。"""
    if not skip_baseline:
        db.add_snapshot(pid, analytics._s(day0), base, usable=True,
                        sold_raw=f"已售{base}")
    if increment <= 0:
        return
    if today:
        # 最后一条落在「上一个完整整点」，把当前整点留给「立即采集」演示
        end_hour = max(1, analytics.now().hour - 1)
        if half_day:
            start_hour = max(2, min(end_hour - 2, 12))
            hours = list(range(start_hour, end_hour + 1, 2))
        else:
            hours = list(range(2, end_hour + 1, 2)) or [1]
        if end_hour not in hours:
            hours.append(end_hour)
        hours = sorted(set(h for h in hours if 1 <= h <= 23))
    else:
        hours = [3, 7, 11, 15, 19, 23]
    for i, h in enumerate(hours, 1):
        value = base + round(increment * i / len(hours))
        db.add_snapshot(pid, analytics._s(day0 + timedelta(hours=h)), value,
                        usable=True, sold_raw=f"已售{value}")


def seed(db: dbmod.Database, count: int = 8, reset: bool = False) -> dict:
    if reset:
        with db.conn() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM products WHERE id LIKE 'demo_%'")]
            for pid in ids:
                con.execute("DELETE FROM products WHERE id=?", (pid,))
                con.execute("DELETE FROM snapshots WHERE product_id=?", (pid,))
                con.execute("DELETE FROM high_water WHERE product_id=?", (pid,))

    today0 = analytics.day_floor(analytics.now())
    created = 0
    for spec in SPECS[:max(0, min(count, len(SPECS)))]:
        pid, title, category, price, start, daily, note = spec
        if db.get_product(pid):
            continue
        db.upsert_product({
            "id": pid, "title": title, "category": category, "price": price,
            "shop_name": "演示店铺",
            # demo_e 由插件推送，本轮不主动采集，用来保留「1万+ 不参与计算」这个形态
            "source": "plugin" if pid == "demo_e" else "demo", "active": 1,
            "notes_count": {"demo_a": 96, "demo_b": 1200, "demo_c": 40,
                            "demo_d": 880, "demo_e": 210, "demo_f": 150,
                            "demo_g": 65, "demo_h": 130}.get(pid),
            "competitor_count": {"demo_a": 3, "demo_b": 28, "demo_c": 1,
                                 "demo_d": 40, "demo_e": 2, "demo_f": 12,
                                 "demo_g": 6, "demo_h": 9}.get(pid),
            "season_tag": {"demo_a": "假期前", "demo_b": "", "demo_c": "暑假",
                           "demo_d": "年终", "demo_e": "", "demo_f": "",
                           "demo_g": "毕业季", "demo_h": "复试季"}.get(pid, ""),
        })
        created += 1
        cumulative = start
        # 过去 3 天：0 点基线 + 全天整点（半日口径的商品故意一条历史都不留）
        if pid != "demo_g":
            for offset in (-3, -2, -1):
                d0 = today0 + timedelta(days=offset)
                inc = daily[offset + 3]
                _write_day(db, pid, d0, cumulative, inc, today=False)
                cumulative += inc
        # 今天
        today_inc = daily[3]
        if pid == "demo_g":
            _write_day(db, pid, today0, cumulative, today_inc,
                       today=True, half_day=True, skip_baseline=True)
        elif pid == "demo_f":
            # 采集失败：保留 0 点基线，之后一条快照都不写（失败绝不当 0）
            db.add_snapshot(pid, analytics._s(today0), cumulative, usable=True,
                            sold_raw=f"已售{cumulative}")
            db.update_product(pid, {"last_error": "HTTP 500：采集失败（演示）"})
            db.log("error", "collect", "HTTP 500：采集失败（演示）",
                   product_id=pid, title=title)
        elif pid == "demo_e":
            _write_day(db, pid, today0, cumulative, today_inc, today=True)
            # 页面文案变成「1万+」：写一条不可用快照，绝不进入差值计算
            db.add_snapshot(pid, analytics._s(analytics.hour_floor(analytics.now())),
                            10000, usable=False, sold_raw="1万+", price=price)
            db.log("warn", "collect", "读数不精确：1万+，已跳过增量计算",
                   product_id=pid, title=title)
        else:
            _write_day(db, pid, today0, cumulative, today_inc, today=True)
    db.log("info", "demo", f"演示数据已生成：{created} 个商品")
    return {"created": created, "total": len(db.list_products()),
            "snapshots": db.snapshot_count(), "note": "演示数据仅用于验证口径"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="生成演示商品与销量快照")
    ap.add_argument("--db", default="data/radar.db")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    database = dbmod.init(args.db)
    print(seed(database, args.count, args.reset))
