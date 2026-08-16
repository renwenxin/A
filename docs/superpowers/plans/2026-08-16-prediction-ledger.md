# 复盘预测台账 + 准确率统计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把复盘生成的精选标的/情绪周期/竞价预期三类预测写入 SQLite 台账，次日自动验证并统计准确率（整体/类型/分数段），提供独立页面 `/prediction_ledger`。

**Architecture:** 新增 `ashare_review/prediction_ledger/` 包（store=SQLite 读写 / validate=纯函数判定 / service=编排），`daily.py` 仅加两个结构化字段（`next_bias`/`direction`）和 `limit_up_codes`，`/review` 路由在拿到 report 后幂等调用 record_day + validate_pending，历史精选通过 CLI 一次性迁移。

**Tech Stack:** Python 3 + sqlite3（内置）+ Flask + pytest + pandas（仅测试 FakeTdx 用）。

**设计依据:** `docs/superpowers/specs/2026-08-16-prediction-ledger-design.md`（commit f6b4261）

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `ashare_review/prediction_ledger/__init__.py` | 包标记（空） |
| `ashare_review/prediction_ledger/validate.py` | 纯函数判定引擎：grade_pick/grade_cycle/grade_auction/hit_for |
| `ashare_review/prediction_ledger/store.py` | SQLite 存储：建表/upsert/get_unverified/mark_verified/set_actual/rows/summary |
| `ashare_review/prediction_ledger/service.py` | 编排：record_day/validate_pending/migrate_picks_history + DB_PATH |
| `ashare_review/prediction_ledger/migrate.py` | CLI 入口：`python -m ashare_review.prediction_ledger.migrate` |
| `ashare_review/report/daily.py` | 修改：加 next_bias / direction / limit_up_codes（只增不改） |
| `ashare_review/web/app.py` | 修改：/review 接线 + /prediction_ledger + /api/ledger/validate |
| `ashare_review/web/templates/base.html` | 修改：导航加"📒 预测台账" |
| `ashare_review/web/templates/prediction_ledger.html` | 新建：台账页面 |
| `ashare_review/tests/test_prediction_ledger.py` | 全部新测试 |

**关键约定（所有任务遵守）：**
- 数据库路径：`data/prediction_ledger.db`；函数签名用 `db_path=None` → `db_path or DB_PATH`（运行时可注入，测试用 tmp_path）
- 日期统一 `YYYYMMDD` 字符串
- 幂等：唯一约束 `(pred_date, pred_type, item_key)` + INSERT OR IGNORE
- 涨停幅度：主板 9.8% / 创业板30x·科创68x 19.6% / 北交8x·4x·92x 29.4%
- TDX 行情读取：`tdx.read_daily(code, market)` 返回 DataFrame，`iloc[-1]`=最新交易日，`iloc[-2]`=前一交易日；列含 `open`/`close`

---

### Task 1: 包骨架 + 判定引擎 validate.py（纯函数）

**Files:**
- Create: `ashare_review/prediction_ledger/__init__.py`（空文件）
- Create: `ashare_review/prediction_ledger/validate.py`
- Create: `ashare_review/tests/test_prediction_ledger.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_prediction_ledger.py
"""预测台账单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 判定引擎 ----------

def test_grade_pick_zt_priority():
    from ashare_review.prediction_ledger.validate import grade_pick
    assert grade_pick(20.0, is_zt=True) == 'zt'      # 涨停优先
    assert grade_pick(1.5, is_zt=True) == 'zt'       # 即使涨幅小也判涨停


def test_grade_pick_boundaries():
    from ashare_review.prediction_ledger.validate import grade_pick
    assert grade_pick(3.0) == 'up3'                   # 正好 3% → up3
    assert grade_pick(2.99) == 'up'                   # <3% → up
    assert grade_pick(0.0) == 'up'                    # 平盘 → up
    assert grade_pick(-2.99) == 'flat'                # -3% 以内 → flat
    assert grade_pick(-3.0) == 'down'                 # 正好 -3% → down
    assert grade_pick(-5.0) == 'down'


def test_grade_cycle_boundaries():
    from ashare_review.prediction_ledger.validate import grade_cycle
    assert grade_cycle(100, 110) == 'up'              # r=1.1 → up
    assert grade_cycle(100, 109) == 'flat'            # r=1.09 → flat
    assert grade_cycle(100, 90) == 'down'             # r=0.9 → down
    assert grade_cycle(100, 91) == 'flat'             # r=0.91 → flat
    assert grade_cycle(100, 100) == 'flat'
    assert grade_cycle(0, 10) is None                 # 当日涨停数为 0 无法判定


def test_grade_auction_boundaries():
    from ashare_review.prediction_ledger.validate import grade_auction
    assert grade_auction(1.5) == 'high'               # ≥1.5% → high
    assert grade_auction(1.49) == 'flat'
    assert grade_auction(-0.5) == 'low'               # ≤-0.5% → low
    assert grade_auction(-0.49) == 'flat'
    assert grade_auction(0.0) == 'flat'


def test_hit_for_all_types():
    from ashare_review.prediction_ledger.validate import hit_for
    # picks：zt/up3 命中
    assert hit_for('picks', None, 'zt') == 1
    assert hit_for('picks', None, 'up3') == 1
    assert hit_for('picks', None, 'up') == 0
    assert hit_for('picks', None, 'down') == 0
    # cycle：方向一致命中
    assert hit_for('cycle', 'up', 'up') == 1
    assert hit_for('cycle', 'up', 'down') == 0
    assert hit_for('cycle', 'flat', 'flat') == 1
    # auction：方向一致命中
    assert hit_for('auction', 'high', 'high') == 1
    assert hit_for('auction', 'high', 'low') == 0
    # 无法判定
    assert hit_for('picks', None, None) is None
    assert hit_for('cycle', None, 'up') is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ashare_review.prediction_ledger'`

- [ ] **Step 3: 实现最小代码**

