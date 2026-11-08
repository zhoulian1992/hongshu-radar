#!/usr/bin/env python3
"""红薯雷达 · 本地选品监控工作台（Mac / 零第三方依赖）

启动：  python3 app.py
打开：  http://127.0.0.1:8777
停止：  Ctrl+C

所有数据只存在本机 data/radar.db，服务只监听 127.0.0.1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from radar import db as dbmod            # noqa: E402
from radar.scheduler import Scheduler    # noqa: E402
from radar.server import AppState, create_server  # noqa: E402

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8777,
    "db_path": "data/radar.db",
    "interval_minutes": 60,
    "open_browser": True,
    "default_source": "demo",
    "fetch_command": "",
    "min_wait": 0.1,
    "max_wait": 0.3,
    "demo_mode": True,
    "notify": {"enabled": False, "type": "wecom", "webhook": ""},
}


def load_config(path: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for key, value in user.items():
            if key == "notify" and isinstance(value, dict):
                cfg["notify"] = {**DEFAULT_CONFIG["notify"], **value}
            else:
                cfg[key] = value
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def main():
    ap = argparse.ArgumentParser(description="红薯雷达 · 本地选品监控工作台")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-seed", action="store_true", help="不自动生成演示数据")
    args = ap.parse_args()

    cfg = load_config(args.config)
    port = args.port or int(cfg.get("port", 8777))
    cfg["port"] = port

    db_path = cfg["db_path"]
    if not os.path.isabs(db_path):
        db_path = os.path.join(ROOT, db_path)
    db = dbmod.init(db_path)

    # 首次启动灌一份演示数据：文章建议「先拿一个商品试，确认能读到数据、能正常计算」
    if cfg.get("demo_mode") and not args.no_seed and not db.list_products():
        from seed_demo import seed
        result = seed(db, 8)
        db.log("info", "app", f"首次启动，已生成演示数据：{result}")

    scheduler = Scheduler(db, lambda: cfg)
    state = AppState(db, cfg, args.config, scheduler)
    httpd, _ = create_server(state, cfg.get("host", "127.0.0.1"), port)
    scheduler.start()

    url = f"http://127.0.0.1:{port}"
    print("=" * 62)
    print("  红薯雷达 · 本地选品监控工作台")
    print(f"  打开：{url}")
    print(f"  数据：{db_path}")
    print(f"  周期：每 {cfg['interval_minutes']} 分钟（按整点）"
          f" ｜ 数据源：{cfg['default_source']}")
    if cfg.get("demo_mode"):
        print("  演示模式已开启：当前是演示数据，界面右上角可清空并接入真实商品")
    print("  Ctrl+C 停止；只读 MCP 配置说明见 /api/mcp")
    print("=" * 62, flush=True)

    if cfg.get("open_browser") and not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        scheduler.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
