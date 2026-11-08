"""整点调度：按自然时间边界跑，不把「刚启动」当成正式整点。

例：09:35 启动、周期 60 分钟 → 第一次正式采集是 10:00，下一次 11:00。
同时只允许一轮采集，定时轮和「立即采集」用同一把锁互斥。
"""
from __future__ import annotations

import json
import threading
from datetime import timedelta

from . import analytics, db as dbmod


def _interval(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("interval_minutes", 60)))
    except (TypeError, ValueError):
        return 60


def current_slot(cfg: dict | None = None, at=None) -> str:
    """当前时间所属的本轮整点（向下取整到采集周期边界）。"""
    cfg = cfg or {}
    at = at or analytics.now()
    step = _interval(cfg)
    start = analytics.day_floor(at)
    elapsed = int((at.replace(second=0, microsecond=0) - start).total_seconds() // 60)
    offset = (elapsed // step) * step
    return (start + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M:%S")


def next_boundary(cfg: dict, at=None):
    at = at or analytics.now()
    step = _interval(cfg)
    start = analytics.day_floor(at)
    elapsed = int((at.replace(second=0, microsecond=0) - start).total_seconds() // 60)
    offset = (elapsed // step + 1) * step
    return start + timedelta(minutes=offset)


class Scheduler:
    def __init__(self, database: dbmod.Database, config_provider):
        self.db = database
        self.config_provider = config_provider   # callable -> 最新 cfg（支持界面改设置）
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_round: dict | None = None
        self.running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="radar-scheduler",
                                        daemon=True)
        self._thread.start()
        self.running = True
        cfg = self.config_provider()
        self.db.log("info", "scheduler",
                    f"监控已启动，周期 {_interval(cfg)} 分钟，下次正式采集 "
                    f"{next_boundary(cfg).strftime('%Y-%m-%d %H:%M')}")

    def stop(self):
        self._stop.set()
        self.running = False
        self.db.log("info", "scheduler", "监控已停止")

    def _loop(self):
        while not self._stop.is_set():
            cfg = self.config_provider()
            target = next_boundary(cfg)
            wait = max(0.5, (target - analytics.now()).total_seconds())
            if self._stop.wait(timeout=min(wait, 60)):
                break
            # 只有真正到点才跑，避免「配置中途改动」或 60 秒唤醒造成误触发
            if analytics.now() < target:
                continue
            self.run_round(slot=target.strftime("%Y-%m-%d %H:%M:%S"))

    def run_round(self, *, slot: str | None = None,
                  product_ids: list[str] | None = None) -> dict:
        from .collector import run_round as _run
        if not self._lock.acquire(blocking=False):
            return {"busy": True, "reason": "已有采集任务在跑，请稍候"}
        try:
            cfg = self.config_provider()
            result = _run(self.db, cfg, slot=slot, product_ids=product_ids)
            self.last_round = result
            try:
                from .notify import send_round_report
                send_round_report(self.db, cfg, result)
            except Exception as exc:
                self.db.log("warn", "notify", f"推送失败（不影响采集）：{exc}")
            return result
        finally:
            self._lock.release()

    def status(self) -> dict:
        cfg = self.config_provider()
        return {
            "running": self.running,
            "interval_minutes": _interval(cfg),
            "next_at": next_boundary(cfg).strftime("%Y-%m-%d %H:%M:%S"),
            "current_slot": current_slot(cfg),
            "cooldown_until": self.db.setting("cooldown_until"),
            "last_round": self.last_round,
        }
