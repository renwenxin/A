# ashare_review/one_two_v2/ledger.py
"""今日1进2 — SQLite 结果台账 + 维度命中率统计"""
import json
import os
import sqlite3
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pick_date TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT DEFAULT '',
  score REAL,
  dimensions TEXT DEFAULT '{}',
  tactic TEXT DEFAULT 'auction',
  next_date TEXT,
  next_result TEXT,
  hit INTEGER,
  auction_ratio REAL,
  mcap REAL,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pick ON picks(pick_date, code);
CREATE INDEX IF NOT EXISTS idx_pick_date ON picks(pick_date);
"""


class Ledger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self._conn()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def record_pick(self, pick_date: str, code: str, name: str, score: float,
                    dimensions: dict, tactic: str, mcap: Optional[float] = None) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO picks (pick_date, code, name, score, dimensions, tactic, mcap) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pick_date, code, name, score, json.dumps(dimensions, ensure_ascii=False), tactic, mcap))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def verify_pick(self, pick_date: str, code: str, next_result: str,
                    hit: int, auction_ratio: Optional[float] = None) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE picks SET next_result=?, hit=?, auction_ratio=? "
                "WHERE pick_date=? AND code=? AND hit IS NULL",
                (next_result, hit, auction_ratio, pick_date, code))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def get_pick(self, pick_date: str, code: str) -> Optional[dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM picks WHERE pick_date=? AND code=?", (pick_date, code)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_pending(self) -> List[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM picks WHERE hit IS NULL ORDER BY pick_date").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_picks(self, pick_date: str) -> List[dict]:
        conn = self._conn()
        try:
            if pick_date:
                rows = conn.execute(
                    "SELECT * FROM picks WHERE pick_date=? ORDER BY score DESC", (pick_date,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM picks ORDER BY pick_date DESC, score DESC LIMIT 30").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def dimension_stats(self) -> Dict:
        """各维度正分 vs 非正分命中率对比 + 按战法统计"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT dimensions, tactic, hit FROM picks WHERE hit IS NOT NULL").fetchall()
        finally:
            conn.close()
        dims = {}
        by_tactic = {}
        for r in rows:
            try:
                d = json.loads(r['dimensions'] or '{}')
            except Exception:
                d = {}
            t = r['tactic'] or 'auction'
            b = by_tactic.setdefault(t, {'total': 0, 'hit': 0})
            b['total'] += 1
            if r['hit'] == 1:
                b['hit'] += 1
            for k, v in d.items():
                s = v.get('score', 0) if isinstance(v, dict) else v
                pos = 'pos' if s > 0 else 'neg'
                e = dims.setdefault(k, {'pos_total': 0, 'pos_hit': 0, 'neg_total': 0, 'neg_hit': 0})
                e[f'{pos}_total'] += 1
                if r['hit'] == 1:
                    e[f'{pos}_hit'] += 1
        for k in dims:
            e = dims[k]
            e['pos_rate'] = round(e['pos_hit'] / e['pos_total'], 4) if e['pos_total'] else None
            e['neg_rate'] = round(e['neg_hit'] / e['neg_total'], 4) if e['neg_total'] else None
        for t in by_tactic:
            b = by_tactic[t]
            b['rate'] = round(b['hit'] / b['total'], 4) if b['total'] else None
        return {'dimensions': dims, 'by_tactic': by_tactic}
