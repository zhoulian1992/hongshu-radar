"""可选的采集时报推送（企微 / 飞书机器人）。

原版只做了企微；文章里说「我的消息推送到企微，你可以改成飞书」——这里两种都支持，
配置里改 notify.type 即可。推送失败只写日志，绝不影响数据落库。
"""
from __future__ import annotations

import json
import urllib.request

from . import analytics, db as dbmod


def _post(webhook: str, payload: dict, timeout: float = 8.0):
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def send_text(database: dbmod.Database, cfg: dict, text: str) -> bool:
    notify = cfg.get("notify") or {}
    if not notify.get("enabled") or not notify.get("webhook"):
        return False
    webhook = str(notify["webhook"])
    if not webhook.startswith("https://"):
        database.log("warn", "notify", "Webhook 格式不合法（需 https://）")
        return False
    kind = notify.get("type", "wecom")
    if kind == "feishu":
        payload = {"msg_type": "text", "content": {"text": text}}
    else:
        payload = {"msgtype": "text", "text": {"content": text}}
    _post(webhook, payload)
    return True


def send_round_report(database: dbmod.Database, cfg: dict, round_result: dict):
    notify = cfg.get("notify") or {}
    if not notify.get("enabled") or not notify.get("webhook"):
        return
    slot = round_result.get("slot", "")
    summary = round_result.get("summary") or {}
    rows = []
    for product in database.list_products(include_inactive=False):
        m = analytics.compute_metrics(database, product)
        if m["hour_delta"]:
            rows.append((m["hour_delta"], m))
    rows.sort(key=lambda x: x[0], reverse=True)
    lines = [f"红薯雷达 · {slot[11:16]} 采集时报",
             f"成功 {summary.get('ok', 0)} / 失败 {summary.get('failed', 0)} / 模糊值 {summary.get('imprecise', 0)}"]
    for i, (_, m) in enumerate(rows[:8], 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        lines.append(f"{medal} {(m['shop_name'] or m['title'])[:18]}")
        lines.append(f"   上小时 {m['hour_delta']} 单 ｜ 今日 {m['today_delta'] if m['today_delta'] is not None else '—'} 单")
    failed = [pid for pid, r in (round_result.get("results") or {}).items()
              if r.get("status") == "failed"]
    if failed:
        lines.append(f"⚠️ 采集失败 {len(failed)} 个：{', '.join(failed[:3])}")
    send_text(database, cfg, "\n".join(lines))
    database.log("success", "notify", f"{slot} 时报已推送")
