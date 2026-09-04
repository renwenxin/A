# 启动突破 V3 — 斐波那契纪律 + 回踩低吸信号 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 /breakout_v3（Vol180SimPortfolio mode=v3）上叠加斐波那契均线纪律（13/34/55/89）与「回踩低吸」三条件信号列，并同步页面规则文字与策略文档。

**Architecture:** 引擎侧扩展现有 `screen_candidates`/`_scan_buy_signals`/`refresh_daily_status`/`get_summary`：
`_read_latest()` 顺带算出 5/10/20/13/34/55/89 均线、前5日均量与近 3 根 K 线 → 候选行携带 fib 状态与回踩指标；
板块中军用 spot(成交额≥20亿 且 趋势向上) 按日缓存。页面侧在现有 watch/buy 卡片加状态徽章，并新增全宽「回踩低吸」面板。测试沿用 monkeypatch fake-io 风格（不依赖 TDX 网络）。

**Tech Stack:** Python 3 + pandas + numpy + Flask（模板端到端渲染）；AKShare spot（仅中军判定，异常降级为"数据缺失"，不拦截）。

**设计依据:** `docs/superpowers/specs/2026-09-04-v3-fib-pullback-design.md`

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `ashare_review/tools/sim_portfolio.py` | 引擎：fib 均线/状态、回踩指标、板块中军、`state['pullback']`、summary 输出 |
| `ashare_review/web/templates/breakout_v3.html` | 页面：fib 徽章、回踩低吸面板、选股标准/口诀文字 |
| `ashare_review/data/V3_STRATEGY.md` | 策略文档同步 |
| `tests/test_v3_fib_discipline.py` | fib 分档 + screen/buy/refresh 过滤 单元测试 |
| `tests/test_v3_pullback_signal.py` | 回踩三条件各分支 + 中军 mock 测试 |

**既有约束（不许破坏）：**
- `test_v3_chase_limit.py` 等 monkeypatch `_read_latest` 只给 `{close, vol, mavol180}` → 新逻辑遇缺字段必须**放行**（视同 ok），否则旧测试红。
- 每次改动后跑：`python -m pytest tests/test_v3_chase_limit.py tests/test_v3_fib_discipline.py tests/test_v3_pullback_signal.py -q`；最终全套 `python -m pytest -q`。

---

### Task 1: `_read_latest()` 顺带产出 fib 均线 + 回踩指标

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`（`_read_latest`, 约 L264-301；文件头常量区）
- Test: `tests/test_v3_fib_discipline.py`（新建，此任务先建第 1 个用例）
- Test: `tests/test_v3_pullback_signal.py`（新建，此任务建第 1 个用例）

- [ ] **Step 1: 常量区追加（sim_portfolio.py 顶部常量段，约 L46 后）**

```python
# ── 斐波那契均线纪律（V3 新增） ──
FIB_MA_PERIODS = [13, 34, 55, 89]     # 只做 13 日线上；破 34/55 谨慎；破 89 休息
FIB_MA13 = 13
FIB_MA34 = 34
FIB_MA55 = 55
FIB_MA89 = 89
PULLBACK_MA_PERIODS = [5, 10, 20]     # 回踩低吸：5/10/20 日线附近
PULLBACK_BIAS_PCT = 3.0               # 乖离 < 3%
PULLBACK_VOL_RATIO = 0.6              # 量 < 前5日均量 × 60%
```

- [ ] **Step 2: 写失败测试（tests/test_v3_fib_discipline.py）**

```python
"""启动突破 V3 — 斐波那契均线纪律 单元测试

背景: 用户教学 —— 均线设 13/34/55/89(斐波那契)；只做 K 线在 13 日线上的图形；
日线破位 34/55 均线→谨慎参与；破位 89 均线→直接休息。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_latest(closes, volumes, opens=None, highs=None, lows=None):
    """构造 _read_latest 的输出结构（含新增字段）。"""
    n = len(closes)
    opens = opens or [closes[i] for i in range(n)]
    highs = highs or [max(opens[i], closes[i]) * 1.01 for i in range(n)]
    lows = lows or [min(opens[i], closes[i]) * 0.99 for i in range(n)]
    vol_ma5_prev = sum(volumes[-6:-1]) / 5.0 if n >= 6 else 0
    def _ma(p):
        return sum(closes[-p:]) / p if n >= p else None
    return {
        'close': closes[-1],
        'prev_close': closes[-2] if n >= 2 else closes[-1],
        'vol': volumes[-1],
        'mavol180': sum(volumes[-180:]) / min(len(volumes), 180),
        'ma5': _ma(5), 'ma10': _ma(10), 'ma20': _ma(20),
        'ma13': _ma(13), 'ma34': _ma(34), 'ma55': _ma(55), 'ma89': _ma(89),
        'vol_ma5_prev': round(vol_ma5_prev, 0),
        'tail': [
            {'o': opens[-3], 'h': highs[-3], 'l': lows[-3], 'c': closes[-3]},
            {'o': opens[-2], 'h': highs[-2], 'l': lows[-2], 'c': closes[-2]},
            {'o': opens[-1], 'h': highs[-1], 'l': lows[-1], 'c': closes[-1]},
        ] if n >= 3 else [],
    }


def test_fib_state_ok_caution_rest():
    """斐波那契状态分档：可做/谨慎/休息 按优先级互斥。"""
    from ashare_review.tools.sim_portfolio import fib_state
    # 可做: close 在所有 fib 均线上方
    assert fib_state(close=100, ma13=90, ma34=88, ma55=85, ma89=80) == 'ok'
    # 谨慎: 破 34/55 (但仍在 89 上方)
    assert fib_state(close=83, ma13=90, ma34=88, ma55=85, ma89=80) == 'caution'
    assert fib_state(close=86, ma13=90, ma34=88, ma55=85, ma89=80) == 'caution'
    # 休息: 破 89
    assert fib_state(close=78, ma13=90, ma34=88, ma55=85, ma89=80) == 'rest'
    # 缺均线 → 不拦截(ok)
    assert fib_state(100, None, None, None, None) == 'ok'
```

- [ ] **Step 3: 写失败测试（tests/test_v3_pullback_signal.py）**

```python
"""启动突破 V3 — 回踩低吸信号 单元测试

