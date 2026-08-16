# 策略验证台（Strategy Bench）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一入口跑 5 个核心策略回测 → 标准绩效指标 → SQLite 历史快照 → 双快照对比，提供 /strategy_bench 页面。

**Architecture:** 新包 `ashare_review/strategy_bench/`（metrics=纯函数指标 / store=SQLite / adapters=5 策略适配器 / service=编排+后台任务），零侵入现有回测脚本（adapter 内部构造现有类并调用其 run，透传 tdx/ak，归一化逐笔交易）。

**Tech Stack:** Python 3 + sqlite3 + Flask + pytest + pandas/numpy（现有依赖）。

**设计依据:** `docs/superpowers/specs/2026-08-16-strategy-bench-design.md`（commit 0fd4fe9）

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `ashare_review/strategy_bench/__init__.py` | 包标记 |
| `ashare_review/strategy_bench/metrics.py` | 统一指标纯函数：build_equity_curve / compute_metrics |
| `ashare_review/strategy_bench/store.py` | BenchStore：快照 CRUD + compare |
| `ashare_review/strategy_bench/adapters/__init__.py` | 包标记 |
| `ashare_review/strategy_bench/adapters/base.py` | StrategyAdapter 基类 + 归一化辅助 |
| `ashare_review/strategy_bench/adapters/registry.py` | REGISTRY + get_adapter + list_adapters |
| `ashare_review/strategy_bench/adapters/v3.py` | 启动突破V3 |
| `ashare_review/strategy_bench/adapters/one_two.py` | 1进2接力 |
| `ashare_review/strategy_bench/adapters/ice.py` | 冰点抄底 |
| `ashare_review/strategy_bench/adapters/tail.py` | 尾盘战法 |
| `ashare_review/strategy_bench/adapters/zt_replica.py` | 涨停复制 |
| `ashare_review/strategy_bench/service.py` | run_backtest / 后台任务 JOBS |
| `ashare_review/web/app.py` | 修改：5 个路由 + 导航 |
| `ashare_review/web/templates/base.html` | 修改：导航加「🧪 策略验证台」 |
| `ashare_review/web/templates/strategy_bench.html` | 新建：页面 |
| `ashare_review/tests/test_strategy_bench.py` | 全部新测试 |

**关键约定：**
- 归一化交易 schema：`{'entry_date': 'YYYYMMDD', 'exit_date': 'YYYYMMDD', 'return_pct': float}`
- 现有回测类构造后**覆盖属性**注入 fake（不修改现有类）：`bt = V3Backtest(); bt.tdx = fake_tdx`；IceBottomBacktest 支持构造注入
- 日期格式转换：现有脚本多用 `'%Y-%m-%d'` → `str.replace('-', '')`
- 后台任务：模块级 `JOBS: Dict[str, dict]` + `threading.Lock`，线程内跑 adapter→metrics→store
- DB：`data/strategy_bench.db`（.gitignore 的 `*.db` 已覆盖）

---

### Task 1: 统一指标 metrics.py（纯函数）

**Files:**
- Create: `ashare_review/strategy_bench/__init__.py`（空）
- Create: `ashare_review/strategy_bench/metrics.py`
- Create: `ashare_review/tests/test_strategy_bench.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_strategy_bench.py
"""策略验证台单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 统一指标 ----------

def _trades():
    return [
        {'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 10.0},
        {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -5.0},
        {'entry_date': '20260812', 'exit_date': '20260813', 'return_pct': 6.0},
    ]


def test_metrics_basic():
    from ashare_review.strategy_bench.metrics import compute_metrics
    m = compute_metrics(_trades())
    assert m['total_trades'] == 3
    assert m['wins'] == 2 and m['losses'] == 1
    assert round(m['win_rate'], 1) == 66.7
    assert m['avg_win'] == 8.0 and m['avg_loss'] == 5.0
    assert m['profit_loss_ratio'] == 1.6
    assert m['profit_factor'] == 3.2
    assert round(m['total_return'], 2) == 10.77   # 1.1*0.95*1.06-1


def test_metrics_equity_curve():
    from ashare_review.strategy_bench.metrics import build_equity_curve
    curve = build_equity_curve(_trades())
    # 按 exit_date 排序累乘：10% → 1.1*0.95=4.5% → 1.045*1.06=10.77%
    assert curve == [['20260811', 10.0], ['20260812', 4.5], ['20260813', 10.77]]


def test_metrics_max_drawdown():
    from ashare_review.strategy_bench.metrics import compute_metrics
    trades = [
        {'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 20.0},
        {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -10.0},
        {'entry_date': '20260812', 'exit_date': '20260813', 'return_pct': -5.0},
    ]
    m = compute_metrics(trades)
    # 曲线: 20 → 1.2*0.9-1=8 → 1.08*0.95-1=2.6；峰值 20，谷值 2.6 → mdd=-17.4
    assert round(m['max_drawdown'], 2) == -17.4


def test_metrics_empty_and_edge():
    from ashare_review.strategy_bench.metrics import compute_metrics
    m = compute_metrics([])
    assert m['total_trades'] == 0
    assert m['win_rate'] is None and m['annual_return'] is None
    # 全部同收益 → std=0 → sharpe None
    same = [{'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 5.0},
            {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': 5.0}]
    m2 = compute_metrics(same)
    assert m2['sharpe'] is None
    # 单笔
    one = [{'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 3.0}]
    m3 = compute_metrics(one)
    assert m3['total_trades'] == 1 and m3['win_rate'] == 100.0


def test_metrics_annual_and_sharpe_with_calendar():
    from ashare_review.strategy_bench.metrics import compute_metrics
    from ashare_review.utils.calendar import TradingCalendar
    cal = TradingCalendar()
    # 2026-08-10(周一) ~ 2026-08-14(周五) = 5 个交易日
    trades = _trades()
    m = compute_metrics(trades, calendar=cal)
    # 跨度 5 交易日：年化 = 1.1077^(252/5)-1
    assert m['annual_return'] is not None
    # 夏普 = mean/std * sqrt(3*252/5)；mean=3.667, std=6.342 → 0.578*12.296 ≈ 7.11
    assert round(m['sharpe'], 2) == 7.11
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: FAIL — ModuleNotFoundError（strategy_bench 不存在）

- [ ] **Step 3: 实现 metrics.py**

```python
# ashare_review/strategy_bench/metrics.py
"""策略验证台 — 统一绩效指标（纯函数）

输入：归一化逐笔交易 [{entry_date, exit_date, return_pct}]
指标口径见 specs/2026-08-16-strategy-bench-design.md §4。
夏普为交易级近似：mean(r)/std(r) × √(年化交易次数)，口径一致可横评。
"""
import math
from datetime import datetime
from typing import Dict, List, Optional

from ..utils.calendar import TradingCalendar


def _ymd_to_date(s: str):
    try:
        return datetime.strptime(s, '%Y%m%d').date()
    except (ValueError, TypeError):
        return None


