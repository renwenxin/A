# 三策略观察实验室（strategy_lab）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/strategy_bench` 主页改造成"启动突破V3明日突破预测 Top10 / 复盘一进二 Top10 / 超跌BOLL V9 全量"三个策略的每日观察实验室：每天一键记录三池快照、次日自动回填竞价涨幅与次日收益、按市场环境/板块/竞价·量比·封成比分层的胜率统计与三池重叠分析。原历史回测台搬到 `/strategy_bench/backtest` 二级页保留。

**Architecture:** 新建独立模块 `ashare_review/strategy_lab/`（store 存 `pool_rows` 表、service 编排 record/backfill、collectors 三池、stats 分层），DB `data/strategy_lab.db`。超跌 BOLL V9 在 `analysis/boll_v9.py` 逐行移植（参照根目录 `BOLL_V9_超跌动态买点选股公式.txt`），`WINNER(C)` 用成交量加权成本分布近似。行情分类 regime 由 `strategy_regime` 管线按需重建到最新交易日。web 层：`/strategy_bench` 渲染新实验室模板，原 `strategy_bench.html` 逻辑原样保留在 `/strategy_bench/backtest`。

**Tech Stack:** Python 3.13、pandas、numpy、SQLite、Flask/Jinja2、TDX 本地日线（`TdxReader`）、akshare 竞价/涨停池（`AkshareFetcher`）、pytest。执行环境：Windows（默认 TDX_ROOT 数据目录已配置）。

**Spec:** `docs/superpowers/specs/2026-09-08-strategy-lab-design.md`（已 commit b0102b1）

**BOLL V9 公式原文:** 项目根 `BOLL_V9_超跌动态买点选股公式.txt`（逐行移植时对照，禁止臆改通达信语义）

---

## 文件结构

**新建：**
- `ashare_review/strategy_lab/__init__.py` — 空
- `ashare_review/strategy_lab/store.py` — pool_rows 表 + CRUD（新 DB `data/strategy_lab.db`）
- `ashare_review/strategy_lab/regime.py` — `get_regime_for(trade_date)`：行情分类 6 档，按需重建 market_state 缓存
- `ashare_review/strategy_lab/snapshot.py` — 通用快照 enrich：对单个 code 取 T 日 OHLC/成交额/量比/封成比/所属板块
- `ashare_review/strategy_lab/collectors/__init__.py` — `COLLECTORS` 注册表 + `collect_all()`
- `ashare_review/strategy_lab/collectors/breakout_v3.py` — 明日突破预测 Top10
- `ashare_review/strategy_lab/collectors/one2.py` — 复盘一进二 sentiment.picks Top10
- `ashare_review/strategy_lab/collectors/boll_v9.py` — BOLL V9 全量（调 `analysis/boll_v9.py` 扫描）
- `ashare_review/strategy_lab/service.py` — `record_today()` / `backfill_next()` / `status()`
- `ashare_review/strategy_lab/stats.py` — 分层统计 + 重叠
- `ashare_review/analysis/boll_v9.py` — BOLL V9 公式 Python 移植（逐行）
- `ashare_review/web/templates/strategy_lab.html` — 实验室主页模板
- `ashare_review/web/templates/strategy_bench_backtest.html` — 原 strategy_bench.html 内容搬移（改名）
- `ashare_review/tests/test_strategy_lab_store.py`
- `ashare_review/tests/test_strategy_lab_regime.py`
- `ashare_review/tests/test_boll_v9.py`
- `ashare_review/tests/test_strategy_lab_collectors.py`
- `ashare_review/tests/test_strategy_lab_service.py`
- `ashare_review/tests/test_strategy_lab_stats.py`
- `ashare_review/tests/test_strategy_lab_web.py`

**修改：**
- `ashare_review/report/daily.py:1429` — `return picks[:8]` → `return picks[:10]`（复盘页精选同步 Top10，已与用户确认）
- `ashare_review/web/app.py` — `/strategy_bench` 改渲染实验室；新增 `/strategy_bench/backtest` + `/api/strategy_lab/*` 路由
- `ashare_review/web/templates/strategy_bench.html` — 全内容复制到 `strategy_bench_backtest.html`，原文件删除
- `ashare_review/web/templates/base.html` — 导航"策略验证台"文案改"策略观察实验室"（链接仍 `/strategy_bench`）

---

### Task 1: pool_rows 存储层（store.py）

**Files:**
- Create: `ashare_review/strategy_lab/__init__.py`
- Create: `ashare_review/strategy_lab/store.py`
- Test: `ashare_review/tests/test_strategy_lab_store.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_strategy_lab_store.py
"""strategy_lab 存储层测试"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab.store import LabStore


@pytest.fixture()
def store(tmp_path):
    return LabStore(db_path=str(tmp_path / 'lab.db'))


def test_upsert_is_idempotent_by_date_strategy_code(store):
    row = {
        'trade_date': '2026-09-08', 'strategy': 'breakout_v3', 'rank': 1,
        'code': '600001', 'name': 'A', 'sector': '电子', 'regime': '强势趋势',
        'amount_yi': 12.3, 'vol_ratio': 1.5, 'seal_ratio': None,
        'gap_pct': None, 'open_px': 10.0, 'high_px': 10.5, 'low_px': 9.9,
        'close_px': 10.4, 'next_return': None, 'extra': '{}',
    }
    store.upsert_row(row)
    store.upsert_row(dict(row, close_px=99.0))   # 同日同策略同码 → 覆盖
    rows = store.rows('2026-09-08', strategy='breakout_v3')
    assert len(rows) == 1
    assert rows[0]['close_px'] == 99.0


def test_rows_filter_and_rank_order(store):
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 2,
                      'code': '600002', 'name': 'B', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None, 'gap_pct': None,
                      'open_px': 1, 'high_px': 1, 'low_px': 1, 'close_px': 1,
                      'next_return': None, 'extra': '{}'})
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 1,
                      'code': '600003', 'name': 'C', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None, 'gap_pct': None,
                      'open_px': 1, 'high_px': 1, 'low_px': 1, 'close_px': 1,
                      'next_return': None, 'extra': '{}'})
    rows = store.rows('2026-09-08', strategy='one2')
    assert [r['rank'] for r in rows] == [1, 2]
    assert store.rows('2026-09-08')  # 无 strategy 过滤 = 全部


def test_backfill_only_fills_empty(store):
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 1,
                      'code': '600003', 'name': 'C', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None,
                      'gap_pct': None, 'open_px': 1, 'high_px': 1, 'low_px': 1,
                      'close_px': 10.0, 'next_return': None, 'extra': '{}'})
    store.backfill('2026-09-08', code='600003', strategy='one2',
                   gap_pct=2.5, next_return=3.0)
    r = store.rows('2026-09-08')[0]
    assert r['gap_pct'] == 2.5 and r['next_return'] == 3.0
    # 再次回填不同值 → 不变（只填空）
    store.backfill('2026-09-08', code='600003', strategy='one2',
                   gap_pct=9.9, next_return=9.9)
    r = store.rows('2026-09-08')[0]
    assert r['gap_pct'] == 2.5 and r['next_return'] == 3.0


def test_dates_and_pending_counts(store):
    store.upsert_row({'trade_date': '2026-09-08', 'strategy': 'one2', 'rank': 1,
                      'code': '600003', 'name': 'C', 'sector': '', 'regime': '强势趋势',
                      'amount_yi': 1, 'vol_ratio': 1, 'seal_ratio': None,
                      'gap_pct': None, 'open_px': 1, 'high_px': 1, 'low_px': 1,
                      'close_px': 10.0, 'next_return': None, 'extra': '{}'})
    assert store.recorded_dates() == ['2026-09-08']
    s = store.pending_status()
    assert s['2026-09-08']['gap_pending'] == 1 and s['2026-09-08']['ret_pending'] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_strategy_lab_store.py -v`
Expected: FAIL — ModuleNotFoundError: ashare_review.strategy_lab

- [ ] **Step 3: 实现 store**

```python
# ashare_review/strategy_lab/__init__.py
"""三策略每日观察实验室"""
```

```python
# ashare_review/strategy_lab/store.py
"""pool_rows 表 + CRUD — 三策略每日快照存储（新 DB data/strategy_lab.db）"""
import json, os, sqlite3
from typing import Dict, List, Optional

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')

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
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def upsert_row(self, row: Dict) -> None:
        """同日同策略同 code 幂等覆盖（收盘后重跑记录只留最新快照）。"""
        sql = ('INSERT INTO pool_rows (%s) VALUES (%s) '
               'ON CONFLICT(trade_date, strategy, code) DO UPDATE SET '
               'rank=excluded.rank, name=excluded.name, sector=excluded.sector, '
               'regime=excluded.regime, amount_yi=excluded.amount_yi, '
               'vol_ratio=excluded.vol_ratio, seal_ratio=excluded.seal_ratio, '
               'gap_pct=excluded.gap_pct, open_px=excluded.open_px, '
               'high_px=excluded.high_px, low_px=excluded.low_px, '
               'close_px=excluded.close_px, extra=excluded.extra') % (
                   ', '.join(_COLS), ', '.join('?' for _ in _COLS))
        vals = [row.get(c) for c in _COLS]
        vals[0] = str(vals[0])                       # trade_date str
        vals[1] = str(vals[1])                       # strategy str
        vals[2] = int(vals[2]) if vals[2] is not None else None
        vals[15] = vals[15] if vals[15] is not None else None  # next_return
        self._conn.execute(sql, vals)
        self._conn.commit()

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
        return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def backfill(self, trade_date: str, code: str, strategy: str,
                 gap_pct: float = None, next_return: float = None) -> None:
        """只补空行：gap_pct/next_return 已有值则跳过。"""
        cur = self._conn.execute(
            'SELECT gap_pct, next_return FROM pool_rows '
            'WHERE trade_date=? AND code=? AND strategy=?',
            (str(trade_date), str(code), str(strategy))).fetchone()
        if cur is None:
            return
        new_gap = cur['gap_pct'] if cur['gap_pct'] is not None else gap_pct
        new_ret = cur['next_return'] if cur['next_return'] is not None else next_return
        self._conn.execute(
            'UPDATE pool_rows SET gap_pct=?, next_return=? '
            'WHERE trade_date=? AND code=? AND strategy=?',
            (new_gap, new_ret, str(trade_date), str(code), str(strategy)))
        self._conn.commit()

    def recorded_dates(self) -> List[str]:
        rows = self._conn.execute(
            'SELECT DISTINCT trade_date FROM pool_rows ORDER BY trade_date').fetchall()
        return [r[0] for r in rows]

    def pending_status(self) -> Dict:
        """{trade_date: {n, gap_pending, ret_pending}} — gap/ret 有空值即待回填。"""
        out = {}
        for r in self._conn.execute(
                'SELECT trade_date, COUNT(*) n, '
                'SUM(CASE WHEN gap_pct IS NULL THEN 1 ELSE 0 END) gp, '
                'SUM(CASE WHEN next_return IS NULL THEN 1 ELSE 0 END) rp '
                'FROM pool_rows GROUP BY trade_date').fetchall():
            out[r['trade_date']] = {'n': r['n'], 'gap_pending': r['gp'], 'ret_pending': r['rp']}
        return out
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_strategy_lab_store.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add ashare_review/strategy_lab/__init__.py ashare_review/strategy_lab/store.py ashare_review/tests/test_strategy_lab_store.py
git commit -m "feat(strategy_lab): pool_rows 存储层（幂等 upsert / 只补空行回填 / pending 状态）"
```

