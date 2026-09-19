# 启动突破 V3 对齐五打法(一) — P0 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「启动突破 V3」对齐逻辑哥五打法（一）的三处核心缺口：① 只做小票（流通市值 ≤100 亿）② 情绪周期闸门（退潮/冰点禁开仓）③ 趋势线持股 + 破趋势止损 + -8% 底线（替换 -6%、取消移动止盈、兜底 5→20 交易日）。

**Architecture:** 全部改动落在现有 V3 引擎（`tools/sim_portfolio.py`）及其镜像回测（`analysis/v3_backtest.py`、`analysis/v4_backtest.py`），页面与文档同步。市值用本地流通股本缓存（`data/cache/float_share_map.json`）离线计算；情绪复用 `live_diagnosis.get_regime_diagnosis()`；趋势线用 `max(买入日最低价, MA13)` 近似视频里的主图趋势线。

**Tech Stack:** Python 3 + pandas + Flask + pytest（fake-io monkeypatch 风格）。

**设计依据:** `docs/superpowers/specs/2026-09-19-v3-launch-breakout-alignment-design.md`

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `ashare_review/tools/sim_portfolio.py` | 主引擎：小票过滤/加分、情绪闸门、趋势线持股、-8% 底线、20 日兜底、持仓新字段 |
| `ashare_review/analysis/v3_backtest.py` | 回测镜像：同步卖出逻辑（trend_line/-8%/20d/去移动止盈） |
| `ashare_review/analysis/v4_backtest.py` | 同上（V4 = V3 + 情绪过滤的副本） |
| `ashare_review/web/templates/breakout_v3.html` | 页面：市值/闸门/趋势线止损位展示 + 规则文案 |
| `ashare_review/data/V3_STRATEGY.md` | 策略文档 P0 部分 |
| `tests/test_v3_small_cap.py` 等 | 新增 4 个测试文件 |

**既有约束（不得破坏）**：
- 旧测试 monkeypatch `_read_latest` 返回极简 dict（无 ma/市值）→ 新逻辑遇缺数据必须**放行**。
- 每任务后跑：`python -m pytest tests/test_v3_*.py -q`；最终全套 `python -m pytest -q`。
- 注意：仓库现有测试目录为 `ashare_review/tests/`（历史）与根 `tests/`（近两期新增），两处都要跑。

---