def build_equity_curve(trades: List[Dict]) -> List[list]:
    """按 exit_date 排序的累计收益序列：[[exit_date, 累计收益%], ...]"""
    ordered = sorted(trades, key=lambda t: str(t.get('exit_date', '')))
    cum = 1.0
    curve = []
    for t in ordered:
        cum *= 1 + float(t.get('return_pct', 0.0)) / 100.0
        curve.append([str(t.get('exit_date', '')), round((cum - 1) * 100, 4)])
    return curve


def compute_metrics(trades: List[Dict], calendar: Optional[TradingCalendar] = None) -> Dict:
    """统一指标。0 笔/无法计算 → 对应字段 None。"""
    if not trades:
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': None,
                'avg_win': None, 'avg_loss': None, 'profit_loss_ratio': None,
                'profit_factor': None, 'total_return': None, 'annual_return': None,
                'max_drawdown': None, 'sharpe': None}

    n = len(trades)
    wins = [t for t in trades if t['return_pct'] > 0]
    losses = [t for t in trades if t['return_pct'] <= 0]
    win_rate = len(wins) / n * 100
    avg_win = sum(t['return_pct'] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(abs(t['return_pct']) for t in losses) / len(losses) if losses else 0.0
    pl_ratio = round(avg_win / avg_loss, 4) if avg_loss > 0 else None
    total_profit = sum(t['return_pct'] for t in wins)
    total_loss = sum(abs(t['return_pct']) for t in losses)
    profit_factor = round(total_profit / total_loss, 4) if total_loss > 0 else None

    total_return = 1.0
    for t in trades:
        total_return *= 1 + t['return_pct'] / 100.0
    total_return_pct = round((total_return - 1) * 100, 4)

    curve = build_equity_curve(trades)
    peak = -math.inf
    max_drawdown = 0.0
    for _, v in curve:
        peak = max(peak, v)
        max_drawdown = min(max_drawdown, v - peak)

    annual_return = None
    sharpe = None
    if calendar:
        starts = [_ymd_to_date(t.get('entry_date')) for t in trades]
        ends = [_ymd_to_date(t.get('exit_date')) for t in trades]
        starts = [d for d in starts if d]
        ends = [d for d in ends if d]
        if starts and ends:
            span = calendar.trading_days_between(min(starts), max(ends))
            if span and span > 0:
                annual_return = round((total_return ** (252.0 / span) - 1) * 100, 4)
                trades_per_year = n * 252.0 / span
                rets = [t['return_pct'] for t in trades]
                mean_r = sum(rets) / n
                std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / n)
                if std_r > 0:
                    sharpe = round(mean_r / std_r * math.sqrt(trades_per_year), 4)

    return {'total_trades': n, 'wins': len(wins), 'losses': len(losses),
            'win_rate': round(win_rate, 2), 'avg_win': round(avg_win, 4),
            'avg_loss': round(avg_loss, 4), 'profit_loss_ratio': pl_ratio,
            'profit_factor': profit_factor, 'total_return': total_return_pct,
            'annual_return': annual_return, 'max_drawdown': round(max_drawdown, 4),
            'sharpe': sharpe}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add ashare_review/strategy_bench/__init__.py ashare_review/strategy_bench/metrics.py ashare_review/tests/test_strategy_bench.py
git commit -m "feat(bench): 统一绩效指标（权益曲线/年化/回撤/夏普）"
```

---

### Task 2: 快照存储 store.py（BenchStore）

**Files:**
- Create: `ashare_review/strategy_bench/store.py`
- Test: `ashare_review/tests/test_strategy_bench.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_strategy_bench.py

# ---------- Task 2: 快照存储 ----------

def _snapshot_metrics_a():
    return {'annual_return': 12.3, 'max_drawdown': -18.2, 'sharpe': 1.2,
            'win_rate': 55.0, 'profit_loss_ratio': 1.8, 'profit_factor': 2.0,
            'total_return': 40.0, 'total_trades': 50}


def _snapshot_metrics_b():
    return {'annual_return': 15.1, 'max_drawdown': -15.5, 'sharpe': 1.5,
            'win_rate': 58.0, 'profit_loss_ratio': 2.0, 'profit_factor': 2.3,
            'total_return': 48.0, 'total_trades': 52}


def test_store_snapshot_crud(tmp_path):
    from ashare_review.strategy_bench.store import BenchStore
    store = BenchStore(str(tmp_path / 't.db'))
    sid = store.upsert_snapshot('v3', {'lookback_days': 60}, 'abc1234',
                                _snapshot_metrics_a(), [['20260811', 10.0]], 50)
    assert sid > 0
    s = store.get_snapshot(sid)
    assert s['strategy_id'] == 'v3'
    assert s['params'] == {'lookback_days': 60}
    assert s['git_sha'] == 'abc1234'
    assert s['metrics']['win_rate'] == 55.0
    assert s['equity_curve'] == [['20260811', 10.0]]
    assert s['trades_count'] == 50


def test_store_list_and_latest(tmp_path):
    from ashare_review.strategy_bench.store import BenchStore
    store = BenchStore(str(tmp_path / 't.db'))
    store.upsert_snapshot('v3', {}, 'a', _snapshot_metrics_a(), [], 1)
    store.upsert_snapshot('one_two', {}, 'b', _snapshot_metrics_b(), [], 2)
    store.upsert_snapshot('v3', {}, 'c', _snapshot_metrics_a(), [], 3)
    lst = store.list_snapshots(strategy_id='v3')
    assert len(lst) == 2
    assert lst[0]['git_sha'] == 'c'          # 倒序
    assert len(store.list_snapshots()) == 3
    latest = store.latest_snapshot('v3')
    assert latest['git_sha'] == 'c'
    assert store.latest_snapshot('ice') is None


def test_store_compare(tmp_path):
    from ashare_review.strategy_bench.store import BenchStore
    store = BenchStore(str(tmp_path / 't.db'))
    id_a = store.upsert_snapshot('v3', {'lookback_days': 60}, 'a',
                                 _snapshot_metrics_a(), [], 50)
    id_b = store.upsert_snapshot('v3', {'lookback_days': 120}, 'b',
                                 _snapshot_metrics_b(), [], 52)
    cmp = store.compare(id_a, id_b)
    assert cmp['a']['id'] == id_a and cmp['b']['id'] == id_b
    by_key = {m['key']: m for m in cmp['metrics']}
    assert by_key['annual_return']['a'] == 12.3
    assert by_key['annual_return']['b'] == 15.1
    assert round(by_key['annual_return']['delta'], 1) == 2.8
    assert by_key['annual_return']['better'] == 'b'
    # 最大回撤 -15.5 > -18.2（更浅）→ better=b
    assert by_key['max_drawdown']['better'] == 'b'
    # total_trades 无 better
    assert by_key['total_trades']['better'] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: FAIL — ModuleNotFoundError（store 不存在）