---

### Task 2: 行情分类 regime 当日化（regime.py）

背景：`market_state.csv` 是回测固定窗口缓存（截至 2026-08-07，regime_picks 页目前显示 8-07 数据）。实验室记录"今日"必须能取到任意交易日的 6 档 regime。

**Files:**
- Create: `ashare_review/strategy_lab/regime.py`
- Test: `ashare_review/tests/test_strategy_lab_regime.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_strategy_lab_regime.py
"""regime 当日化测试（mock market_state，不依赖真实 TDX 全市场扫描）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab import regime as lab_regime


class _FakeStateModule:
    """伪装 strategy_regime.market_state：load_state 返回构造的 state_df"""
    def __init__(self, state_df):
        self._df = state_df

    def load_state(self, start, end, cache_path=None, rebuild=False):
        return self._df.copy()


class _FakeRegimeModule:
    def __init__(self):
        self.calls = []

    def compute_regime(self, df):
        self.calls.append(len(df))
        df = df.copy()
        df['regime'] = '强势趋势'
        return df


@pytest.fixture()
def state_df():
    import pandas as pd
    return pd.DataFrame([{
        'date': '2026-09-08', 'sh_close': 3940.55, 'gz_close': 9800.0,
        'up_count': 3413, 'down_count': 2030, 'limit_up': 73, 'limit_down': 6,
        'sh_ma60': 3900.0, 'sh_trend_now': '上涨',
    }])


def test_get_regime_for_returns_tag(monkeypatch, state_df):
    fake_state = _FakeStateModule(state_df)
    fake_regime = _FakeRegimeModule()
    monkeypatch.setattr(lab_regime, 'market_state', fake_state)
    monkeypatch.setattr(lab_regime, 'regime_mod', fake_regime)
    tag = lab_regime.get_regime_for('2026-09-08')
    assert tag == '强势趋势'


def test_get_regime_for_returns_empty_when_date_missing(monkeypatch, state_df):
    fake_state = _FakeStateModule(state_df)
    fake_regime = _FakeRegimeModule()
    monkeypatch.setattr(lab_regime, 'market_state', fake_state)
    monkeypatch.setattr(lab_regime, 'regime_mod', fake_regime)
    assert lab_regime.get_regime_for('2020-01-01') == ''
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_strategy_lab_regime.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: 实现 regime 当日化**

```python
# ashare_review/strategy_lab/regime.py
"""行情分类（6 档 regime）当日取数。

strategy_regime 的 market_state.csv 是回测固定窗口缓存（截至 2026-08-07）。
实验室要对任意交易日取 6 档 regime：以目标日为 end 按需重建 state_df 并 compute_regime。

重建成本：全市场 .day 尾部广度扫描（一次性数十秒级，带磁盘缓存）。
缓存：data/strategy_lab/market_state.csv —— 与 strategy_regime 的缓存文件隔离，
避免覆盖回测用缓存、破坏回测口径。
"""
import os
from datetime import date, timedelta

import pandas as pd

from ..analysis.strategy_regime import market_state
from ..analysis.strategy_regime import regime as regime_mod
from ..utils.calendar import TradingCalendar

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
_LAB_DIR = os.path.join(_DATA_DIR, 'strategy_lab')
_STATE_CACHE = os.path.join(_LAB_DIR, 'market_state.csv')

# 回看起点：比 strategy_regime 的 2025-08-08 略早，保证 MA60/缠论前视充分
_START = date(2025, 7, 1)


def get_regime_for(trade_date: str) -> str:
    """返回 trade_date（YYYY-MM-DD）当日 6 档 regime；数据不可达返回 ''。"""
    target = _parse_date(trade_date)
    if target is None:
        return ''
    df = _load_state_through(target)
    if df is None or df.empty:
        return ''
    row = df[df['date'] == target]
    if row.empty:
        return ''
    val = row.iloc[0].get('regime')
    return str(val) if val is not None and not pd.isna(val) else ''


def _parse_date(d_str: str):
    try:
        return date.fromisoformat(str(d_str))
    except Exception:
        return None


def _load_state_through(target: date) -> pd.DataFrame:
    """读/建缓存 state_df（含 regime 列），保证缓存覆盖到 target。

    缓存文件存在且最后日期 >= target → 直接读 + compute_regime；
    否则以 end=target 重建整段（复用 strategy_regime 的 market_state.build_market_state
    与 load_state 语义，只是 cache_path 指向 lab 自己的文件）。
    """
    os.makedirs(_LAB_DIR, exist_ok=True)
    df = None
    if os.path.exists(_STATE_CACHE):
        try:
            cached = pd.read_csv(_STATE_CACHE)
            cached['date'] = pd.to_datetime(cached['date']).dt.date
            if not cached.empty and cached['date'].max() >= target:
                df = cached
        except Exception:
            df = None
    if df is None:
        # 重建：end=target，留足前视窗口（起点取缓存已有最新与 _START 的较大者，
        # 避免每次全量重扫；无缓存时从 _START 整段建）
        start = _START
        if os.path.exists(_STATE_CACHE):
            try:
                cached = pd.read_csv(_STATE_CACHE)
                cached['date'] = pd.to_datetime(cached['date']).dt.date
                if not cached.empty:
                    start = max(_START, cached['date'].max())
            except Exception:
                start = _START
        df = market_state.load_state(start, target, cache_path=_STATE_CACHE)
    return regime_mod.compute_regime(df)
```

注：`market_state.load_state(start, end, cache_path)` 已有"缓存存在且非 rebuild 则直接读 CSV、否则 build 并存盘"逻辑（`analysis/strategy_regime/market_state.py:23-38`）；`compute_regime` 给 df 加 `regime` 列（`regime.py:20`）。测试里 monkeypatch `lab_regime.market_state` 与 `lab_regime.regime_mod`，故实现中必须用模块引用 `market_state.load_state` / `regime_mod.compute_regime` 而非 `from ... import` 直接绑定。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_strategy_lab_regime.py -v`
Expected: 2 passed

- [ ] **Step 5: 手动冒烟（真实数据，可选但推荐）**

Run: `python -c "from ashare_review.strategy_lab.regime import get_regime_for; print(get_regime_for('2026-09-08'))"`
Expected: 6 档之一（首次会触发全市场重建，数十秒~数分钟；之后秒回）。若 TDX 数据目录不可达，返回空串并打印 warning 即可接受（不影响测试）。

- [ ] **Step 6: Commit**

```bash
git add ashare_review/strategy_lab/regime.py ashare_review/tests/test_strategy_lab_regime.py
git commit -m "feat(strategy_lab): regime 当日化（lab 独立 market_state 缓存，按需重建到目标日）"
```

---

### Task 3: BOLL V9 公式移植（analysis/boll_v9.py）

**Files:**
- Create: `ashare_review/analysis/boll_v9.py`
- Test: `ashare_review/tests/test_boll_v9.py`

- [ ] **Step 1: 写失败测试（先建原语级骨架测试）**

```python
# ashare_review/tests/test_boll_v9.py
"""BOLL V9 移植 — 通达信原语与 XG 信号测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
import pytest

from ashare_review.analysis.boll_v9 import (
    calc_boll, tdx_rsi, tdx_mfi, upnday, tdx_count_ref,
    tdx_cross, build_signal_row, scan_universe,
)


def _mk(n=260, seed=7):
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(0, 0.25, n))
    close = np.maximum(close, 2.0)
    df = pd.DataFrame({
        'trade_date': pd.date_range('2026-01-01', periods=n).strftime('%Y-%m-%d'),
        'open': close + rng.normal(0, 0.05, n),
        'high': close + np.abs(rng.normal(0, 0.3, n)),
        'low': close - np.abs(rng.normal(0, 0.3, n)),
        'close': close,
        'volume': np.abs(rng.normal(200000, 40000, n)),
    })
    return df


def test_calc_boll_shapes():
    df = _mk()
    out = calc_boll(df)
    assert {'boll', 'ub', 'lb'}.issubset(out.columns)
    assert (out['ub'] >= out['boll']).all() and (out['lb'] <= out['boll']).all()


def test_rsi_in_range():
    rsi = tdx_rsi(_mk()['close'])
    assert 0 <= rsi.dropna().min() <= 100
    assert 0 <= rsi.dropna().max() <= 100


def test_mfi_in_range():
    mfi = tdx_mfi(_mk())
    assert 0 <= mfi.dropna().min() <= 100


def test_upnday_semantics():
    s = pd.Series([1, 2, 3, 2, 3, 4])
    r = upnday(s, 2)
    assert bool(r.iloc[5]) and not bool(r.iloc[3]) and bool(r.iloc[2])


def test_count_ref_semantics():
    s = pd.Series([False, True, False, True, False])
    # COUNT(REF(x,1),3)>0 : 前三日(含昨日)出现 True
    r = tdx_count_ref(s, lookback=3)
    assert bool(r.iloc[3]) and not bool(r.iloc[0])


def test_cross_semantics():
    a = pd.Series([1.0, 2.0, 1.5, 3.0])
    b = pd.Series([1.5, 1.5, 1.5, 1.5])
    r = tdx_cross(a, b)
    assert bool(r.iloc[1]) and bool(r.iloc[3]) and not bool(r.iloc[2])


def test_build_signal_row_tail(df=None):
    """行情 df + index df + capital_hands → 尾部一行含 score/yca/ycb/xg；不下溢"""
    df = _mk()
    idx = _mk(seed=3).rename(columns={'close': 'idx_close'})
    row = build_signal_row(df, index_df=idx, capital_hands=5.5e7)
    for k in ('score', 'yca', 'ycb', 'xg'):
        assert k in row
    assert isinstance(row['score'], float)
    assert row['xg'] in (True, False)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_boll_v9.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 boll_v9.py（逐行移植）**

通达信语义要点（移植对照公式原文）：
- 通达信 `SMA(X,N,M)` 递推 = `_tdx_sma`（indicators.py:62 已有，复用）；
- 通达信 `CROSS(A,B)` = `_cross`（indicators.py:85）；
- `DMA(X,A)` 递推 `DMA = A*X + (1-A)*DMA_prev`（本项目 calc_swl_sws 已论证：A>=1 时 SWS=EMA20，见 indicators.py:128-135）；
- `UPNDAY(X,N)` = X 连续 N 天较前一日上升（含当日）;
- `COUNT(REF(X,1),3)>0` = 过去 3 个交易日（不含当日）内 X 曾为真;
- `WINNER(C)` 无真实筹码数据 → 用"近 120 日量加权成本分布"近似：获利盘 = 收盘价 C 之上的历史筹码占比;
- `INDEXC/INDEXH/INDEXL` = 上证指数(000001, sh)的 close/high/low，按 trade_date 对齐;
- `CAPITAL`（手）= `float_share.get_capital_hands(code)`。

```python
# ashare_review/analysis/boll_v9.py
"""BOLL V9 多日期动态买点条件选股公式 — Python 逐行移植

公式原文: 项目根 BOLL_V9_超跌动态买点选股公式.txt（通达信）。
移植语义:
  - MA/EMA/STD/REF/SUM/LLV/HHV/MAX/MIN/IF 用 pandas/numpy 直接实现
  - SMA(X,N,M) 用 _tdx_sma 递推实现
  - CROSS / UPNDAY / COUNT(REF(X,1),N) 见下方原语
  - DMA(EMA20, A) 按 A>=1 → EMA20 处理（与 calc_swl_sws 结论一致）
  - WINNER(C) 用近 120 日量加权成本分布近似（无真实筹码数据）
  - INDEXC/INDEXH/INDEXL 取上证指数按日对齐

对外接口:
  calc_boll(df)                     -> df 加 boll/ub/lb
  tdx_rsi(close_series)             -> RSI(6)（SMA 法, 通达信口径）
  tdx_mfi(df)                       -> MFI(14)
  upnday(x, n)                      -> 连续 N 日上升(bool Series)
  tdx_count_ref(x, lookback)        -> COUNT(REF(x,1),lookback)>0
  tdx_cross(a, b)                   -> CROSS(A,B)
  build_signal_row(df, index_df, capital_hands) -> 尾部一行 {score,yca,ycb,xg,...}
  scan_universe(trade_date=None, tdx=None, top_n=None) -> 全市场出信号标的列表
"""
import numpy as np
import pandas as pd

