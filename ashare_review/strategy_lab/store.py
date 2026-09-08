"""pool_rows 表 + CRUD — 三策略每日快照存储（新 DB data/strategy_lab.db）"""
import os
import sqlite3
from typing import Dict, List

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS pool_rows (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date    TEXT NOT NULL,
  strategy      TEXT NOT NULL,
  rank          INTEGER,
  code          TEXT NOT NULL,
  name          TEXT NOT NULL DEFAULT '',
  sector        TEXT DEFAULT '',
  regime        TEXT DEFAULT '',
  amount_yi     REAL,
  vol_ratio     REAL,
  seal_ratio    REAL,
  gap_pct       REAL,
  open_px       REAL,
  high_px       REAL,
  low_px        REAL,
  close_px      REAL,
  next_return   REAL,
  extra         TEXT DEFAULT '',
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(trade_date, strategy, code)
)
'''

_COLS = ('trade_date strategy rank code name sector regime amount_yi vol_ratio '
         'seal_ratio gap_pct open_px high_px low_px close_px next_return extra').split()


class LabStore:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(_DATA_DIR, 'strategy_lab.db')
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def upsert_row(self, row: Dict) -> None:
        """同日同策略同 code 幂等覆盖（收盘后重跑记录只留最新快照）。

        覆盖列：rank/name/sector/regime/amount_yi/vol_ratio/seal_ratio/
                open_px/high_px/low_px/close_px/extra。
        gap_pct 与 next_return 是次日回填专用字段（backfill），upsert 不覆盖，
        避免 record 重跑（collectors 默认 None）把已回填的竞价涨幅/次日收益抹掉。
        """
        sql = ('INSERT INTO pool_rows (%s) VALUES (%s) '
               'ON CONFLICT(trade_date, strategy, code) DO UPDATE SET '
               'rank=excluded.rank, name=excluded.name, sector=excluded.sector, '
               'regime=excluded.regime, amount_yi=excluded.amount_yi, '
               'vol_ratio=excluded.vol_ratio, seal_ratio=excluded.seal_ratio, '
               'open_px=excluded.open_px, '
               'high_px=excluded.high_px, low_px=excluded.low_px, '
               'close_px=excluded.close_px, extra=excluded.extra') % (
                   ', '.join(_COLS), ', '.join('?' for _ in _COLS))
        vals = [row.get(c) for c in _COLS]
        vals[0] = str(vals[0])
        vals[1] = str(vals[1])
        vals[2] = int(vals[2]) if vals[2] is not None else None
        conn = self._connect()
        try:
            conn.execute(sql, vals)
            conn.commit()
        finally:
            conn.close()

    def rows(self, trade_date: str = None, strategy: str = None) -> List[Dict]:
        q = 'SELECT * FROM pool_rows WHERE 1=1'
        args = []
        if trade_date:
            q += ' AND trade_date=?'
            args.append(str(trade_date))
        if strategy:
            q += ' AND strategy=?'
            args.append(str(strategy))
        q += ' ORDER BY trade_date, strategy, rank'
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(q, args).fetchall()]
        finally:
            conn.close()

    def backfill(self, trade_date: str, code: str, strategy: str,
                 gap_pct: float = None, next_return: float = None) -> None:
        """只补空行：gap_pct/next_return 已有值则跳过；行不存在则静默返回。"""
        conn = self._connect()
        try:
            cur = conn.execute(
                'SELECT gap_pct, next_return FROM pool_rows '
                'WHERE trade_date=? AND code=? AND strategy=?',
                (str(trade_date), str(code), str(strategy))).fetchone()
            if cur is None:
                return
            new_gap = cur['gap_pct'] if cur['gap_pct'] is not None else gap_pct
            new_ret = cur['next_return'] if cur['next_return'] is not None else next_return
            conn.execute(
                'UPDATE pool_rows SET gap_pct=?, next_return=? '
                'WHERE trade_date=? AND code=? AND strategy=?',
                (new_gap, new_ret, str(trade_date), str(code), str(strategy)))
            conn.commit()
        finally:
            conn.close()

    def recorded_dates(self) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                'SELECT DISTINCT trade_date FROM pool_rows ORDER BY trade_date').fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def pending_status(self) -> Dict:
        conn = self._connect()
        try:
            out = {}
            for r in conn.execute(
                    'SELECT trade_date, COUNT(*) n, '
                    'SUM(CASE WHEN gap_pct IS NULL THEN 1 ELSE 0 END) gp, '
                    'SUM(CASE WHEN next_return IS NULL THEN 1 ELSE 0 END) rp '
                    'FROM pool_rows GROUP BY trade_date').fetchall():
                out[r['trade_date']] = {'n': r['n'], 'gap_pending': r['gp'], 'ret_pending': r['rp']}
            return out
        finally:
            conn.close()