- [ ] **Step 3: 实现 store.py**

```python
# ashare_review/strategy_bench/store.py
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
        return {'a': a, 'b': b, 'metrics': metrics}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: 8 passed（5 + 3）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/strategy_bench/store.py ashare_review/tests/test_strategy_bench.py
git commit -m "feat(bench): 快照存储 BenchStore（CRUD/对比）"
```

---

### Task 3: 适配器基类 + 归一化辅助

**Files:**
- Create: `ashare_review/strategy_bench/adapters/__init__.py`（空）
- Create: `ashare_review/strategy_bench/adapters/base.py`
- Test: `ashare_review/tests/test_strategy_bench.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_strategy_bench.py

# ---------- Task 3: 基类 + 注册表 + 归一化 ----------

def test_normalize_v3_style_trades():
    """v3/zt/ice 共用归一化：buy_date/sell_date('%Y-%m-%d') + net_ret(%)"""
    from ashare_review.strategy_bench.adapters.base import normalize_v3_style_trades
    raw = [
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 8.5, 'code': '600001'},
        {'buy_date': '2026-08-11', 'sell_date': '2026-08-12', 'net_ret': -3.2},
    ]
    trades = normalize_v3_style_trades(raw)
    assert trades == [
        {'entry_date': '20260810', 'exit_date': '20260814', 'return_pct': 8.5},
        {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -3.2},
    ]


def test_normalize_one_two_trades():
    """one_two：entry_date/exit_date(可能带'-') + return_pct"""
    from ashare_review.strategy_bench.adapters.base import normalize_one_two_trades
    raw = [
        {'entry_date': '2026-08-10', 'exit_date': '2026-08-11', 'return_pct': 6.0, 'result': 'win'},
        {'entry_date': '20260812', 'exit_date': '20260812', 'return_pct': -4.0, 'result': 'loss'},
    ]
    trades = normalize_one_two_trades(raw)
    assert trades[0] == {'entry_date': '20260810', 'exit_date': '20260811', 'return_pct': 6.0}
    assert trades[1]['return_pct'] == -4.0


def test_normalize_tail_signals():
    """尾盘：信号行 → trade_date 入场，open_ret(%) 为收益，exit=次日"""
    from ashare_review.strategy_bench.adapters.base import normalize_tail_signals
    import pandas as pd
    from ashare_review.utils.calendar import TradingCalendar
    cal = TradingCalendar()
    sig = pd.DataFrame([
        {'trade_date': '2026-08-10', 'open_ret': 2.5, 'signal': '超跌'},
        {'trade_date': '2026-08-11', 'open_ret': -1.0, 'signal': '平台突破'},
    ])
    trades = normalize_tail_signals(sig, 'open_ret', cal)
    assert len(trades) == 2
    assert trades[0]['entry_date'] == '20260810'
    assert trades[0]['return_pct'] == 2.5
    assert trades[0]['exit_date'] == '20260811'   # 2026-08-10 的下一个交易日


```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: FAIL — ImportError（adapters 不存在）

- [ ] **Step 3: 实现 base.py + registry.py**

```python
# ashare_review/strategy_bench/adapters/base.py
"""策略适配器基类 + 归一化辅助（不改动现有回测脚本）"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ...utils.calendar import TradingCalendar


def normalize_v3_style_trades(raw_trades: List[Dict]) -> List[Dict]:
    """v3/zt_replica/ice 共用：buy_date/sell_date('%Y-%m-%d') + net_ret(%) → 统一 schema"""
    trades = []
    for t in raw_trades or []:
        entry = str(t.get('buy_date', '')).replace('-', '')
        exit_ = str(t.get('sell_date', '')).replace('-', '')
        if not entry or not exit_:
            continue
        trades.append({'entry_date': entry, 'exit_date': exit_,
                       'return_pct': float(t.get('net_ret', 0.0))})
    return trades


def normalize_one_two_trades(raw_trades: List[Dict]) -> List[Dict]:
    """one_two：entry_date/exit_date + return_pct"""
    trades = []
    for t in raw_trades or []:
        entry = str(t.get('entry_date', '')).replace('-', '')
        exit_ = str(t.get('exit_date', '')).replace('-', '')
        if not entry:
            continue
        trades.append({'entry_date': entry, 'exit_date': exit_ or entry,
                       'return_pct': float(t.get('return_pct', 0.0))})
    return trades


def normalize_tail_signals(sig_df, ret_col: str,
                           calendar: TradingCalendar) -> List[Dict]:
    """尾盘：信号行 → trade_date 入场，ret_col(%) 为收益，exit=下一交易日"""
    if sig_df is None or sig_df.empty:
        return []
    trades = []
    for _, row in sig_df.iterrows():
        td = str(row.get('trade_date', ''))[:10].replace('-', '')
        ret = row.get(ret_col)
        if not td or ret is None or str(ret) == 'nan':
            continue
        try:
            from datetime import datetime
            d = datetime.strptime(td, '%Y%m%d').date()
            nxt = calendar.next_trading_day(d, offset=1)
        except (ValueError, TypeError):
            continue
        if nxt is None:
            continue
        trades.append({'entry_date': td, 'exit_date': nxt.strftime('%Y%m%d'),
                       'return_pct': float(ret)})
    return trades


class StrategyAdapter(ABC):
    strategy_id: str = ''
    name: str = ''
    description: str = ''
    param_schema: List[Dict] = []   # [{name,label,type,default,min,max,help}]

    @abstractmethod
    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        """返回归一化逐笔交易 [{entry_date, exit_date, return_pct}]"""
```



- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: 11 passed（8 + 3）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/strategy_bench/adapters ashare_review/tests/test_strategy_bench.py
git commit -m "feat(bench): 适配器基类/注册表/归一化辅助"
```

---

### Task 4: 5 个策略适配器 + 注册表

**Files:**
- Create: `ashare_review/strategy_bench/adapters/v3.py`
- Create: `ashare_review/strategy_bench/adapters/one_two.py`
- Create: `ashare_review/strategy_bench/adapters/ice.py`
- Create: `ashare_review/strategy_bench/adapters/tail.py`
- Create: `ashare_review/strategy_bench/adapters/zt_replica.py`
- Create: `ashare_review/strategy_bench/adapters/registry.py`
- Test: `ashare_review/tests/test_strategy_bench.py`（追加，仅测归一化纯函数与 schema）

- [ ] **Step 1: 写失败测试（归一化 + schema）**

```python
# 追加到 test_strategy_bench.py

# ---------- Task 4: 5 个适配器 ----------

def _make_adapter(id_):
    from ashare_review.strategy_bench.adapters.registry import get_adapter
    return get_adapter(id_)


def test_v3_adapter_normalize():
    from ashare_review.strategy_bench.adapters.v3 import V3Adapter
    a = V3Adapter()
    trades = a.normalize({'trades': [
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 8.5},
        {'buy_date': '2026-08-11', 'sell_date': '2026-08-12', 'net_ret': -3.2},
    ]})
    assert trades[0]['entry_date'] == '20260810' and trades[0]['return_pct'] == 8.5
    assert a.strategy_id == 'v3' and a.name == '启动突破V3'
    assert a.param_schema[0]['name'] == 'lookback_days'