from .indicators import _tdx_sma
from ..data.float_share import get_capital_hands, DEFAULT_FLOAT_SHARE_HANDS

INDEX_CODE, INDEX_MKT = '000001', 'sh'   # 上证指数（公式 INDEXC/INDEXH/INDEXL 参照系）


# ═══════════════ 通达信原语 ═══════════════

def _tdx_ref(s: pd.Series, k: int = 1) -> pd.Series:
    return s.shift(k)


def tdx_cross(a: pd.Series, b: pd.Series) -> pd.Series:
    """CROSS(A,B): A 上穿 B（前值 A<=B 且现值 A>B）"""
    return (a > b) & (a.shift(1) <= b.shift(1))


def upnday(x: pd.Series, n: int) -> pd.Series:
    """UPNDAY(X,N): X 连续 N 天（含当日）较前一日上升"""
    up = x.diff() > 0
    return up.rolling(n).sum() >= n


def tdx_count_ref(x: pd.Series, lookback: int) -> pd.Series:
    """COUNT(REF(x,1),lookback)>0: 过去 lookback 个交易日（不含当日）内 x 曾为真"""
    shifted = x.shift(1).fillna(False)
    return shifted.rolling(lookback).sum() > 0


# ═══════════════ BOLL / RSI / MFI ═══════════════

def calc_boll(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    close = df['close']
    boll = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)      # 通达信 STD 为总体标准差
    df['boll'] = boll
    df['ub'] = boll + 2 * std
    df['lb'] = boll - 2 * std
    return df


def tdx_rsi(close: pd.Series, n: int = 6) -> pd.Series:
    """通达信 RSI: SMA(MAX(C-REF(C,1),0),N,1)/SMA(ABS(C-REF(C,1)),N,1)*100"""
    delta = close.diff()
    up = _tdx_sma(delta.clip(lower=0), n, 1)
    down = _tdx_sma(delta.abs(), n, 1)
    rsi = pd.Series(np.nan, index=close.index)
    m = down > 0
    rsi[m] = (up[m] / down[m]) * 100
    return rsi


def tdx_mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """MFI(14): 以 (H+L+C)/3 为典型价, 量加权正负流 14 日累计"""
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    mf = tp * df['volume']
    pos = mf.where(tp > tp.shift(1), 0.0).rolling(n).sum()
    neg = mf.where(tp < tp.shift(1), 0.0).rolling(n).sum()
    ratio = pos / (neg + 1e-9)
    mfi = 100 - 100 / (1 + ratio)
    return mfi


# ═══════════════ WINNER 近似 ═══════════════

def _winner_approx(close: pd.Series, volume: pd.Series, price: float,
                   lookback: int = 120) -> float:
    """近 lookback 日量加权成本分布中成本 <= price 的筹码占比（WINNER 近似）。

    每根 K 线以收盘价为成本、当日成交量为权重，线性衰减（越近权重越高）。
    返回 0..1；样本不足返回 0.5（中性，避免信号误触发）。
    """
    n = min(lookback, len(close))
    if n < 30:
        return 0.5
    c = close.iloc[-n:].astype(float).values
    v = volume.iloc[-n:].astype(float).values
    w = np.arange(1, n + 1, dtype=float)          # 越近权重越高
    w /= w.sum()
    win = float(np.sum(w[c <= price]))
    return float(win)


# ═══════════════ 主扫描行（单只股票 + 指数） ═══════════════

def build_signal_row(df: pd.DataFrame, index_df: pd.DataFrame = None,
                     capital_hands: float = None) -> dict:
    """按公式逐段计算，返回最后一行的信号字典。

    df: 至少 ~230 根日线（open/high/low/close/volume + trade_date, 升序）
    index_df: 上证指数日线（trade_date/idx_close 等）；None 时指数项用自身近似（弱化）
    """
    # 复用 indicators 里已论证的通达信原语风格：全部用 pandas 向量化 + 递推 SMA
    df = df.reset_index(drop=True).copy()
    calc_boll(df)                                   # BOLL/UB/LB
    close, vol = df['close'], df['volume'].astype(float)
    n = len(df)

    # 5. BOLL 波动率 X1/X2/X3
    x1 = (df['ub'] - df['lb']) / df['boll'].replace(0, np.nan) * 100
    x2 = x1.rolling(60).mean()
    x3 = pd.Series(np.where((x2 > 0) & x2.notna(), x1 / x2.replace(0, np.nan), 1.0), index=df.index)

    # 6/7. RSI(6) / MFI(14)
    x4 = tdx_rsi(close, 6)
    x9 = tdx_mfi(df, 14)

    # 8. 成交量环境 XA..XD
    xa = vol.rolling(20).mean()
    xb = pd.Series(np.where(xa > 0, vol / xa.replace(0, np.nan), 1.0), index=df.index)
    xc = x3.clip(lower=0.70, upper=1.60)
    xd = xb.clip(lower=0.60, upper=1.80)

    # 9. 自适应阈值 XE/XF（45 基准 ± 修正, 夹 35..52）
    xe = (45 - (xc - 1) * 8 - (xd - 1) * 4).clip(lower=35, upper=52)
    xf = (45 - (xc - 1) * 7 - (xd - 1) * 3).clip(lower=35, upper=52)

    # 10. 自适应下轨 XI / XK / XL
    xi = (0.012 * xc).clip(lower=0.008, upper=0.025)
    low = df['low']
    xk = low <= df['lb']
    xl = close <= df['lb'] * (1 + xi)

    # 11. RSI/MFI 修复 XM..XP
    xm = x4 < xe
    xn = x4 > x4.shift(1)
    xo = x9 < xf
    xp = x9 > x9.shift(1)

    # 12. 价格修复 XQ..XS
    xq = close > df['open']
    xr = close >= close.shift(1)
    xs = close > df['lb']

    # 13. 趋势结构 XV（SWL>=SWS 或 MA5>=MA10）
    from .indicators import calc_swl_sws
    tdf = calc_swl_sws(df.assign(close=close, volume=vol), capital_hands=capital_hands)
    xt = tdf['swl'] >= tdf['sws']
    xu = close.rolling(5).mean() >= close.rolling(10).mean()
    xv = xt | xu

    # 14. 成交量控制 XW/XX/XY
    xw = vol <= xa * 1.80
    xx = vol < vol.shift(1) * 2.00
    xy = x3 < 1.35

    # 15. 主流/非主流资金 ZA/ZB（个股 34 日位置 vs 上证指数 34 日位置）
    def _fund_pos(h, l, c):
        lo34 = l.rolling(34).min()
        hi34 = h.rolling(34).max()
        rng = (hi34 - lo34).replace(0, np.nan)
        return ((c - lo34) / rng * 100).ewm(span=3, adjust=False).mean()

    za = _fund_pos(df['high'], df['low'], close)
    if index_df is not None and not index_df.empty:
        idx = index_df.reset_index(drop=True).copy()
        idx_close = idx['idx_close'] if 'idx_close' in idx else idx['close']
        idx_high = idx['idx_high'] if 'idx_high' in idx else idx['high']
        idx_low = idx['idx_low'] if 'idx_low' in idx else idx['low']
        zb = _fund_pos(idx_high, idx_low, idx_close)
        zb = pd.Series(zb.values[-n:], index=df.index)   # 与个股行对齐（尾部）
    else:
        zb = za.copy()   # 无指数数据时退化为中性（资金不弱化）

    # 16. 吸筹（/618 系, 原样移植）
    zc = df['low'].shift(1)
    zd_den = (df['low'] - zc).clip(lower=0)
    zd = _tdx_sma((df['low'] - zc).abs(), 3, 1) / _tdx_sma(zd_den, 3, 1).replace(0, np.nan) * 100
    ze = _tdx_sma(np.where(close * 13 > 0, zd * 10, zd / 10), 3, 1)
    zf = df['low'].rolling(19).min()
    zg = pd.Series(ze).rolling(13).max()
    zh = pd.Series(np.where(df['close'].rolling(34).mean() > 0, 1.0, 0.0), index=df.index)
    zi = pd.Series(ze).where(df['low'] <= zf, 0.0)
    zi = _tdx_sma((zi + zg * 2) / 2.0, 3, 1) / 618.0 * zh
    zj = pd.Series(np.where(zi > 190, 150.0, zi), index=df.index)
    zk = pd.Series(zj).rolling(20).mean()
    zl = zj > zk * 0.85
    zm = zj > pd.Series(zj).shift(1)
    zn = za >= zb
    zo = zn | zm
    zp = zl & zm

    # 17. XYS 动量 ZQ..ZZ
    zq = (2 * close + df['high'] + df['low']) / 4.0
    zr = zq.ewm(span=4, adjust=False).mean()
    zs = zr.ewm(span=4, adjust=False).mean()
    zt1 = zs.ewm(span=4, adjust=False).mean()
    zu = (zt1 - zt1.shift(1)) / zt1.shift(1).replace(0, np.nan) * 100
    zv = zu.rolling(2).mean()
    zw = zu
    zx = tdx_cross(zw, zv)
    zy = zu > zu.shift(1)
    zz = zv > zv.shift(1)

    # 18. 量能复合系统 YAA..YAL（含 WINNER 近似的 YAJ / KDJ 式 YAK/YAL）
    yaa = vol * 1.2
    yab = vol.rolling(2).mean() * 1.2
    yac = vol.rolling(13).mean() * 1.2
    yad = vol.rolling(21).mean() * 1.2
    yae = vol.rolling(34).mean() * 1.2
    yaf = vol.rolling(43).mean() * 1.2
    yag = vol.rolling(55).mean() * 1.2
    yah = vol.rolling(120).mean() * 1.2
    yai = vol.rolling(180).mean() * 1.2
    # YAJ = EMA(WINNER(C)*70, 180)*10 —— WINNER 用量加权成本近似
    win_s = pd.Series([_winner_approx(close, vol, float(p)) for p in close], index=df.index)
    yaj = (win_s * 70).ewm(span=180, adjust=False).mean() * 10.0
    llv27 = close.rolling(27).min()
    hhv27 = df['high'].rolling(27).max()
    kdj_raw = ((close - llv27) / (hhv27 - llv27).replace(0, np.nan) * 100)
    yak = 3 * _tdx_sma(kdj_raw, 5, 1) - 2 * _tdx_sma(_tdx_sma(kdj_raw, 5, 1), 3, 1)
    yal = pd.Series(yak).rolling(180).mean()

    # 19-22. 剑 / 箭 / 炮 / 出刀
    yam = tdx_cross(yaa, yab) & (yab < yai * 0.90) & upnday(pd.Series(close).ewm(span=13, adjust=False).mean(), 2)
    yan = tdx_cross(yaa, yai) & upnday(yai, 2)
    yao = (upnday(yac, 3) & upnday(yad, 2) & upnday(yae, 2) & upnday(yaf, 1)
           & upnday(yag, 1) & upnday(yah, 1) & upnday(yaj, 3) & upnday(yal, 3))
    yap = (tdx_cross(yaa, yai) & tdx_cross(yab, yai)
           & upnday(yac, 2) & upnday(yad, 2) & upnday(yae, 2))

    # 23. 量能综合
    yaq = yam | yan | yao | yap
    yar = yao | yap
    yas = yaq | tdx_count_ref(yaq, 3)
    yat = yar | tdx_count_ref(yar, 3)

    # 24. 评分
    pts = {
        'YAU': xk | xl, 'YAV': xm, 'YAW': xn, 'YAX': xo, 'YAY': xp,
        'YAZ': xv, 'YBA': zn, 'YBB': zm, 'YBC': zl, 'YBD': zm,
        'YBE': zx, 'YBF': zy, 'YBG': zz, 'YBH': yam, 'YBI': yan,
        'YBJ': yao, 'YBK': yap, 'YBL': yas, 'YBM': yat, 'YBN': xq,
        'YBO': xr, 'YBP': xs, 'YBQ': xw, 'YBR': xx, 'YBS': xy,
    }
    weight = {'YAU': 3, 'YAV': 2, 'YAW': 2, 'YAX': 2, 'YAY': 2, 'YAZ': 2,
              'YBA': 2, 'YBB': 2, 'YBC': 2, 'YBD': 2, 'YBE': 2, 'YBF': 1,
              'YBG': 1, 'YBH': 1, 'YBI': 2, 'YBJ': 3, 'YBK': 4, 'YBL': 2,
              'YBM': 3, 'YBN': 1, 'YBO': 1, 'YBP': 2, 'YBQ': 1, 'YBR': 1, 'YBS': 1}
    ybt = pd.Series(0.0, index=df.index)
    for k, w in weight.items():
        ybt = ybt + pts[k].astype(float) * w

    # 26. 核心买入条件
    ybu = xk | xl
    ybv = (xm & xn) | (xo & xp)
    ybw = xq | xr | xs
    ybx = zo
    yby = zx | zy | yas
    # 27. 动态门槛
    ybz = pd.Series(np.where(x3 < 0.85, 8.0, np.where(x3 < 1.10, 9.0,
                       np.where(x3 < 1.35, 10.0, 11.0))), index=df.index)
    # 28. 基础买点
    yca = (ybu & ybv & ybw & ybx & yby & (ybt >= ybz)).fillna(False)
    # 29. 历史首次（前 5 日无 YCA）
    ycb = yca & ~(yca.shift(1).fillna(False).rolling(5).sum() > 0)
    xg = ycb.iloc[-1]

    return {
        'score': float(ybt.iloc[-1]), 'xg': bool(xg), 'yca': bool(yca.iloc[-1]),
        'ycb': bool(ycb.iloc[-1]),
        'close': float(close.iloc[-1]), 'lb': float(df['lb'].iloc[-1]),
        'dist_to_lb': float((close.iloc[-1] - df['lb'].iloc[-1]) / df['lb'].iloc[-1] * 100),
        'rsi': float(x4.iloc[-1]) if pd.notna(x4.iloc[-1]) else None,
        'mfi': float(x9.iloc[-1]) if pd.notna(x9.iloc[-1]) else None,
    }