背景: 用户教学 —— 三条件同时满足才上车:
  1. 板块有中军: 板块内有成交额>20亿且趋势向上的票
  2. 个股在回踩: 股价在 5/10/20 日线附近(乖离<3%)
  3. 缩量企稳:   量<前5日均量60% 且出现下影线/底分型
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_latest(closes, volumes, opens=None, highs=None, lows=None):
    n = len(closes)
    opens = opens or [closes[i] for i in range(n)]
    highs = highs or [max(opens[i], closes[i]) * 1.01 for i in range(n)]
    lows = lows or [min(opens[i], closes[i]) * 0.99 for i in range(n)]
    vol_ma5_prev = sum(volumes[-6:-1]) / 5.0 if n >= 6 else 0
    def _ma(p):
        return sum(closes[-p:]) / p if n >= p else None
    return {
        'close': closes[-1],
        'prev_close': closes[-2] if n >= 2 else closes[-1],
        'vol': volumes[-1],
        'mavol180': sum(volumes[-180:]) / min(len(volumes), 180),
        'ma5': _ma(5), 'ma10': _ma(10), 'ma20': _ma(20),
        'ma13': _ma(13), 'ma34': _ma(34), 'ma55': _ma(55), 'ma89': _ma(89),
        'vol_ma5_prev': round(vol_ma5_prev, 0),
        'tail': [
            {'o': opens[-3], 'h': highs[-3], 'l': lows[-3], 'c': closes[-3]},
            {'o': opens[-2], 'h': highs[-2], 'l': lows[-2], 'c': closes[-2]},
            {'o': opens[-1], 'h': highs[-1], 'l': lows[-1], 'c': closes[-1]},
        ] if n >= 3 else [],
    }


def test_pullback_metrics_dist_min():
    """回踩指标: 距 5/10/20 均线最近者乖离。"""
    from ashare_review.tools.sim_portfolio import pullback_metrics
    # 收盘 ≈ MA20(=10)，距 5/10 远 → dist_min ≈ 0.x%（<3 命中回踩）
    closes = [10.0] * 20 + [10.05]
    latest = _mk_latest(closes, volumes=[100] * 21)
    m = pullback_metrics(latest)
    assert m['dist_min_pct'] < 3.0
    # 收盘离所有均线 > 5% → 非回踩
    closes2 = [10.0] * 20 + [11.0]
    m2 = pullback_metrics(_mk_latest(closes2, volumes=[100] * 21))
    assert m2['dist_min_pct'] > 5.0
```

- [ ] **Step 4: 运行确认失败**

Run: `python -m pytest tests/test_v3_fib_discipline.py tests/test_v3_pullback_signal.py -q`
Expected: FAIL with `ImportError: cannot import name 'fib_state'`

- [ ] **Step 5: 实现（sim_portfolio.py）**

在模块级（`_today_str()` 附近）加两个纯函数：

```python
def fib_state(close, ma13, ma34, ma55, ma89):
    """斐波那契均线状态（优先级从高到低，互斥）:
    - rest:    close < MA89 → 休息(硬过滤)
    - caution: close < MA34 或 close < MA55 (且 ≥ MA89) → 谨慎参与
    - ok:      其余 → 可做
    缺均线(None)一律返回 ok（不拦截，兼容旧测试/数据不足）。
    """
    if ma89 is not None and close < ma89:
        return 'rest'
    if ((ma34 is not None and close < ma34)
            or (ma55 is not None and close < ma55)):
        return 'caution'
    return 'ok'


def pullback_metrics(latest: dict) -> dict:
    """从 _read_latest 输出计算回踩低吸指标（缺字段时返回空 dict = 不命中）。

    Returns:
        {dist_min_pct, vol_ratio_5, has_lower_shadow, has_bottom_fractal}
    """
    close = latest.get('close')
    if close is None:
        return {}
    ma_vals = [latest.get(f'ma{p}') for p in PULLBACK_MA_PERIODS]
    valid = [m for m in ma_vals if m]
    dists = [abs(close / m - 1) * 100 for m in valid]
    if not dists:
        return {}
    # 下影线: 最近一根 下影 ≥ 实体 × 0.5（含十字星/大长腿）
    tail = latest.get('tail') or []
    has_lower_shadow = False
    has_bottom_fractal = False
    if len(tail) >= 1:
        bar = tail[-1]
        body = abs(bar['c'] - bar['o'])
        lower_shadow = min(bar['o'], bar['c']) - bar['l']
        if body > 0:
            has_lower_shadow = lower_shadow >= body * 0.5
        else:
            has_lower_shadow = lower_shadow > 0  # 十字星带下影
    if len(tail) >= 3:
        b1, b2, b3 = tail[-3], tail[-2], tail[-1]
        has_bottom_fractal = (b2['l'] < b1['l'] and b2['l'] < b3['l']
                              and b2['h'] < b1['h'] and b2['h'] < b3['h'])
    vol = latest.get('vol')
    vol_ma5 = latest.get('vol_ma5_prev') or 0
    vol_ratio_5 = (vol / vol_ma5) if (vol is not None and vol_ma5 > 0) else None
    return {
        'dist_min_pct': round(min(dists), 2),
        'vol_ratio_5': round(vol_ratio_5, 2) if vol_ratio_5 is not None else None,
        'has_lower_shadow': has_lower_shadow,
        'has_bottom_fractal': has_bottom_fractal,
    }
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m pytest tests/test_v3_fib_discipline.py tests/test_v3_pullback_signal.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py tests/test_v3_fib_discipline.py tests/test_v3_pullback_signal.py
git commit -m "feat(v3): fib均线状态+回踩指标 纯函数（TDD 先行）"
```

---

### Task 2: `_read_latest()` 产出 fib 均线与 tail

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py:264-301`（`_read_latest`）
- Test: `tests/test_v3_fib_discipline.py`

- [ ] **Step 1: 写失败测试（追加到 test_v3_fib_discipline.py）**

```python
def test_read_latest_computes_fib_ma():
    """_read_latest 真实实现需产出 ma5/10/20/13/34/55/89 + vol_ma5_prev + tail。

    用 monkeypatch 让 tdx 读一个内存 .day 文件太长 → 此处改为直接断言
    _read_latest 的输出契约由 pullback_metrics/fib_state 消费（见上），
    并验证 Vol180SimPortfolio._read_latest 在真实 TDX 数据上返回新键（若数据存在）。
    """
    # 真实 _read_latest 依赖 TDX 数据目录，仅做键名契约冒烟：存在数据则含新键
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    # 候选池文件存在的环境下跑真实读；不存在则跳过（CI 无数据）
    latest = p._read_latest('600001')
    if latest is not None:
        for k in ('ma5', 'ma10', 'ma20', 'ma13', 'ma34', 'ma55', 'ma89',
                  'vol_ma5_prev', 'tail'):
            assert k in latest, f'{k} 缺失'
    monkeypatch.undo()
```

- [ ] **Step 2: 运行确认失败（有 TDX 数据时）**