def test_one_two_adapter_normalize():
    from ashare_review.strategy_bench.adapters.one_two import OneTwoAdapter
    a = OneTwoAdapter()
    trades = a.normalize({'valid_trades': [
        {'entry_date': '2026-08-10', 'exit_date': '2026-08-11', 'return_pct': 6.0},
        {'entry_date': '20260812', 'exit_date': '20260812', 'return_pct': -4.0},
    ]})
    assert trades[0]['entry_date'] == '20260810' and trades[0]['return_pct'] == 6.0
    assert trades[1]['return_pct'] == -4.0


def test_ice_adapter_normalize():
    from ashare_review.strategy_bench.adapters.ice import IceAdapter
    a = IceAdapter()
    trades = a.normalize([
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 5.0},
    ])
    assert trades[0]['entry_date'] == '20260810' and trades[0]['return_pct'] == 5.0


def test_zt_replica_adapter_normalize():
    from ashare_review.strategy_bench.adapters.zt_replica import ZTReplicaAdapter
    a = ZTReplicaAdapter()
    trades = a.normalize({'trades': [
        {'buy_date': '2026-08-10', 'sell_date': '2026-08-14', 'net_ret': 3.3},
    ]})
    assert trades[0]['return_pct'] == 3.3


def test_registry_completeness():
    from ashare_review.strategy_bench.adapters.registry import list_adapters, get_adapter
    ids = [a.strategy_id for a in list_adapters()]
    assert set(ids) == {'v3', 'one_two', 'ice', 'tail', 'zt_replica'}
    a = get_adapter('v3')
    assert a.name == '启动突破V3'
    assert get_adapter('nope') is None
    for adapter in list_adapters():
        for p in adapter.param_schema:
            assert {'name', 'label', 'type', 'default'} <= set(p), adapter.strategy_id


def test_adapters_params_schema_values():
    for sid, expect in [
        ('v3', ['lookback_days', 'max_positions']),
        ('one_two', ['lookback_days', 'top_n', 'min_score']),
        ('ice', ['lookback_days']),
        ('tail', ['days', 'limit']),
        ('zt_replica', ['lookback_days', 'only_double_cannon']),
    ]:
        a = _make_adapter(sid)
        names = [p['name'] for p in a.param_schema]
        assert set(expect) <= set(names), sid
        for p in a.param_schema:
            assert p['type'] in ('int', 'float', 'bool'), (sid, p['name'])
            assert 'default' in p
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: FAIL — ImportError/AttributeError（adapter 未实现 normalize/schema）

- [ ] **Step 3: 实现 5 个 adapter**

```python
# ashare_review/strategy_bench/adapters/v3.py
"""启动突破 V3 适配器"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_v3_style_trades


class V3Adapter(StrategyAdapter):
    strategy_id = 'v3'
    name = '启动突破V3'
    description = 'MAVOL180 放量突破 + 压力位突破（含资金/仓位模型）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 250, 'min': 20, 'max': 500},
        {'name': 'max_positions', 'label': '最大持仓数', 'type': 'int', 'default': 10, 'min': 1, 'max': 20},
    ]

    def normalize(self, result: dict) -> List[Dict]:
        return normalize_v3_style_trades((result or {}).get('trades', []))

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.v3_backtest import V3Backtest
        lookback = int(params.get('lookback_days', 250))
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=lookback)
        bt = V3Backtest()
        if tdx is not None:
            bt.tdx = tdx
        result = bt.run(start_date=start, end_date=end,
                        max_positions=int(params.get('max_positions', 10)))
        return self.normalize(result)
```

```python
# ashare_review/strategy_bench/adapters/one_two.py
"""1进2 接力适配器"""
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_one_two_trades


class OneTwoAdapter(StrategyAdapter):
    strategy_id = 'one_two'
    name = '1进2接力'
    description = '首板次日接力（双数据源：akshare 优先 + TDX 回退）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 60, 'min': 10, 'max': 250},
        {'name': 'top_n', 'label': '每日入选前N', 'type': 'int', 'default': 5, 'min': 1, 'max': 20},
        {'name': 'min_score', 'label': '最低评分', 'type': 'int', 'default': 0, 'min': 0, 'max': 100,
         'help': '0 使用脚本内置默认(40)'},
    ]

    def normalize(self, result: dict) -> List[Dict]:
        return normalize_one_two_trades((result or {}).get('valid_trades', []))

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.one_two_backtest import OneTwoBacktest
        bt = OneTwoBacktest()
        if tdx is not None:
            bt.tdx = tdx
        if ak is not None:
            bt.ak = ak
        result = bt.run(
            lookback_days=int(params.get('lookback_days', 60)),
            top_n=int(params.get('top_n', 5)),
            min_score=int(params.get('min_score', 0)),
        )
        return self.normalize(result)
```

```python
# ashare_review/strategy_bench/adapters/ice.py
"""冰点抄底适配器（需要 market_state 构建 state_df）"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_v3_style_trades


class IceAdapter(StrategyAdapter):
    strategy_id = 'ice'
    name = '冰点抄底'
    description = '冰点反转确认日收盘买入（缠论二买 + 超跌反弹）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 250, 'min': 60, 'max': 500},
    ]

    def normalize(self, raw_trades: List[Dict]) -> List[Dict]:
        return normalize_v3_style_trades(raw_trades)

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.strategy_regime.ice_backtest import IceBottomBacktest
        from ...analysis.strategy_regime import market_state
        from ...data.tdx_reader import TdxReader
        tdx = tdx or TdxReader()
        lookback = int(params.get('lookback_days', 250))
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=lookback)
        state_df = market_state.build_market_state(tdx, start, end)
        if state_df is None or state_df.empty:
            return []
        bt = IceBottomBacktest(tdx=tdx)
        raw = bt.run(state_df, start, end)
        return self.normalize(raw or [])
```

