"""本地 SQLite 存储层。

设计原则（对齐原版提示词 + 文章里的防错要求）：
- 全部业务数据只存本机，WAL 模式，参数化 SQL。
- 采集失败「不写快照」——只写日志，避免下一次成功时凭空算出一大堆销量。
- 快照带 usable 标记：「1万+」「1.2万」这类非精确值不参与任何增量计算。
- 高水位单独记录，防止累计已售偶发回退造成虚假负销量。
- 表结构自动迁移，升级不需要删库。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import urllib.parse
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def ts(dt: datetime | None = None) -> str:
    """统一时间字符串：Asia/Shanghai，YYYY-MM-DD HH:MM:SS，可直接做字符串比较。"""
    if dt is None:
        dt = datetime.now(TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)


# 每张表的目标列定义（用于自动迁移补列）
SCHEMA: dict[str, dict[str, str]] = {
    "products": {
        "id": "TEXT PRIMARY KEY",
        "title": "TEXT NOT NULL",
        "url": "TEXT",
        "shop_name": "TEXT",
        "shop_id": "TEXT",
        "cover": "TEXT",
        "category": "TEXT",
        "price": "REAL",
        "active": "INTEGER DEFAULT 1",
        "source": "TEXT DEFAULT 'manual'",
        "source_url": "TEXT",
        "notes_count": "INTEGER",
        "first_note_at": "TEXT",
        "competitor_count": "INTEGER",
        "season_tag": "TEXT",
        "research_json": "TEXT DEFAULT '{}'",
        "created_at": "TEXT",
        "last_error": "TEXT",
        "last_ok_at": "TEXT",
        "delisted_at": "TEXT",
        "delisted_reason": "TEXT",
    },
    "snapshots": {
        "product_id": "TEXT NOT NULL",
        "captured_at": "TEXT NOT NULL",
        "sold": "INTEGER",
        "usable": "INTEGER DEFAULT 1",
        "sold_raw": "TEXT",
        "price": "REAL",
        "shop_sold": "INTEGER",
        "fans": "INTEGER",
        "stock_status": "INTEGER",
        "deliverable": "INTEGER",
        "PRIMARY KEY": "(product_id, captured_at)",
    },
    "high_water": {
        "product_id": "TEXT PRIMARY KEY",
        "sold": "INTEGER NOT NULL",
        "updated_at": "TEXT",
    },
    "settings": {
        "key": "TEXT PRIMARY KEY",
        "value": "TEXT NOT NULL",
    },
    "logs": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "ts": "TEXT",
        "level": "TEXT",
        "source": "TEXT",
        "product_id": "TEXT",
        "title": "TEXT",
        "message": "TEXT",
        "detail": "TEXT",
    },
    "deleted_products": {
        "product_id": "TEXT PRIMARY KEY",
        "deleted_at": "TEXT",
    },
    "risk_checks": {
        "check_id": "TEXT PRIMARY KEY",
        "product_id": "TEXT",
        "candidate_id": "TEXT",
        "title": "TEXT",
        "fingerprint": "TEXT",
        "list_status": "TEXT",
        "suggested_action": "TEXT",
        "handling_level": "TEXT",
        "lint_level": "TEXT",
        "hits": "TEXT",
        "checked_at": "TEXT",
        "db_date": "TEXT",
        "state": "TEXT DEFAULT 'ok'",
        "error": "TEXT",
        "payload": "TEXT",
    },
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snap_product_time ON snapshots(product_id, captured_at)",
    "CREATE INDEX IF NOT EXISTS idx_snap_time ON snapshots(captured_at)",
    "CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(ts)",
    "CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)",
]


class Database:
    def __init__(self, path: str, read_only: bool = False):
        self.path = path
        self.read_only = read_only
        if not read_only:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._write_lock = threading.RLock()
        if not read_only:
            self._migrate()

    @contextmanager
    def conn(self):
        if self.read_only:
            uri = "file:" + urllib.parse.quote(os.path.abspath(self.path)) + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
        else:
            con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        if not self.read_only:
            con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        try:
            yield con
            if not self.read_only:
                con.commit()
        except Exception:
            if not self.read_only:
                con.rollback()
            raise
        finally:
            con.close()

    def _migrate(self):
        with self.conn() as con:
            for table, columns in SCHEMA.items():
                cols_sql = ", ".join(f"{k} {v}" for k, v in columns.items())
                con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols_sql})")
                existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns.items():
                    if name == "PRIMARY KEY":
                        continue
                    if name not in existing:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            for stmt in INDEXES:
                con.execute(stmt)

    # ---------- products ----------
    def upsert_product(self, fields: dict) -> bool:
        """插入或补全商品信息。返回 True 表示是新商品。已下架/已删除的不会被重新激活。"""
        fields = dict(fields)
        pid = fields.get("id")
        if not pid or not fields.get("title"):
            raise ValueError("id 和 title 不能为空")
        fields.setdefault("created_at", ts())
        with self._write_lock, self.conn() as con:
            row = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if row:
                # 只补空字段，不覆盖用户手填的研判信息
                patch = {k: v for k, v in fields.items()
                         if k not in ("id", "created_at") and v is not None
                         and (row[k] is None or row[k] == "" or k in ("shop_name", "cover", "url"))}
                if patch:
                    sets = ", ".join(f"{k}=?" for k in patch)
                    con.execute(f"UPDATE products SET {sets} WHERE id=?",
                                (*patch.values(), pid))
                return False
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            con.execute(f"INSERT INTO products ({cols}) VALUES ({marks})",
                        tuple(fields.values()))
            return True

    def update_product(self, pid: str, patch: dict):
        if not patch:
            return
        with self._write_lock, self.conn() as con:
            sets = ", ".join(f"{k}=?" for k in patch)
            con.execute(f"UPDATE products SET {sets} WHERE id=?", (*patch.values(), pid))

    def get_product(self, pid: str):
        with self.conn() as con:
            row = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            return dict(row) if row else None

    def list_products(self, include_inactive: bool = True):
        sql = "SELECT * FROM products"
        if not include_inactive:
            sql += " WHERE active=1"
        sql += " ORDER BY created_at DESC"
        with self.conn() as con:
            return [dict(r) for r in con.execute(sql)]

    def is_deleted(self, pid: str) -> bool:
        with self.conn() as con:
            return con.execute("SELECT 1 FROM deleted_products WHERE product_id=?",
                               (pid,)).fetchone() is not None

    def delete_product(self, pid: str):
        """删除商品与历史快照，并记入 deleted_products（避免自动拓品时重新加入）。"""
        with self._write_lock, self.conn() as con:
            con.execute("DELETE FROM products WHERE id=?", (pid,))
            con.execute("DELETE FROM snapshots WHERE product_id=?", (pid,))
            con.execute("DELETE FROM high_water WHERE product_id=?", (pid,))
            con.execute("INSERT OR REPLACE INTO deleted_products VALUES (?, ?)", (pid, ts()))

    # ---------- snapshots / high water ----------
    def add_snapshot(self, product_id: str, captured_at: str, sold: int | None,
                     *, usable: bool = True, sold_raw: str | None = None,
                     price: float | None = None, shop_sold: int | None = None,
                     fans: int | None = None, stock_status: int | None = None,
                     deliverable: int | None = None):
        """同一整点重复写入时覆盖（幂等）。usable=False 的快照只做展示，绝不参与计算。"""
        with self._write_lock, self.conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO snapshots "
                "(product_id,captured_at,sold,usable,sold_raw,price,shop_sold,fans,stock_status,deliverable) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (product_id, captured_at, sold, 1 if usable else 0, sold_raw, price,
                 shop_sold, fans, stock_status, deliverable),
            )
            if usable and sold is not None:
                row = con.execute("SELECT sold FROM high_water WHERE product_id=?",
                                  (product_id,)).fetchone()
                if not row or sold > row["sold"]:
                    con.execute("INSERT OR REPLACE INTO high_water VALUES (?,?,?)",
                                (product_id, sold, ts()))
        self.update_product(product_id, {"last_ok_at": captured_at, "last_error": None})

    def snapshots(self, product_id: str, start: str | None = None,
                  end: str | None = None, usable_only: bool = True):
        sql = "SELECT * FROM snapshots WHERE product_id=?"
        args: list = [product_id]
        if usable_only:
            sql += " AND usable=1"
        if start:
            sql += " AND captured_at>=?"
            args.append(start)
        if end:
            sql += " AND captured_at<=?"
            args.append(end)
        sql += " ORDER BY captured_at"
        with self.conn() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def high_water(self, product_id: str) -> int | None:
        with self.conn() as con:
            row = con.execute("SELECT sold FROM high_water WHERE product_id=?",
                              (product_id,)).fetchone()
            return row["sold"] if row else None

    def snapshot_count(self) -> int:
        with self.conn() as con:
            return con.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]

    def db_size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def cleanup(self, keep_days: int = 90, log_days: int = 30) -> dict:
        """保留近期快照 + 每个商品一条更早的基线，避免清理后算不出销量。"""
        from datetime import timedelta
        cutoff = ts(datetime.now(TZ) - timedelta(days=keep_days))
        log_cutoff = ts(datetime.now(TZ) - timedelta(days=log_days))
        with self._write_lock, self.conn() as con:
            keep_ids = [r[0] for r in con.execute(
                "SELECT MIN(rowid) FROM snapshots WHERE captured_at<? GROUP BY product_id",
                (cutoff,)).fetchall()]
            keep_ids = [i for i in keep_ids if i]
            if keep_ids:
                marks = ",".join("?" for _ in keep_ids)
                removed = con.execute(
                    f"DELETE FROM snapshots WHERE captured_at<? AND rowid NOT IN ({marks})",
                    (cutoff, *keep_ids)).rowcount
            else:
                removed = con.execute("DELETE FROM snapshots WHERE captured_at<?",
                                      (cutoff,)).rowcount
            logs_removed = con.execute("DELETE FROM logs WHERE ts<?", (log_cutoff,)).rowcount
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with sqlite3.connect(self.path) as c:
            c.execute("VACUUM")
        return {"snapshots_removed": removed, "logs_removed": logs_removed,
                "baselines_kept": len(keep_ids), "size": self.db_size()}

    # ---------- logs / settings ----------
    def log(self, level: str, source: str, message: str, *,
            product_id: str | None = None, title: str | None = None,
            detail: str | None = None):
        with self._write_lock, self.conn() as con:
            con.execute(
                "INSERT INTO logs (ts,level,source,product_id,title,message,detail) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts(), level, source, product_id, title, message, detail),
            )

    def logs(self, level: str | None = None, limit: int = 300):
        sql = "SELECT * FROM logs"
        args: list = []
        if level and level != "all":
            sql += " WHERE level=?"
            args.append(level)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def clear_logs(self):
        with self._write_lock, self.conn() as con:
            con.execute("DELETE FROM logs")

    # ---------- 上架前风险体检缓存 ----------
    def save_risk(self, row: dict):
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._write_lock, self.conn() as con:
            con.execute(f"INSERT OR REPLACE INTO risk_checks ({cols}) VALUES ({marks})",
                        tuple(row.values()))

    def get_risk(self, check_id: str):
        with self.conn() as con:
            row = con.execute("SELECT * FROM risk_checks WHERE check_id=?",
                              (check_id,)).fetchone()
            return dict(row) if row else None

    def risk_for_products(self) -> dict:
        """看板用：按商品 ID 关联，只取摘要，不带完整 payload。"""
        with self.conn() as con:
            rows = con.execute(
                "SELECT product_id,candidate_id,title,list_status,suggested_action,"
                "handling_level,lint_level,hits,checked_at,db_date,state,error,fingerprint "
                "FROM risk_checks").fetchall()
        return {r["product_id"] or r["candidate_id"]: dict(r) for r in rows}

    def risk_rows(self, state: str | None = None):
        sql = ("SELECT check_id,product_id,candidate_id,title,list_status,"
               "suggested_action,handling_level,lint_level,hits,checked_at,"
               "db_date,state,error FROM risk_checks")
        args: list = []
        if state:
            sql += " WHERE state=?"
            args.append(state)
        sql += " ORDER BY checked_at DESC"
        with self.conn() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def clear_risk(self):
        with self._write_lock, self.conn() as con:
            con.execute("DELETE FROM risk_checks")

    def risk_counts(self) -> dict:
        with self.conn() as con:
            rows = con.execute(
                "SELECT list_status, COUNT(*) AS n FROM risk_checks "
                "WHERE state='ok' GROUP BY list_status").fetchall()
        return {r["list_status"]: r["n"] for r in rows}

    def setting(self, key: str, default=None):
        with self.conn() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value):
        with self._write_lock, self.conn() as con:
            con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))


_DB: Database | None = None


def init(path: str) -> Database:
    global _DB
    _DB = Database(path)
    return _DB


def get_db() -> Database:
    if _DB is None:
        raise RuntimeError("数据库尚未初始化，先调用 radar.db.init(path)")
    return _DB