```python
# ashare_review/prediction_ledger/validate.py
"""预测台账 — 命中判定引擎（纯函数，无 IO）

actual 枚举:
  picks:   zt=涨停  up3=涨≥3%  up=收涨  flat=-3%~0  down=<-3%
  cycle:   up=涨停家数增≥10%  down=减≥10%  flat=其他
  auction: high=平均高开≥1.5%  low=≤-0.5%  flat=其他
direction 枚举: cycle=up|flat|down  auction=high|flat|low  picks=无
"""
from typing import Optional


def grade_pick(today_chg: float, is_zt: bool = False) -> str:
    """精选标的次日实际表现分级。涨停优先，其余按涨幅区间。"""
    if is_zt:
        return 'zt'
    if today_chg >= 3.0:
        return 'up3'
    if 0.0 <= today_chg < 3.0:
        return 'up'
    if -3.0 <= today_chg < 0.0:
        return 'flat'
    return 'down'


def grade_cycle(today_zt: int, next_zt: int) -> Optional[str]:
    """情绪周期次日实际方向：r=次日涨停家数/当日涨停家数。"""
    if today_zt <= 0:
        return None
    r = next_zt / today_zt
    if r >= 1.1:
        return 'up'
    if r <= 0.9:
        return 'down'
    return 'flat'


def grade_auction(avg_gap: float) -> str:
    """竞价预期次日实际：当日涨停池次日平均高开幅度(%)。"""
    if avg_gap >= 1.5:
        return 'high'
    if avg_gap <= -0.5:
        return 'low'
    return 'flat'


def hit_for(pred_type: str, direction: Optional[str], actual: Optional[str]) -> Optional[int]:
    """判定命中：返回 1/0，无法判定返回 None。"""
    if not actual:
        return None
    if pred_type == 'picks':
        return 1 if actual in ('zt', 'up3') else 0
    if not direction:
        return None
    return 1 if direction == actual else 0
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: 6 passed（本任务 6 个测试）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/prediction_ledger/__init__.py ashare_review/prediction_ledger/validate.py ashare_review/tests/test_prediction_ledger.py
git commit -m "feat(ledger): 判定引擎 grade/hit 纯函数"
```

---

### Task 2: daily.py 结构化字段（next_bias / direction / limit_up_codes）

**Files:**
- Modify: `ashare_review/report/daily.py`（3 处：模块级常量、_detect_cycle_stage return、_forecast_next_auction return、generate return）
- Test: `ashare_review/tests/test_prediction_ledger.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_prediction_ledger.py

# ---------- Task 2: daily.py 结构化字段 ----------

def _lu(code='600001', name='测试', t='10:00', consecutive=1, is_first=True,
        is_seal=True, is_broken=False, seal_amount=1000.0, turnover=5000.0,
        cap=30.0, board_type='换手板', close=10.0):
    from ashare_review.data.models import LimitUpInfo
    return LimitUpInfo(code=code, name=name, limit_up_time=t, seal_amount=seal_amount,
                       turnover=turnover, float_market_cap=cap, consecutive=consecutive,
                       is_first=is_first, is_seal=is_seal, is_broken=is_broken,
                       board_type=board_type, close_price=close)


def _make_cycle_report():
    from ashare_review.report.daily import DailyReport
    return DailyReport(tdx=None, ak_fetcher=None)  # 这两个方法不触网


def test_cycle_next_bias_all_stages():
    rep = _make_cycle_report()
    cases = [
        # (limit_ups 构造参数, 期望 stage, 期望 next_bias)
        (100, 80, 6, 30, '高潮末期', 'down'),   # total≥100 封板率80% 高度6 一字30%
        (100, 80, 6, 20, '高潮期', 'flat'),     # 同上但一字<30%
        (60, 45, 5, 5, '发酵期', 'up'),         # ≥50 封板率75% 高度5
        (40, 26, 4, 3, '启动期', 'up'),         # ≥30 封板率65% 高度4
        (10, 8, 1, 0, '冰点期', 'flat'),        # total<15
        (40, 20, 2, 1, '退潮期', 'down'),       # 封板率50%<55
        (30, 20, 2, 2, '震荡期', 'flat'),       # 其他
    ]
    for total, sealed, max_cons, yizi, expect_stage, expect_bias in cases:
        limit_ups = []
        for i in range(total):
            is_yizi = i < yizi
            is_seal = i < sealed
            cons = max_cons if i == 0 else (1 if is_seal else 0)
            limit_ups.append(_lu(
                code=f'600{i:03d}', t='09:25' if is_yizi else '10:00',
                consecutive=cons, is_seal=is_seal,
                is_broken=(not is_seal) if not is_yizi else False,
                is_first=(cons == 1)))
        cycle = rep._detect_cycle_stage(limit_ups, {})
        assert cycle['stage'] == expect_stage, f"total={total} sealed={sealed}"
        assert cycle['next_bias'] == expect_bias, f"stage={cycle['stage']}"


def test_auction_direction_all_forecasts():
    rep = _make_cycle_report()
    cases = [
        # (总数, 一字数, 早盘数, 炸板数, 期望 forecast, 期望 direction)
        (30, 12, 16, 1, '火爆', 'high'),    # 一字≥10 且早盘占比≥50%
        (50, 3, 22, 2, '偏强', 'high'),     # 早盘占比≥40% 且总数≥50
        (30, 1, 8, 2, '中性', 'flat'),      # 早盘占比≥20%
        (30, 1, 2, 8, '偏弱', 'low'),       # 炸板>20%
        (20, 0, 2, 0, '观望', 'low'),       # 其余
    ]
    for total, yizi, early, broken, expect_fc, expect_dir in cases:
        limit_ups = []
        for i in range(total):
            if i < yizi:
                t = '09:25'
            elif i < yizi + early:
                t = '09:40'
            else:
                t = '14:00'
            limit_ups.append(_lu(code=f'600{i:03d}', t=t,
                                 is_seal=(i >= broken), is_broken=(i < broken),
                                 consecutive=(2 if i % 5 == 0 else 1)))
        fc = rep._forecast_next_auction(limit_ups, {})
        assert fc['forecast'] == expect_fc, f"total={total} yizi={yizi} early={early} broken={broken}"
        assert fc['direction'] == expect_dir, f"forecast={fc['forecast']}"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py::test_cycle_next_bias_all_stages ashare_review/tests/test_prediction_ledger.py::test_auction_direction_all_forecasts -v`
Expected: FAIL — KeyError 'next_bias'（字段不存在）

- [ ] **Step 3: 实现**

在 `daily.py` 模块级（_is_yizi_board 之前）加：

```python
# ---- 预测台账：情绪周期 stage → 次日方向 映射（待验证假设，台账数据可反过来校准） ----
_NEXT_BIAS_BY_STAGE = {
    '启动期': 'up', '发酵期': 'up',
    '高潮末期': 'down', '退潮期': 'down',
    '高潮期': 'flat', '震荡期': 'flat', '冰点期': 'flat',
}
# ---- 预测台账：竞价 forecast → 方向 映射 ----
_AUCTION_DIRECTION = {
    '火爆': 'high', '偏强': 'high', '中性': 'flat',
    '偏弱': 'low', '观望': 'low',
}
```