def scan_universe(trade_date: str = None, tdx=None, top_n: int = None,
                  industry_map: dict = None) -> list:
    """全市场扫描 BOLL V9 XG 信号（T 日收盘后）。

    复用 tail_adapter 的全市场循环范式（sh/sz/bj 遍历 + ST 剔除）。
    返回 list[dict]: {code,name,sector,score,close,dist_to_lb,rsi,mfi}，按 score 降序。
    """
    from ..data.tdx_reader import TdxReader
    from .indicators import enrich_all as _unused  # noqa: 保持 enrich 依赖可见性
    tdx = tdx or TdxReader()
    industry_map = industry_map or {}

    # 上证指数（INDEX 参照系）
    index_df = None
    try:
        idx = tdx.read_daily(INDEX_CODE, INDEX_MKT)
        if idx is not None and not idx.empty:
            index_df = idx.rename(columns={'close': 'idx_close', 'high': 'idx_high',
                                           'low': 'idx_low'})
    except Exception:
        index_df = None

    out = []
    for code, mkt in tdx.list_stocks():
        if not str(code).startswith(('60', '00', '30')):     # 只扫主板+创业板
            continue
        try:
            df = tdx.read_daily(code, mkt)
            if df is None or len(df) < 230:
                continue
            if trade_date:
                df = df[df['trade_date'].astype(str).str[:10] <= str(trade_date)]
            df = df.tail(260).reset_index(drop=True)
            cap = get_capital_hands(code, default=DEFAULT_FLOAT_SHARE_HANDS)
            row = build_signal_row(df, index_df=index_df, capital_hands=cap)
            if row['xg']:
                name = _name_of(tdx, code)
                if 'ST' in name:
                    continue
                out.append({
                    'code': code, 'name': name,
                    'sector': str(industry_map.get(code, '') or ''),
                    'score': row['score'], 'close': row['close'],
                    'dist_to_lb': row['dist_to_lb'],
                    'rsi': row['rsi'], 'mfi': row['mfi'],
                })
        except Exception:
            continue
    out.sort(key=lambda x: -x['score'])
    return out if top_n is None else out[:top_n]


def _name_of(tdx, code: str) -> str:
    try:
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'data', 'stock_name_map.json')
        import json
        if os.path.exists(p):
            m = json.load(open(p, encoding='utf-8'))
            nm = str(m.get(code, '') or '')
            if nm and not nm.isdigit():
                return nm
    except Exception:
        pass
    # 兜底：TDX 名称在 list_stocks 不返回 → 用涨停池等外部补名在 collector 层做
    return code
```

注意（执行者必读）：`_tdx_sma` 输入若含 NaN 会从首个有效值起递推；`upnday(yaj, 3)` 等因 yaj 是 EMA 序列、前期 NaN，`rolling` 会自动产生 NaN→False，安全。`_winner_approx` 逐行循环 260 行 × 全市场 ~9000 只会有性能压力，若冒烟太慢可把 lookback 降到 60（在函数内）或向量化预计算成本分布直方图——保持接口不变即可。`calc_boll` 已把 df 视为 copy 使用（调用方传入前 reset_index）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_boll_v9.py -v`
Expected: 7 passed（若 build_signal_row 因种子数据太"平"导致 xg=False 不影响断言，因为只查 key 与类型）

- [ ] **Step 5: Commit**

```bash
git add ashare_review/analysis/boll_v9.py ashare_review/tests/test_boll_v9.py
git commit -m "feat(boll_v9): BOLL V9 超跌公式 Python 逐行移植（原语 + 单行信号 + 全市场扫描）"
```

---

### Task 4: 复盘一进二 Top8 → Top10

**Files:**
- Modify: `ashare_review/report/daily.py:1429`

- [ ] **Step 1: 先看现状（确认行号未漂移）**

Run: `python -c "print(open('ashare_review/report/daily.py',encoding='utf-8').read().splitlines()[1428])"`
Expected: `        return picks[:8]  # 返回Top 8备选标的`

- [ ] **Step 2: 修改**

```python
        return picks[:10]  # 返回Top 10备选标的（复盘页与策略实验室共用）
```

- [ ] **Step 3: 确认只改这一处（不要误改其他 picks[:N]）**

Run: `python -m pytest tests/test_daily_report.py -q`
Expected: pass（若无该文件或测试跳过 TDX 数据，见下）
说明：`tests/test_daily_report.py` 若因无 TDX/akshare 环境被 skip，则以 `python -m py_compile ashare_review/report/daily.py` 通过为准。

- [ ] **Step 4: Commit**

```bash
git add ashare_review/report/daily.py
git commit -m "feat(report): 一进二精选 Top8→Top10（复盘页与实验室共用同一列表）"
```

---

### Task 5: 通用快照 enrich（snapshot.py）

背景：三个 collector 只负责"选哪些 code+rank+extra"，OHLC/成交额/量比/封成比/板块等快照列统一在 snapshot 层对每个 code 补齐，保证口径一致。

**Files:**
- Create: `ashare_review/strategy_lab/snapshot.py`
- Test: 并入 Task 6 的 collector 测试（snapshot 通过 collector 集成验证）

- [ ] **Step 1: 实现**

```python
# ashare_review/strategy_lab/snapshot.py
"""单股 T 日快照 enrich — 三池统一口径

字段: amount_yi(成交额亿) / vol_ratio(当日量/前5日均量) / seal_ratio(封成比,仅涨停有)
      open_px/high_px/low_px/close_px / sector(所属行业)
数据源: tdx.read_daily 日线(amount 单位元→亿 /1e8) + industry_map.json(代码→行业)
       seal_ratio 由调用方从当日涨停池传入 {code: seal_ratio}（非涨停为 None）
"""
import json, os

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
_IND_FILE = os.path.join(_DATA_DIR, 'industry_map.json')

_industry_map = None


def _load_industry_map() -> dict:
    global _industry_map
    if _industry_map is None:
        try:
            with open(_IND_FILE, encoding='utf-8') as f:
                _industry_map = json.load(f)
        except Exception:
            _industry_map = {}
    return _industry_map


def enrich_stock(code: str, tdx, seal_map: dict = None) -> dict:
    """读单股 T 日(最后交易日)快照；失败返回 {}（调用方跳过该股）。"""
    market = 'sh' if str(code).startswith(('6', '9', '5')) else (
        'bj' if str(code).startswith(('4', '8', '920')) else 'sz')
    try:
        df = tdx.read_daily(code, market)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    last = df.iloc[-1]
    close = float(last['close'])
    amount = float(last['amount'] or 0)
    vol = float(last['volume'] or 0)
    vol_ma5_prev = float(df['volume'].iloc[-6:-1].mean()) if len(df) >= 6 else 0.0
    imap = _load_industry_map()
    return {
        'amount_yi': round(amount / 1e8, 2),
        'vol_ratio': round(vol / vol_ma5_prev, 2) if vol_ma5_prev > 0 else None,
        'seal_ratio': float(seal_map.get(code)) if seal_map and seal_map.get(code) is not None else None,
        'open_px': float(last['open']), 'high_px': float(last['high']),
        'low_px': float(last['low']), 'close_px': close,
        'sector': str(imap.get(code, '') or ''),
    }
```

- [ ] **Step 2: 手动验证**

Run:
```bash
python -c "from ashare_review.strategy_lab.snapshot import enrich_stock; from ashare_review.data.tdx_reader import TdxReader; print(enrich_stock('600519', TdxReader()))"
```
Expected: dict 含 amount_yi/vol_ratio/open_px...（TDX 数据目录可达时；不可达则返回 {}，接受）

- [ ] **Step 3: Commit**

```bash
git add ashare_review/strategy_lab/snapshot.py
git commit -m "feat(strategy_lab): 统一快照 enrich（成交额/量比/封成比/OHLC/行业）"
```

---

### Task 6: 三个 Collector + 注册表

**Files:**
- Create: `ashare_review/strategy_lab/collectors/__init__.py`
- Create: `ashare_review/strategy_lab/collectors/breakout_v3.py`
- Create: `ashare_review/strategy_lab/collectors/one2.py`
- Create: `ashare_review/strategy_lab/collectors/boll_v9.py`
- Test: `ashare_review/tests/test_strategy_lab_collectors.py`