Run: `python -m pytest tests/test_v3_fib_discipline.py::test_read_latest_computes_fib_ma -q`
Expected: FAIL with `KeyError` 或断言缺失键（若本机有 600001 数据）。无数据环境则 PASS（允许跳过）——但 Task 2 仍须完成实现以保证有数据环境正确。

- [ ] **Step 3: 实现（`_read_latest` 尾部替换）**

把 `_read_latest` 中解析后的 `closes/volumes` 之外，同窗口内补算均线与近 3 根 K 线：

```python
    def _read_latest(self, code: str) -> Optional[dict]:
        """只读最近一根K线 + MAVOL180 + 斐波那契均线 + 回踩指标（日常扫描用）。"""
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8', '4')):
            market = 'bj'
        try:
            fpath = os.path.join(self.tdx._market_dir(market), f'{market}{code}.day')
            if not os.path.exists(fpath):
                return None
            fsize = os.path.getsize(fpath)
            if fsize < RECORD_SIZE * MAVOL_PERIOD:
                return None
            read_bytes = min(RECORD_SIZE * 200, fsize)
            with open(fpath, 'rb') as f:
                f.seek(fsize - read_bytes)
                raw = f.read(read_bytes)
            n = len(raw) // RECORD_SIZE
            if n < 2:
                return None
            closes, volumes = [], []
            tail_bars = []  # 近 3 根 {o,h,l,c}
            for j in range(n):
                offset = j * RECORD_SIZE
                dt, op, hi, lo, cl_int, amt, vol, _ = struct.unpack(
                    'IIIIIfII', raw[offset:offset + RECORD_SIZE])
                closes.append(cl_int / 100.0)
                volumes.append(float(vol))
                if j >= n - 3:
                    tail_bars.append({
                        'o': op / 100.0, 'h': hi / 100.0,
                        'l': lo / 100.0, 'c': cl_int / 100.0,
                    })
            import numpy as np
            closes_np = np.array(closes)
            volumes_np = np.array(volumes)
            close = float(closes_np[-1])
            vol = float(volumes_np[-1])
            prev_close = float(closes_np[-2]) if len(closes_np) >= 2 else close
            mavol180 = float(np.mean(volumes_np[-MAVOL_PERIOD:])) \
                if len(volumes_np) >= MAVOL_PERIOD else 0

            def _ma(p):
                return float(np.mean(closes_np[-p:])) if len(closes_np) >= p else None

            vol_ma5_prev = float(np.mean(volumes_np[-6:-1])) \
                if len(volumes_np) >= 6 else 0
            latest = {
                'close': round(close, 2),
                'prev_close': round(prev_close, 2),
                'vol': int(vol),
                'mavol180': round(mavol180, 0),
                'vol_ma5_prev': round(vol_ma5_prev, 0),
                'tail': tail_bars,
            }
            for p in [5, 10, 20, 13, 34, 55, 89]:
                latest[f'ma{p}'] = round(_ma(p), 3) if _ma(p) is not None else None
            return latest
        except Exception:
            return None
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_v3_fib_discipline.py tests/test_v3_chase_limit.py -q`
Expected: PASS（chase_limit 的 monkeypatch `_read_latest` 输出缺新键也不受影响）

- [ ] **Step 5: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py
git commit -m "feat(v3): _read_latest 产出 5/10/20/13/34/55/89 均线 + 前5日均量 + tail"
```

---

### Task 3: screen_candidates 挂 fib 状态 + 破 89 剔除

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py:421-486`（`screen_candidates`）
- Test: `tests/test_v3_fib_discipline.py`

- [ ] **Step 1: 写失败测试**

```python
def test_screen_candidates_excludes_rest_below_ma89():
    """破 89 日线 → 观察池剔除；谨慎档保留但带 fib_state=caution。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    pool = [
        {'code': '600001', 'pressure': 10.0, 'limit_count': 12, 'name': '可做'},
        {'code': '600002', 'pressure': 10.0, 'limit_count': 12, 'name': '谨慎'},
        {'code': '600003', 'pressure': 10.0, 'limit_count': 12, 'name': '休息'},
    ]
    # close 分别 9.5(ok) / 8.6(caution, 破55) / 7.5(rest, 破89) —— 压力位10, 均在 -10% 内
    latest = {
        '600001': {'close': 9.5, 'vol': 100, 'mavol180': 50,
                   'ma13': 9.0, 'ma34': 8.8, 'ma55': 8.7, 'ma89': 8.0,
                   'ma5': 9.4, 'ma10': 9.2, 'ma20': 9.1, 'vol_ma5_prev': 90, 'tail': []},
        '600002': {'close': 8.6, 'vol': 100, 'mavol180': 50,
                   'ma13': 9.0, 'ma34': 8.8, 'ma55': 8.7, 'ma89': 8.0,
                   'ma5': 8.7, 'ma10': 8.7, 'ma20': 8.6, 'vol_ma5_prev': 90, 'tail': []},
        '600003': {'close': 7.5, 'vol': 100, 'mavol180': 50,
                   'ma13': 9.0, 'ma34': 8.8, 'ma55': 8.7, 'ma89': 8.0,
                   'ma5': 8.0, 'ma10': 7.9, 'ma20': 7.8, 'vol_ma5_prev': 90, 'tail': []},
    }
    monkeypatch.setattr(p, '_get_eligible_pool', lambda: pool)
    monkeypatch.setattr(p, '_read_latest', lambda code: latest.get(code))
    cands = p.screen_candidates('2026-09-01')
    by_code = {c['code']: c for c in cands}
    assert '600001' in by_code and by_code['600001']['fib_state'] == 'ok'
    assert '600002' in by_code and by_code['600002']['fib_state'] == 'caution'
    assert '600003' not in by_code          # 破 89 → 休息剔除
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_v3_fib_discipline.py::test_screen_candidates_excludes_rest_below_ma89 -q`
Expected: FAIL（600003 仍在结果里 / 无 fib_state）

- [ ] **Step 3: 实现（screen_candidates 循环体内，取 latest 后加 fib 过滤）**

在 `latest = self._read_latest(code)` 与 `if latest is None: continue` 之间**之后**，pressure 检查附近插入（`dist_pct` 计算前后均可，放在拿到 `close` 之后）：

```python
                # ── 斐波那契均线纪律: 破 89 休息 → 硬剔除 ──
                fs = fib_state(
                    close,
                    latest.get('ma13'), latest.get('ma34'),
                    latest.get('ma55'), latest.get('ma89'),
                )
                if fs == 'rest':
                    continue
```