`_detect_cycle_stage` 的 return dict（约第 588 行）加一行：

```python
        return {
            'stage': stage,
            'stage_class': stage_class,
            'stage_emoji': stage_emoji,
            'stage_desc': stage_desc.strip(),
            'action': action,
            'risk_level': risk_level,
            'next_bias': _NEXT_BIAS_BY_STAGE.get(stage, 'flat'),
            'metrics': {
                'total_zt': total,
                'seal_rate': round(seal_rate, 1),
                'max_consecutive': max_cons,
                'yizi_ratio': round(yizi_ratio * 100, 1),
                'first_boards': first_count,
                'height_trend': height_analysis,
            }
        }
```

`_forecast_next_auction` 的 return dict（约第 776 行）加一行：

```python
        return {
            'forecast': forecast,
            'forecast_desc': forecast_desc,
            'direction': _AUCTION_DIRECTION.get(forecast, 'flat'),
            'early_sealed': early_sealed,
            'morning_sealed': morning_sealed,
            'afternoon_sealed': afternoon_sealed,
            'yizi_count': yizi_count,
            'strong_multi': strong_multi[:5],
        }
```

`generate()` 的 return dict（约第 142 行）加一行（供竞价验证找回当日涨停池）：

```python
        return {
            'date': report_date,
            'limit_up_codes': [lu.code for lu in limit_ups],
            'is_trading_day': self.calendar.is_trading_day(
                datetime.strptime(trade_date, '%Y%m%d').date()),
            # ...其余原样不动
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: 8 passed（Task1 的 6 个 + 本任务 2 个）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/report/daily.py ashare_review/tests/test_prediction_ledger.py
git commit -m "feat(ledger): daily.py 增加 next_bias/direction/limit_up_codes 结构化字段"
```

---

### Task 3: SQLite 存储层 store.py