```python
# ashare_review/strategy_bench/adapters/tail.py
"""尾盘战法适配器（复刻 main() 全市场扫描流程）"""
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_tail_signals


class TailAdapter(StrategyAdapter):
    strategy_id = 'tail'
    name = '尾盘战法'
    description = '尾盘选股（超跌反弹 + 平台突破），次日开盘卖出'
    param_schema = [
        {'name': 'days', 'label': '回测交易日数', 'type': 'int', 'default': 250, 'min': 30, 'max': 500},
        {'name': 'limit', 'label': '扫描股票数(0=全部)', 'type': 'int', 'default': 0, 'min': 0, 'max': 10000,
         'help': '调试用：限制扫描只数，0 表示全市场'},
    ]

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        import pandas as pd
        from ...analysis import tail_session_backtest as tail
        from ...data.tdx_reader import TdxReader
        from ...utils.calendar import TradingCalendar
        tdx = tdx or TdxReader()
        cal = TradingCalendar()
        days = int(params.get('days', 250))
        limit = int(params.get('limit', 0))
        sig_params = {
            'drawdown_window': 60, 'drawdown_min': 25.0, 'daily_gain_min': 4.0,
            'box_window': 60, 'vol_ratio_min': 1.5, 'platform_width_max': 18.0,
        }
        stocks = [(c, m) for c, m in tdx.list_stocks() if m != 'bj' and tail.is_a_stock(c)]
        if limit > 0:
            stocks = stocks[:limit]
        all_signals = []
        for code, market in stocks:
            try:
                df = tdx.read_daily(code, market)
            except Exception:
                continue
            if df.empty or len(df) < 300:
                continue
            df = df.tail(days + 300).reset_index(drop=True)
            sig = tail.compute_signals(df, code, sig_params)
            if sig is not None and not sig.empty:
                cutoff = df['trade_date'].iloc[-days]
                sig = sig[pd.to_datetime(sig['trade_date']) >= pd.to_datetime(cutoff)]
                if not sig.empty:
                    all_signals.append(sig)
        if not all_signals:
            return []
        S = pd.concat(all_signals, ignore_index=True)
        return normalize_tail_signals(S, 'open_ret', cal)
```

注册表 `registry.py`（放在 5 个 adapter 之后）：

```python
# ashare_review/strategy_bench/adapters/registry.py
"""适配器注册表"""
from typing import Dict, List

from .base import StrategyAdapter
from .v3 import V3Adapter
from .one_two import OneTwoAdapter
from .ice import IceAdapter
from .tail import TailAdapter
from .zt_replica import ZTReplicaAdapter


def _build() -> Dict[str, StrategyAdapter]:
    adapters = [V3Adapter(), OneTwoAdapter(), IceAdapter(), TailAdapter(), ZTReplicaAdapter()]
    return {a.strategy_id: a for a in adapters}


def get_adapter(strategy_id: str) -> StrategyAdapter:
    return _build().get(strategy_id)


def list_adapters() -> List[StrategyAdapter]:
    return list(_build().values())
```

```python
# ashare_review/strategy_bench/adapters/zt_replica.py
"""涨停复制适配器"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from .base import StrategyAdapter, normalize_v3_style_trades


class ZTReplicaAdapter(StrategyAdapter):
    strategy_id = 'zt_replica'
    name = '涨停复制'
    description = '近期涨停回调企稳后的二次启动（含双响炮模式）'
    param_schema = [
        {'name': 'lookback_days', 'label': '回看天数', 'type': 'int', 'default': 365, 'min': 60, 'max': 500},
        {'name': 'only_double_cannon', 'label': '仅双响炮', 'type': 'bool', 'default': False},
    ]

    def normalize(self, result: dict) -> List[Dict]:
        return normalize_v3_style_trades((result or {}).get('trades', []))

    def run(self, params: Dict, tdx=None, ak=None) -> List[Dict]:
        from ...analysis.zt_replica_backtest import ZTReplicaBacktest
        lookback = int(params.get('lookback_days', 365))
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=lookback)
        bt = ZTReplicaBacktest(only_double_cannon=bool(params.get('only_double_cannon', False)))
        if tdx is not None:
            bt.tdx = tdx
        result = bt.run(start_date=start, end_date=end)
        return self.normalize(result)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: 17 passed（12 + 5）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/strategy_bench/adapters ashare_review/tests/test_strategy_bench.py
git commit -m "feat(bench): 5 个策略适配器（v3/one_two/ice/tail/zt_replica）"
```

---

### Task 5: 编排层 service.py（run_backtest + 后台任务）

**Files:**
- Create: `ashare_review/strategy_bench/service.py`
- Test: `ashare_review/tests/test_strategy_bench.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_strategy_bench.py

# ---------- Task 5: 编排层 ----------

def test_run_backtest_with_mocked_adapter(tmp_path, monkeypatch):
    import json
    from ashare_review.strategy_bench import service as bench_service
    from ashare_review.strategy_bench.store import BenchStore

    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))

    class FakeAdapter:
        strategy_id = 'v3'
        name = '启动突破V3'
        param_schema = []
        def run(self, params, tdx=None, ak=None):
            return [{'entry_date': '20260810', 'exit_date': '20260814', 'return_pct': 8.5},
                    {'entry_date': '20260811', 'exit_date': '20260812', 'return_pct': -3.2}]

    monkeypatch.setattr(bench_service, 'get_adapter', lambda sid: FakeAdapter())
    snap_id = bench_service.run_backtest('v3', {'lookback_days': 60})
    assert snap_id > 0
    store = BenchStore(str(tmp_path / 't.db'))
    s = store.get_snapshot(snap_id)
    assert s['strategy_id'] == 'v3'
    assert s['metrics']['total_trades'] == 2
    assert s['metrics']['win_rate'] == 50.0
    assert len(s['equity_curve']) == 2
    assert s['trades_count'] == 2


def test_run_backtest_bad_strategy(tmp_path, monkeypatch):
    from ashare_review.strategy_bench import service as bench_service
    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))
    monkeypatch.setattr(bench_service, 'get_adapter', lambda sid: None)
    assert bench_service.run_backtest('nope', {}) == 0


def test_job_lifecycle(tmp_path, monkeypatch):
    import threading
    from ashare_review.strategy_bench import service as bench_service
    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))

    class FakeAdapter:
        strategy_id = 'v3'
        def run(self, params, tdx=None, ak=None):
            return [{'entry_date': '20260810', 'exit_date': '20260814', 'return_pct': 1.0}]

    monkeypatch.setattr(bench_service, 'get_adapter', lambda sid: FakeAdapter())
    job_id = bench_service.start_job('v3', {'lookback_days': 60})
    # 轮询直到结束
    import time
    for _ in range(100):
        st = bench_service.get_job(job_id)
        if st['status'] in ('done', 'error'):
            break
        time.sleep(0.05)
    assert st['status'] == 'done'
    assert st['snapshot_id'] > 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: FAIL — ModuleNotFoundError（service 不存在）

- [ ] **Step 3: 实现 service.py**

```python
# ashare_review/strategy_bench/service.py
"""策略验证台 — 编排层（跑回测 + 后台任务）"""
import json
import os
import subprocess
import threading
import time
import uuid
from typing import Dict, List, Optional

from ..utils.calendar import TradingCalendar
from .metrics import compute_metrics
from .store import BenchStore

DB_PATH = os.environ.get(
    'BENCH_DB',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'strategy_bench.db'))

JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _git_sha() -> str:
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                           text=True, timeout=5, cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return r.stdout.strip()[:40] if r.returncode == 0 else ''
    except Exception:
        return ''