### Task 1: 常量 + 市值工具函数

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`（常量区 ~L56-68；纯函数区 ~L77-160）
- Test: `tests/test_v3_small_cap.py`（新建）

- [ ] **Step 1: 常量区追加**

```python
# ── 小票过滤 / 趋势线持股（对齐逻辑哥五打法一 2026-09-19） ──
SMALL_CAP_MAX_YI = 100.0      # 流通市值上限(亿)：超过不入候选
SMALL_CAP_BONUS_LO = 20.0     # 小票加分区间下沿(亿)
SMALL_CAP_BONUS_HI = 80.0     # 小票加分区间上沿(亿)
SMALL_CAP_BONUS = 8           # 小票加分
V3_STOP_LOSS_PCT = -8.0       # V3 绝对止损底线(%)（原 -6%，仅 V3 生效，不影响 V1/V2）
V3_MAX_HOLD_DAYS = 20         # V3 最长持有(交易日)兜底（原 5）
TREND_LINE_MA = 13            # 趋势线用 MA13（与“只做13日线上”闭环）
```

- [ ] **Step 2: 写失败测试（tests/test_v3_small_cap.py）**

```python
"""启动突破 V3 — 小票过滤 单元测试（逻辑哥五打法一：只做小票）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_float_mcap_yi():
    from ashare_review.tools.sim_portfolio import float_mcap_yi
    # 流通股本 1e7 手 = 1e9 股；收盘 10 元 → 100 亿
    assert float_mcap_yi(1e7, 10.0) == 100.0
    assert float_mcap_yi(1e7, 5.0) == 50.0
    # 缺数据 → None（调用方放行）
    assert float_mcap_yi(None, 10.0) is None
    assert float_mcap_yi(1e7, None) is None
    assert float_mcap_yi(0, 10.0) is None
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_v3_small_cap.py -q`
Expected: FAIL with `ImportError: cannot import name 'float_mcap_yi'`

- [ ] **Step 4: 实现纯函数（放在 `fib_state` 附近）**

```python
def float_mcap_yi(hands, close):
    """流通市值(亿) = 流通股本(手) × 100股/手 × 收盘价 / 1e8。

    数据缺失（无股本/无价格）返回 None —— 调用方按“市值未知”放行，不拦截。
    """
    try:
        if not hands or not close or hands <= 0 or close <= 0:
            return None
        return round(float(hands) * 100.0 * float(close) / 1e8, 2)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_v3_small_cap.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py tests/test_v3_small_cap.py
git commit -m "feat(v3): 小票/趋势线常量 + 流通市值工具函数（P0-T1）"
```

---

### Task 2: screen_candidates 小票硬过滤 + 挂市值字段

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`（`screen_candidates`，约 L648-760）
- Test: `tests/test_v3_small_cap.py`

- [ ] **Step 1: 追加失败测试**

```python
def _portfolio(monkeypatch, pool, latest_map, hands_map=None):
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_eligible_pool', lambda: pool)
    monkeypatch.setattr(p, '_read_latest', lambda code: latest_map.get(code))
    monkeypatch.setattr(p, '_capital_hands', lambda code: (hands_map or {}).get(code))
    return p


def test_screen_candidates_small_cap_filter():
    """>100 亿剔除；≤100 亿保留且带 float_mcap_yi；市值未知放行。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    pool = [
        {'code': '600001', 'pressure': 11.0, 'limit_count': 12, 'name': '小票'},
        {'code': '600002', 'pressure': 11.0, 'limit_count': 12, 'name': '大票'},
        {'code': '600003', 'pressure': 11.0, 'limit_count': 12, 'name': '未知'},
    ]
    latest = {
        '600001': {'close': 10.0, 'vol': 100, 'mavol180': 50, 'prev_close': 9.8,
                   'ma13': 9.5, 'ma34': 9.0, 'ma55': 8.5, 'ma89': 8.0},
        '600002': {'close': 10.0, 'vol': 100, 'mavol180': 50, 'prev_close': 9.8,
                   'ma13': 9.5, 'ma34': 9.0, 'ma55': 8.5, 'ma89': 8.0},
        '600003': {'close': 10.0, 'vol': 100, 'mavol180': 50, 'prev_close': 9.8,
                   'ma13': 9.5, 'ma34': 9.0, 'ma55': 8.5, 'ma89': 8.0},
    }
    # 600001: 1e7手×100×10 = 100亿（临界，保留）
    # 600002: 3e7手×100×10 = 300亿（剔除）
    # 600003: 无股本数据 → 放行
    hands = {'600001': 1e7, '600002': 3e7}
    p = _portfolio(monkeypatch, pool, latest, hands)
    cands = p.screen_candidates('2026-09-01')
    by = {c['code']: c for c in cands}
    assert '600001' in by and by['600001']['float_mcap_yi'] == 100.0
    assert '600002' not in by
    assert '600003' in by and by['600003']['float_mcap_yi'] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_v3_small_cap.py::test_screen_candidates_small_cap_filter -q`
Expected: FAIL（600002 仍在结果里 / 无 float_mcap_yi）

- [ ] **Step 3: 实现（screen_candidates 循环内）**

在 fib 过滤块（`fs = fib_state(...)` / `if fs == 'rest': continue`）**之后**、`fib_extra` 之前插入：

```python
                # ── 小票硬过滤（只做小票）：市值未知放行并标注 ──
                mcap_yi = float_mcap_yi(self._capital_hands(code), close)
                if mcap_yi is not None and mcap_yi > SMALL_CAP_MAX_YI:
                    continue