**Files:**
- Create: `ashare_review/prediction_ledger/store.py`
- Test: `ashare_review/tests/test_prediction_ledger.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_prediction_ledger.py

# ---------- Task 3: SQLite 存储层 ----------

def _sample_rows():
    return [
        {'pred_date': '20260814', 'pred_type': 'picks', 'item_key': '600001',
         'item_name': '测试A', 'direction': None, 'score': 62,
         'detail': '{"reasons": []}'},
        {'pred_date': '20260814', 'pred_type': 'picks', 'item_key': '600002',
         'item_name': '测试B', 'direction': None, 'score': 45,
         'detail': '{"reasons": []}'},
        {'pred_date': '20260814', 'pred_type': 'cycle', 'item_key': 'daily',
         'item_name': '发酵期', 'direction': 'up', 'score': None,
         'detail': '{"total_zt": 60}'},
        {'pred_date': '20260814', 'pred_type': 'auction', 'item_key': 'daily',
         'item_name': '偏强', 'direction': 'high', 'score': None,
         'detail': '{"pool_codes": ["600001", "600002"]}'},
    ]


def test_store_upsert_idempotent(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    assert store.upsert_predictions(_sample_rows()) == 4
    assert store.upsert_predictions(_sample_rows()) == 0   # 重复写不产生新行
    assert len(store.rows(365)) == 4


def test_store_get_unverified_and_mark(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    store.upsert_predictions(_sample_rows())
    pending = store.get_unverified()
    assert len(pending) == 4
    first = pending[0]
    store.mark_verified(first['id'], 'zt', 1)
    assert len(store.get_unverified()) == 3


def test_store_summary_aggregation(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    store.upsert_predictions(_sample_rows())
    # 手工验证：600001 命中，600002 未中，cycle/auction 各命中
    rows = store.rows(365)
    for r in rows:
        if r['pred_type'] == 'picks':
            store.mark_verified(r['id'], 'zt' if r['item_key'] == '600001' else 'down',
                                1 if r['item_key'] == '600001' else 0)
        elif r['pred_type'] == 'cycle':
            store.mark_verified(r['id'], 'up', 1)
        else:
            store.mark_verified(r['id'], 'high', 1)
    s = store.summary(365)
    assert s['picks']['total'] == 2 and s['picks']['verified'] == 2 and s['picks']['hit'] == 1
    assert s['picks']['rate'] == 0.5
    assert s['cycle']['rate'] == 1.0
    assert s['auction']['rate'] == 1.0
    # 分数段：≥60 → 1/1；50-59 → 0；<50 → 0/1
    buckets = {b['label']: b for b in s['buckets']}
    assert buckets['≥60']['hit'] == 1 and buckets['≥60']['verified'] == 1
    assert buckets['50-59']['verified'] == 0
    assert buckets['<50']['hit'] == 0 and buckets['<50']['verified'] == 1
    # 覆盖统计
    assert s['coverage']['verified_days'] == 1
    assert s['coverage']['pending'] == 0


def test_store_summary_window_filter(tmp_path):
    from datetime import date
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    today = date.today().strftime('%Y%m%d')
    store.upsert_predictions([{
        'pred_date': today, 'pred_type': 'picks', 'item_key': '600001',
        'item_name': '今天', 'direction': None, 'score': 60, 'detail': '{}'}])
    s = store.summary(1)     # 1 天窗口：包含今天
    assert s['picks']['total'] == 1
    s2 = store.summary(0)    # 0 天窗口：cutoff=今天，pred_date >= 今天 仍含今天（边界）
    assert s2['picks']['total'] == 1


def test_store_set_actual(tmp_path):
    from ashare_review.prediction_ledger.store import LedgerStore
    store = LedgerStore(str(tmp_path / 't.db'))
    store.upsert_predictions(_sample_rows())
    store.set_actual('20260814', 'picks', '600001', 'up3', 1)
    rows = store.rows(365)
    row = [r for r in rows if r['item_key'] == '600001'][0]
    assert row['actual'] == 'up3' and row['hit'] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: FAIL — ModuleNotFoundError（store 不存在）

- [ ] **Step 3: 实现 store.py**

```python
# ashare_review/prediction_ledger/store.py
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
                params: List = [cutoff]
                if lo is not None:
                    sql += " AND score >= ?"
                    params.append(lo)
                if hi is not None:
                    sql += " AND score < ?"
                    params.append(hi)
                row = conn.execute(sql, params).fetchone()
                verified = row['verified'] or 0
                buckets.append({'label': label, 'total': row['total'] or 0,
                                'hit': row['hit'] or 0,
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: 13 passed（含 Task1 6 个 + Task2 2 个 + Task3 5 个）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/prediction_ledger/store.py ashare_review/tests/test_prediction_ledger.py
git commit -m "feat(ledger): SQLite 存储层（幂等写入/验证/聚合统计）"
```

---

### Task 4: 编排层 service.py — record_day + validate_pending

**Files:**
- Create: `ashare_review/prediction_ledger/service.py`
- Test: `ashare_review/tests/test_prediction_ledger.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_prediction_ledger.py

# ---------- Task 4: 编排层 record_day / validate_pending ----------

class FakeTdx:
    """返回两日行情（前一交易日 + 次日）：code -> [(open, close), (open, close)]"""
    def __init__(self, data):
        self.data = data  # {code: [(prev_open, prev_close), (next_open, next_close)]}

    def read_daily(self, code, market):
        import pandas as pd
        bars = self.data.get(str(code))
        if not bars:
            return pd.DataFrame()
        return pd.DataFrame([{'open': b[0], 'close': b[1]} for b in bars])


class FakeAk:
    """涨停池可控：date -> LimitUpInfo 列表"""
    def __init__(self, pools=None, raise_on=None):
        self.pools = pools or {}        # {'20260814': [LimitUpInfo, ...]}
        self.raise_on = raise_on or set()

    def get_limit_up_pool(self, trade_date):
        if trade_date in self.raise_on:
            raise RuntimeError('network down')
        return self.pools.get(trade_date, [])


def _lu_info(code, consecutive=1):
    from ashare_review.data.models import LimitUpInfo
    return LimitUpInfo(code=code, name='测试', limit_up_time='10:00', seal_amount=1000,
                       turnover=5000, float_market_cap=30, consecutive=consecutive,
                       is_first=consecutive == 1, is_seal=True, is_broken=False,
                       board_type='换手板', close_price=10.0)


def _canned_report():
    return {
        'date': '2026-08-14',
        'limit_up_codes': ['600001', '600002'],
        'sentiment': {'picks': [
            {'code': '600001', 'name': '测试A', 'score': 62, 'reasons': ['首板']},
            {'code': '600002', 'name': '测试B', 'score': 45, 'reasons': []},
        ]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'stage_desc': '赚钱效应增强',
                  'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high',
                             'forecast_desc': '多数涨停股预期高开'},
    }


def test_record_day_writes_three_types(tmp_path):
    from ashare_review.prediction_ledger.service import record_day
    from ashare_review.prediction_ledger.store import LedgerStore
    db = str(tmp_path / 't.db')
    assert record_day(_canned_report(), '20260814', db) == 4
    assert record_day(_canned_report(), '20260814', db) == 0   # 幂等
    store = LedgerStore(db)
    rows = store.rows(365)
    assert len(rows) == 4
    types = {r['pred_type'] for r in rows}
    assert types == {'picks', 'cycle', 'auction'}


def test_record_day_skips_error_report(tmp_path):
    from ashare_review.prediction_ledger.service import record_day
    assert record_day({'error': 'boom'}, '20260814', str(tmp_path / 't.db')) == 0
    assert record_day(None, '20260814', str(tmp_path / 't.db')) == 0


def test_validate_pending_picks(tmp_path):
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    # 次日 20260815（周五；2026-08-15 是周六，这里直接构造日历数据不可靠，改用真实日历兜底）
    # 用交易日历找 20260814 的下一交易日
    from ashare_review.utils.calendar import TradingCalendar
    cal = TradingCalendar()
    next_ymd = None
    from datetime import datetime, timedelta
    d = datetime.strptime('20260814', '%Y%m%d').date()
    n = cal.next_trading_day(d, offset=1)
    next_ymd = n.strftime('%Y%m%d')
    # 600001 次日 +10%（涨停）；600002 次日 -5%
    tdx = FakeTdx({'600001': [(10.0, 10.0), (11.0, 11.0)],   # 次日 11.0/10.0-1=10%
                   '600002': [(10.0, 10.0), (9.5, 9.5)]})    # 次日 -5%
    ak = FakeAk({next_ymd: [_lu_info('600001', consecutive=2)]})
    n_validated = validate_pending(tdx, ak, calendar=cal, db_path=db)
    assert n_validated == 4   # 2 精选 + 1 cycle + 1 auction 全部有数据可验证
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365) if r['pred_type'] == 'picks'}
    assert rows['600001']['actual'] == 'zt' and rows['600001']['hit'] == 1
    assert rows['600002']['actual'] == 'down' and rows['600002']['hit'] == 0


def test_validate_pending_cycle_and_auction(tmp_path):
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    next_ymd = cal.next_trading_day(datetime.strptime('20260814', '%Y%m%d').date(),
                                    offset=1).strftime('%Y%m%d')
    # cycle：今日 60 只 → 次日 66 只（r=1.1 → up，预测 up → 命中）
    # auction：600001 次日高开 +2%（10.0→10.2），600002 高开 +1% → avg 1.5% → high，预测 high → 命中
    tdx = FakeTdx({'600001': [(10.0, 10.0), (10.2, 10.5)],
                   '600002': [(10.0, 10.0), (10.1, 10.0)]})
    pool = [_lu_info(f'600{i:03d}') for i in range(66)]   # 66 只：r=66/60=1.1 → up
    pool[0], pool[1] = _lu_info('600001'), _lu_info('600002')
    ak = FakeAk({next_ymd: pool})
    n = validate_pending(tdx, ak, calendar=cal, db_path=db)
    assert n == 4
    store = LedgerStore(db)
    rows = {r['pred_type']: r for r in store.rows(365)}
    assert rows['cycle']['actual'] == 'up' and rows['cycle']['hit'] == 1
    assert rows['auction']['actual'] == 'high' and rows['auction']['hit'] == 1