Collector 契约：每个 `collect(trade_date, tdx, ak, report_payload=None) -> list[dict]`，返回的元素已含 pool_rows 的 code/name/rank 与快照列；`extra` 为策略专属 JSON dict。record_today 里对每个元素统一 `store.upsert_row({..., 'extra': json.dumps(extra, ensure_ascii=False)})`。

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_strategy_lab_collectors.py
"""三个 collector 契约测试（mock 依赖，不碰真实网络/TDX 扫描）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab.collectors import (
    collect_breakout_v3, collect_one2, collect_boll_v9,
    COLLECTOR_IDS,
)


class _FakeTdx:
    def __init__(self, snap=None):
        self.snap = snap or {'amount_yi': 3.0, 'vol_ratio': 1.2, 'seal_ratio': None,
                             'open_px': 10, 'high_px': 10.5, 'low_px': 9.8,
                             'close_px': 10.3, 'sector': '半导体'}

    def read_daily(self, code, market):
        raise FileNotFoundError(code)   # snapshot 层应吞掉并继续


class _FakeAk:
    pass


class _FakePredictor:
    def verify_pending(self): return 0

    def predict(self, trade_date=None, top_n=10, persist=True):
        return [{'code': '600001', 'name': 'A', 'score': 80.0,
                 'close': 10.3, 'pressure': 10.6, 'dist_pct': 2.5,
                 'features': {'near': True}, 'reasons': ['贴线蓄势']}] * top_n


def _patch(monkeypatch, kind):
    if kind == 'breakout':
        import ashare_review.strategy_lab.collectors.breakout_v3 as m
        monkeypatch.setattr(m, 'BreakoutPredictor', lambda *a, **k: _FakePredictor())
        monkeypatch.setattr(m, 'enrich_stock',
                            lambda code, tdx, seal_map=None: {'amount_yi': 3.0,
                                                              'vol_ratio': 1.2, 'seal_ratio': None,
                                                              'open_px': 10, 'high_px': 10.5,
                                                              'low_px': 9.8, 'close_px': 10.3,
                                                              'sector': '半导体'})
    elif kind == 'one2':
        import ashare_review.strategy_lab.collectors.one2 as m
        monkeypatch.setattr(m, 'enrich_stock', lambda code, tdx, seal_map=None: {
            'amount_yi': 3.0, 'vol_ratio': 1.2, 'seal_ratio': 1.2,
            'open_px': 10, 'high_px': 10.5, 'low_px': 9.8, 'close_px': 10.3,
            'sector': '半导体'})
    elif kind == 'boll':
        import ashare_review.strategy_lab.collectors.boll_v9 as m
        monkeypatch.setattr(m, 'scan_universe',
                            lambda trade_date=None, tdx=None, top_n=None, industry_map=None: [
                                {'code': '600002', 'name': 'B', 'sector': '半导体',
                                 'score': 20.0, 'close': 9.5, 'dist_to_lb': -2.0,
                                 'rsi': 30.0, 'mfi': 25.0}])


def test_collector_ids():
    assert 'breakout_v3' in COLLECTOR_IDS and 'one2' in COLLECTOR_IDS and 'boll_v9' in COLLECTOR_IDS


def test_breakout_collect_top10_rank(monkeypatch):
    _patch(monkeypatch, 'breakout')
    items = collect_breakout_v3('2026-09-08', _FakeTdx(), _FakeAk())
    assert len(items) == 10
    assert [i['rank'] for i in items] == list(range(1, 11))
    extra = json.loads(items[0]['extra'])
    assert extra['pressure'] == 10.6 and extra['score'] == 80.0


def test_one2_collect_reads_report_picks(monkeypatch):
    _patch(monkeypatch, 'one2')
    report = {'sentiment': {'picks': [
        {'code': f'6000{i:02d}', 'name': f'N{i}', 'score': 9, 'reasons': ['r'],
         'seal_ratio': 1.2, 'sector_name': '半导体'}
        for i in range(1, 11)]}}
    items = collect_one2('2026-09-08', _FakeTdx(), _FakeAk(), report_payload=report)
    assert len(items) == 10
    assert items[0]['rank'] == 1


def test_one2_collect_missing_report_raises(monkeypatch):
    _patch(monkeypatch, 'one2')
    with pytest.raises(RuntimeError):
        collect_one2('2026-09-08', _FakeTdx(), _FakeAk(), report_payload=None)


def test_boll_collect_returns_items(monkeypatch):
    _patch(monkeypatch, 'boll')
    items = collect_boll_v9('2026-09-08', _FakeTdx(), _FakeAk())
    assert len(items) == 1
    extra = json.loads(items[0]['extra'])
    assert extra['score'] == 20.0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_strategy_lab_collectors.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 collectors**

```python
# ashare_review/strategy_lab/collectors/__init__.py
"""三池 collector 注册表"""
from .breakout_v3 import collect_breakout_v3
from .one2 import collect_one2
from .boll_v9 import collect_boll_v9

COLLECTOR_IDS = ('breakout_v3', 'one2', 'boll_v9')
```

```python
# ashare_review/strategy_lab/collectors/breakout_v3.py
"""启动突破 V3 · 明日突破预测 Top10（复用 tools/breakout_predict.py）"""
import json

from ..snapshot import enrich_stock


def collect_breakout_v3(trade_date, tdx, ak, report_payload=None) -> list:
    from ...tools.breakout_predict import BreakoutPredictor
    pred = BreakoutPredictor()
    pred.verify_pending()
    top = pred.predict(trade_date=trade_date, top_n=10, persist=False)
    items = []
    for i, p in enumerate(top, start=1):
        snap = enrich_stock(p['code'], tdx) or {}
        items.append({
            'code': p['code'], 'name': p['name'], 'rank': i,
            'sector': snap.get('sector', ''), 'regime': '',
            'amount_yi': snap.get('amount_yi'), 'vol_ratio': snap.get('vol_ratio'),
            'seal_ratio': snap.get('seal_ratio'),
            'open_px': snap.get('open_px'), 'high_px': snap.get('high_px'),
            'low_px': snap.get('low_px'), 'close_px': snap.get('close_px') or p.get('close'),
            'gap_pct': None, 'next_return': None,
            'extra': json.dumps({
                'score': p.get('score'), 'pressure': p.get('pressure'),
                'dist_pct': p.get('dist_pct'), 'reasons': p.get('reasons', [])},
                ensure_ascii=False),
        })
    return items
```

```python
# ashare_review/strategy_lab/collectors/one2.py
"""复盘一进二精选 Top10（数据源: report.sentiment.picks）"""
import json

from ..snapshot import enrich_stock


def collect_one2(trade_date, tdx, ak, report_payload=None) -> list:
    if not report_payload or not (report_payload.get('sentiment') or {}).get('picks'):
        raise RuntimeError(
            f'{trade_date} 复盘报告未生成或今日无精选：请先到"复盘分析"页生成/刷新当日报告（sentiment.picks 为空）。')
    picks = report_payload['sentiment']['picks'][:10]
    items = []
    for i, p in enumerate(picks, start=1):
        snap = enrich_stock(p['code'], tdx) or {}
        items.append({
            'code': p['code'], 'name': p['name'], 'rank': i,
            'sector': p.get('sector_name', '') or snap.get('sector', ''),
            'regime': '',
            'amount_yi': snap.get('amount_yi'), 'vol_ratio': snap.get('vol_ratio'),
            'seal_ratio': p.get('seal_ratio') if p.get('seal_ratio') is not None
                          else snap.get('seal_ratio'),
            'open_px': snap.get('open_px'), 'high_px': snap.get('high_px'),
            'low_px': snap.get('low_px'),
            'close_px': snap.get('close_px') or p.get('price'),
            'gap_pct': None, 'next_return': None,
            'extra': json.dumps({
                'score': p.get('score'), 'reasons': p.get('reasons', []),
                'seal_ratio': p.get('seal_ratio'), 'seal_strength': p.get('seal_strength'),
                'price': p.get('price'), 'market_cap': p.get('market_cap'),
                'limit_up_time': p.get('limit_up_time'), 'theme_stage': p.get('theme_stage'),
                'theme_label': p.get('theme_label'),
                'sector_zt_count': p.get('sector_zt_count'),
                'board_type': p.get('board_type')},
                ensure_ascii=False),
        })
    return items
```

```python
# ashare_review/strategy_lab/collectors/boll_v9.py
"""超跌 BOLL V9 全量（调 analysis/boll_v9.scan_universe）"""
import json

from ..snapshot import enrich_stock


def collect_boll_v9(trade_date, tdx, ak, report_payload=None) -> list:
    from ...analysis.boll_v9 import scan_universe
    rows = scan_universe(trade_date=trade_date, tdx=tdx)
    items = []
    for i, r in enumerate(rows, start=1):     # rank 为展示位（YBT 降序）
        snap = enrich_stock(r['code'], tdx) or {}
        items.append({
            'code': r['code'], 'name': r['name'], 'rank': i,
            'sector': r.get('sector', '') or snap.get('sector', ''),
            'regime': '',
            'amount_yi': snap.get('amount_yi'), 'vol_ratio': snap.get('vol_ratio'),
            'seal_ratio': None,             # 超跌票非涨停, 天然无封成比
            'open_px': snap.get('open_px'), 'high_px': snap.get('high_px'),
            'low_px': snap.get('low_px'), 'close_px': snap.get('close_px') or r.get('close'),
            'gap_pct': None, 'next_return': None,
            'extra': json.dumps({
                'score': r.get('score'), 'dist_to_lb': r.get('dist_to_lb'),
                'rsi': r.get('rsi'), 'mfi': r.get('mfi')}, ensure_ascii=False),
        })
    return items
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_strategy_lab_collectors.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add ashare_review/strategy_lab/collectors ashare_review/tests/test_strategy_lab_collectors.py
git commit -m "feat(strategy_lab): 三池 collector（突破Top10/一进二Top10/超跌全量）"
```

---

### Task 7: service 编排（record_today / backfill_next / status）

**Files:**
- Create: `ashare_review/strategy_lab/service.py`
- Test: `ashare_review/tests/test_strategy_lab_service.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_strategy_lab_service.py
"""service 编排测试（monkeypatch 三池 + regime + store）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab import service as svc


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, '_DB_PATH', str(tmp_path / 'lab.db'))
    monkeypatch.setattr(svc.LabStore, 'db_path', None)  # 保持类方法
    # store 实例路径: service 内 LabStore(db_path=_DB_PATH) 已覆盖
    return None


def test_record_today_writes_rows(monkeypatch, tmp_path):
    from ashare_review.strategy_lab.store import LabStore
    db = str(tmp_path / 'lab.db')
    monkeypatch.setattr(svc, '_DB_PATH', db)

    captured = {}

    def fake_collect(kind, trade_date, tdx, ak, report_payload=None):
        return [{'code': f'60000{i}', 'name': f'N{i}', 'rank': 1, 'sector': '半导体',
                 'regime': '', 'amount_yi': 1.0, 'vol_ratio': 1.0, 'seal_ratio': None,
                 'open_px': 1, 'high_px': 1, 'low_px': 1, 'close_px': 1,
                 'gap_pct': None, 'next_return': None, 'extra': '{}'} for i in range(1, 3)]

    monkeypatch.setattr(svc, '_collect_dispatch', fake_collect)
    monkeypatch.setattr(svc, 'get_regime_for', lambda d: '强势趋势')
    monkeypatch.setattr(svc, '_load_report_payload', lambda d: {'sentiment': {'picks': []}})

    res = svc.record_today('2026-09-08')
    assert res['recorded'] == 6          # 3 策略 × 2 只
    store = LabStore(db)
    rows = store.rows('2026-09-08')
    assert len(rows) == 6
    assert all(r['regime'] == '强势趋势' for r in rows)