```

并在 `fib_extra` dict 中追加一行：

```python
                    'float_mcap_yi': mcap_yi,
```

（`**fib_extra` 已同时展开到 breakout / watching 两个 append，无需分别加。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_v3_small_cap.py -q`
Expected: PASS（2 个用例）

- [ ] **Step 5: 回归**

Run: `python -m pytest tests/ ashare_review/tests -q`
Expected: PASS（旧 monkeypatch fixture 无 `_capital_hands` patch → 真实方法读缓存；若缓存缺失返回 None → 放行，不误杀）

> 若个别旧测试因真实 `_capital_hands` 读取慢/异常而变慢，可在该测试里 monkeypatch `_capital_hands` 返回 None；不要改生产逻辑。

- [ ] **Step 6: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py tests/test_v3_small_cap.py
git commit -m "feat(v3): screen_candidates 小票硬过滤(≤100亿) + 挂 float_mcap_yi（P0-T2）"
```

---

### Task 3: 小票加分 + 情绪周期闸门

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`（`_scan_buy_signals` ~L779-830；`run_daily` 写 state）
- Test: `tests/test_v3_emotion_gate.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""启动突破 V3 — 情绪周期闸门 + 小票加分 单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _p(monkeypatch, gate):
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_market_state',
                        lambda trade_date=None: {'is_bull': True, 'sh_close': 3000, 'sh_ma60': 2900})
    monkeypatch.setattr(p, '_emotion_gate', lambda: gate)
    return p


def _cand(code, mcap=None):
    c = {'code': code, 'close': 10.2, 'top_line': 10.0, 'dist_pct': 2.0,
         'prev_close': 9.9, 'vol': 100, 'mavol180': 50, 'vol_ratio': 2.0,
         'limit_count': 12, 'fib_state': 'ok', 'ma13': 9.8}
    if mcap is not None:
        c['float_mcap_yi'] = mcap
    return c


def test_gate_blocks_on_ebb():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    p = _p(monkeypatch, {'block': True, 'best': False, 'raise_bar': False,
                         'regime': '退潮下跌', 'emotion': '低迷'})
    assert p._scan_buy_signals([_cand('600001')], mode='v3') == []


def test_gate_best_adds_score_and_raise_bar_threshold():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    # 最佳档 +5 分
    p = _p(monkeypatch, {'block': False, 'best': True, 'raise_bar': False,
                         'regime': '强势趋势', 'emotion': '活跃'})
    sigs = p._scan_buy_signals([_cand('600001')], mode='v3')
    assert sigs and sigs[0]['score'] >= 65
    # 修复档：门槛 75，低分被剔除
    p2 = _p(monkeypatch, {'block': False, 'best': False, 'raise_bar': True,
                          'regime': '震荡观望', 'emotion': '低迷'})
    low = _cand('600002')
    low['vol_ratio'] = 1.3          # 基础分 70 < 75 → 被修复档门槛拦掉
    assert p2._scan_buy_signals([low], mode='v3') == []


def test_small_cap_bonus():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    g = {'block': False, 'best': False, 'raise_bar': False, 'regime': '震荡观望', 'emotion': '温和'}
    p = _p(monkeypatch, g)
    s_small = p._scan_buy_signals([_cand('600001', mcap=50.0)], mode='v3')[0]['score']
    p2 = _p(monkeypatch, g)
    s_big = p2._scan_buy_signals([_cand('600002', mcap=200.0)], mode='v3')[0]['score']
    assert s_small == s_big + 8
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_v3_emotion_gate.py -q`
Expected: FAIL（`_emotion_gate` 不存在 / 加分未实现）

- [ ] **Step 3: 实现 `_emotion_gate()`（放 `_scan_buy_signals` 之前）**