def test_validate_pending_network_down_skips(tmp_path):
    from ashare_review.prediction_ledger.service import record_day, validate_pending
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    db = str(tmp_path / 't.db')
    record_day(_canned_report(), '20260814', db)
    cal = TradingCalendar()
    next_ymd = cal.next_trading_day(datetime.strptime('20260814', '%Y%m%d').date(),
                                    offset=1).strftime('%Y%m%d')
    tdx = FakeTdx({'600001': [(10.0, 10.0), (11.0, 11.0)],
                   '600002': [(10.0, 10.0), (9.5, 9.5)]})
    ak = FakeAk({}, raise_on={next_ymd})      # 涨停池网络失败
    n = validate_pending(tdx, ak, calendar=cal, db_path=db)
    assert n == 3   # 精选走 TDX 降级(2) + auction 走 TDX(1)；cycle 无涨停池跳过
    store = LedgerStore(db)
    rows = {r['pred_type']: r for r in store.rows(365)}
    assert rows['cycle']['hit'] is None        # cycle 无涨停池 → 不判
    assert rows['auction']['hit'] == 1         # auction 走 TDX 平均高开 → 验证
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: FAIL — ModuleNotFoundError（service 不存在）

- [ ] **Step 3: 实现 service.py**

```python
# ashare_review/prediction_ledger/service.py
"""预测台账 — 编排层

record_day:         把复盘报告中的三类预测写入台账（幂等）
validate_pending:   自动验证所有未验证记录（tdx 本地行情 + ak 涨停池，失败降级）
migrate_picks_history: 一次性追溯导入 picks_history.json 的历史精选
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..utils.calendar import TradingCalendar
from .store import LedgerStore
from .validate import grade_pick, grade_cycle, grade_auction, hit_for

DB_PATH = os.environ.get(
    'LEDGER_DB',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'prediction_ledger.db'))
PICKS_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'picks_history.json')


def _market_of(code: str) -> str:
    if str(code).startswith(('8', '4', '92')):
        return 'bj'
    return 'sh' if str(code).startswith('6') else 'sz'


def _zt_limit_pct(code: str) -> float:
    if str(code).startswith(('30', '68')):
        return 19.6
    if str(code).startswith(('8', '4', '92')):
        return 29.4
    return 9.8


def _next_trade_ymd(calendar: TradingCalendar, trade_date: str) -> Optional[str]:
    try:
        d = datetime.strptime(trade_date, '%Y%m%d').date()
    except ValueError:
        return None
    n = calendar.next_trading_day(d, offset=1)
    return n.strftime('%Y%m%d') if n else None


def record_day(report: Optional[Dict], trade_date: str, db_path: Optional[str] = None) -> int:
    """把报告中的三类预测写入台账。返回插入行数。幂等。"""
    db_path = db_path or DB_PATH
    if not report or report.get('error'):
        return 0
    rows: List[Dict] = []

    for p in (report.get('sentiment') or {}).get('picks', []) or []:
        rows.append({
            'pred_date': trade_date, 'pred_type': 'picks',
            'item_key': str(p.get('code', '')), 'item_name': p.get('name', ''),
            'direction': None, 'score': p.get('score'),
            'detail': json.dumps({'reasons': p.get('reasons', [])}, ensure_ascii=False),
        })

    cycle = report.get('cycle') or {}
    if cycle.get('stage'):
        rows.append({
            'pred_date': trade_date, 'pred_type': 'cycle', 'item_key': 'daily',
            'item_name': cycle['stage'], 'direction': cycle.get('next_bias'),
            'score': None,
            'detail': json.dumps({
                'stage': cycle['stage'],
                'stage_desc': cycle.get('stage_desc', ''),
                'total_zt': (cycle.get('metrics') or {}).get('total_zt', 0),
            }, ensure_ascii=False),
        })

    auction = report.get('auction_forecast') or {}
    if auction.get('forecast'):
        rows.append({
            'pred_date': trade_date, 'pred_type': 'auction', 'item_key': 'daily',
            'item_name': auction['forecast'], 'direction': auction.get('direction'),
            'score': None,
            'detail': json.dumps({
                'forecast': auction['forecast'],
                'forecast_desc': auction.get('forecast_desc', ''),
                'pool_codes': report.get('limit_up_codes', []),
            }, ensure_ascii=False),
        })

    return LedgerStore(db_path).upsert_predictions(rows)


def _pick_actual(tdx, code: str, zt_codes: set) -> Tuple[Optional[str], Optional[int]]:
    """验证单只精选：TDX 次日行情 + 涨停集合判定。返回 (actual, hit)。"""
    try:
        df = tdx.read_daily(code, _market_of(code))
        if df is None or df.empty or len(df) < 2:
            return None, None
        prev_close = float(df.iloc[-2]['close'])
        today_close = float(df.iloc[-1]['close'])
        today_chg = (today_close - prev_close) / prev_close * 100 if prev_close else 0.0
    except Exception:
        return None, None
    # 涨停判定：次日涨停池集合 或 涨幅达标（涨停池网络失败时的降级）
    is_zt = str(code) in zt_codes or today_chg >= _zt_limit_pct(code)
    actual = grade_pick(today_chg, is_zt)
    return actual, hit_for('picks', None, actual)


def _auction_actual(tdx, codes: List[str]) -> Optional[str]:
    """当日涨停池次日平均高开幅度分级。数据不可用返回 None。"""
    gaps = []
    for code in codes:
        try:
            df = tdx.read_daily(code, _market_of(code))
            if df is None or df.empty or len(df) < 2:
                continue
            prev_close = float(df.iloc[-2]['close'])
            open_price = float(df.iloc[-1]['open'])
            if prev_close:
                gaps.append((open_price / prev_close - 1) * 100)
        except Exception:
            continue
    if not gaps:
        return None
    return grade_auction(sum(gaps) / len(gaps))


def validate_pending(tdx, ak, calendar: Optional[TradingCalendar] = None,
                     db_path: Optional[str] = None) -> int:
    """验证所有未验证记录，返回成功验证条数。单条失败跳过，不中断。"""
    db_path = db_path or DB_PATH
    calendar = calendar or TradingCalendar()
    store = LedgerStore(db_path)
    pending = store.get_unverified()
    by_date: Dict[str, List[dict]] = {}
    for row in pending:
        by_date.setdefault(row['pred_date'], []).append(row)

    validated = 0
    for pred_date, rows in by_date.items():
        next_date = _next_trade_ymd(calendar, pred_date)
        if not next_date:
            continue
        try:
            next_pool = ak.get_limit_up_pool(next_date) or []
            pool_ok = True
        except Exception:
            next_pool, pool_ok = [], False
        zt_codes = {str(lu.code) for lu in next_pool}

        for row in rows:
            try:
                if row['pred_type'] == 'picks':
                    actual, hit = _pick_actual(tdx, row['item_key'], zt_codes)
                elif row['pred_type'] == 'cycle':
                    if not pool_ok:
                        continue
                    detail = json.loads(row['detail']) if row['detail'] else {}
                    today_zt = int(detail.get('total_zt', 0))
                    actual = grade_cycle(today_zt, len(zt_codes))
                    hit = hit_for('cycle', row['direction'], actual)
                elif row['pred_type'] == 'auction':
                    detail = json.loads(row['detail']) if row['detail'] else {}
                    actual = _auction_actual(tdx, detail.get('pool_codes', []))
                    hit = hit_for('auction', row['direction'], actual)
                else:
                    continue
                if actual is not None and hit is not None:
                    store.mark_verified(row['id'], actual, hit)
                    validated += 1
            except Exception:
                continue
    return validated
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: 18 passed（新增 5 个）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/prediction_ledger/service.py ashare_review/tests/test_prediction_ledger.py
git commit -m "feat(ledger): 编排层 record_day/validate_pending（自动验证+网络降级）"
```

