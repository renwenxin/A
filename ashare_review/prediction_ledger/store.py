"""预测台账 — SQLite 存储层

表结构见 specs/2026-08-16-prediction-ledger-design.md 第 3 节。
写入幂等：唯一约束 (pred_date, pred_type, item_key) + INSERT OR IGNORE。
"""
import os
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Optional

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
  win INTEGER,
  win_open INTEGER,
  win_high INTEGER,
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
            # 迁移：旧库补 win 三口径列（次日收盘/开盘/高点 vs 首日开盘）
            cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
            if 'win' not in cols:
                conn.execute("ALTER TABLE predictions ADD COLUMN win INTEGER")
            if 'win_open' not in cols:
                conn.execute("ALTER TABLE predictions ADD COLUMN win_open INTEGER")
            if 'win_high' not in cols:
                conn.execute("ALTER TABLE predictions ADD COLUMN win_high INTEGER")
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

    def mark_verified(self, row_id: int, actual: str, hit: int,
                      win: Optional[int] = None, win_open: Optional[int] = None,
                      win_high: Optional[int] = None) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE predictions SET actual=?, hit=?, win=?, win_open=?, win_high=? "
                         "WHERE id=?",
                         (actual, hit, win, win_open, win_high, row_id))
            conn.commit()
        finally:
            conn.close()

    def set_actual(self, pred_date: str, pred_type: str, item_key: str,
                   actual: str, hit: int, win: Optional[int] = None,
                   win_open: Optional[int] = None, win_high: Optional[int] = None) -> None:
        """按唯一键补写验证结果（迁移用，幂等）。"""
        conn = self._connect()
        try:
            conn.execute("UPDATE predictions SET actual=?, hit=?, win=?, win_open=?, win_high=? "
                         "WHERE pred_date=? AND pred_type=? AND item_key=?",
                         (actual, hit, win, win_open, win_high, pred_date, pred_type, item_key))
            conn.commit()
        finally:
            conn.close()

    def update_detail(self, pred_date: str, pred_type: str, item_key: str,
                      detail: str) -> None:
        """按唯一键刷新明细（不覆盖 actual/hit）。复盘报告重生成时更新明细用。"""
        conn = self._connect()
        try:
            conn.execute("UPDATE predictions SET detail=? "
                         "WHERE pred_date=? AND pred_type=? AND item_key=?",
                         (detail, pred_date, pred_type, item_key))
            conn.commit()
        finally:
            conn.close()

    def rows(self, window_days: int = 30) -> List[dict]:
        cutoff = (date.today() - timedelta(days=window_days)).strftime('%Y%m%d')
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM predictions WHERE pred_date >= ? "
                "ORDER BY pred_date DESC, "
                "CASE WHEN pred_type = 'market_open' THEN 1 ELSE 0 END, "
                "id DESC", (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def summary(self, window_days: int = 30) -> Dict:
        """聚合统计：picks/buckets/cycle/auction 按近 window_days 天窗口过滤；
        coverage（verified_days/pending）为全局口径，不受窗口影响。"""
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
            pick_auction = _rate('pick_auction')
            # 竞价判断按判定拆分命中率；胜率三口径=次日收盘/开盘/高点 vs 首日开盘（关联同标的精选）
            pick_auction_buckets = []
            for verdict in ('抢筹', '达标', '观望'):
                row = conn.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN pa.hit = 1 THEN 1 ELSE 0 END) AS hit, "
                    "SUM(CASE WHEN pa.hit IS NOT NULL THEN 1 ELSE 0 END) AS verified, "
                    "SUM(CASE WHEN p.win = 1 THEN 1 ELSE 0 END) AS wins, "
                    "SUM(CASE WHEN p.win IS NOT NULL THEN 1 ELSE 0 END) AS win_verified, "
                    "SUM(CASE WHEN p.win_open = 1 THEN 1 ELSE 0 END) AS wins_open, "
                    "SUM(CASE WHEN p.win_open IS NOT NULL THEN 1 ELSE 0 END) AS win_open_verified, "
                    "SUM(CASE WHEN p.win_high = 1 THEN 1 ELSE 0 END) AS wins_high, "
                    "SUM(CASE WHEN p.win_high IS NOT NULL THEN 1 ELSE 0 END) AS win_high_verified "
                    "FROM predictions pa "
                    "LEFT JOIN predictions p ON p.pred_date = pa.pred_date "
                    "AND p.item_key = pa.item_key AND p.pred_type = 'picks' "
                    "WHERE pa.pred_type='pick_auction' "
                    "AND pa.direction=? AND pa.pred_date >= ?",
                    (verdict, cutoff)).fetchone()
                verified = row['verified'] or 0
                win_verified = row['win_verified'] or 0
                win_open_verified = row['win_open_verified'] or 0
                win_high_verified = row['win_high_verified'] or 0
                pick_auction_buckets.append({
                    'label': verdict, 'total': row['total'] or 0,
                    'verified': verified, 'hit': row['hit'] or 0,
                    'rate': round((row['hit'] or 0) / verified, 4)
                    if verified else None,
                    'wins': row['wins'] or 0,
                    'win_rate': round((row['wins'] or 0) / win_verified, 4)
                    if win_verified else None,
                    'wins_open': row['wins_open'] or 0,
                    'win_open_rate': round((row['wins_open'] or 0) / win_open_verified, 4)
                    if win_open_verified else None,
                    'wins_high': row['wins_high'] or 0,
                    'win_high_rate': round((row['wins_high'] or 0) / win_high_verified, 4)
                    if win_high_verified else None})
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM predictions WHERE hit IS NULL "
                "AND pred_type != 'market_open'"
            ).fetchone()['n'] or 0
            verified_days = conn.execute(
                "SELECT COUNT(DISTINCT pred_date) AS n FROM predictions WHERE hit IS NOT NULL"
            ).fetchone()['n'] or 0

            # 次日收盘胜负汇总：胜 = 次日收盘 > 首日开盘（仅精选）
            def _win_counts(where_extra: str = '', params: tuple = ()) -> Dict:
                row = conn.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS wins "
                    f"FROM predictions WHERE pred_type='picks' AND win IS NOT NULL "
                    f"AND pred_date >= ? {where_extra}",
                    (cutoff,) + params).fetchone()
                total = row['total'] or 0
                wins = row['wins'] or 0
                return {'total': total, 'wins': wins,
                        'losses': total - wins,
                        'rate': round(wins / total, 4) if total else None}
            win = _win_counts()
            win_buckets = []
            for label, lo, hi in SCORE_BUCKETS:
                conds = []
                sqlp = ()
                if lo is not None:
                    conds.append("score >= ?")
                    sqlp = sqlp + (lo,)
                if hi is not None:
                    conds.append("score < ?")
                    sqlp = sqlp + (hi,)
                where_extra = (" AND " + " AND ".join(conds)) if conds else ""
                win_buckets.append({
                    'label': label,
                    **_win_counts(where_extra, sqlp),
                })

            return {'picks': picks, 'buckets': buckets,
                    'cycle': cycle, 'auction': auction,
                    'pick_auction': pick_auction,
                    'pick_auction_buckets': pick_auction_buckets,
                    'win': win, 'win_buckets': win_buckets,
                    'coverage': {'verified_days': verified_days, 'pending': pending}}
        finally:
            conn.close()