```python
    def _emotion_gate(self) -> dict:
        """情绪周期闸门（对齐五打法一）。

        - block:     regime=退潮下跌 或 emotion∈{极冰点,冰点} → 禁开仓
        - best:      regime=强势趋势 或 emotion∈{活跃,火爆} → 信号评分 +5
        - raise_bar: emotion∈{低迷,温和}（冰点后的修复期）→ 评分门槛提到 75
        取不到数据时一律不拦（fail-open）。
        """
        try:
            from ..analysis.strategy_regime import live_diagnosis as _ld
            d = _ld.get_regime_diagnosis() or {}
        except Exception:
            d = {}
        regime = d.get('regime') or ''
        emotion = d.get('emotion') or ''
        if regime == '退潮下跌' or emotion in ('极冰点', '冰点'):
            return {'block': True, 'best': False, 'raise_bar': False,
                    'regime': regime, 'emotion': emotion}
        return {
            'block': False,
            'best': (regime == '强势趋势') or (emotion in ('活跃', '火爆')),
            'raise_bar': emotion in ('低迷', '温和'),
            'regime': regime,
            'emotion': emotion,
        }
```

- [ ] **Step 4: 接进 `_scan_buy_signals`**

方法开头（`market = self._get_market_state()` 之前）加：

```python
        gate = self._emotion_gate()
        if gate.get('block'):
            return []          # 退潮/冰点：禁开仓（观察池仍展示）
```

评分段（`score = min(score, 100)` 之后、熊市过滤之前）加：

```python
                # ── 小票加分（20-80亿） ──
                mcap = c.get('float_mcap_yi')
                if mcap is not None and SMALL_CAP_BONUS_LO <= mcap <= SMALL_CAP_BONUS_HI:
                    score = min(score + SMALL_CAP_BONUS, 100)
                # ── 情绪最佳档加分 ──
                if gate.get('best'):
                    score = min(score + 5, 100)
```

熊市过滤那行改为同时考虑修复档：

```python
                # ── V3: 熊市或情绪修复档 → 门槛 75 ──
                if mode == 'v3' and (not is_bull or gate.get('raise_bar')) and score < 75:
                    continue
```

- [ ] **Step 5: `run_daily` 落闸门状态（供页面展示）+ `get_summary` 暴露**

在 `run_daily` 的 `market = self._get_market_state(td)` 之后加：

```python
        # 情绪闸门快照（供页面顶部展示）
        self._state['emotion_gate'] = self._emotion_gate()
```

`get_summary()` 返回 dict 顶层加（与 `'pullback_list'` 同级）：

```python
            'emotion_gate': self._state.get('emotion_gate', {}),
```

- [ ] **Step 6: 运行确认通过 + 回归**

Run: `python -m pytest tests/test_v3_emotion_gate.py -q` → PASS
Run: `python -m pytest tests/ ashare_review/tests -q` → PASS
（旧测试无 `_emotion_gate` patch 时会走真实 `live_diagnosis`；若其抛异常则 fail-open 不拦，不影响旧断言。）