---

### Task 5: 历史追溯 migrate_picks_history + CLI

**Files:**
- Modify: `ashare_review/prediction_ledger/service.py`（追加函数）
- Create: `ashare_review/prediction_ledger/migrate.py`
- Test: `ashare_review/tests/test_prediction_ledger.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_prediction_ledger.py

# ---------- Task 5: 历史追溯 ----------

def test_migrate_picks_history(tmp_path):
    import json
    from ashare_review.prediction_ledger.service import migrate_picks_history
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.utils.calendar import TradingCalendar
    from datetime import datetime
    hist = str(tmp_path / 'picks_history.json')
    json.dump({
        '20260813': [{'code': '600001', 'name': 'A', 'score': 61, 'reasons': []},
                     {'code': '600002', 'name': 'B', 'score': 50, 'reasons': []}],
        '20260810': [{'code': '600003', 'name': 'C', 'score': 55, 'reasons': []}],
    }, open(hist, 'w', encoding='utf-8'))
    cal = TradingCalendar()
    # 600001 次日涨停；600002 次日 -2%；600003 无 TDX 数据（跳过）
    tdx = FakeTdx({'600001': [(10.0, 10.0), (11.0, 11.0)],
                   '600002': [(10.0, 10.0), (9.8, 9.8)]})
    ak = FakeAk({})   # 无涨停池 → 降级按涨幅
    db = str(tmp_path / 't.db')
    inserted = migrate_picks_history(tdx, ak, calendar=cal, db_path=db, history_file=hist)
    assert inserted == 2
    # 幂等：再跑一遍不新增
    assert migrate_picks_history(tdx, ak, calendar=cal, db_path=db, history_file=hist) == 0
    store = LedgerStore(db)
    rows = {r['item_key']: r for r in store.rows(365)}
    assert rows['600001']['actual'] == 'zt' and rows['600001']['hit'] == 1
    assert rows['600002']['actual'] == 'flat' and rows['600002']['hit'] == 0


def test_migrate_missing_file(tmp_path):
    from ashare_review.prediction_ledger.service import migrate_picks_history
    assert migrate_picks_history(None, None, history_file=str(tmp_path / 'nope.json'),
                                 db_path=str(tmp_path / 't.db')) == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: FAIL — ImportError（migrate_picks_history 不存在）

- [ ] **Step 3: 实现**

在 `service.py` 末尾追加：

```python
def migrate_picks_history(tdx, ak, calendar: Optional[TradingCalendar] = None,
                          db_path: Optional[str] = None,
                          history_file: Optional[str] = None) -> int:
    """追溯导入 picks_history.json 的历史精选（含验证结果）。幂等，返回插入行数。"""
    db_path = db_path or DB_PATH
    history_file = history_file or PICKS_HISTORY_FILE
    if not os.path.exists(history_file):
        return 0
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except Exception:
        return 0
    calendar = calendar or TradingCalendar()
    store = LedgerStore(db_path)
    inserted = 0
    for pred_date, picks in history.items():
        next_date = _next_trade_ymd(calendar, pred_date)
        if not next_date:
            continue
        try:
            next_pool = ak.get_limit_up_pool(next_date) or []
        except Exception:
            next_pool = []
        zt_codes = {str(lu.code) for lu in next_pool}
        for p in picks or []:
            code = str(p.get('code', ''))
            if not code:
                continue
            actual, hit = _pick_actual(tdx, code, zt_codes)
            if actual is None:
                continue
            inserted += store.upsert_predictions([{
                'pred_date': pred_date, 'pred_type': 'picks',
                'item_key': code, 'item_name': p.get('name', ''),
                'direction': None, 'score': p.get('score'),
                'detail': json.dumps({'reasons': p.get('reasons', [])}, ensure_ascii=False),
            }])
            store.set_actual(pred_date, 'picks', code, actual, hit)
    return inserted