def run_backtest(strategy_id: str, params: dict, tdx=None, ak=None,
                 db_path: Optional[str] = None) -> int:
    """同步跑一次回测并落库，返回 snapshot_id；失败返回 0。"""
    db_path = db_path or DB_PATH
    from .adapters.registry import get_adapter
    adapter = get_adapter(strategy_id)
    if adapter is None:
        return 0
    try:
        trades = adapter.run(params or {}, tdx=tdx, ak=ak) or []
    except Exception:
        return 0
    if not trades:
        return 0
    metrics = compute_metrics(trades, calendar=TradingCalendar())
    from .metrics import build_equity_curve
    curve = build_equity_curve(trades)
    store = BenchStore(db_path)
    return store.upsert_snapshot(strategy_id, params or {}, _git_sha(),
                                 metrics, curve, len(trades))


def start_job(strategy_id: str, params: dict, tdx=None, ak=None) -> str:
    """启动后台回测任务，返回 job_id。"""
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        JOBS[job_id] = {'status': 'running', 'progress': '排队中', 'snapshot_id': None,
                        'error': None, 'started_at': time.time()}

    def _worker():
        try:
            JOBS[job_id]['progress'] = '回测运行中…'
            snap_id = run_backtest(strategy_id, params, tdx=tdx, ak=ak)
            with _JOBS_LOCK:
                JOBS[job_id]['status'] = 'done' if snap_id else 'error'
                JOBS[job_id]['snapshot_id'] = snap_id
                JOBS[job_id]['error'] = None if snap_id else '无有效交易或回测失败'
        except Exception as e:
            with _JOBS_LOCK:
                JOBS[job_id]['status'] = 'error'
                JOBS[job_id]['error'] = str(e)

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _JOBS_LOCK:
        return dict(JOBS.get(job_id) or {}) or None
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: 20 passed（17 + 3）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/strategy_bench/service.py ashare_review/tests/test_strategy_bench.py
git commit -m "feat(bench): 编排层 run_backtest + 后台任务"
```

---

### Task 6: app.py 路由 + 导航

**Files:**
- Modify: `ashare_review/web/app.py`（5 个路由）
- Modify: `ashare_review/web/templates/base.html`（导航）
- Test: `ashare_review/tests/test_strategy_bench.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_strategy_bench.py

# ---------- Task 6: Web 路由 ----------

def test_strategy_bench_page(tmp_path, monkeypatch):
    from ashare_review.strategy_bench import service as bench_service
    from ashare_review.web.app import app
    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/strategy_bench')
    assert rv.status_code == 200
    body = rv.data.decode('utf-8')
    assert '策略验证台' in body
    assert '启动突破V3' in body and '1进2接力' in body


def test_strategy_bench_run_api(tmp_path, monkeypatch):
    import unittest.mock as mock
    from ashare_review.strategy_bench import service as bench_service
    from ashare_review.web.app import app
    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))
    monkeypatch.setattr(bench_service, 'start_job', lambda sid, params: 'job123')
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.post('/api/strategy_bench/run', json={'strategy_id': 'v3', 'params': {'lookback_days': 60}})
    assert rv.status_code == 200
    assert rv.get_json()['job_id'] == 'job123'


def test_strategy_bench_compare_api(tmp_path, monkeypatch):
    from ashare_review.strategy_bench import service as bench_service
    from ashare_review.strategy_bench.store import BenchStore
    from ashare_review.web.app import app
    monkeypatch.setattr(bench_service, 'DB_PATH', str(tmp_path / 't.db'))
    store = BenchStore(str(tmp_path / 't.db'))
    id_a = store.upsert_snapshot('v3', {}, 'a', {'annual_return': 10.0, 'total_trades': 10}, [], 10)
    id_b = store.upsert_snapshot('v3', {}, 'b', {'annual_return': 12.0, 'total_trades': 12}, [], 12)
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get(f'/api/strategy_bench/compare?a={id_a}&b={id_b}')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['a']['id'] == id_a and data['b']['id'] == id_b
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: FAIL — 404/AssertionError（路由未实现）

- [ ] **Step 3: 实现路由 + 导航**

在 `app.py` 文件末尾（`/api/ledger/validate` 之后）加：

```python
# ======================================================================
# 策略验证台（统一回测 + 绩效对比）
# ======================================================================

@app.route('/strategy_bench')
def strategy_bench():
    from ..strategy_bench.adapters.registry import list_adapters
    from ..strategy_bench.service import DB_PATH
    from ..strategy_bench.store import BenchStore
    adapters = sorted(list_adapters(), key=lambda a: a.strategy_id)
    adapters_json = [{'strategy_id': a.strategy_id, 'name': a.name,
                      'description': a.description, 'param_schema': a.param_schema}
                     for a in adapters]
    store = BenchStore(DB_PATH)
    return render_template('strategy_bench.html',
                           adapters=adapters_json, snapshots=store.list_snapshots())


@app.route('/api/strategy_bench/run', methods=['POST'])
def api_strategy_bench_run():
    from ..strategy_bench.service import start_job
    data = request.get_json(silent=True) or {}
    strategy_id = data.get('strategy_id', '')
    params = data.get('params', {}) or {}
    job_id = start_job(strategy_id, params)
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/strategy_bench/job/<job_id>')
def api_strategy_bench_job(job_id):
    from ..strategy_bench.service import get_job
    job = get_job(job_id)
    if job is None:
        return jsonify({'error': 'job not found'}), 404
    return jsonify(job)


@app.route('/api/strategy_bench/snapshots')
def api_strategy_bench_snapshots():
    from ..strategy_bench.service import DB_PATH
    from ..strategy_bench.store import BenchStore
    strategy_id = request.args.get('strategy_id') or None
    store = BenchStore(DB_PATH)
    return jsonify({'snapshots': store.list_snapshots(strategy_id=strategy_id)})


@app.route('/api/strategy_bench/compare')
def api_strategy_bench_compare():
    from ..strategy_bench.service import DB_PATH
    from ..strategy_bench.store import BenchStore
    try:
        id_a = int(request.args.get('a', 0))
        id_b = int(request.args.get('b', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid id'}), 400
    if id_a <= 0 or id_b <= 0:
        return jsonify({'error': 'invalid id'}), 400
    store = BenchStore(DB_PATH)
    cmp = store.compare(id_a, id_b)
    if cmp is None:
        return jsonify({'error': 'snapshot not found'}), 404
    return jsonify(cmp)
```

在 `base.html` 导航的"预测台账"之后加：

