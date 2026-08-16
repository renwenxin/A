"""预测台账 — SQLite 存储层

表结构见 specs/2026-08-16-prediction-ledger-design.md 第 3 节。
写入幂等：唯一约束 (pred_date, pred_type, item_key) + INSERT OR IGNORE。
"""
import os
import sqlite3
from datetime import date, timedelta
from typing import Dict, List

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pred_date TEXT NOT NULL,
  pred_type TEXT NOT NULL,
  item_key TEXT NOT NULL,
  item_name TEXT DEFAULT '',
  direction TEXT,
  score REAL,
  detail TEXT DEFAULT '',
  actual TEXT,
  hit INTEGER,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pred ON predictions(pred_date, pred_type, item_key);
CREATE INDEX IF NOT EXISTS idx_pred_date ON predictions(pred_date);
CREATE INDEX IF NOT EXISTS idx_pred_type ON predictions(pred_type);
"""

SCORE_BUCKETS = [('≥60', 60, None), ('50-59', 50, 60), ('<50', None, 50)]


class LedgerStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def upsert_predictions(self, rows: List[Dict]) -> int:
        """幂等写入，返回实际插入行数。"""
        if not rows:
            return 0
        conn = self._connect()
        inserted = 0
        try:
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO predictions "
                    "(pred_date, pred_type, item_key, item_name, direction, score, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r['pred_date'], r['pred_type'], r['item_key'],
                     r.get('item_name', ''), r.get('direction'), r.get('score'),
                     r.get('detail', '')))
                inserted += cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return inserted

    def get_unverified(self) -> List[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM predictions WHERE hit IS NULL ORDER BY pred_date").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_verified(self, row_id: int, actual: str, hit: int) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE predictions SET actual=?, hit=? WHERE id=?",
                         (actual, hit, row_id))
            conn.commit()
        finally:
            conn.close()

    def set_actual(self, pred_date: str, pred_type: str, item_key: str,
                   actual: str, hit: int) -> None:
        """按唯一键补写验证结果（迁移用，幂等）。"""
        conn = self._connect()
        try:
            conn.execute("UPDATE predictions SET actual=?, hit=? "
                         "WHERE pred_date=? AND pred_type=? AND item_key=?",
                         (actual, hit, pred_date, pred_type, item_key))
            conn.commit()
        finally:
            conn.close()

    def rows(self, window_days: int = 30) -> List[dict]:
        cutoff = (date.today() - timedelta(days=window_days)).strftime('%Y%m%d')
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM predictions WHERE pred_date >= ? "
                "ORDER BY pred_date DESC, id DESC", (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def summary(self, window_days: int = 30) -> Dict:
        cutoff = (date.today() - timedelta(days=window_days)).strftime('%Y%m%d')
        conn = self._connect()
        try:
            def _rate(pred_type: str) -> Dict:
                row = conn.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) AS verified, "
                    "SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) AS hit "
                    "FROM predictions WHERE pred_type=? AND pred_date >= ?",
                    (pred_type, cutoff)).fetchone()
                total = row['total'] or 0
                verified = row['verified'] or 0
                hit = row['hit'] or 0
                return {'total': total, 'verified': verified, 'hit': hit,
                        'rate': round(hit / verified, 4) if verified else None}

            picks = _rate('picks')
            buckets = []
            for label, lo, hi in SCORE_BUCKETS:
                sql = ("SELECT COUNT(*) AS total, "
                       "SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) AS hit, "
                       "SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) AS verified "
                       "FROM predictions WHERE pred_type='picks' AND pred_date >= ?")
                params = [cutoff]
                if lo is not None:
                    sql += " AND score >= ?"
                    params.append(lo)
                if hi is not None:
                    sql += " AND score < ?"
                    params.append(hi)
                row = conn.execute(sql, params).fetchone()
                verified = row['verified'] or 0
                buckets.append({'label': label, 'total': row['total'] or 0,
                                'verified': verified, 'hit': row['hit'] or 0,
                                'rate': round((row['hit'] or 0) / verified, 4)
                                if verified else None})
            cycle = _rate('cycle')
            auction = _rate('auction')
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM predictions WHERE hit IS NULL"
            ).fetchone()['n'] or 0
            verified_days = conn.execute(
                "SELECT COUNT(DISTINCT pred_date) AS n FROM predictions WHERE hit IS NOT NULL"
            ).fetchone()['n'] or 0
            return {'picks': picks, 'buckets': buckets,
                    'cycle': cycle, 'auction': auction,
                    'coverage': {'verified_days': verified_days, 'pending': pending}}
        finally:
            conn.close()