- [ ] **Step 7: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py tests/test_v3_emotion_gate.py
git commit -m "feat(v3): 情绪周期闸门(退潮/冰点禁开仓·最佳档+5·修复档门槛75) + 小票加分（P0-T3）"
```

---

### Task 4: 趋势线持股 + 破趋势止损 + -8% 底线（sim 引擎）

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`（`_check_sell_v3` ~L913-1045；持仓初始化 ~L1240-1270 与 refresh 自动买入段）
- Test: `tests/test_v3_trend_exit.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""启动突破 V3 — 趋势线持股/破趋势止损/-8%底线 单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pos(**kw):
    p = {'buy_date': '2026-09-01', 'buy_price': 10.0, 'had_zt': False,
         'highest_close': 10.0, 'trend_line': 9.5, 'awaiting_reversal': False}
    p.update(kw)
    return p


def _p(monkeypatch, df):
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    monkeypatch.setattr(p, '_read_sell_df', lambda code, up_to_date=None: df)
    return p


def _df(close, low=None, open_=None, vol=100):
    import pandas as pd
    return pd.DataFrame([{'open': open_ if open_ is not None else close,
                          'high': close, 'low': low if low is not None else close,
                          'close': close, 'volume': vol}])


def test_break_trend_line_sells():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    p = _p(monkeypatch, _df(close=9.4, low=9.3))     # 收盘 < 趋势线 9.5
    sig = p._check_sell_v3('600001', _pos(), '2026-09-02')
    assert sig and '破趋势线' in sig['sell_reason']


def test_eight_pct_floor_sells():
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    p = _p(monkeypatch, _df(close=9.1, low=9.0))     # -9% ≤ -8%，且趋势线放到 8.0 避免先触发
    sig = p._check_sell_v3('600001', _pos(trend_line=8.0), '2026-09-02')
    assert sig and '止损8%' in sig['sell_reason']


def test_no_trailing_take_profit_anymore():
    """取消移动止盈：从最高 12 回落到 11（-8.3% 但仍在趋势线上、且非 -8% 亏损）→ 不卖。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    p = _p(monkeypatch, _df(close=11.0, low=10.9))
    assert p._check_sell_v3('600001', _pos(highest_close=12.0, trend_line=9.5), '2026-09-02') is None


def test_trend_line_only_rises_on_hold():
    """收阳且收盘>趋势线 → 趋势线上移到当日最低（只升不降）。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    p = _p(monkeypatch, _df(close=10.5, low=10.2, open_=10.1))
    pos = _pos(trend_line=9.5)
    assert p._check_sell_v3('600001', pos, '2026-09-02') is None
    assert pos['trend_line'] == 10.2
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_v3_trend_exit.py -q`
Expected: FAIL（`trend_line` 分支不存在；移动止盈仍会触发第 3 个用例）

- [ ] **Step 3: 重写 `_check_sell_v3` 的判定段**

保留方法开头的 df 读取 / highest 更新 / trading_days 计算；把「硬止损 + 移动止盈 + 涨停/断板/兜底」整段替换为下述顺序（**删除移动止盈整段**，`HOLD_MAX = 5` 改成 `V3_MAX_HOLD_DAYS`）：

```python
        # ── 1) 破趋势线 → 无条件走（主止损） ──
        trend_line = float(position.get('trend_line') or 0)
        if trend_line > 0 and close < trend_line:
            return {
                'sell_price': round(close, 2),
                'sell_reason': f'破趋势线{trend_line:.2f}',
                'is_zt': False,
                'days_held': trading_days,
            }

        # ── 2) -8% 绝对底线 ──
        if buy_price > 0:
            loss_pct = (close - buy_price) / buy_price
            if loss_pct <= V3_STOP_LOSS_PCT / 100.0:
                return {
                    'sell_price': round(close, 2),
                    'sell_reason': f'止损{abs(V3_STOP_LOSS_PCT):.0f}%',
                    'is_zt': False,
                    'days_held': trading_days,
                }

        # ── 3) 今日涨停 → 继续持有（趋势线随最低价上移） ──
        if is_zt_today:
            position['had_zt'] = True
            position['awaiting_reversal'] = False
            position['trend_line'] = round(max(trend_line, float(df['low'].iloc[idx])), 2)
            return None

        # ── 4) N 字反包（原逻辑保持不变） ──
        #    （保持现有 had_zt / awaiting_reversal 分支原样）

        # ── 5) 兜底：持有 ≥ V3_MAX_HOLD_DAYS ──
        if trading_days >= V3_MAX_HOLD_DAYS:
            return {
                'sell_price': round(close, 2),
                'sell_reason': f'持有{trading_days}天到期',
                'is_zt': False,
                'days_held': trading_days,
            }

        # ── 6) 趋势线只升不降：收阳且收盘 > 趋势线 → 上移到当日最低 ──
        today_open = float(df['open'].iloc[idx]) if idx >= 0 else close
        today_low = float(df['low'].iloc[idx]) if idx >= 0 else close
        if close > today_open and close > trend_line and today_low > trend_line:
            position['trend_line'] = round(today_low, 2)

        return None
```