```html
            <a href="/strategy_bench" class="nav-item {% if request.endpoint == 'strategy_bench' %}active{% endif %}">
                <span class="nav-icon">🧪</span>
                <span class="nav-label">策略验证台</span>
            </a>
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py -v`
Expected: 23 passed（20 + 3）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/web/app.py ashare_review/web/templates/base.html ashare_review/tests/test_strategy_bench.py
git commit -m "feat(bench): 路由（页面/运行/轮询/快照/对比）+ 导航"
```

---

### Task 7: 页面 strategy_bench.html

**Files:**
- Create: `ashare_review/web/templates/strategy_bench.html`

- [ ] **Step 1: 写模板**

```html
{% extends "base.html" %}
{% block title %}策略验证台 · 竞价交易系统{% endblock %}
{% block content %}
<div class="content-area">
    <div class="page-header">
        <div>
            <div class="page-title">🧪 策略验证台</div>
            <div class="page-date">统一回测 · 标准绩效 · 历史快照 · 双快照对比</div>
        </div>
    </div>

    <div class="card">
        <div class="card-header">
            ① 选择策略
            <span class="card-badge">5 个核心战法</span>
        </div>
        <div class="card-body">
            <div style="display:flex;gap:8px;flex-wrap:wrap;" id="strategy-tabs">
                {% for a in adapters %}
                <button class="btn btn-secondary btn-sm strategy-tab" data-id="{{ a.strategy_id }}"
                        {% if loop.first %}style="background:#4f46e5;color:#fff;"{% endif %}>
                    {{ a.name }}
                </button>
                {% endfor %}
                <!-- adapters 为 dict 列表（路由已序列化），JS 里用 ADAPTER_MAP[sid].param_schema -->
            </div>
            <div id="adapter-desc" style="margin-top:8px;color:#666;font-size:.9em;"></div>
            <div id="param-form" style="margin-top:12px;display:flex;gap:12px;flex-wrap:wrap;"></div>
            <div style="margin-top:14px;display:flex;align-items:center;gap:12px;">
                <button class="btn btn-primary" id="btn-run">⚡ 运行回测</button>
                <span id="run-status" style="font-size:.9em;color:#666;"></span>
            </div>
            <div id="progress-bar" style="display:none;margin-top:10px;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden;">
                <div id="progress-fill" style="height:100%;width:0%;background:#4f46e5;transition:width .4s;"></div>
            </div>
        </div>
    </div>

    <div id="result-section" style="display:none;">
        <div class="section-title">📊 回测结果</div>
        <div class="stat-row" id="metric-cards"></div>
        <div class="card" style="margin-top:12px;">
            <div class="card-header">📈 权益曲线</div>
            <div class="card-body">
                <svg id="equity-chart" width="100%" height="220" viewBox="0 0 800 220" preserveAspectRatio="none"></svg>
            </div>
        </div>
    </div>

    <div class="section-title">📚 历史快照</div>
    <div class="card">
        <div class="card-body no-padding">
            <div class="table-wrap">
                <table class="sector-table">
                    <tr><th></th><th>策略</th><th>时间</th><th>git</th><th>参数</th>
                        <th>年化</th><th>回撤</th><th>夏普</th><th>胜率</th><th>交易数</th></tr>
                    <tbody>
                    {% for s in snapshots %}
                    <tr data-snap-id="{{ s.id }}" data-strategy="{{ s.strategy_id }}">
                        <td><input type="checkbox" class="snap-check"></td>
                        <td>{{ s.strategy_id }}</td>
                        <td style="font-size:.85em;">{{ s.created_at }}</td>
                        <td style="font-size:.85em;">{{ s.git_sha[:7] if s.git_sha }}</td>
                        <td style="font-size:.85em;color:#666;">{{ s.params | tojson }}</td>
                        <td>{{ '%.1f%%' % s.metrics.annual_return if s.metrics.get('annual_return') is not none else '—' }}</td>
                        <td>{{ '%.1f%%' % s.metrics.max_drawdown if s.metrics.get('max_drawdown') is not none else '—' }}</td>
                        <td>{{ '%.2f' % s.metrics.sharpe if s.metrics.get('sharpe') is not none else '—' }}</td>
                        <td>{{ '%.1f%%' % s.metrics.win_rate if s.metrics.get('win_rate') is not none else '—' }}</td>
                        <td>{{ s.trades_count }}</td>
                    </tr>
                    {% endfor %}
                    {% if not snapshots %}
                    <tr><td colspan="10" style="color:#888;text-align:center;">暂无快照，运行一次回测后出现</td></tr>
                    {% endif %}
                    </tbody>
                </table>
            </div>
        </div>
        <div class="card-body">
            <button class="btn btn-secondary btn-sm" id="btn-compare" disabled>🔀 对比选中快照</button>
            <span id="compare-status" style="font-size:.9em;color:#666;margin-left:8px;"></span>
        </div>
    </div>

    <div id="compare-section" style="display:none;margin-top:16px;">
        <div class="section-title">🔀 快照对比</div>
        <div class="card">
            <div class="card-body no-padding">
                <div class="table-wrap">
                    <table class="sector-table">
                        <tr><th>指标</th><th>快照 A</th><th>快照 B</th><th>Δ</th><th>更优</th></tr>
                        <tbody id="compare-body"></tbody>
                    </table>
                </div>
            </div>
        </div>
        <div class="card" style="margin-top:12px;">
            <div class="card-header">权益曲线叠加</div>
            <div class="card-body">
                <svg id="compare-chart" width="100%" height="220" viewBox="0 0 800 220" preserveAspectRatio="none"></svg>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