并把 `fib_state` 写入两类候选 dict（watching 与 breakout 分支都要加，且集中一次）：在 `dist_pct` 计算后，两个 append 之前统一准备：

```python
                fib_extra = {
                    'fib_state': fs,
                    # 回踩指标所需字段一次带全（watch/breakout 行共用，供 pullback_metrics 消费）
                    'ma5': latest.get('ma5'), 'ma10': latest.get('ma10'),
                    'ma20': latest.get('ma20'),
                    'ma13': latest.get('ma13'), 'ma34': latest.get('ma34'),
                    'ma55': latest.get('ma55'), 'ma89': latest.get('ma89'),
                    'vol_ma5_prev': latest.get('vol_ma5_prev'),
                    'tail': latest.get('tail') or [],
                }
```

然后在两个 `candidates.append({...})` 的 dict 中各加 `**fib_extra,`（注意逗号位置合法）。最终候选 dict 形如：

```python
                candidates.append({
                    'code': code, 'close': round(close, 2),
                    'prev_close': round(prev_close, 2),
                    'top_line': pressure, 'dist_pct': dist_pct,
                    'vol': int(vol), 'mavol180': round(mavol180, 0),
                    'vol_ratio': round(vol / mavol180, 1) if mavol180 > 0 else 0,
                    'limit_count': limit_count, 'status': 'watching',
                    **fib_extra,
                })
```

（breakout 分支同结构加 `**fib_extra,`）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_v3_fib_discipline.py tests/test_v3_chase_limit.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py
git commit -m "feat(v3): screen_candidates 破89剔除 + 候选行挂 fib_state"
```

---

### Task 4: 买入信号 13 日线纪律

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py:488-544`（`_scan_buy_signals`）
- Test: `tests/test_v3_fib_discipline.py`

- [ ] **Step 1: 写失败测试**

```python
def test_scan_buy_signals_respects_fib_discipline():
    """买点: close<=MA13 不给信号；fib=rest 防御性跳过；caution 允许(带警示)。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    p = Vol180SimPortfolio()
    p._state['holding'] = {}
    monkeypatch.setattr(p, '_get_market_state',
                        lambda trade_date=None: {'is_bull': True, 'sh_close': 3000, 'sh_ma60': 2900})
    def cand(code, close, ma13, fs):
        return {'code': code, 'close': close, 'top_line': 10.0,
                'dist_pct': 2.0, 'prev_close': 9.9,
                'vol': 100, 'mavol180': 50, 'vol_ratio': 2.0, 'limit_count': 12,
                'ma13': ma13, 'fib_state': fs}
    sigs = p._scan_buy_signals([
        cand('600001', 10.2, 9.8, 'ok'),       # close>MA13 → 允许
        cand('600002', 10.2, 10.5, 'caution'),  # close<=MA13 → 拦截
        cand('600003', 10.2, 9.0, 'rest'),      # 休息 → 防御拦截
    ], mode='v3')
    codes = {s['code'] for s in sigs}
    assert '600001' in codes
    assert '600002' not in codes
    assert '600003' not in codes
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_v3_fib_discipline.py::test_scan_buy_signals_respects_fib_discipline -q`
Expected: FAIL（600002 仍在）

- [ ] **Step 3: 实现（`_scan_buy_signals` 循环顶部加两行）**

在 `for c in candidates:` 之后、`if c['close'] > c['top_line'] ...` 之前插入：

```python
            # ── 斐波那契纪律: 只做 13 日线上 · 休息票防御性拦截 ──
            if c.get('fib_state') == 'rest':
                continue
            if (c.get('ma13') is not None
                    and c.get('close') is not None
                    and c['close'] <= c['ma13']):
                continue
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_v3_fib_discipline.py tests/test_v3_chase_limit.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py
git commit -m "feat(v3): 买入信号 13日线纪律（close>MA13 才给信号）"
```

---

### Task 5: 板块中军（spot 按日缓存，缺失降级放行）

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`（类内新增方法，常量区已含 ZHONGJUN 阈值）
- Test: `tests/test_v3_pullback_signal.py`

- [ ] **Step 1: 常量追加（模块常量区）**

```python
ZHONGJUN_AMOUNT = 2_000_000_000       # 中军成交额阈值: 20 亿元（与 daily_pool 一致）
ZHONGJUN_UP_PCT = 5.0                 # 趋势向上近似: 当日涨幅 ≥ 5%（大阳）
```

- [ ] **Step 2: 写失败测试（中军纯函数 + 板块归属主/备分支）**

```python
def test_sector_zhongjun_requires_20yi_and_up():
    """板块中军: 板块内存在 成交额≥20亿 且 当日涨幅≥5% 的票。"""
    from ashare_review.tools.sim_portfolio import sector_has_zhongjun
    members = ['600001', '600002']
    rows = {
        '600001': {'chg': 3.0, 'amount': 30_000_000_000},   # 涨幅不足
        '600002': {'chg': 6.5, 'amount': 5_000_000_000},    # 金额不足
    }
    assert sector_has_zhongjun(members, rows) is False
    rows2 = {'600001': {'chg': 6.0, 'amount': 30_000_000_000}}
    assert sector_has_zhongjun(members, rows2) is True