```

新建 `ashare_review/prediction_ledger/migrate.py`：

```python
"""CLI：追溯导入历史精选到预测台账

用法: python -m ashare_review.prediction_ledger.migrate
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from .service import migrate_picks_history


def main() -> None:
    tdx = TdxReader()
    ak = AkshareFetcher()
    n = migrate_picks_history(tdx, ak)
    print(f'历史精选追溯完成，写入 {n} 条（幂等，可重复运行）')


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: 20 passed（新增 2 个）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/prediction_ledger/service.py ashare_review/prediction_ledger/migrate.py ashare_review/tests/test_prediction_ledger.py
git commit -m "feat(ledger): 历史精选追溯迁移 + CLI"
```

---

### Task 6: app.py 接线 + 路由 + 导航

**Files:**
- Modify: `ashare_review/web/app.py`（/review 两处渲染点 + 2 个新路由）
- Modify: `ashare_review/web/templates/base.html`（导航）
- Test: `ashare_review/tests/test_prediction_ledger.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_prediction_ledger.py

# ---------- Task 6: Web 接线 ----------

def test_review_route_records_ledger(tmp_path, monkeypatch):
    import unittest.mock as mock
    import pandas as pd
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.store import LedgerStore
    from ashare_review.report.daily import DailyReport
    from ashare_review.web.app import app, tdx, ak_fetcher

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'web.db'))
    monkeypatch.setattr(ak_fetcher, 'get_limit_up_pool', lambda d: [])   # 无网络
    monkeypatch.setattr(tdx, 'read_daily', lambda *a, **k: pd.DataFrame())  # 无 TDX 数据
    canned = {
        'date': '2026-08-14',
        'limit_up_codes': ['600001'],
        'sentiment': {'picks': [{'code': '600001', 'name': '测试A', 'score': 62, 'reasons': []}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'stage_desc': 'x',
                  'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high', 'forecast_desc': 'y'},
    }
    app.config['TESTING'] = True
    with mock.patch.object(DailyReport, 'generate', return_value=canned):
        c = app.test_client()
        rv = c.get('/review?date=20260814&refresh=1')
        assert rv.status_code == 200
    store = LedgerStore(str(tmp_path / 'web.db'))
    rows = store.rows(365)
    assert len(rows) == 4
    assert {r['pred_type'] for r in rows} == {'picks', 'cycle', 'auction'}


def test_prediction_ledger_page(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.prediction_ledger.service import record_day
    from ashare_review.web.app import app

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'page.db'))
    record_day({
        'limit_up_codes': ['600001'],
        'sentiment': {'picks': [{'code': '600001', 'name': '测试A', 'score': 62, 'reasons': []}]},
        'cycle': {'stage': '发酵期', 'next_bias': 'up', 'metrics': {'total_zt': 60}},
        'auction_forecast': {'forecast': '偏强', 'direction': 'high'},
    }, '20260814', str(tmp_path / 'page.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/prediction_ledger')
    assert rv.status_code == 200
    body = rv.data.decode('utf-8')
    assert '测试A' in body
    assert '发酵期' in body


def test_ledger_validate_api(tmp_path, monkeypatch):
    from ashare_review.prediction_ledger import service as ledger_service
    from ashare_review.web.app import app

    monkeypatch.setattr(ledger_service, 'DB_PATH', str(tmp_path / 'api.db'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.post('/api/ledger/validate')
    assert rv.status_code == 200
    assert rv.get_json()['ok'] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: FAIL — AssertionError 或 404（路由未实现）

- [ ] **Step 3: 实现**

在 `app.py` 的 `review()` 函数上方加辅助函数（放在第 1404 行 `_review_migrate_old_cache` 附近）：

```python
def _ledger_sync(report, trade_date):
    """把复盘报告预测写入台账并验证昨日预测（幂等，失败不影响复盘）"""
    if not report or report.get('error'):
        return
    try:
        from ..prediction_ledger.service import record_day, validate_pending
        record_day(report, trade_date)
        validate_pending(tdx, ak_fetcher)
    except Exception:
        import traceback
        traceback.print_exc()
```

在 `review()` 的两处 `return render_template('review_v2.html', ...)` **之前**各插入一行：

```python
        _ledger_sync(payload, trade_date)   # 缓存命中路径（payload 为 report dict）
```

```python
        _ledger_sync(report, trade_date)    # 新生成路径
```

在文件末尾（`/api/review/article` 之后）加两个新路由：

```python
# ======================================================================
# 预测台账（复盘预测的次日验证 + 准确率统计）
# ======================================================================

TYPE_LABELS = {'picks': '精选标的', 'cycle': '情绪周期', 'auction': '竞价预期'}
DIR_LABELS = {'up': '走强', 'down': '退潮', 'high': '高开', 'low': '低开', 'flat': '震荡/平淡'}
ACTUAL_LABELS = {'zt': '涨停', 'up3': '涨≥3%', 'up': '收涨', 'flat': '震荡', 'down': '大跌',
                 'high': '高开', 'low': '低开'}


@app.route('/prediction_ledger')
def prediction_ledger():
    from ..prediction_ledger.service import DB_PATH
    from ..prediction_ledger.store import LedgerStore
    store = LedgerStore(DB_PATH)
    rows = store.rows()
    for r in rows:
        r['type_label'] = TYPE_LABELS.get(r['pred_type'], r['pred_type'])
        if r['pred_type'] == 'picks':
            r['dir_label'] = f"{r['score']}分" if r['score'] is not None else '—'
            r['actual_label'] = ACTUAL_LABELS.get(r['actual'], r['actual'] or '—')
        else:
            r['dir_label'] = DIR_LABELS.get(r['direction'], r['direction'] or '—')
            r['actual_label'] = ACTUAL_LABELS.get(r['actual'], r['actual'] or '—')
    return render_template('prediction_ledger.html',
                           summary=store.summary(), rows=rows)


@app.route('/api/ledger/validate', methods=['POST'])
def api_ledger_validate():
    from ..prediction_ledger.service import validate_pending
    n = validate_pending(tdx, ak_fetcher)
    return jsonify({'ok': True, 'validated': n})
```

在 `base.html` 的导航里，`消息雷达` 之后加：

```html
            <a href="/prediction_ledger" class="nav-item {% if request.endpoint == 'prediction_ledger' %}active{% endif %}">
                <span class="nav-icon">📒</span>
                <span class="nav-label">预测台账</span>
            </a>
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py -v`
Expected: 23 passed（新增 3 个）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/web/app.py ashare_review/web/templates/base.html ashare_review/tests/test_prediction_ledger.py
git commit -m "feat(ledger): /review 自动记录验证 + /prediction_ledger 页面 + 验证 API"
```

---

### Task 7: 台账页面 prediction_ledger.html

**Files:**
- Create: `ashare_review/web/templates/prediction_ledger.html`

- [ ] **Step 1: 写模板**

```html
{% extends "base.html" %}
{% block title %}预测台账 · 竞价交易系统{% endblock %}
{% block content %}
<div class="content-area">
    <div class="page-header">
        <div>
            <div class="page-title">📒 预测台账</div>
            <div class="page-date">复盘预测的次日验证与准确率统计 · 近30天</div>
        </div>
        <div>
            <button class="btn btn-primary" id="btn-validate">⚡ 验证未验证项</button>
        </div>
    </div>

    {% if summary.coverage.verified_days == 0 and summary.coverage.pending == 0 %}
    <div class="card">
        <div class="card-body">
            <div class="empty-result">
                <div class="empty-icon">📒</div>
                <h3>还没有预测记录</h3>
                <p>打开 <a href="/review">复盘分析</a> 生成一次复盘后，系统会自动把当日三类预测写入台账，并在次日自动验证。</p>
            </div>
        </div>
    </div>
    {% else %}

    <div class="grid-2">
        <div class="card">
            <div class="card-header">📊 精选标的命中率
                <span class="card-badge">{{ summary.picks.hit }}/{{ summary.picks.verified }}</span></div>
            <div class="card-body">
                <div class="stat-big">
                    {% if summary.picks.rate is not none %}{{ (summary.picks.rate * 100)|round(1) }}%{% else %}--{% endif %}
                </div>
                <div class="bucket-list">
                    {% for b in summary.buckets %}
                    <div class="bucket-row">
                        <span class="bucket-label">{{ b.label }}</span>
                        <div class="bucket-bar">
                            <div class="bucket-fill" style="width: {{ (b.rate * 100)|round(1) if b.rate is not none else 0 }}%;"></div>
                        </div>
                        <span class="bucket-rate">
                            {{ (b.rate * 100)|round(1) if b.rate is not none else '--' }}%
                            <small>{{ b.hit }}/{{ b.verified }}</small>
                        </span>
                    </div>
                    {% endfor %}
                </div>
                <div class="stat-note">命中 = 次日涨停或涨幅≥3% · 按分数段分组</div>
            </div>
        </div>
        <div class="card">
            <div class="card-header">🔄 情绪周期准确率
                <span class="card-badge">{{ summary.cycle.hit }}/{{ summary.cycle.verified }}</span></div>
            <div class="card-body">
                <div class="stat-big">
                    {% if summary.cycle.rate is not none %}{{ (summary.cycle.rate * 100)|round(1) }}%{% else %}--{% endif %}
                </div>
                <div class="stat-note">涨停家数 ±10% 判定方向 · 预测方向一致即命中</div>
            </div>
        </div>
    </div>

    <div class="grid-2">
        <div class="card">
            <div class="card-header">⚡ 竞价预期准确率
                <span class="card-badge">{{ summary.auction.hit }}/{{ summary.auction.verified }}</span></div>
            <div class="card-body">
                <div class="stat-big">
                    {% if summary.auction.rate is not none %}{{ (summary.auction.rate * 100)|round(1) }}%{% else %}--{% endif %}
                </div>
                <div class="stat-note">当日涨停池次日平均高开 ≥1.5% 高开 / ≤-0.5% 低开</div>
            </div>
        </div>
        <div class="card">
            <div class="card-header">🗓 样本覆盖</div>
            <div class="card-body">
                <div class="stat-big">{{ summary.coverage.verified_days }} <small>天已验证</small></div>
                <div class="stat-note">{{ summary.coverage.pending }} 条待验证（打开复盘页会自动验证）</div>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="card-header">
            明细
            <span style="margin-left:auto;display:flex;gap:10px;">
                <select id="f-type">
                    <option value="">全部类型</option>
                    <option value="picks">精选标的</option>
                    <option value="cycle">情绪周期</option>
                    <option value="auction">竞价预期</option>
                </select>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
                    <input type="checkbox" id="f-pending"> 只看未验证
                </label>
            </span>
        </div>
        <div class="card-body" style="padding:0;">
            <table class="data-table" style="width:100%;">
                <thead>
                    <tr><th>日期</th><th>类型</th><th>内容</th><th>预测</th><th>实际</th><th>判定</th></tr>
                </thead>
                <tbody>
                {% for r in rows %}
                <tr data-type="{{ r.pred_type }}" data-hit="{{ 'pending' if r.hit is none else r.hit }}">
                    <td>{{ r.pred_date }}</td>
                    <td>{{ r.type_label }}</td>
                    <td>{% if r.pred_type == 'picks' %}{{ r.item_name }} <small>({{ r.item_key }})</small>{% else %}{{ r.item_name }}{% endif %}</td>
                    <td>{{ r.dir_label }}</td>
                    <td>{{ r.actual_label }}</td>
                    <td>
                        {% if r.hit is none %}<span class="tag tag-pending">⏳ 待验证</span>
                        {% elif r.hit == 1 %}<span class="tag tag-hit">✅ 命中</span>
                        {% else %}<span class="tag tag-miss">❌ 未中</span>{% endif %}
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    {% endif %}
</div>
{% endblock %}
{% block scripts %}
<script>
(function () {
    var btn = document.getElementById('btn-validate');
    if (btn) {
        btn.addEventListener('click', function () {
            btn.disabled = true;
            fetch('/api/ledger/validate', {method: 'POST'})
                .then(function (r) { return r.json(); })
                .then(function (d) { alert('已验证 ' + d.validated + ' 条'); location.reload(); })
                .catch(function () { alert('验证失败，请稍后重试'); btn.disabled = false; });
        });
    }
    var fType = document.getElementById('f-type');
    var fPending = document.getElementById('f-pending');
    if (fType && fPending) {
        function applyFilter() {
            var t = fType.value, onlyPending = fPending.checked;
            document.querySelectorAll('table.data-table tbody tr').forEach(function (tr) {
                var ok = (!t || tr.dataset.type === t) && (!onlyPending || tr.dataset.hit === 'pending');
                tr.style.display = ok ? '' : 'none';
            });
        }
        fType.addEventListener('change', applyFilter);
        fPending.addEventListener('change', applyFilter);
    }
})();
</script>
{% endblock %}
```

- [ ] **Step 2: 冒烟测试**

Run: `python -m pytest ashare_review/tests/test_prediction_ledger.py::test_prediction_ledger_page -v`
Expected: PASS（模板渲染 200 且含关键内容）

- [ ] **Step 3: 提交**

```bash
git add ashare_review/web/templates/prediction_ledger.html
git commit -m "feat(ledger): 台账页面（统计面板+明细筛选）"
```

---

### Task 8: 全量回归 + 运行迁移 + 推送

- [ ] **Step 1: 跑全量测试**

Run: `python -m pytest ashare_review/tests -q`
Expected: 全部通过（原 48 + 新增 23 = 71 passed，不破坏既有功能）

- [ ] **Step 2: 运行历史迁移（真实数据）**

Run: `python -m ashare_review.prediction_ledger.migrate`
Expected: 输出"历史精选追溯完成，写入 N 条"。可重复运行输出 0。

- [ ] **Step 3: 验证 DB 与页面**

Run: `python -c "import sqlite3; c=sqlite3.connect('ashare_review/data/prediction_ledger.db'); print(c.execute('select pred_date,pred_type,count(*) from predictions group by 1,2 order by 1 desc limit 10').fetchall()"`
Expected: 有 picks 记录（含迁移的历史日期与今日新记录）

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "feat(ledger): 预测台账完整交付（历史迁移数据）"
git push origin main
```