(function () {
    // 策略元数据（服务端注入）
    var ADAPTERS = {{ adapters | tojson }};
    var ADAPTER_MAP = {};
    ADAPTERS.forEach(function (a) { ADAPTER_MAP[a.strategy_id] = a; });
    var current = ADAPTERS.length ? ADAPTERS[0].strategy_id : '';

    function renderForm(sid) {
        var a = ADAPTER_MAP[sid];
        if (!a) return;
        document.getElementById('adapter-desc').textContent = a.description || '';
        var box = document.getElementById('param-form');
        box.innerHTML = '';
        a.param_schema.forEach(function (p) {
            var label = document.createElement('label');
            label.style.cssText = 'display:flex;flex-direction:column;font-size:.85em;gap:4px;';
            label.innerHTML = '<span>' + p.label + '</span>';
            var input;
            if (p.type === 'bool') {
                input = document.createElement('input');
                input.type = 'checkbox';
                input.checked = !!p.default;
                input.dataset.param = p.name;
            } else {
                input = document.createElement('input');
                input.type = 'number';
                input.value = p.default;
                input.min = p.min; input.max = p.max;
                input.style.cssText = 'padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;width:120px;';
                input.dataset.param = p.name;
            }
            label.appendChild(input);
            box.appendChild(label);
        });
    }

    function currentParams() {
        var params = {};
        document.querySelectorAll('#param-form [data-param]').forEach(function (el) {
            params[el.dataset.param] = el.type === 'checkbox' ? el.checked
                : (el.type === 'number' ? Number(el.value) : el.value);
        });
        return params;
    }

    // 策略 tab
    document.querySelectorAll('.strategy-tab').forEach(function (btn) {
        btn.addEventListener('click', function () {
            current = btn.dataset.id;
            document.querySelectorAll('.strategy-tab').forEach(function (b) {
                b.style.background = (b === btn) ? '#4f46e5' : '';
                b.style.color = (b === btn) ? '#fff' : '';
            });
            renderForm(current);
        });
    });

    // 运行
    document.getElementById('btn-run').addEventListener('click', function () {
        var btn = this;
        btn.disabled = true;
        document.getElementById('progress-bar').style.display = 'block';
        var status = document.getElementById('run-status');
        status.textContent = '启动中…';
        fetch('/api/strategy_bench/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({strategy_id: current, params: currentParams()})
        }).then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d.ok) { status.textContent = '❌ ' + (d.error || '启动失败'); btn.disabled = false; return; }
            pollJob(d.job_id, btn, status);
          })
          .catch(function () { status.textContent = '❌ 启动失败'; btn.disabled = false; });
    });

    function pollJob(jobId, btn, status) {
        var fill = document.getElementById('progress-fill');
        fetch('/api/strategy_bench/job/' + jobId)
          .then(function (r) { return r.json(); })
          .then(function (j) {
            status.textContent = j.progress || j.status;
            fill.style.width = (j.status === 'done' || j.status === 'error') ? '100%' : '60%';
            if (j.status === 'done') {
                status.textContent = '✅ 回测完成';
                setTimeout(function () { location.reload(); }, 600);
            } else if (j.status === 'error') {
                status.textContent = '❌ ' + (j.error || '回测失败');
                btn.disabled = false;
            } else {
                setTimeout(function () { pollJob(jobId, btn, status); }, 2000);
            }
          })
          .catch(function () { status.textContent = '❌ 轮询失败'; btn.disabled = false; });
    }

    // 快照对比
    var checks = document.querySelectorAll('.snap-check');
    checks.forEach(function (c) {
        c.addEventListener('change', function () {
            var n = document.querySelectorAll('.snap-check:checked').length;
            document.getElementById('btn-compare').disabled = n !== 2;
        });
    });
    document.getElementById('btn-compare').addEventListener('click', function () {
        var ids = [];
        document.querySelectorAll('.snap-check:checked').forEach(function (c) {
            ids.push(c.closest('tr').dataset.snapId);
        });
        fetch('/api/strategy_bench/compare?a=' + ids[0] + '&b=' + ids[1])
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.error) { document.getElementById('compare-status').textContent = '❌ ' + d.error; return; }
            renderCompare(d);
          });
    });

    function renderCompare(d) {
        document.getElementById('compare-section').style.display = 'block';
        var body = document.getElementById('compare-body');
        body.innerHTML = '';
        d.metrics.forEach(function (m) {
            var tr = document.createElement('tr');
            var better = m.better ? (m.better === 'b' ? 'B' : 'A') : '—';
            tr.innerHTML = '<td>' + m.label + '</td><td>' + fmt(m.a) + '</td><td>' + fmt(m.b) + '</td>' +
                '<td>' + (m.delta !== null && m.delta !== undefined ? fmt(m.delta, true) : '—') + '</td>' +
                '<td>' + better + '</td>';
            body.appendChild(tr);
        });
        drawCurve(document.getElementById('compare-chart'), [d.curves.a, d.curves.b], ['#4f46e5', '#16a34a']);
    }

    function fmt(v, signed) {
        if (v === null || v === undefined) return '—';
        var s = (signed && v > 0) ? '+' : '';
        return s + (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));
    }

    function drawCurve(svg, curves, colors) {
        var W = 800, H = 220, pad = 12;
        var all = [];
        curves.forEach(function (c) { all = all.concat(c || []); });
        if (!all.length) { return; }
        var vals = all.map(function (p) { return p[1]; });
        var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
        var span = (mx - mn) || 1;
        var xmax = all.length - 1;
        function pt(p, i) {
            return [(pad + i / Math.max(xmax, 1) * (W - 2 * pad)).toFixed(1),
                    (H - pad - (p[1] - mn) / span * (H - 2 * pad)).toFixed(1)];
        }
        svg.innerHTML = '';
        curves.forEach(function (c) {
            if (!c || !c.length) return;
            var d = '';
            c.forEach(function (p, i) { var xy = pt(p, i); d += (i ? 'L' : 'M') + xy[0] + ' ' + xy[1]; });
            var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', d);
            path.setAttribute('fill', 'none');
            path.setAttribute('stroke', colors[curves.indexOf(c)]);
            path.setAttribute('stroke-width', '2');
            svg.appendChild(path);
        });
    }

    if (current) renderForm(current);
})();
</script>
{% endblock %}
```

- [ ] **Step 2: 冒烟测试**

Run: `python -m pytest ashare_review/tests/test_strategy_bench.py::test_strategy_bench_page -v`
Expected: PASS（页面 200，含 5 策略名）

- [ ] **Step 3: 提交**

```bash
git add ashare_review/web/templates/strategy_bench.html
git commit -m "feat(bench): 验证台页面（tab/表单/进度/指标卡/曲线/快照/对比）"
```

---

### Task 8: 全量回归 + 真实冒烟 + 推送

- [ ] **Step 1: 全量测试**

Run: `python -m pytest ashare_review/tests -q`
Expected: 全部通过（75 既有 + 23 新增 = 98 passed）

- [ ] **Step 2: 真实冒烟（小参数，逐策略验证归一化+落库）**

Run（v3，最快）：
```bash
python -c "from ashare_review.strategy_bench.service import run_backtest, DB_PATH; from ashare_review.strategy_bench.store import BenchStore; sid = run_backtest('v3', {'lookback_days': 60}); print('snapshot_id:', sid); s = BenchStore(DB_PATH).get_snapshot(sid) if sid else None; print('metrics:', s['metrics'] if s else None)"
```
Expected: snapshot_id > 0，metrics 含 total_trades/win_rate/annual_return 等

再各跑一次（可并行/先后）：
- one_two：`run_backtest('one_two', {'lookback_days': 30, 'top_n': 5})`
- zt_replica：`run_backtest('zt_replica', {'lookback_days': 120})`
- ice：`run_backtest('ice', {'lookback_days': 250})`（需 market_state 构建，较慢）
- tail：`run_backtest('tail', {'days': 60, 'limit': 300})`（限 300 只加速）

若有策略返回 0（无交易/数据不足），记录但不阻塞——确认非代码异常即可。

- [ ] **Step 3: 验证 DB**

Run: `python -c "import sqlite3; c=sqlite3.connect('ashare_review/data/strategy_bench.db'); print(c.execute('select strategy_id,count(*),max(created_at) from snapshots group by 1').fetchall())"`
Expected: 每个策略至少一条快照（或明确记录了失败的策略）

- [ ] **Step 4: 提交推送**

```bash
git status   # 确认只含本功能文件
git add ashare_review/strategy_bench ashare_review/web/app.py ashare_review/web/templates/strategy_bench.html ashare_review/web/templates/base.html ashare_review/tests/test_strategy_bench.py
git commit -m "feat(bench): 策略验证台完整交付"
git push origin main
```