def test_zhongjun_sector_of_code_industry_primary_concept_fallback():
    """板块归属: 有行业用行业；行业缺失时用概念命中算中军。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio, zhongjun_sector_of
    imap = {'600001': '半导体', '600002': '半导体'}
    cmap = {'AI算力': {'members': {'600003': 1, '600004': 1}},
            '半导体': {'members': {'600001': 1, '600002': 1}}}
    # spot: 600002 是半导体中军(≥20亿+大阳)
    rows = {'600002': {'chg': 6.0, 'amount': 30_000_000_000}}
    # 600001 有行业 → 用行业组判定 → True
    assert zhongjun_sector_of('600001', imap, cmap, rows) is True
    # 600003 无行业 → 概念 'AI算力' 无中军 → False
    assert zhongjun_sector_of('600003', imap, cmap, rows) is False
    # 600004 无行业 → 概念 'AI算力' 无中军（但若同概念 600003 放量也 false 保持）
    rows2 = {'600003': {'chg': 6.0, 'amount': 30_000_000_000}}
    assert zhongjun_sector_of('600004', imap, cmap, rows2) is True
    # 数据缺失: spot 空 → None（放行）
    assert zhongjun_sector_of('600001', imap, cmap, {}) is None
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_v3_pullback_signal.py::test_sector_zhongjun_requires_20yi_and_up tests/test_v3_pullback_signal.py::test_zhongjun_sector_of_code_industry_primary_concept_fallback -q`
Expected: FAIL（ImportError: cannot import name 'sector_has_zhongjun' / 'zhongjun_sector_of'）

- [ ] **Step 4: 实现纯函数 + 实例方法（磁盘缓存 {code: bool/缺失}）**

模块级纯函数：

```python
def sector_has_zhongjun(members: list, spot_rows: dict) -> bool:
    """板块是否有中军: members 中任一票 当日涨幅≥5% 且 成交额≥20亿。"""
    for code in members:
        r = spot_rows.get(code)
        if not r:
            continue
        try:
            chg = float(r.get('chg', 0))
            amount = float(r.get('amount', 0))
        except (TypeError, ValueError):
            continue
        if chg >= ZHONGJUN_UP_PCT and amount >= ZHONGJUN_AMOUNT:
            return True
    return False


def zhongjun_sector_of(code: str, industry_map: dict, concept_map: dict,
                       spot_rows: dict):
    """code 所属板块是否有中军。

    板块归属规则: 有行业(industry_map) → 用该行业全体成员判定(主)；
    无行业 → 用 code 命中的所有概念成员(concept_map)取并集判定(备)。
    Returns: True=有中军 / False=明确无 / None=无 spot 数据(放行,不拦截)。
    """
    if not spot_rows:
        return None
    industry = industry_map.get(code, '')
    if industry:
        members = [c for c, v in industry_map.items() if v == industry and c != code]
        if sector_has_zhongjun(members or [code], spot_rows):
            return True
        # 行业判定失败 → 再看概念(补位，行业可能只是粗分类)
        found = False
    else:
        found = True   # 无行业 → 只走概念
    concept_members = set()
    for name, meta in concept_map.items():
        m = (meta.get('members') or {}) if isinstance(meta, dict) else {}
        if code in m:
            concept_members.update(k for k in m.keys() if k != code)
    if concept_members and sector_has_zhongjun(list(concept_members), spot_rows):
        return True
    if industry and not found and concept_members:
        return False          # 行业与概念都无 → 明确无
    if industry and not concept_members:
        return False          # 仅行业维度且无中军
    if not industry and concept_members:
        return False
    return None               # 无行业、无概念、无成员 → 数据不足，放行
```

类方法（只对请求的 code 计算 + 磁盘按日缓存 {code: true/false}；进程内再包一层）：

```python
    def _zhongjun_cache(self, trade_date: str, codes: list) -> dict:
        """当日板块中军缓存(只算请求的 codes): {code: True/False}；无 spot 数据返回 {}。

        Args:
            trade_date: YYYY-MM-DD（磁盘缓存键）
            codes: 本批候选代码（避免全市场概念扫描）
        """
        codes = [c for c in (codes or []) if c]
        if not codes:
            return {}
        key = (trade_date or '').replace('-', '')
        mem = getattr(self, '_zj_cache', None)
        if mem is None:
            mem = {}
            self._zj_cache_date = key
            self._zj_cache = mem
        if getattr(self, '_zj_cache_date', None) != key:
            mem.clear()
            self._zj_cache_date = key
        disk = os.path.join(DATA_DIR, 'cache', f'zhongjun_sector_{key}.json')
        disk_cache = {}
        if os.path.exists(disk):
            try:
                with open(disk, 'r', encoding='utf-8') as f:
                    disk_cache = json.load(f)
            except Exception:
                disk_cache = {}
        missing = [c for c in codes if c not in mem and c not in disk_cache]
        if missing:
            try:
                from ..data.akshare_fetcher import AkshareFetcher
                df = AkshareFetcher().get_spot_df()
            except Exception:
                df = None
            if df is not None and not df.empty:
                imap, cmap = self._load_industry_map(), self._load_concept_map()
                rows = {}
                for _, row in df.iterrows():
                    try:
                        c = str(row.get('代码', '')).zfill(6)
                        rows[c] = {'chg': float(row.get('涨跌幅', 0) or 0),
                                   'amount': float(row.get('成交额', 0) or 0)}
                    except Exception:
                        continue
                for c in missing:
                    disk_cache[c] = zhongjun_sector_of(c, imap, cmap, rows)
                os.makedirs(os.path.dirname(disk), exist_ok=True)
                try:
                    with open(disk, 'w', encoding='utf-8') as f:
                        json.dump(disk_cache, f, ensure_ascii=False)
                except Exception:
                    pass
        out = {c: disk_cache.get(c) for c in codes if c in disk_cache}
        mem.update(out)
        return out
```

`_load_industry_map()` / `_load_concept_map()` 缓存到实例属性：

```python
    def _load_industry_map(self) -> dict:
        if getattr(self, '_imap', None) is not None:
            return self._imap
        out = {}
        p = os.path.join(DATA_DIR, 'industry_map.json')
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    out = json.load(f)
            except Exception:
                pass
        self._imap = out
        return out

    def _load_concept_map(self) -> dict:
        if getattr(self, '_cmap', None) is not None:
            return self._cmap
        out = {}
        p = os.path.join(DATA_DIR, 'concept_map.json')
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                out = data.get('concepts', data) if isinstance(data, dict) else {}
            except Exception:
                pass
        self._cmap = out
        return out
```

> 结构说明：`concept_map.json` 顶层为 `{"updated":..., "concepts": {概念名: {"members": {code: 1}}}}`，
> 本方法返回内层 `concepts`（即 `{概念名: {"members": ...}}`），供 `zhongjun_sector_of` 消费。

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_v3_pullback_signal.py::test_sector_zhongjun_requires_20yi_and_up tests/test_v3_pullback_signal.py::test_zhongjun_sector_of_code_industry_primary_concept_fallback -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py tests/test_v3_pullback_signal.py
git commit -m "feat(v3): 板块中军判定（成交额≥20亿且涨幅≥5%；行业主+概念补位；按日缓存）"
```

---

### Task 6: 回踩低吸信号（engine 端产出 state['pullback']）

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`（`run_daily`、`refresh_daily_status`、`get_summary`）
- Test: `tests/test_v3_pullback_signal.py`

- [ ] **Step 1: 写失败测试**

```python
def test_pullback_signal_all_three_conditions():
    """回踩低吸: 三条件 AND（回踩+缩量企稳+中军）命中。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio, pullback_signal_pass
    latest = {
        'close': 10.05,
        'ma5': 10.4, 'ma10': 10.3, 'ma20': 10.0,   # 距 MA20 0.5%
        'vol': 40, 'vol_ma5_prev': 100,             # 量=前5日均量40%
        'tail': [
            {'o': 10.2, 'h': 10.4, 'l': 10.0, 'c': 10.1},
            {'o': 10.1, 'h': 10.2, 'l': 10.0, 'c': 10.05},
            {'o': 10.06, 'h': 10.2, 'l': 9.8, 'c': 10.05},  # 下影 0.26≥实体0.01*0.5
        ],
    }
    sig = pullback_signal_pass(latest, zhongjun=True)
    assert sig is not None and sig['zhongjun'] is True
    # 无中军 → 不命中
    assert pullback_signal_pass(latest, zhongjun=False) is None
    # 放量 → 不命中
    latest2 = dict(latest, vol=90)
    assert pullback_signal_pass(latest2, zhongjun=True) is None