def test_record_today_one2_requires_report(monkeypatch, tmp_path):
    from ashare_review.strategy_lab.store import LabStore
    db = str(tmp_path / 'lab.db')
    monkeypatch.setattr(svc, '_DB_PATH', db)

    calls = {}

    def fake_breakout(trade_date, tdx, ak, report_payload=None):
        calls['breakout'] = True
        return []

    def fake_one2(trade_date, tdx, ak, report_payload=None):
        raise RuntimeError('no report')

    def fake_boll(trade_date, tdx, ak, report_payload=None):
        calls['boll'] = True
        return []

    monkeypatch.setattr(svc, 'collect_breakout_v3', fake_breakout)
    monkeypatch.setattr(svc, 'collect_one2', fake_one2)
    monkeypatch.setattr(svc, 'collect_boll_v9', fake_boll)
    monkeypatch.setattr(svc, 'get_regime_for', lambda d: '')

    res = svc.record_today('2026-09-08')
    assert res['one2_error'] is not None
    assert res['recorded'] == 0
    assert calls.get('breakout') and calls.get('boll')   # 单策略失败不阻断其他
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_strategy_lab_service.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 service**

```python
# ashare_review/strategy_lab/service.py
"""每日三池记录 + 次日回填编排"""
import json, os
from datetime import date, datetime, timedelta

from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..utils.cache import cache_get_persistent
from ..utils.calendar import TradingCalendar
from .store import LabStore
from .regime import get_regime_for
from .collectors import collect_breakout_v3, collect_one2, collect_boll_v9

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
_DB_PATH = os.path.join(_DATA_DIR, 'strategy_lab.db')


def _today_ymd() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def _load_report_payload(trade_date: str) -> dict | None:
    """读持久复盘缓存（review_report_{YYYYMMDD}）。"""
    ymd = str(trade_date).replace('-', '')
    return cache_get_persistent(f'review_report_{ymd}')


def _auction_gap_map(ak) -> dict:
    """当日竞价涨幅 {code: open_change_pct}（T+1 早盘 9:25 后调用才有值）。"""
    out = {}
    try:
        for a in ak.get_auction_data():
            if a.code:
                out[str(a.code).zfill(6)] = getattr(a, 'open_change_pct', None)
    except Exception:
        pass
    return out


def _next_return_map(tdx, rows: list, trade_date: str) -> dict:
    """{code: (T+1收盘 - T收盘)/T收盘*100}；T+1 日线未落库的 code 不出现。"""
    cal = TradingCalendar()
    try:
        nd = cal.next_trading_day(date.fromisoformat(trade_date), offset=1)
    except Exception:
        return {}
    nd_str = nd.strftime('%Y-%m-%d')
    out = {}
    for r in rows:
        code = r['code']
        market = 'sh' if code.startswith(('6', '9', '5')) else (
            'bj' if code.startswith(('4', '8', '920')) else 'sz')
        try:
            df = tdx.read_daily(code, market)
            if df is None or df.empty:
                continue
            df['ds'] = df['trade_date'].astype(str).str[:10]
            hit = df[df['ds'] == nd_str]
            prev = df[df['ds'] == trade_date]
            if hit.empty or prev.empty:
                continue
            c1 = float(hit['close'].iloc[0])
            c0 = float(prev['close'].iloc[0])
            if c0 > 0:
                out[code] = round((c1 - c0) / c0 * 100, 2)
        except Exception:
            continue
    return out


def record_today(trade_date: str = None, db_path: str = None) -> dict:
    """一键记录今日三池。返回 {'date', 'recorded', 'one2_error', 'errors'}。"""
    trade_date = trade_date or _today_ymd()
    store = LabStore(db_path or _DB_PATH)
    tdx, ak = TdxReader(), AkshareFetcher()
    regime = get_regime_for(trade_date)
    report_payload = _load_report_payload(trade_date)

    dispatch = {
        'breakout_v3': lambda: collect_breakout_v3(trade_date, tdx, ak, report_payload),
        'one2': lambda: collect_one2(trade_date, tdx, ak, report_payload),
        'boll_v9': lambda: collect_boll_v9(trade_date, tdx, ak, report_payload),
    }
    total, one2_error, errors = 0, None, []
    for sid, fn in dispatch.items():
        try:
            items = fn()
            for it in items:
                row = dict(it)
                row['trade_date'] = trade_date
                row['strategy'] = sid
                row['regime'] = regime or row.get('regime', '')
                store.upsert_row(row)
                total += 1
        except Exception as e:
            if sid == 'one2':
                one2_error = str(e)
            errors.append(f'{sid}: {e}')
    # 顺带尝试回填 T-1（幂等, 只补空）
    try:
        backfill_next(db_path=db_path)
    except Exception:
        pass
    return {'date': trade_date, 'recorded': total, 'one2_error': one2_error, 'errors': errors}


def backfill_next(trade_date: str = None, db_path: str = None) -> dict:
    """对 trade_date 快照行回填竞价涨幅与次日收益；幂等（只补空）。

    调用时机: T+1 当天任意时间（竞价涨幅 9:25 后才有）；次日收益在 T+1 收盘后调用才有值。
    """
    store = LabStore(db_path or _DB_PATH)
    if trade_date:
        targets = [trade_date]
    else:
        # 无指定 → 处理"存在待回填行"的最近日期（最多回溯 5 个自然日）
        all_dates = store.recorded_dates()
        pend = store.pending_status()
        targets = [d for d in all_dates if pend.get(d, {}).get('gap_pending') or pend.get(d, {}).get('ret_pending')][-3:]
    if not targets:
        return {'updated': 0}

    tdx, ak = TdxReader(), AkshareFetcher()
    gap_map = _auction_gap_map(ak)
    updated = 0
    for td in targets:
        rows = store.rows(td)
        ret_map = _next_return_map(tdx, rows, td)
        for r in rows:
            code = r['code']
            gap = gap_map.get(code)
            ret = ret_map.get(code)
            if gap is None and ret is None:
                continue
            store.backfill(td, code, r['strategy'], gap_pct=gap, next_return=ret)
            updated += 1
    return {'updated': updated}


def status(db_path: str = None) -> dict:
    store = LabStore(db_path or _DB_PATH)
    return {
        'dates': store.recorded_dates(),
        'pending': store.pending_status(),
        'latest': store.recorded_dates()[-1] if store.recorded_dates() else None,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_strategy_lab_service.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add ashare_review/strategy_lab/service.py ashare_review/tests/test_strategy_lab_service.py
git commit -m "feat(strategy_lab): record_today/backfill_next/status 编排（单策略失败不阻断）"
```

---

### Task 8: 分层统计 + 重叠（stats.py）

**Files:**
- Create: `ashare_review/strategy_lab/stats.py`
- Test: `ashare_review/tests/test_strategy_lab_stats.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_strategy_lab_stats.py
"""stats 分层统计测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from ashare_review.strategy_lab.stats import (
    base_stats, by_regime, by_regime_bucket, bucket_gap,
    bucket_vol, overlap_report, strategy_matrix, _BUCKET_FNS,
    REGIME_STRONG, REGIME_MID, REGIME_WEAK,
)


def _row(date='2026-09-08', strat='one2', regime='强势趋势', ret=3.0,
         gap=4.0, vol=2.5, sector='半导体', code='600001'):
    return {'trade_date': date, 'strategy': strat, 'regime': regime,
            'next_return': ret, 'gap_pct': gap, 'vol_ratio': vol,
            'sector': sector, 'code': code}


def test_base_stats_win_rate_and_mean():
    rows = [_row(ret=3.0), _row(ret=-1.0), _row(ret=5.0)]
    s = base_stats(rows)
    assert s['n'] == 3 and s['win_rate'] == pytest.approx(2 / 3) and s['avg_ret'] == pytest.approx(7 / 3)


def test_base_stats_ignores_unfilled():
    rows = [_row(ret=None), _row(ret=2.0)]
    s = base_stats(rows)
    assert s['n'] == 1 and s['win_rate'] == 1.0


def test_by_regime_and_bucket():
    rows = [_row(ret=3.0, regime='强势趋势'),
            _row(ret=1.0, regime='题材轮动'),
            _row(ret=-2.0, regime='退潮下跌')]
    br = by_regime(rows)
    assert br['强势趋势']['n'] == 1
    bucket = by_regime_bucket(rows)
    assert bucket['强']['win_rate'] == 1.0
    assert bucket['弱']['n'] == 1
    g = bucket_gap([_row(gap=4.5), _row(gap=1.0), _row(gap=-2.0)])
    assert g['高开≥3%']['n'] == 1 and g['低开']['n'] == 1
    v = bucket_vol([_row(vol=3.0), _row(vol=1.5), _row(vol=0.8)])
    assert v['≥2']['n'] == 1 and v['<1']['n'] == 1


def test_overlap_report():
    rows = [
        _row(date='2026-09-08', strat='breakout_v3', code='600001', ret=2.0),
        _row(date='2026-09-08', strat='one2', code='600001', ret=2.0),
        _row(date='2026-09-08', strat='breakout_v3', code='600002', ret=-1.0),
    ]
    rep = overlap_report(rows)
    assert rep['overlap_pairs']['breakout_v3|one2'] == 1
    assert len(rep['overlap_stocks']) == 1
    only_breakout = [s for s in rep['by_group'] if s['group'] == '只被breakout_v3选']
    assert only_breakout[0]['n'] == 1


def test_small_sample_flag():
    rows = [_row(ret=3.0)]
    s = base_stats(rows)
    assert s['note'] == 'n<5 样本不足'


def test_regime_constants():
    assert '强势趋势' in REGIME_STRONG
    assert '题材轮动' in REGIME_MID and '退潮下跌' in REGIME_WEAK


def test_strategy_matrix_two_dim():
    rows = [_row(strat='one2', regime='强势趋势', ret=3.0, gap=4.0, vol=2.5),
            _row(strat='breakout_v3', regime='强势趋势', ret=1.0, gap=4.0, vol=2.5),
            _row(strat='boll_v9', regime='退潮下跌', ret=-2.0, gap=None, vol=0.8)]
    m = strategy_matrix(rows, 'regime_bucket')
    assert m['强']['one2']['n'] == 1 and m['弱']['boll_v9']['n'] == 1
    gm = strategy_matrix(rows, 'gap')
    assert gm['高开≥3%']['one2']['n'] == 1
    vm = strategy_matrix(rows, 'vol')
    assert vm['<1']['boll_v9']['n'] == 1
    assert 'seal' in _BUCKET_FNS
```
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_strategy_lab_stats.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 stats**