同时更新方法 docstring 为「破趋势线止损 / -8% 底线 / N字反包 / 20日兜底（已取消移动止盈）」，并删除文件末尾那段重复的移动止盈代码（原 ~L1030-1045 的 `── 移动止盈也适用于没涨停的情况 ──` 分支）。

- [ ] **Step 4: 持仓初始化写入 `trend_line` / `entry_low`**

在 `run_daily` 自动买入（`self._state['holding'][code] = {...}`，约 L1240-1270）与 `refresh_daily_status` 的同类写入处，各加：

```python
                    'entry_low': round(entry_low, 2),
                    'trend_line': round(max(entry_low, ma13 or entry_low), 2),
```

其中买入前取：

```python
                try:
                    entry_low = float(df_buy['low'].iloc[-1])
                except Exception:
                    entry_low = buy_price_actual
                ma13 = (self._read_latest(code) or {}).get('ma13')
```

- [ ] **Step 5: 运行确认通过 + 回归**

Run: `python -m pytest tests/test_v3_trend_exit.py -q` → PASS
Run: `python -m pytest tests/ ashare_review/tests -q` → 需修正因“取消移动止盈/改 -8%”而失效的旧断言（预期集中在 `test_v3_*`；逐条按新口径更新，不得删除用例）

- [ ] **Step 6: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py tests/test_v3_trend_exit.py
git commit -m "feat(v3): 趋势线持股+破趋势止损-8%底线，取消移动止盈、兜底20日（P0-T4）"
```

---

### Task 5: 回测引擎同步（v3_backtest / v4_backtest）

**Files:**
- Modify: `ashare_review/analysis/v3_backtest.py`（`_check_sell_v3` ~L256-365；`MAX_HOLD_DAYS` L39；导出 reason 映射 ~L1201）
- Modify: `ashare_review/analysis/v4_backtest.py`（同上，~L396-505 / L41 / 同导出段）
- Test: `tests/test_v3_backtest_parity.py`（新建）

- [ ] **Step 1: 写失败测试（口径一致性）**

```python
"""回测引擎与实盘引擎 V3 卖出阈值一致（防口径裂）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_stop_loss_and_hold_days_aligned():
    from ashare_review.tools import sim_portfolio as sp
    from ashare_review.analysis import v3_backtest as bt3
    from ashare_review.analysis import v4_backtest as bt4
    assert bt3.V3_STOP_LOSS_PCT == sp.V3_STOP_LOSS_PCT == -8.0
    assert bt4.V3_STOP_LOSS_PCT == sp.V3_STOP_LOSS_PCT == -8.0
    assert bt3.V3_MAX_HOLD_DAYS == sp.V3_MAX_HOLD_DAYS == 20
    assert bt4.V3_MAX_HOLD_DAYS == sp.V3_MAX_HOLD_DAYS == 20
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_v3_backtest_parity.py -q`
Expected: FAIL（AttributeError: V3_STOP_LOSS_PCT）

- [ ] **Step 3: 同步常量与卖出逻辑**

> **注意签名差异**：回测的 `_check_sell_v3(self, code, hold, check_date, df)` 与实盘
> `_check_sell_v3(self, code, position, today)` 不同——回测版直接拿到 `df`、用 `check_date`，
> 币值/日期处理也不同。请**按语义**套用 Task 4 的判定顺序（破趋势线 → -8% → 涨停持有并上移趋势线 →
> N字反包 → 20 日兜底 → 趋势线上移），不要照抄实盘代码。

两个回测文件：
1. 常量区加（同 Task 1 三个值）：

```python
V3_STOP_LOSS_PCT = -8.0
V3_MAX_HOLD_DAYS = 20
TREND_LINE_MA = 13
```

2. `MAX_HOLD_DAYS`（=5）在 V3 卖出路径改为引用 `V3_MAX_HOLD_DAYS`（常量保留兼容其它引用）。
3. `_check_sell_v3` 按 Task 4 的顺序重写：**破趋势线（hold['trend_line']）→ -8% → 涨停持有(趋势线上移) → N字反包 → 20日兜底 → 趋势线上移**，删除移动止盈分支（含「没涨停但浮盈5%+启用移动止盈」段）。
4. 买入建仓处（`'highest_close': buy_open` 附近）加 `'entry_low': ...`、`'trend_line': max(entry_low, ma13 or entry_low)`；MA13 用该股 `df['close'].rolling(13).mean()` 在买入日落点计算。
5. 导出段的 reason 映射更新：`'止损' in reason → '止损-8%'`；删除/停用 `'移动止盈'` 分支。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `python -m pytest tests/test_v3_backtest_parity.py -q` → PASS
Run: `python -m pytest ashare_review/tests -q` → PASS（`test_v3_chase_limit` 等口径测试若因阈值变化失败，按新口径更新断言）

- [ ] **Step 5: Commit**

```bash
git add ashare_review/analysis/v3_backtest.py ashare_review/analysis/v4_backtest.py tests/test_v3_backtest_parity.py
git commit -m "feat(v3): 回测引擎同步趋势线/-8%/20日卖出口径 + parity 测试（P0-T5）"
```

---

### Task 6: 页面展示 + 规则文案（P0 部分）

**Files:**
- Modify: `ashare_review/web/templates/breakout_v3.html`
- Modify: `ashare_review/data/V3_STRATEGY.md`

- [ ] **Step 1: 页面加情绪闸门横幅**

顶部 `guide-banner` 之后插入（数据来自 summary.emotion_gate）：

```html
<div id="emotion-gate" class="guide-banner" style="border-left:4px solid #7c3aed;display:none;">
    <span id="emotion-gate-text"></span>
</div>
```

`renderAll(data)` 内加渲染：

```js
    renderEmotionGate(data.emotion_gate || data.summary && data.summary.emotion_gate || {});
```

```js
// ── 情绪周期闸门 ──
function renderEmotionGate(g) {
    const el = document.getElementById('emotion-gate');
    const tx = document.getElementById('emotion-gate-text');
    if (!g || !g.regime) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    const blocked = g.block;
    const color = blocked ? '#dc2626' : (g.best ? '#16a34a' : '#7c3aed');
    let s = '🌡️ 情绪周期：<b>' + esc(g.regime) + '</b>' + (g.emotion ? ' · ' + esc(g.emotion) : '');
    if (blocked) s += ' · <b style="color:' + color + ';">禁开仓：退潮/冰点期不做启动突破</b>';
    else if (g.best) s += ' · <b style="color:' + color + ';">最佳档：修复→高潮，可正常出击</b>';
    else if (g.raise_bar) s += ' · <b style="color:' + color + ';">修复期：评分门槛 75</b>';
    tx.innerHTML = s;
}
```

- [ ] **Step 2: 观察池/买入卡片显示市值与趋势线**

`renderWatchPool` 卡片信息行加：

```js
        if (s.float_mcap_yi !== undefined && s.float_mcap_yi !== null) {
            html += ' | 流通市值 ' + s.float_mcap_yi + '亿';
        }
```

`renderHoldPanel` 持仓卡片加趋势线止损参考：

```js
        if (s.trend_line) {
            html += '<div style="font-size:0.72em;color:#dc2626;margin-top:1px;">趋势线止损 '
                 + s.trend_line + '（收盘跌破无条件走 · -8% 底线）</div>';
        }
```

- [ ] **Step 3: 选股标准/口诀文案更新**

「📋 选股标准」硬性条件后追加：

```html
<strong style="color:#0284c7;">小票与情绪（五打法一）：</strong><br>
✅ <b>只做小票</b>：流通市值 ≤100 亿（20–80 亿加分）<br>
✅ <b>情绪周期</b>：退潮/冰点禁开仓；最佳=修复→高潮；修复期评分门槛 75<br>
✅ <b>持股纪律</b>：以趋势线（max(买入日低点, MA13)）为底线，<b>不破趋势不走</b>；<br>
&nbsp;&nbsp;&nbsp;收盘跌破趋势线 → 无条件走 · -8% 绝对底线 · 最长 20 交易日兜底<br>
```

口诀横幅追加：`只做小票 · 破趋势就走 · 退潮冰点休息`

- [ ] **Step 4: V3_STRATEGY.md 更新（P0 部分）**

新增「2.6 小票与情绪周期（2026-09-19）」与改写「4.1 V3 自动卖出规则」表：主止损=破趋势线、绝对底线 -8%、最长 20 日、取消移动止盈；同步「5.3 总仓位×行情」加情绪档说明。

- [ ] **Step 5: 冒烟**

Run: `python -m pytest ashare_review/tests/test_home_overview.py -q` → PASS
Run: `python -c "import sys;sys.path.insert(0,'.');from ashare_review.web.app import app;c=app.test_client();r=c.get('/breakout_v3');b=r.get_data(as_text=True);print(r.status_code,'emotion-gate' in b,'趋势线止损' in b)"`
Expected: `200 True True`

- [ ] **Step 6: Commit**

```bash
git add ashare_review/web/templates/breakout_v3.html ashare_review/data/V3_STRATEGY.md
git commit -m "feat(v3): 页面情绪闸门/市值/趋势线止损展示 + 规则文案与策略文档（P0-T6）"
```

---

### Task 7: 全量回归 + P0 收尾

- [ ] **Step 1: 新增用例**

Run: `python -m pytest tests/test_v3_small_cap.py tests/test_v3_emotion_gate.py tests/test_v3_trend_exit.py tests/test_v3_backtest_parity.py -q`
Expected: PASS

- [ ] **Step 2: 仓库全套**

Run: `python -m pytest -q`
Expected: PASS（254+ 全绿；若有失败先判断是否为既有数据文件差异）

- [ ] **Step 3: 重跑 V3 基线回测（口径变更后必须重算）**

Run: `python -m ashare_review.analysis.strategy_regime.backtest_v3 --use-cache`
Expected: 正常产出 xlsx；把新的胜率/均收益/盈亏比写入 `V3_STRATEGY.md` 回测参考段（若与旧数字差异显著，在文档注明口径变更日期）

- [ ] **Step 4: 收尾 commit**

```bash
git add -A docs ashare_review/data/V3_STRATEGY.md
git commit -m "docs(v3): P0 口径变更后回测基线重算 + 文档数字同步"
```

---

## 验收对照（spec → task）

| spec 需求 | task |
|---|---|
| A1 小票硬过滤 ≤100 亿 + 20-80 亿加分 + 未知放行 | T1, T2, T3 |
| A3 情绪闸门（退潮/冰点禁开仓、最佳档、修复档 75） | T3 |
| B1 趋势线持股（max(买入日低点, MA13)，只升不降） | T4, T5 |
| B2 破趋势走 + -8% 底线 + 取消移动止盈 + 20 日兜底 | T4, T5 |
| 回测口径同步（防实盘/回测裂） | T5 |
| 页面展示 + 文案 + 策略文档 | T6 |
| 全套 pytest + 基线回测重算 | T7 |

## P0 之后（另立计划）

- **P1**：A2 周线双突破（引擎 + `breakout_predict` 评分）+ D1 催化清单（`app.py` API + 页面勾选）+ D2 文档；
- **P2**：C1 回踩加仓（未破前高 / 回踩站稳趋势线，10%→20%）+ C2 做T提示。

## 遗留与诚实话

- 趋势线是近似（视频为主图自定义线），故保留 -8% 与 20 日双保险；
- 回测侧的小票过滤/情绪闸门未在 P0 同步（历史流通股本非 point-in-time、情绪需按日对齐），
  **P0 只同步卖出逻辑**；选股口径的回测同步列入 P1，并在 `V3_STRATEGY.md` 注明回测与实盘的
  候选池差异；
- 口径变更会改变历史表现，T7 必须重算基线，避免文档与实现不一致。
