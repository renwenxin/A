"""策略验证台 — 快照存储（SQLite）"""
import json
import os
import sqlite3
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT NOT NULL,
  params TEXT NOT NULL,
  git_sha TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  metrics TEXT NOT NULL,
  equity_curve TEXT,
  trades_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_strategy ON snapshots(strategy_id, created_at);
"""

# 指标展示顺序 + better 方向（larger=True 表示数值越大越好；回撤为负值，越大=越浅=越好）
METRIC_META = [
    ('annual_return', '年化收益', True),
    ('max_drawdown', '最大回撤', True),
    ('sharpe', '夏普', True),
    ('win_rate', '胜率', True),
    ('profit_loss_ratio', '盈亏比', True),
    ('profit_factor', '利润因子', True),
    ('total_return', '累计收益', True),
    ('total_trades', '交易数', None),
]


class BenchStore:
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

    def upsert_snapshot(self, strategy_id: str, params: dict, git_sha: Optional[str],
                        metrics: dict, equity_curve: list, trades_count: int) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO snapshots (strategy_id, params, git_sha, metrics, equity_curve, trades_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (strategy_id, json.dumps(params, ensure_ascii=False), git_sha or '',
                 json.dumps(metrics, ensure_ascii=False),
                 json.dumps(equity_curve, ensure_ascii=False), trades_count))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        d['params'] = json.loads(d['params'] or '{}')
        d['metrics'] = json.loads(d['metrics'] or '{}')
        d['equity_curve'] = json.loads(d['equity_curve']) if d.get('equity_curve') else []
        return d

    def list_snapshots(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[dict]:
        conn = self._connect()
        try:
            if strategy_id:
                rows = conn.execute(
                    "SELECT * FROM snapshots WHERE strategy_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (strategy_id, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM snapshots ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,)).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def latest_snapshot(self, strategy_id: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE strategy_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (strategy_id,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def get_snapshot(self, snap_id: int) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def compare(self, id_a: int, id_b: int) -> Optional[Dict]:
        a = self.get_snapshot(id_a)
        b = self.get_snapshot(id_b)
        if not a or not b:
            return None
        metrics = []
        for key, label, larger_better in METRIC_META:
            va = a['metrics'].get(key)
            vb = b['metrics'].get(key)
            if va is None and vb is None:
                continue
            delta = None
            delta_pct = None
            better = None
            if va is not None and vb is not None:
                delta = round(vb - va, 4)
                if va != 0:
                    delta_pct = round((vb - va) / abs(va) * 100, 2)
                if larger_better is not None:
                    better = 'b' if vb > va else ('a' if vb < va else None)
            metrics.append({'key': key, 'label': label, 'a': va, 'b': vb,
                            'delta': delta, 'delta_pct': delta_pct, 'better': better})
        return {'a': a, 'b': b, 'metrics': metrics,
                'curves': {'a': a.get('equity_curve') or [], 'b': b.get('equity_curve') or []}}