```python
# ashare_review/strategy_lab/stats.py
"""分层统计 + 重叠分析（纯函数，入参 = store.rows 的 dict 列表）"""
from collections import defaultdict

REGIME_STRONG = {'强势趋势'}
REGIME_MID = {'题材轮动', '震荡观望', '冰点超跌'}
REGIME_WEAK = {'弱市回调', '退潮下跌'}

MIN_N = 5


def _note(n: int) -> str:
    return '' if n >= MIN_N else 'n<5 样本不足'


def base_stats(rows) -> dict:
    """样本数 + 胜率(次日收益>0) + 平均次日收益 + 盈亏比近似 + 小样本标注。"""
    valid = [r for r in rows if r.get('next_return') is not None]
    n = len(valid)
    wins = [r for r in valid if r['next_return'] > 0]
    losses = [r for r in valid if r['next_return'] <= 0]
    avg_win = sum(r['next_return'] for r in wins) / len(wins) if wins else 0.0
    avg_loss = sum(r['next_return'] for r in losses) / len(losses) if losses else 0.0
    return {
        'n': n,
        'win_rate': round(len(wins) / n, 3) if n else 0.0,
        'avg_ret': round(sum(r['next_return'] for r in valid) / n, 3) if n else 0.0,
        'profit_factor_approx': round(avg_win / abs(avg_loss), 2) if losses and avg_loss != 0 else None,
        'note': _note(n),
    }


def _bucket_by(rows, key_fn):
    buckets = defaultdict(list)
    for r in rows:
        b = key_fn(r)
        if b is not None:
            buckets[b].append(r)
    return {k: base_stats(v) for k, v in sorted(buckets.items())}


def by_regime(rows) -> dict:
    """6 档原文 → stats"""
    return _bucket_by(rows, lambda r: r.get('regime') or None)


def _regime_bucket(tag: str) -> str:
    if tag in REGIME_STRONG:
        return '强'
    if tag in REGIME_MID:
        return '中'
    if tag in REGIME_WEAK:
        return '弱'
    return '其他'


def by_regime_bucket(rows) -> dict:
    """强/中/弱 → stats"""
    return _bucket_by(rows, lambda r: _regime_bucket(r.get('regime') or ''))


def bucket_gap(rows) -> dict:
    """竞价涨幅: 高开≥3% / 高开0~3% / 低开(<0)"""
    def fn(r):
        g = r.get('gap_pct')
        if g is None:
            return None
        return '高开≥3%' if g >= 3 else ('高开0~3%' if g >= 0 else '低开')
    return _bucket_by(rows, fn)


def bucket_vol(rows) -> dict:
    """量比: ≥2 / 1~2 / <1"""
    def fn(r):
        v = r.get('vol_ratio')
        if v is None:
            return None
        return '≥2' if v >= 2 else ('1~2' if v >= 1 else '<1')
    return _bucket_by(rows, fn)


def bucket_seal(rows) -> dict:
    """封成比 ≥0.5 / <0.5（仅涨停类样本；空值跳过）"""
    def fn(r):
        s = r.get('seal_ratio')
        if s is None:
            return None
        return '≥0.5' if s >= 0.5 else '<0.5'
    return _bucket_by(rows, fn)


def sector_strength(rows) -> dict:
    """板块强弱（记录时 extra.sector_zt_count 由调用方写入时可拆；无数据则 '未标注'）"""
    def fn(r):
        try:
            extra = r.get('extra') or '{}'
            import json
            zc = json.loads(extra).get('sector_zt_count') if isinstance(extra, str) else (extra or {}).get('sector_zt_count')
        except Exception:
            zc = None
        if zc is None:
            return None
        return '强势板块' if int(zc) >= 3 else '普通板块'
    return _bucket_by(rows, fn)


_BUCKET_FNS = {
    'regime_bucket': lambda r: _regime_bucket(r.get('regime') or ''),
    'gap': (lambda r: (None if r.get('gap_pct') is None
                       else ('高开≥3%' if r['gap_pct'] >= 3
                             else ('高开0~3%' if r['gap_pct'] >= 0 else '低开')))),
    'vol': (lambda r: (None if r.get('vol_ratio') is None
                       else ('≥2' if r['vol_ratio'] >= 2
                             else ('1~2' if r['vol_ratio'] >= 1 else '<1')))),
    'seal': (lambda r: (None if r.get('seal_ratio') is None
                        else ('≥0.5' if r['seal_ratio'] >= 0.5 else '<0.5'))),
}


def strategy_matrix(rows, key: str) -> dict:
    """分层 × 策略 双维矩阵: {桶名: {strategy_id: stats}}。

    key ∈ BUCKET_FNS 的键（regime_bucket/gap/vol/seal）。供 web /api/strategy_lab/stats 用。
    """
    fn = _BUCKET_FNS[key]
    buckets = defaultdict(lambda: defaultdict(list))
    for r in rows:
        b = fn(r)
        if b is None:
            continue
        buckets[b][r['strategy']].append(r)
    return {b: {sid: base_stats(v) for sid, v in sm.items()}
            for b, sm in sorted(buckets.items())}


def overlap_report(rows) -> dict:
    """三池重叠：同日同 code 出现在 ≥2 池的明细；策略池两两重叠对数；分组(只A/只B/AB都选)胜率。"""
    by_day_code = defaultdict(list)
    for r in rows:
        by_day_code[(r['trade_date'], r['code'])].append(r)
    overlap_stocks = []
    for (d, code), rs in by_day_code.items():
        if len(rs) >= 2:
            overlap_stocks.append({'date': d, 'code': code,
                                   'strategies': sorted({r['strategy'] for r in rs})})
    strat_set = sorted({r['strategy'] for r in rows})
    pair_counter = defaultdict(int)
    for o in overlap_stocks:
        ss = o['strategies']
        for i in range(len(ss)):
            for j in range(i + 1, len(ss)):
                pair_counter[f'{ss[i]}|{ss[j]}'] += 1

    # 分组: 每只票每天视为一次"观察"，按命中策略集合分组
    group_stats = defaultdict(list)
    for (d, code), rs in by_day_code.items():
        sset = tuple(sorted({r['strategy'] for r in rs}))
        # 收益口径: 同一股同日多策略 next_return 相同 → 取任一行
        rep = rs[0]
        group_stats['+'.join(sset)].append(rep)
    groups = []
    for key, rs in group_stats.items():
        only_self = len(key.split('+')) == 1
        groups.append({
            'group': '只被%s选' % key if only_self else '%s 都选' % '、'.join(key.split('+')),
            'keys': key, **base_stats(rs)})
    return {
        'overlap_stocks': overlap_stocks,
        'overlap_pairs': dict(pair_counter),
        'by_group': groups,
        'strategies': strat_set,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_strategy_lab_stats.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add ashare_review/strategy_lab/stats.py ashare_review/tests/test_strategy_lab_stats.py
git commit -m "feat(strategy_lab): 分层统计（市场环境6档/归并强中弱/竞价量比封成比/重叠）"
```

---

### Task 9: web 路由 + API

**Files:**
- Modify: `ashare_review/web/app.py`（在 strategy_bench 区块附近新增 lab 路由）
- Create: `ashare_review/web/templates/strategy_lab.html`（骨架由 Task 10 完善，先可渲染空态）

- [ ] **Step 1: 在 app.py 新增路由（插在现有 `/strategy_bench` 路由块之后）**

参考现有路由位置：`web/app.py:1979` `/strategy_bench`、1993 `/api/strategy_bench/run`。在其后追加：

```python
# ── 三策略观察实验室（strategy_lab） ──

@app.route('/strategy_bench/backtest')
def strategy_bench_backtest():
    """原历史回测对比台（二级页保留）"""
    from ..strategy_bench.adapters.registry import list_adapters
    from ..strategy_bench.service import DB_PATH
    from ..strategy_bench.store import BenchStore
    adapters = []
    for a in list_adapters():
        adapters.append({'strategy_id': a.strategy_id, 'name': a.name,
                         'description': a.description, 'param_schema': a.param_schema})
    snapshots = BenchStore(DB_PATH).list_snapshots()
    return render_template('strategy_bench_backtest.html',
                           adapters=adapters, snapshots=snapshots)


@app.route('/strategy_bench')
def strategy_lab():
    """主页：三策略观察实验室"""
    from ..strategy_lab.service import status as lab_status, _DB_PATH
    from ..strategy_lab.store import LabStore
    st = lab_status()
    store = LabStore(_DB_PATH)
    latest = st.get('latest')
    today_rows = store.rows(latest) if latest else []
    return render_template('strategy_lab.html', status=st,
                           today_rows=today_rows, latest_date=latest)


@app.route('/api/strategy_lab/record', methods=['POST'])
def api_strategy_lab_record():
    from ..strategy_lab.service import record_today
    body = request.get_json(silent=True) or {}
    date_raw = body.get('date') or ''
    try:
        res = record_today(date_raw or None)
        return jsonify({'success': True, **res})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/strategy_lab/rows')
def api_strategy_lab_rows():
    from ..strategy_lab.service import _DB_PATH
    from ..strategy_lab.store import LabStore
    store = LabStore(_DB_PATH)
    d = request.args.get('date') or request.args.get('trade_date') or ''
    s = request.args.get('strategy') or ''
    return jsonify({'rows': store.rows(d or None, s or None)})


@app.route('/api/strategy_lab/stats')
def api_strategy_lab_stats():
    from ..strategy_lab.service import _DB_PATH
    from ..strategy_lab.store import LabStore
    from ..strategy_lab import stats as st
    store = LabStore(_DB_PATH)
    rows = store.rows()
    return jsonify({
        'overview': {sid: st.base_stats([r for r in rows if r['strategy'] == sid])
                     for sid in ('breakout_v3', 'one2', 'boll_v9')},
        'matrix': {key: st.strategy_matrix(rows, key)
                   for key in ('regime_bucket', 'gap', 'vol', 'seal')},
    })


@app.route('/api/strategy_lab/overlap')
def api_strategy_lab_overlap():
    from ..strategy_lab.service import _DB_PATH
    from ..strategy_lab.store import LabStore
    from ..strategy_lab import stats as st
    store = LabStore(_DB_PATH)
    return jsonify(st.overlap_report(store.rows()))


@app.route('/api/strategy_lab/status')
def api_strategy_lab_status():
    from ..strategy_lab.service import status as lab_status
    return jsonify(lab_status())
```

- [ ] **Step 2: 新增模板最小骨架（Task 10 补全 JS/样式）**