def test_pullback_signal_zhongjun_missing_data_allows():
    """中军数据缺失(spot 空) → 不拦截（与 daily_pool 对无行业数据放行一致）。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    from ashare_review.tools.sim_portfolio import pullback_signal_pass
    latest = {
        'close': 10.05, 'ma5': 10.4, 'ma10': 10.3, 'ma20': 10.0,
        'vol': 40, 'vol_ma5_prev': 100,
        'tail': [
            {'o': 10.2, 'h': 10.4, 'l': 10.0, 'c': 10.1},
            {'o': 10.1, 'h': 10.2, 'l': 10.0, 'c': 10.05},
            {'o': 10.06, 'h': 10.2, 'l': 9.8, 'c': 10.05},
        ],
    }
    sig = pullback_signal_pass(latest, zhongjun=None)   # None=数据缺失
    assert sig is not None and sig['zhongjun'] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_v3_pullback_signal.py::test_pullback_signal_all_three_conditions tests/test_v3_pullback_signal.py::test_pullback_signal_zhongjun_missing_data_allows -q`
Expected: FAIL（ImportError: cannot import name 'pullback_signal_pass'）

- [ ] **Step 3: 实现纯函数 + run_daily/refresh 接线**

模块级纯函数（复用 pullback_metrics）：

```python
def pullback_signal_pass(latest: dict, zhongjun) -> Optional[dict]:
    """回踩低吸三条件 AND:
    1. 板块中军(zhongjun=True 命中; None=数据缺失不拦截; False=拦截)
    2. 回踩: dist_min_pct < 3%
    3. 缩量企稳: vol_ratio_5 < 0.6 且 (下影线 or 底分型)
    """
    if zhongjun is False:
        return None
    m = pullback_metrics(latest)
    if not m or not m.get('dist_min_pct') or m['dist_min_pct'] >= PULLBACK_BIAS_PCT:
        return None
    vr = m.get('vol_ratio_5')
    if vr is None or vr >= PULLBACK_VOL_RATIO:
        return None
    if not (m.get('has_lower_shadow') or m.get('has_bottom_fractal')):
        return None
    reasons = [f"回踩MA(乖离{m['dist_min_pct']}%)"]
    if m.get('has_lower_shadow'):
        reasons.append('下影线企稳')
    if m.get('has_bottom_fractal'):
        reasons.append('底分型')
    reasons.append(f"缩量{vr:.0%}")
    return {'zhongjun': zhongjun, 'reasons': reasons, 'metrics': m}
```

`run_daily`：候选生成后（L866-880 附近），**观察池入库时透传 fib/回踩指标字段**，随后生成回踩列表。

把现有入库循环（只存 code/name/close/top_line/dist_pct/vol_ratio/limit_count/update_date）改为存整行候选字段（含 fib_extra）：

```python
        # 更新观察池状态 — 每次扫描清空旧信号，强制重新评估
        self._state['watch'] = {}
        self._state['ready'] = {}  # ← 清空过期买入信号，防止旧数据残留
        for c in watch_list:
            code = c['code']
            entry = dict(c)  # 透传 close/top_line/dist_pct/vol_ratio/limit_count/fib_* /ma*/tail
            entry['name'] = self._get_name(code)
            entry['update_date'] = td
            self._state['watch'][code] = entry

        # ── 回踩低吸信号（独立于高位突破，只扫本候选池） ──
        self._state['pullback'] = self._build_pullback_list(
            watch_list + already_broken, td)
```

新实例方法（放 `run_daily` 之前）：

```python
    def _build_pullback_list(self, cand_rows: list, trade_date: str) -> list:
        """候选行 → 回踩低吸信号（三条件 AND）。中军 True/None=放行(标注)，False=拦截。"""
        codes = [c['code'] for c in cand_rows]
        zj = self._zhongjun_cache(trade_date, codes) if codes else {}
        out = []
        for c in cand_rows:
            code = c['code']
            if c.get('fib_state') == 'rest':
                continue
            zhongjun = zj.get(code) if zj else None
            if zhongjun is False:
                continue
            sig = pullback_signal_pass(c, zhongjun)
            if sig is None:
                continue
            out.append({
                'code': code,
                'name': c.get('name') or self._get_name(code),
                'close': c.get('close'),
                'ma5': c.get('ma5'), 'ma10': c.get('ma10'), 'ma20': c.get('ma20'),
                'fib_state': c.get('fib_state', 'ok'),
                'dist_min_pct': sig['metrics'].get('dist_min_pct'),
                'vol_ratio_5': sig['metrics'].get('vol_ratio_5'),
                'zhongjun': zhongjun,           # True / None(数据缺失)
                'has_lower_shadow': sig['metrics'].get('has_lower_shadow'),
                'has_bottom_fractal': sig['metrics'].get('has_bottom_fractal'),
                'reasons': sig['reasons'],
            })
        out.sort(key=lambda x: (x.get('zhongjun') is not True,
                                 x.get('dist_min_pct') or 99))
        return out
```

`refresh_daily_status`：Step 1 刷新 watch 行时**顺带把 latest 的 fib/ma/tail 字段并入行**，然后剔除破89行并重算回踩（轻量：只处理已有 watch，不重选股）。将现有刷新循环体改为：

```python
        # ── Step 1: 刷新观察池价格 + fib 字段 ──
        updated_watch = {}
        for code, w in self._state['watch'].items():
            latest = self._read_latest(code)
            if latest is None:
                updated_watch[code] = w
                continue

            close = latest['close']; vol = latest['vol']; mavol180 = latest['mavol180']
            top_line = w.get('top_line', 0)
            if top_line <= 0 or mavol180 <= 0:
                updated_watch[code] = w
                continue

            dist_pct = round((close - top_line) / top_line * 100, 1)
            vol_ratio = round(vol / mavol180, 1) if mavol180 > 0 else 0
            fs = fib_state(close,
                           latest.get('ma13'), latest.get('ma34'),
                           latest.get('ma55'), latest.get('ma89'))
            updated_watch[code] = {
                **w, 'close': round(close, 2), 'dist_pct': dist_pct,
                'vol_ratio': vol_ratio, 'vol': int(vol),
                'mavol180': round(mavol180, 0), 'update_date': td,
                # fib / 回踩指标字段并入（供 fib_state/回踩复用，避免二次读盘）
                'fib_state': fs,
                'ma5': latest.get('ma5'), 'ma10': latest.get('ma10'),
                'ma20': latest.get('ma20'),
                'ma13': latest.get('ma13'), 'ma34': latest.get('ma34'),
                'ma55': latest.get('ma55'), 'ma89': latest.get('ma89'),
                'vol_ma5_prev': latest.get('vol_ma5_prev'),
                'tail': latest.get('tail') or [],
            }

        # 破89(休息) → 剔除出观察池（refresh 不重选股，只做纪律剔除）
        self._state['watch'] = {
            code: w for code, w in updated_watch.items()
            if w.get('dist_pct', -999) >= -PRESSURE_DIST_PCT
            and w.get('fib_state') != 'rest'
        }
        # 保持 ready 池不变，不重新选股（与 run_daily 的核心区别）
        new_buys = 0

        # ── 重算回踩低吸（基于刷新后的观察池行） ──
        if self._state['watch']:
            self._state['pullback'] = self._build_pullback_list(
                list(self._state['watch'].values()), td)
```

`get_summary()`：返回 dict 加一列：

```python
            'pullback_list': list(self._state.get('pullback', [])),
```

并在 `_load()` 迁移默认补 `state.setdefault('pullback', [])`；新建 state 默认 dict 也加 `'pullback': [],`。

> 注：refresh 的行来自 run_daily 入库的候选行（已透传 ma*/fib/tail），且刷新时再用 latest 覆盖 fib/ma/tail，故 fib 与回踩都能在 refresh 上工作；旧版 state 里缺字段的 watch 行刷新后也会补齐。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_v3_pullback_signal.py tests/test_v3_fib_discipline.py tests/test_v3_chase_limit.py -q`
Expected: PASS

- [ ] **Step 5: 冒烟（本机有 TDX/spot 数据时）**

Run: `python -c "from ashare_review.tools.sim_portfolio import Vol180SimPortfolio; s=Vol180SimPortfolio(); print(type(s._build_pullback_list([], '2026-09-04')))"`
Expected: 打印 `<class 'list'>`，无异常。

- [ ] **Step 6: Commit**

```bash
git add ashare_review/tools/sim_portfolio.py tests/test_v3_pullback_signal.py
git commit -m "feat(v3): 回踩低吸信号（三条件AND）接入 run_daily/refresh/get_summary"
```

---

### Task 7: 页面 — fib 徽章 + 回踩低吸面板 + 规则文字

**Files:**
- Modify: `ashare_review/web/templates/breakout_v3.html`
- Test: 无新增；跑 `tests/test_home_overview.py` 冒烟（页面仍渲染）

- [ ] **Step 1: 观察池卡片加 fib 徽章（renderWatchPool 内，dist 行上方）**

在 `html += '<div style="font-size:0.72em;color:#999;">涨停' + ...` 前插一行徽章逻辑（在 forEach 内 `const distColor = ...` 附近加）：

```js
        const fib = s.fib_state;
        const fibTag = fib === 'rest' ? '<span style="font-size:0.7em;background:#ef4444;color:#fff;padding:0 4px;border-radius:3px;">休息</span>'
                     : fib === 'caution' ? '<span style="font-size:0.7em;background:#f59e0b;color:#fff;padding:0 4px;border-radius:3px;">谨慎</span>'
                     : '';
        if (fibTag) html += '<div style="margin-top:1px;">' + fibTag + '</div>';
```

- [ ] **Step 2: 买入卡片加 13 日线状态**

在 `renderBuyPanel` 每卡片元信息行前（`'📅 次日开盘买入...'` 行前）加：

```js
        if (s.fib_state === 'caution') {
            html += '<div style="font-size:0.72em;color:#b45309;margin-top:2px;">⚠️ 破34/55日线 · 谨慎参与</div>';
        }
```

- [ ] **Step 3: 新增「🔻 回踩低吸」全宽面板（观察池区块之后，买入区块之前）**

在第 244 行 `<!-- ====== 2. 明日开盘买入 + 今日模拟卖出（两栏） ====== -->` 注释之前插入：

```html
<!-- ====== 1.5 回踩低吸（三条件确认，只扫 V3 候选池） ====== -->
<div class="screener-column" style="border:2px solid #0ea5e9;margin-bottom:14px;">
    <div class="column-header" style="border-bottom-color:#0ea5e9;">
        <h3 style="color:#0284c7;">🔻 回踩低吸（板块中军+回踩5/10/20日线+缩量企稳）</h3>
        <span class="column-badge" style="background:#e0f2fe;color:#075985;" id="pullback-count">-</span>
    </div>
    <p style="margin:0 0 6px;">
        启动后回踩企稳的再上车点 · 三条件同时满足才上车，错了止损就是 ·
        <span style="color:#0284c7;">只做13日线上 · 破34/55谨慎 · 破89休息</span>
    </p>
    <div id="pullback-panel" style="min-height:40px;max-height:300px;overflow-y:auto;">
        <div class="empty-hint">点击「运行扫描」加载...</div>
    </div>
</div>
```

- [ ] **Step 4: JS — renderAll 接线 + 渲染函数**

`renderAll` 内（renderWatchPool 之前）加：

```js
    renderPullback(data.pullback_list || []);
```

页面 script 中新增：

```js
// ── 回踩低吸（三条件确认） ──
function renderPullback(items) {
    document.getElementById('pullback-count').textContent = items.length + '只';
    const el = document.getElementById('pullback-panel');
    if (items.length === 0) {
        el.innerHTML = '<div style="text-align:center;padding:14px;color:#94a3b8;">今日无回踩低吸信号（三条件未同时满足）</div>';
        return;
    }
    let html = '';
    items.forEach(s => {
        const zjTag = s.zhongjun === true
            ? '<span style="font-size:0.7em;background:#0369a1;color:#fff;padding:1px 6px;border-radius:3px;">中军✔</span>'
            : '<span style="font-size:0.7em;background:#e2e8f0;color:#475569;padding:1px 6px;border-radius:3px;">中军数据缺失</span>';
        const fibTag = s.fib_state === 'caution'
            ? '<span style="font-size:0.7em;background:#f59e0b;color:#fff;padding:1px 5px;border-radius:3px;">谨慎</span>' : '';
        html += '<div style="display:inline-block;background:#f0f9ff;border-radius:8px;padding:8px 12px;margin:3px;border:1px solid #7dd3fc;vertical-align:top;min-width:230px;border-left:4px solid #0284c7;">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
        html += '<a href="/stock/' + esc(s.code) + '" style="color:#0284c7;font-weight:700;text-decoration:none;">' + esc(s.code) + '</a>';
        html += '<span style="font-size:0.8em;color:#475569;">' + esc(s.name) + '</span>' + zjTag + fibTag;
        html += '</div>';
        html += '<div style="font-size:0.75em;color:#334155;margin-top:3px;">';
        html += '现价 ' + (s.close || 0) + ' | 回踩乖离 ' + (s.dist_min_pct || 0) + '% | 量比前5日均 ' + (s.vol_ratio_5 || 0) + '';
        html += '</div>';
        html += '<div style="font-size:0.72em;color:#0284c7;margin-top:2px;">' + (s.reasons || []).join(' · ') + '</div>';
        html += '</div>';
    });
    el.innerHTML = html;
}
```

- [ ] **Step 5: 选股标准/口诀文字更新**

(a) 「📋 选股标准」details 内 `硬性条件：` 后加两行：

```html
<strong style="color:#0369a1;">斐波那契均线纪律（日线 13/34/55/89）：</strong><br>
✅ 只做K线在13日线上的图形（买入信号要求收盘&gt;MA13）<br>
⚠️ 破位34/55日线 → 谨慎参与 · 🔴 破位89日线 → 直接休息（自动剔除）<br><br>
<strong style="color:#0ea5e9;">回踩低吸三条件（板块有中军 + 个股回踩 + 缩量企稳）：</strong><br>
✅ 板块有中军：板块内有成交额&gt;20亿且趋势向上的票<br>
✅ 个股在回踩：股价在5日/10日/20日线附近（乖离&lt;3%）<br>
✅ 缩量企稳：成交量&lt;前5日均量60%，且出现下影线/底分型<br>
三条同时满足 → 就上车，不再纠结第四条；错了止损就是。<br>
```

(b) 顶部「策略口诀」banner 加一句：

```html
<span style="color:#0369a1;">只做13日线上 · 破34/55谨慎 · 破89休息</span> &nbsp;|&nbsp;
<span style="color:#0ea5e9;">回踩低吸：板块中军+回踩5/10/20线+缩量企稳</span> &nbsp;|&nbsp;
```

- [ ] **Step 6: 冒烟检查**

Run: `python -m pytest tests/test_home_overview.py -q`
Expected: PASS（页面模板仍渲染，未断链）

- [ ] **Step 7: Commit**

```bash
git add ashare_review/web/templates/breakout_v3.html
git commit -m "feat(v3): 页面 fib徽章 + 回踩低吸面板 + 规则文字同步"
```

---

### Task 8: V3_STRATEGY.md 策略文档同步

**Files:**
- Modify: `ashare_review/data/V3_STRATEGY.md`

- [ ] **Step 1: 更新文档**

在「二、选股标准」的 2.1 硬性条件表后新增小节：

```markdown
### 2.4 斐波那契均线纪律（V3 更新 2026-09-04）

均线体系采用斐波那契 13/34/55/89（逻辑哥教学：只做 K 线在 13 日线上的图形）。

| 状态 | 判定（日线收盘） | 处理 |
|---|---|---|
| 🟢 可做 | close > MA13 且未破 34/55 | 正常参与 |
| 🟠 谨慎 | 破 MA34 或 MA55（≥ MA89） | 行首警示，不加多头分，不硬拦 |
| 🔴 休息 | close < MA89 | 硬过滤：观察池/买入信号/预测剔除 |

买入信号硬性要求：收盘 > MA13。

### 2.5 回踩低吸信号（V3 更新 2026-09-04）

在 V3 候选池内额外扫描「回踩低吸」再上车点，三条件同时满足才入列（页面「🔻 回踩低吸」面板）：

| # | 条件 | 量化标准 |
|---|---|---|
| 1 | 板块有中军 | 板块内有成交额 > 20 亿且当日涨幅 ≥ 5%（趋势向上）的票 |
| 2 | 个股在回踩 | 股价在 5/10/20 日线附近（乖离 < 3%） |
| 3 | 缩量企稳信号 | 成交量 < 前 5 日均量 60%，且出现下影线/底分型 |

说明：中军 spot 数据缺失时不拦截（与 daily_pool 对无行业数据放行一致）；破 89 日线票不参与回踩信号。
```

- [ ] **Step 2: Commit**

```bash
git add ashare_review/data/V3_STRATEGY.md
git commit -m "docs(v3): V3_STRATEGY 同步斐波那契纪律与回踩低吸信号"
```

---

### Task 9: 全量回归 + 收尾

**Files:** 无新增（只跑测试）

- [ ] **Step 1: 新增用例全套**

Run: `python -m pytest tests/test_v3_fib_discipline.py tests/test_v3_pullback_signal.py tests/test_v3_chase_limit.py -q`
Expected: PASS（全绿）

- [ ] **Step 2: 仓库全套回归**

Run: `python -m pytest -q`
Expected: PASS（无回归；若有失败，定位是否为既有数据文件差异，必要时 `git stash` 对照）

- [ ] **Step 3: 页面烟测（可选，需启动 Flask）**

Run: `python run.py` → 打开 `http://127.0.0.1:5000/breakout_v3` → 运行扫描后确认：
- watch/buy 卡片出现 fib 徽章；破 89 票不再出现在观察池；
- 「🔻 回踩低吸」面板按三条件显示；中军数据缺失时标注"数据缺失"且不拦截。

- [ ] **Step 4: 收尾 commit（若测试有微调）**

```bash
git add -A tests
git commit -m "test(v3): 全量回归通过"
```

---

## 验收对照（spec → task）

| spec 需求 | task |
|---|---|
| fib 13/34/55/89 三档状态（可做/谨慎/休息） | Task 1, 3 |
| 破 89 → 观察池/买入/预测剔除 | Task 3, 4（screen 剔除 + buy 防御 + watch refresh 剔除） |
| 只做 13 日线上（买点硬条件） | Task 4 |
| 板块有中军（成交额>20亿 且 趋势向上） | Task 5 |
| 回踩 5/10/20 乖离<3% + 缩量<前5日均量60% + 下影线/底分型 | Task 1, 6 |
| 中军数据缺失不拦截 | Task 5, 6 |
| `state['pullback']` / `pullback_list` 输出 | Task 6 |
| 页面：fib 徽章 + 回踩面板 + 规则文字 | Task 7 |
| V3_STRATEGY.md 同步 | Task 8 |
| 全套 pytest 通过 | Task 9 |