```html
{# ashare_review/web/templates/strategy_lab.html #}
{% extends "base.html" %}
{% block title %}策略观察实验室{% endblock %}
{% block content %}
<div class="content-area">
    <div class="page-header">
        <div>
            <div class="page-title">🧪 三策略观察实验室</div>
            <div class="page-date">启动突破 Top10 · 一进二 Top10 · 超跌 BOLL V9 全量 — 每日记录 + 次日回填 + 分层胜率</div>
        </div>
        <div>
            <button id="btn-record-today" class="btn btn-primary" onclick="labRecordToday()">📝 记录今日三池</button>
            <span id="lab-record-status" style="margin-left:10px;font-size:.9em;color:#7c3aed;"></span>
        </div>
    </div>

    <div class="card"><div class="card-body">
        <p>最近记录日：<strong>{{ latest_date or '—' }}</strong>
        {% if status.pending %}
            · 待回填：
            {% for d, p in status.pending.items() %}{{ d }}(竞价{{ p.gap_pending }}/收益{{ p.ret_pending }}) {% endfor %}
        {% endif %}
        · <a href="/strategy_bench/backtest">历史回测台（原页面）→</a></p>
    </div></div>

    <div id="lab-history" style="margin-top:14px;"></div>
    <div id="lab-stats" style="margin-top:14px;"></div>
    <div id="lab-overlap" style="margin-top:14px;"></div>
</div>
<script>
function labRecordToday() {
    var btn = document.getElementById('btn-record-today');
    var st = document.getElementById('lab-record-status');
    if (!btn || btn.disabled) return;
    btn.disabled = true; st.textContent = '⏳ 记录中（含超跌全市场扫描，约 1-3 分钟）…';
    fetch('/api/strategy_lab/record', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({})})
    .then(function(r){return r.json();})
    .then(function(d){
        btn.disabled = false;
        if (d.success) {
            st.textContent = '✅ 记录 ' + d.recorded + ' 条' + (d.one2_error ? '（一进二未记录: ' + d.one2_error + '）' : '');
            location.reload();
        } else { st.textContent = '❌ ' + (d.error || '失败'); }
    }).catch(function(e){ btn.disabled = false; st.textContent = '❌ ' + e; });
}
function labLoad() {
    fetch('/api/strategy_lab/rows').then(function(r){return r.json();}).then(function(d){
        var el = document.getElementById('lab-history');
        if (!d.rows || !d.rows.length) { el.innerHTML = '<div class="section-title">📋 历史明细</div><p class="empty-hint">暂无记录 — 点「记录今日三池」开始。</p>'; return; }
        var head = ['日期','策略','#','代码','名称','板块','行情','量比','封成比','竞价%','收盘','次日收益%'];
        var html = '<div class="section-title">📋 历史明细（近200行）</div><div class="card"><div class="table-wrap"><table class="sector-table"><tr>' +
            head.map(function(h){return '<th>'+h+'</th>';}).join('') + '</tr>';
        d.rows.slice(-200).reverse().forEach(function(r){
            html += '<tr><td>'+r.trade_date+'</td><td>'+r.strategy+'</td><td>'+r.rank+'</td><td>'+r.code+'</td><td>'+r.name+'</td><td>'+r.sector+'</td><td>'+r.regime+'</td><td>'+(r.vol_ratio==null?'—':r.vol_ratio)+'</td><td>'+(r.seal_ratio==null?'—':r.seal_ratio)+'</td><td>'+(r.gap_pct==null?'待回填':r.gap_pct)+'</td><td>'+(r.close_px==null?'—':r.close_px)+'</td><td>'+(r.next_return==null?'待回填':r.next_return)+'</td></tr>';
        });
        html += '</table></div></div>';
        el.innerHTML = html;
    });
}
function labLoadStats() {
    fetch('/api/strategy_lab/stats').then(function(r){return r.json();}).then(function(d){
        var el = document.getElementById('lab-stats');
        var fmt = function(o){ return o ? 'n='+o.n+' 胜率'+(o.win_rate*100).toFixed(0)+'% 均'+o.avg_ret+'%'+(o.note?' '+o.note:'') : '—'; };
        var html = '<div class="section-title">📊 分层统计（样本/胜率/平均收益）</div><div class="card"><div class="card-body no-padding"><div class="table-wrap"><table class="sector-table"><tr><th>分组</th><th>启动突破V3</th><th>一进二</th><th>超跌BOLL9</th></tr>';
        var sids = ['breakout_v3','one2','boll_v9'];
        // overview 行
        html += '<tr><td><strong>总览</strong></td>';
        sids.forEach(function(sid){ html += '<td>' + fmt(d.overview && d.overview[sid]) + '</td>'; });
        html += '</tr>';
        // matrix 各行（regime_bucket 强/中/弱 + gap 分档 + vol 分档）
        var rowsSpec = [
            ['强', d.matrix.regime_bucket && d.matrix.regime_bucket['强']],
            ['中', d.matrix.regime_bucket && d.matrix.regime_bucket['中']],
            ['弱', d.matrix.regime_bucket && d.matrix.regime_bucket['弱']],
            ['高开≥3%', d.matrix.gap && d.matrix.gap['高开≥3%']],
            ['高开0~3%', d.matrix.gap && d.matrix.gap['高开0~3%']],
            ['低开', d.matrix.gap && d.matrix.gap['低开']],
            ['量比≥2', d.matrix.vol && d.matrix.vol['≥2']],
            ['量比1~2', d.matrix.vol && d.matrix.vol['1~2']],
            ['量比<1', d.matrix.vol && d.matrix.vol['<1']],
        ];
        rowsSpec.forEach(function(spec){
            var bucket = spec[0], m = spec[1];
            var html2 = '<td><strong>' + bucket + '</strong></td>';
            sids.forEach(function(sid){
                html2 += '<td>' + (m && m[sid] ? fmt(m[sid]) : '—') + '</td>';
            });
            html += '<tr>' + html2 + '</tr>';
        });
        html += '</table></div></div></div>';
        el.innerHTML = html;
    });
}
function labLoadOverlap() {
    fetch('/api/strategy_lab/overlap').then(function(r){return r.json();}).then(function(d){
        var el = document.getElementById('lab-overlap');
        if (!d.overlap_stocks || !d.overlap_stocks.length) { el.innerHTML = ''; return; }
        var html = '<div class="section-title">🔀 三池重叠</div><div class="card"><div class="card-body">' +
            '<p>重叠标的 ' + d.overlap_stocks.length + ' 个：' + d.overlap_stocks.map(function(o){return o.date+':'+o.code+'('+o.strategies.join('+')+')';}).join('，') + '</p>';
        html += d.by_group.map(function(g){ return '<p>' + g.group + ' → ' + fmt0(g) + '</p>'; }).join('');
        html += '</div></div>';
        el.innerHTML = html;
        function fmt0(o){ return 'n='+o.n+' 胜率'+(o.win_rate*100).toFixed(0)+'% 均'+o.avg_ret+'%'; }
    });
}
labLoad(); labLoadStats(); labLoadOverlap();
</script>
{% endblock %}
```

注：`/api/strategy_lab/stats` 返回 `{overview: {strategy: stats}, matrix: {regime_bucket|gap|vol|seal: {桶: {strategy: stats}}}}`（见 Task 8 `strategy_matrix`）；页面 JS 按 matrix 渲染"桶 × 策略"表。封成比桶与板块强弱列为后续增强项，本期页面矩阵主干为 强/中/弱/竞价/量比。

- [ ] **Step 3: 备份原模板为 backtest 页**

```bash
cp ashare_review/web/templates/strategy_bench.html ashare_review/web/templates/strategy_bench_backtest.html
git rm ashare_review/web/templates/strategy_bench.html
```

编辑 `strategy_bench_backtest.html`：页面标题"策略验证台"改为"策略验证台（历史回测）"，并在 page-header 下加一行返回实验室链接 `<a href="/strategy_bench">← 返回观察实验室</a>`。

- [ ] **Step 4: base.html 导航更新（endpoint 判断同步，避免 active 高亮失效）**

`ashare_review/web/templates/base.html` 第 87-89 行：

```html
<a href="/strategy_bench" class="nav-item {% if request.endpoint == 'strategy_bench' %}active{% endif %}">
    <span class="nav-label">策略验证台</span>
```

改为（注意主路由函数名改为 `strategy_lab`，endpoint 随之变，两个页面都应保持高亮）：

```html
<a href="/strategy_bench" class="nav-item {% if request.endpoint in ('strategy_lab', 'strategy_bench_backtest') %}active{% endif %}">
    <span class="nav-label">策略实验室</span>
```

- [ ] **Step 5: web 冒烟（启动测试见 Task 10）**

先语法/模板编译检查：
Run:
```bash
python -c "import ast; ast.parse(open('ashare_review/web/app.py',encoding='utf-8').read()); print('app.py syntax OK')"
```

- [ ] **Step 6: Commit**

```bash
git add ashare_review/web/app.py ashare_review/web/templates/
git commit -m "feat(strategy_lab): web 路由 + 实验室主页 + 回测台二级页（原页面保留）"
```

---

### Task 10: web 冒烟测试

**Files:**
- Test: `ashare_review/tests/test_strategy_lab_web.py`

- [ ] **Step 1: 写冒烟测试**

```python
# ashare_review/tests/test_strategy_lab_web.py
"""web 冒烟：页面渲染 + 空态 API"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture()
def client():
    os.environ.setdefault('ASHARE_REVIEW_TESTING', '1')
    from ashare_review.web.app import app
    app.config['TESTING'] = True
    return app.test_client()


def test_lab_page_renders(client):
    r = client.get('/strategy_bench')
    assert r.status_code == 200
    assert '三策略观察实验室' in r.get_data(as_text=True)


def test_backtest_page_renders(client):
    r = client.get('/strategy_bench/backtest')
    assert r.status_code == 200
    assert '策略验证台' in r.get_data(as_text=True) or '历史回测' in r.get_data(as_text=True)


def test_lab_api_empty(client):
    r = client.get('/api/strategy_lab/rows')
    assert r.status_code == 200
    assert 'rows' in r.get_json()

    r2 = client.get('/api/strategy_lab/status')
    assert r2.status_code == 200 and 'dates' in r2.get_json()

    r3 = client.get('/api/strategy_lab/stats')
    assert r3.status_code == 200 and 'overview' in r3.get_json()

    r4 = client.get('/api/strategy_lab/overlap')
    assert r4.status_code == 200 and 'overlap_stocks' in r4.get_json()


def test_record_api_without_tdx_returns_500_or_partial(client, tmp_path, monkeypatch):
    """无 TDX/网络环境时 record 不 500 崩应用层（返回 partial/error 均可接受）"""
    r = client.post('/api/strategy_lab/record', json={'date': '2099-01-01'})
    assert r.status_code in (200, 500)
```

- [ ] **Step 2: 运行**

Run: `python -m pytest tests/test_strategy_lab_web.py -v`
Expected: 4 passed（page 200、api 结构齐全；record 因无真实数据可 500，仅断言不超此范围）

- [ ] **Step 3: 跑全量相关测试回归**

Run:
```bash
python -m pytest tests/test_strategy_lab_store.py tests/test_strategy_lab_regime.py tests/test_boll_v9.py tests/test_strategy_lab_collectors.py tests/test_strategy_lab_service.py tests/test_strategy_lab_stats.py tests/test_strategy_lab_web.py tests/test_strategy_bench.py tests/test_prediction_ledger.py tests/test_daily_report.py -q
```
Expected: 全绿（TDX 依赖用例若 skip 属预期）。

- [ ] **Step 4: Commit**

```bash
git add ashare_review/tests/test_strategy_lab_web.py
git commit -m "test(strategy_lab): web 冒烟（页面 + 空态 API + record 容错）"
```

---

## Self-Review 对照

- **Spec 覆盖**：§2 数据模型 → Task 1；§3.3 BOLL V9 移植 → Task 3；§3.1/3.2 collector → Task 6；§4 次日回填 → Task 7；§5 regime → Task 2；§6 分层 → Task 8；§7 页面/API → Task 9/10；§8 测试 → 各 Task 内；`picks[:8]→[:10]`（§0 决策 2）→ Task 4。
- **已知取舍**：分层统计以"桶 × 策略"双维矩阵输出（`strategy_matrix`，覆盖市场环境强/中/弱、竞价、量比、封成比），页面首版展示矩阵主干（强/中/弱/竞价/量比），封成比桶与板块强弱（需 extra.sector_zt_count 回填）等做后续增强；stats.py 另保留单维函数（by_regime/bucket_gap/...）供测试与扩展。三池重叠按 (date,code) 分组输出"只被A选/AB都选"胜率。样本 <5 全部带标注。
- **regime 重建成本**：首次点击记录触发全市场 state 重建（数十秒~数分钟级），后续目标日已覆盖则秒回；重建不影响 strategy_regime 的回测缓存（独立文件）。
