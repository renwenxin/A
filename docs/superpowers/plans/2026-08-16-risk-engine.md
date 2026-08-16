# 风控规则层（Risk Engine）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把两个模拟持仓的硬编码风控参数（止损/仓位/持仓上限/每日新开）提升为可配置规则，新增组合回撤熔断与 regime 仓位缩放，规则真正作用于买卖执行，持仓页内嵌风控卡片。

**Architecture:** 新包 `ashare_review/risk/`（rules=默认配置+校验 / store=共享 JSON 持久化 / evaluate=纯函数开仓判定），Vol180/ZTReplica 两个 portfolio 类各 3 处薄接入（买入拦截+仓位缩放、止损线读配置）。**默认配置与现状常量完全一致 → 不改配置行为零变化。**

**Tech Stack:** Python 3 + Flask + pytest + pandas（测试 FakeTdx 用）。

**设计依据:** `docs/superpowers/specs/2026-08-16-risk-engine-design.md`（commit ee93e29）

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `ashare_review/risk/__init__.py` | 包标记 |
| `ashare_review/risk/rules.py` | DEFAULT_CONFIG（两 portfolio）+ validate_config + 字段常量 |
| `ashare_review/risk/store.py` | RiskConfig 加载/保存（原子写、损坏回退默认） |
| `ashare_review/risk/evaluate.py` | evaluate() + stop_loss_pct() 纯函数 |
| `ashare_review/tools/sim_portfolio.py` | 修改：3 处接入 |
| `ashare_review/tools/zt_replica_portfolio.py` | 修改：3 处接入 |
| `ashare_review/web/app.py` | 修改：3 个 API（config GET/POST、status） |
| `ashare_review/web/templates/sim_portfolio.html` | 修改：风控卡片 + 编辑弹窗 |
| `ashare_review/web/templates/zt_replica.html` | 修改：风控卡片 + 编辑弹窗 |
| `ashare_review/tests/test_risk_engine.py` | 全部新测试 |

**关键约定：**
- 配置键：stop_loss_pct(负值%) / per_position_pct(%) / max_positions / max_new_per_day / drawdown_breaker_pct / drawdown_recover_pct / regime_scale(6 键 dict)
- DEFAULT_CONFIG 与现状常量一致：vol180 止损 -6、zt_replica -5、仓位 10%、最大持仓 10、每日新开 3、熔断 8%、恢复 4%
- regime 缩放沿用 v3_backtest regime_weights（强势 1.0/题材 0.7/震荡 0.3/弱市 0.2/退潮 0.0/冰点 0.3）
- 份额基数保持 INITIAL_CAPITAL（与现状一致），suggested_size_pct 含 regime 缩放
- 止损代码现状：vol180 `loss_pct <= -0.06`（_check_sell_vol180 L502、_check_sell_v3 L584）、zt_replica `<= -0.05`（L517）

---

### Task 1: rules.py 默认配置 + 校验

**Files:**
- Create: `ashare_review/risk/__init__.py`（空）
- Create: `ashare_review/risk/rules.py`
- Create: `ashare_review/tests/test_risk_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# ashare_review/tests/test_risk_engine.py
"""风控规则引擎单元测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Task 1: 默认配置与校验 ----------

def test_default_config_complete():
    from ashare_review.risk.rules import DEFAULT_CONFIG, REGIMES
    for pid in ('vol180', 'zt_replica'):
        cfg = DEFAULT_CONFIG[pid]
        for key in ('stop_loss_pct', 'per_position_pct', 'max_positions',
                    'max_new_per_day', 'drawdown_breaker_pct',
                    'drawdown_recover_pct', 'regime_scale'):
            assert key in cfg, (pid, key)
        assert set(cfg['regime_scale'].keys()) == set(REGIMES)
        assert cfg['regime_scale'].get('退潮下跌') == 0.0   # 退潮禁开仓


def test_default_config_matches_current_constants():
    """默认配置 = 现状常量（行为零变化保证）"""
    from ashare_review.risk.rules import DEFAULT_CONFIG
    assert DEFAULT_CONFIG['vol180']['stop_loss_pct'] == -6.0
    assert DEFAULT_CONFIG['zt_replica']['stop_loss_pct'] == -5.0
    for pid in ('vol180', 'zt_replica'):
        assert DEFAULT_CONFIG[pid]['per_position_pct'] == 10.0
        assert DEFAULT_CONFIG[pid]['max_positions'] == 10
        assert DEFAULT_CONFIG[pid]['max_new_per_day'] == 3


def test_validate_config():
    from ashare_review.risk.rules import validate_config, DEFAULT_CONFIG
    # 合法配置通过
    errs = validate_config('vol180', DEFAULT_CONFIG['vol180'])
    assert errs == []
    # 止损越界（-50%）
    bad = dict(DEFAULT_CONFIG['vol180'], stop_loss_pct=-50.0)
    assert any('stop_loss_pct' in e for e in validate_config('vol180', bad))
    # 仓位越界
    bad2 = dict(DEFAULT_CONFIG['vol180'], per_position_pct=120.0)
    assert any('per_position_pct' in e for e in validate_config('vol180', bad2))
    # regime 系数负数
    bad3 = dict(DEFAULT_CONFIG['vol180'])
    bad3['regime_scale'] = dict(bad3['regime_scale'], **{'强势趋势': -0.5})
    assert any('regime_scale' in e for e in validate_config('vol180', bad3))
    # 缺字段 → 报缺字段（不静默）
    bad4 = {k: v for k, v in DEFAULT_CONFIG['vol180'].items() if k != 'max_positions'}
    assert any('max_positions' in e for e in validate_config('vol180', bad4))
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: FAIL — ModuleNotFoundError（risk 不存在）

- [ ] **Step 3: 实现 rules.py**

```python
# ashare_review/risk/rules.py
"""风控规则 — 默认配置与校验"""
from typing import Dict, List

REGIMES = ['强势趋势', '题材轮动', '震荡观望', '弱市回调', '退潮下跌', '冰点超跌']

# 默认缩放系数：沿用 v3_backtest regime_weights（退潮=0 禁开仓）
DEFAULT_REGIME_SCALE = {'强势趋势': 1.0, '题材轮动': 0.7, '震荡观望': 0.3,
                        '弱市回调': 0.2, '退潮下跌': 0.0, '冰点超跌': 0.3}

DEFAULT_CONFIG: Dict[str, Dict] = {
    'vol180': {
        'stop_loss_pct': -6.0,
        'per_position_pct': 10.0,
        'max_positions': 10,
        'max_new_per_day': 3,
        'drawdown_breaker_pct': 8.0,
        'drawdown_recover_pct': 4.0,
        'regime_scale': dict(DEFAULT_REGIME_SCALE),
    },
    'zt_replica': {
        'stop_loss_pct': -5.0,
        'per_position_pct': 10.0,
        'max_positions': 10,
        'max_new_per_day': 3,
        'drawdown_breaker_pct': 8.0,
        'drawdown_recover_pct': 4.0,
        'regime_scale': dict(DEFAULT_REGIME_SCALE),
    },
}

_KEYS = ['stop_loss_pct', 'per_position_pct', 'max_positions', 'max_new_per_day',
         'drawdown_breaker_pct', 'drawdown_recover_pct']


def validate_config(portfolio_id: str, cfg: dict) -> List[str]:
    """校验配置，返回错误列表（空=合法）。"""
    errors = []
    if not isinstance(cfg, dict):
        return ['配置必须是对象']
    for key in _KEYS:
        if key not in cfg:
            errors.append(f'缺少字段: {key}')
            continue
        v = cfg[key]
        if not isinstance(v, (int, float)):
            errors.append(f'{key} 必须为数值')
            continue
        if key in ('stop_loss_pct',) and not (-30.0 <= v < 0):
            errors.append(f'stop_loss_pct 需在 -30~0 之间（当前 {v}）')
        elif key == 'per_position_pct' and not (1.0 <= v <= 50.0):
            errors.append(f'per_position_pct 需在 1~50 之间（当前 {v}）')
        elif key in ('max_positions', 'max_new_per_day') and not (1 <= v <= 50):
            errors.append(f'{key} 需在 1~50 之间（当前 {v}）')
        elif key in ('drawdown_breaker_pct', 'drawdown_recover_pct') and not (0 < v <= 50):
            errors.append(f'{key} 需在 0~50 之间（当前 {v}）')
    rs = cfg.get('regime_scale')
    if not isinstance(rs, dict):
        errors.append('缺少字段: regime_scale')
    else:
        for r in REGIMES:
            if r not in rs:
                errors.append(f'regime_scale 缺少: {r}')
            elif not isinstance(rs[r], (int, float)) or rs[r] < 0:
                errors.append(f'regime_scale[{r}] 必须 ≥0（当前 {rs.get(r)}）')
    return errors
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add ashare_review/risk ashare_review/tests/test_risk_engine.py
git commit -m "feat(risk): 默认风控配置与校验"
```

---

### Task 2: store.py 配置持久化

**Files:**
- Create: `ashare_review/risk/store.py`
- Test: `ashare_review/tests/test_risk_engine.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_risk_engine.py

# ---------- Task 2: 配置存储 ----------

def test_store_default_fallback(tmp_path):
    from ashare_review.risk.store import RiskStore
    s = RiskStore(str(tmp_path / 'nope.json'))
    cfg = s.get('vol180')
    assert cfg['stop_loss_pct'] == -6.0          # 文件不存在 → 默认
    assert s.get('zt_replica')['stop_loss_pct'] == -5.0


def test_store_save_and_load(tmp_path):
    from ashare_review.risk.store import RiskStore
    path = str(tmp_path / 'risk.json')
    s = RiskStore(path)
    s.set('vol180', {'stop_loss_pct': -4.0, 'per_position_pct': 8.0})
    s2 = RiskStore(path)
    cfg = s2.get('vol180')
    assert cfg['stop_loss_pct'] == -4.0
    assert cfg['per_position_pct'] == 8.0
    # 未设置的部分回退默认（缺字段合并）
    assert cfg['max_positions'] == 10


def test_store_corrupt_json_falls_back(tmp_path):
    from ashare_review.risk.store import RiskStore
    p = tmp_path / 'risk.json'
    p.write_text('{broken json', encoding='utf-8')
    s = RiskStore(str(p))
    assert s.get('vol180')['stop_loss_pct'] == -6.0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: FAIL — ModuleNotFoundError（store 不存在）

- [ ] **Step 3: 实现 store.py**

```python
# ashare_review/risk/store.py
"""风控规则 — 共享配置持久化（原子写，损坏回退默认）"""
import json
import logging
import os
import tempfile
from typing import Dict, Optional

from .rules import DEFAULT_CONFIG, validate_config

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get(
    'RISK_CONFIG',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'risk_config.json'))


def _merge_default(portfolio_id: str, saved: dict) -> dict:
    """已保存配置与默认合并：缺字段用默认。"""
    base = dict(DEFAULT_CONFIG[portfolio_id])
    for k, v in (saved or {}).items():
        if k == 'regime_scale' and isinstance(v, dict):
            merged = dict(base['regime_scale'])
            merged.update(v)
            base['regime_scale'] = merged
        else:
            base[k] = v
    return base


class RiskStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or CONFIG_PATH

    def _load_raw(self) -> dict:
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning('风控配置读取失败 %s（%s），回退默认', self.path, e)
        return {}

    def get(self, portfolio_id: str) -> dict:
        raw = self._load_raw()
        saved = raw.get(portfolio_id)
        cfg = _merge_default(portfolio_id, saved)
        return cfg

    def get_all(self) -> Dict[str, dict]:
        return {pid: self.get(pid) for pid in DEFAULT_CONFIG}

    def set(self, portfolio_id: str, cfg: dict) -> None:
        """校验并保存单份配置（缺字段用默认补齐）。非法抛 ValueError。"""
        if portfolio_id not in DEFAULT_CONFIG:
            raise ValueError(f'未知持仓: {portfolio_id}')
        full = _merge_default(portfolio_id, cfg)
        errors = validate_config(portfolio_id, full)
        if errors:
            raise ValueError('; '.join(errors))
        raw = self._load_raw()
        raw[portfolio_id] = full
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or '.',
                                   suffix='.tmp', prefix='risk_config_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: 6 passed（3 + 3）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/risk/store.py ashare_review/tests/test_risk_engine.py
git commit -m "feat(risk): 配置持久化（原子写/损坏回退/缺字段合并）"
```

---

### Task 3: evaluate.py 开仓判定纯函数

**Files:**
- Create: `ashare_review/risk/evaluate.py`
- Test: `ashare_review/tests/test_risk_engine.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_risk_engine.py

# ---------- Task 3: 开仓判定 ----------

def _cfg(**kw):
    from ashare_review.risk.rules import DEFAULT_CONFIG
    c = dict(DEFAULT_CONFIG['vol180'])
    c.update(kw)
    return c


def test_evaluate_normal_open():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    r = evaluate(cfg, {'positions': 3, 'opened_today': 1,
                       'total_value': 1_050_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r['can_open'] is True
    assert r['blocked_reasons'] == []
    assert r['suggested_size_pct'] == 10.0
    assert r['regime_scale'] == 1.0
    # 回撤 (110-105)/110 = 4.5% < 8% → 放行


def test_evaluate_drawdown_breaker():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    # 回撤 = (110-101)/110 = 8.18% ≥ 8% → 拦
    r = evaluate(cfg, {'positions': 1, 'opened_today': 0,
                       'total_value': 1_010_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r['can_open'] is False
    assert any('回撤' in s for s in r['blocked_reasons'])
    # 恰好 = 8% → 触发（(110-101.2)/110=8%）
    r2 = evaluate(cfg, {'positions': 1, 'opened_today': 0,
                        'total_value': 1_012_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r2['can_open'] is False
    # 熔断后回撤 < 4% → 解除
    r3 = evaluate(cfg, {'positions': 1, 'opened_today': 0,
                        'total_value': 1_065_000, 'history_peak': 1_100_000}, '强势趋势')
    assert r3['can_open'] is True


def test_evaluate_regime_scale():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    # 题材轮动 → 0.7 → 建议 7%
    r = evaluate(cfg, {'positions': 0, 'opened_today': 0,
                       'total_value': 1_000_000, 'history_peak': 1_000_000}, '题材轮动')
    assert r['can_open'] is True and r['suggested_size_pct'] == 7.0
    # 退潮下跌 scale=0 → 禁开仓
    r2 = evaluate(cfg, {'positions': 0, 'opened_today': 0,
                        'total_value': 1_000_000, 'history_peak': 1_000_000}, '退潮下跌')
    assert r2['can_open'] is False
    assert any('退潮' in s for s in r2['blocked_reasons'])
    # 未知 regime → 1.0 不误拦
    r3 = evaluate(cfg, {'positions': 0, 'opened_today': 0,
                        'total_value': 1_000_000, 'history_peak': 1_000_000}, '未知行情')
    assert r3['can_open'] is True and r3['regime_scale'] == 1.0


def test_evaluate_limits_and_multi():
    from ashare_review.risk.evaluate import evaluate
    cfg = _cfg()
    r = evaluate(cfg, {'positions': 10, 'opened_today': 3,
                       'total_value': 900_000, 'history_peak': 1_100_000}, '退潮下跌')
    assert r['can_open'] is False
    reasons = '；'.join(r['blocked_reasons'])
    assert '回撤' in reasons and '退潮' in reasons and '持仓数' in reasons and '新开' in reasons


def test_stop_loss_pct():
    from ashare_review.risk.evaluate import stop_loss_pct
    assert stop_loss_pct({'stop_loss_pct': -6.0}) == -6.0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: FAIL — ModuleNotFoundError（evaluate 不存在）

- [ ] **Step 3: 实现 evaluate.py**

```python
# ashare_review/risk/evaluate.py
"""风控规则 — 开仓判定纯函数"""
from typing import Dict, List


def evaluate(config: dict, state: dict, regime: str) -> Dict:
    """开仓判定。state = {positions, opened_today, total_value, history_peak}

    返回 {can_open, blocked_reasons[], suggested_size_pct, regime_scale, drawdown_pct}
    """
    peak = float(state.get('history_peak', 0) or 0)
    total = float(state.get('total_value', 0) or 0)
    drawdown_pct = (peak - total) / peak * 100 if peak > 0 else 0.0

    blocked: List[str] = []
    breaker = float(config.get('drawdown_breaker_pct', 8.0))
    recover = float(config.get('drawdown_recover_pct', 4.0))
    if drawdown_pct >= breaker:
        blocked.append(f'组合回撤 {drawdown_pct:.1f}% ≥ 熔断线 {breaker:.1f}%')

    scale = float(config.get('regime_scale', {}).get(regime, 1.0))
    if scale <= 0:
        blocked.append(f'行情「{regime}」禁止开新仓')

    max_pos = int(config.get('max_positions', 10))
    if int(state.get('positions', 0)) >= max_pos:
        blocked.append(f'持仓数已达上限 {max_pos} 只')

    max_new = int(config.get('max_new_per_day', 3))
    if int(state.get('opened_today', 0)) >= max_new:
        blocked.append(f'今日已新开 {max_new} 只')

    return {
        'can_open': len(blocked) == 0,
        'blocked_reasons': blocked,
        'suggested_size_pct': round(float(config.get('per_position_pct', 10.0)) * scale, 1),
        'regime_scale': scale,
        'drawdown_pct': round(drawdown_pct, 2),
    }


def stop_loss_pct(config: dict) -> float:
    """卖出点读取的止损线（负值 %）。"""
    return float(config.get('stop_loss_pct', -6.0))
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: 11 passed（6 + 5）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/risk/evaluate.py ashare_review/tests/test_risk_engine.py
git commit -m "feat(risk): 开仓判定引擎（回撤熔断/regime 缩放/上限）"
```

---

### Task 4: Vol180SimPortfolio 接入

**Files:**
- Modify: `ashare_review/tools/sim_portfolio.py`
- Test: `ashare_review/tests/test_risk_engine.py`（追加）

- [ ] **Step 1: 写失败测试（止损线接入 + 配置读取）**

```python
# 追加到 test_risk_engine.py

# ---------- Task 4: Vol180 接入 ----------

class FakeTdx2:
    """可控日线：code -> DataFrame(trade_date/open/high/low/close/volume)"""
    def __init__(self, data):
        self.data = data  # {code: [(date, open, close), ...]}

    def read_daily(self, code, market):
        import pandas as pd
        from datetime import datetime
        bars = self.data.get(str(code))
        if not bars:
            return pd.DataFrame()
        rows = [{'trade_date': datetime.strptime(d, '%Y-%m-%d').date(),
                 'open': o, 'high': o, 'low': c, 'close': c, 'volume': 100}
                for d, o, c in bars]
        return pd.DataFrame(rows).sort_values('trade_date').reset_index(drop=True)


def _vol180_portfolio(tmp_path, monkeypatch, config_path=None):
    from ashare_review.tools.sim_portfolio import Vol180SimPortfolio
    import tempfile
    path = config_path or str(tmp_path / 'risk.json')
    monkeypatch.setenv('RISK_CONFIG', path)
    p = Vol180SimPortfolio()
    p._state['holding'] = {}          # 清空真实状态，避免污染
    return p


def test_vol180_stop_loss_default_unchanged(tmp_path, monkeypatch):
    """默认配置 -6%：跌 5% 不止损，跌 7% 止损（与现状一致）"""
    from ashare_review.risk.store import RiskStore
    path = str(tmp_path / 'risk.json')
    RiskStore(path).set('vol180', {})   # 写默认
    p = _vol180_portfolio(tmp_path, monkeypatch, path)
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 9.5),
                                 ('2026-08-12', 10.0, 9.3)]})   # 最新 9.3 → -7%
    p._state['holding'] = {'600001': {'buy_date': '2026-08-10', 'buy_price': 10.0, 'had_zt': False}}
    sell = p._check_sell_vol180('600001', p._state['holding']['600001'], '2026-08-12')
    assert sell is not None and '止损' in sell['sell_reason']
    # 跌 5%：最新 9.5 → 不止损
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                 ('2026-08-12', 10.0, 9.5)]})
    sell2 = p._check_sell_vol180('600001', p._state['holding']['600001'], '2026-08-12')
    assert sell2 is None


def test_vol180_stop_loss_config_changes_behavior(tmp_path, monkeypatch):
    """改配置止损 -3%：跌 5% 即触发（验证配置真正生效）"""
    from ashare_review.risk.store import RiskStore
    path = str(tmp_path / 'risk.json')
    RiskStore(path).set('vol180', {'stop_loss_pct': -3.0})
    p = _vol180_portfolio(tmp_path, monkeypatch, path)
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                 ('2026-08-12', 10.0, 9.5)]})   # -5% ≥ 3% 线
    p._state['holding'] = {'600001': {'buy_date': '2026-08-10', 'buy_price': 10.0, 'had_zt': False}}
    sell = p._check_sell_vol180('600001', p._state['holding']['600001'], '2026-08-12')
    assert sell is not None and '止损' in sell['sell_reason']
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: FAIL — 行为不符（止损仍硬编码 -6%，config 改动不生效）

- [ ] **Step 3: 实现接入**

在 `sim_portfolio.py` 修改 3 处：

**a) `__init__` 加配置持有：**
```python
    def __init__(self):
        self.tdx = TdxReader()
        from ..risk.store import RiskStore
        self._risk = RiskStore()
        os.makedirs(DATA_DIR, exist_ok=True)
        self._state = self._load()
        self._name_cache: Dict[str, str] = {}
```

**b) 止损两处（`_check_sell_vol180` 与 `_check_sell_v3` 的硬止损块）**：
```python
        # ── V2: 硬止损（读风控配置，默认 -6%） ──
        from ..risk.evaluate import stop_loss_pct
        stop = stop_loss_pct(self._risk.get('vol180')) / 100.0
        if buy_price > 0:
            loss_pct = (close - buy_price) / buy_price
            if loss_pct <= stop:
                return {
                    'sell_price': round(close, 2),
                    'sell_reason': f'止损{abs(stop*100):.0f}%',
                    'is_zt': False,
                    'days_held': trading_days,
                }
```
（`_check_sell_v3` 的硬止损块同样替换，sell_reason 同步改为动态数值）

**c) 买入段（run_daily 内 ~L798-824）**：
把 `available_slots`/`max_new`/`position_capital` 改为读配置 + evaluate：

```python
        new_buys = 0
        cfg = self._risk.get('vol180')
        # ── 风控判定 ──
        holdings_val = sum(
            h.get('shares', 0) * (h.get('current_price', h.get('buy_price', 0)) or 0)
            for h in self._state['holding'].values()
        )
        hist_peak = INITIAL_CAPITAL
        for snap in self._state.get('portfolio_history', []):
            hist_peak = max(hist_peak, snap.get('total', 0) or 0)
        from ..risk.evaluate import evaluate
        from ..analysis.strategy_regime import live_diagnosis as _ld
        try:
            regime = _ld.get_regime_diagnosis().get('regime', '震荡观望') or '震荡观望'
        except Exception:
            regime = '震荡观望'
        risk = evaluate(cfg, {
            'positions': len(self._state['holding']) + len(self._state['ready']),
            'opened_today': new_buys,
            'total_value': self._state.get('cash', INITIAL_CAPITAL) + holdings_val,
            'history_peak': hist_peak,
        }, regime)
        if risk['blocked_reasons']:
            print(f"[SimPortfolio] 风控拦截开仓: {'；'.join(risk['blocked_reasons'])}")
        self._state['last_risk'] = risk   # 供 status API 读取
        available_slots = max(0, cfg['max_positions'] - len(self._state['holding']) - len(self._state['ready']))
        max_new = min(cfg['max_new_per_day'], available_slots)

        for sig in buy_signals[:max_new]:
            if not risk['can_open']:
                break
            ...
            # 计算买入份额（读配置 + regime 缩放）
            position_capital = INITIAL_CAPITAL * (risk['suggested_size_pct'] / 100.0)
```

（run_daily 里 `self._state['last_risk'] = risk` 已覆盖 JSON 可序列化的 risk dict；status API 读取 `state['last_risk']` 并补充实时组合状态）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: 13 passed（11 + 2）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/tools/sim_portfolio.py ashare_review/tests/test_risk_engine.py
git commit -m "feat(risk): Vol180 接入（止损线读配置/开仓拦截/仓位缩放）"
```

---

### Task 5: ZTReplicaSimPortfolio 接入

**Files:**
- Modify: `ashare_review/tools/zt_replica_portfolio.py`
- Test: `ashare_review/tests/test_risk_engine.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_risk_engine.py

# ---------- Task 5: ZTReplica 接入 ----------

def test_zt_replica_stop_loss_config(tmp_path, monkeypatch):
    """默认 -5%：跌 4% 不止损；改配置 -3% 后跌 4% 触发"""
    from ashare_review.risk.store import RiskStore
    from ashare_review.tools.zt_replica_portfolio import ZTReplicaSimPortfolio
    path = str(tmp_path / 'risk.json')
    RiskStore(path).set('zt_replica', {})   # 默认 -5%
    monkeypatch.setenv('RISK_CONFIG', path)
    p = ZTReplicaSimPortfolio()
    p.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                 ('2026-08-12', 10.0, 9.6)]})   # -4%
    pos = {'buy_date': '2026-08-10', 'buy_price': 10.0, 'had_zt': False, 'highest_close': 10.0}
    sell = p._check_sell('600001', pos, '2026-08-12')
    assert sell is None                       # -4% > -5% → 不止损
    # 改 -3%
    RiskStore(path).set('zt_replica', {'stop_loss_pct': -3.0})
    p2 = ZTReplicaSimPortfolio()
    p2.tdx = FakeTdx2({'600001': [('2026-08-10', 10.0, 10.0), ('2026-08-11', 10.0, 10.0),
                                  ('2026-08-12', 10.0, 9.6)]})
    sell2 = p2._check_sell('600001', pos, '2026-08-12')
    assert sell2 is not None and '止损' in sell2['sell_reason']
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: FAIL — 行为不符

- [ ] **Step 3: 实现接入**

在 `zt_replica_portfolio.py` 修改 3 处：

**a) `__init__` 加 `self._risk = RiskStore()`**（仿 Task 4 a）

**b) 止损（`_check_sell` 的 `# ── 0. -5% 硬止损` 块）**：
```python
        # ── 0. 硬止损（读风控配置，默认 -5%） ──
        from ..risk.evaluate import stop_loss_pct
        stop = stop_loss_pct(self._risk.get('zt_replica')) / 100.0
        if buy_price > 0 and (close - buy_price) / buy_price <= stop:
            return {'sell_price': round(close, 2), 'sell_reason': f'🛑止损{abs(stop*100):.0f}%',
                    'days_held': trading_days}
```

**c) 买入段（run_daily 内 ~L642-660 与 ~L700-710）**，具体代码：

在买入信号处理前（`max_new = min(...)` 处）插入风控判定：

```python
        cfg = self._risk.get('zt_replica')
        holdings_val = sum(
            h.get('shares', 0) * (h.get('current_price', h.get('buy_price', 0)) or 0)
            for h in self._state['holding'].values()
        )
        hist_peak = INITIAL_CAPITAL
        for snap in self._state.get('portfolio_history', []):
            hist_peak = max(hist_peak, snap.get('total', 0) or 0)
        from ..risk.evaluate import evaluate
        from ..analysis.strategy_regime import live_diagnosis as _ld
        try:
            regime = _ld.get_regime_diagnosis().get('regime', '震荡观望') or '震荡观望'
        except Exception:
            regime = '震荡观望'
        risk = evaluate(cfg, {
            'positions': len(self._state['holding']) + len(self._state['ready']),
            'opened_today': 0,
            'total_value': self._state.get('cash', INITIAL_CAPITAL) + holdings_val,
            'history_peak': hist_peak,
        }, regime)
        if risk['blocked_reasons']:
            print(f"[ZTReplica] 风控拦截开仓: {'；'.join(risk['blocked_reasons'])}")
        self._state['last_risk'] = risk
        max_new = min(cfg['max_new_per_day'], available_slots)
```

把 `max_new` 循环内份额计算两处（~L657 与 ~L706）的 `INITIAL_CAPITAL * PER_POSITION_PCT` 改为：
```python
        shares = int(INITIAL_CAPITAL * (risk['suggested_size_pct'] / 100.0) / max(price, 0.01) / 100) * 100
```
（两处 price 分别为 sig['close'] 与 bp_actual，其余不变）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: 14 passed（13 + 1）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/tools/zt_replica_portfolio.py ashare_review/tests/test_risk_engine.py
git commit -m "feat(risk): ZTReplica 接入（止损线读配置/开仓拦截/仓位缩放）"
```

---

### Task 6: app.py 风控 API

**Files:**
- Modify: `ashare_review/web/app.py`
- Test: `ashare_review/tests/test_risk_engine.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_risk_engine.py

# ---------- Task 6: Web API ----------

def test_risk_config_api(tmp_path, monkeypatch):
    from ashare_review.risk import store as risk_store
    from ashare_review.web.app import app
    monkeypatch.setattr(risk_store, 'CONFIG_PATH', str(tmp_path / 'risk.json'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/api/risk/config')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['vol180']['stop_loss_pct'] == -6.0
    assert data['zt_replica']['stop_loss_pct'] == -5.0
    # 保存
    rv2 = c.post('/api/risk/config', json={'portfolio_id': 'vol180',
                                           'config': {'stop_loss_pct': -4.0}})
    assert rv2.status_code == 200
    rv3 = c.get('/api/risk/config')
    assert rv3.get_json()['vol180']['stop_loss_pct'] == -4.0
    # 非法 → 400
    rv4 = c.post('/api/risk/config', json={'portfolio_id': 'vol180',
                                           'config': {'stop_loss_pct': -50.0}})
    assert rv4.status_code == 400
    # 未知持仓 → 400
    rv5 = c.post('/api/risk/config', json={'portfolio_id': 'nope', 'config': {}})
    assert rv5.status_code == 400


def test_risk_status_api(tmp_path, monkeypatch):
    from ashare_review.risk import store as risk_store
    from ashare_review.web.app import app
    monkeypatch.setattr(risk_store, 'CONFIG_PATH', str(tmp_path / 'risk.json'))
    app.config['TESTING'] = True
    c = app.test_client()
    rv = c.get('/api/risk/status?portfolio=vol180')
    assert rv.status_code == 200
    data = rv.get_json()
    assert 'regime' in data and 'suggested_size_pct' in data
    assert 'drawdown_pct' in data and 'can_open' in data
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: FAIL — 404（路由未实现）

- [ ] **Step 3: 实现路由（app.py 末尾）**

```python
# ======================================================================
# 风控规则层（Risk Engine）
# ======================================================================

@app.route('/api/risk/config')
def api_risk_config():
    from ..risk.store import RiskStore
    store = RiskStore()
    return jsonify(store.get_all())


@app.route('/api/risk/config', methods=['POST'])
def api_risk_config_save():
    from ..risk.store import RiskStore
    data = request.get_json(silent=True) or {}
    portfolio_id = data.get('portfolio_id', '')
    config = data.get('config', {}) or {}
    try:
        RiskStore().set(portfolio_id, config)
        return jsonify({'ok': True})
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


def _portfolio_risk_state(portfolio_id: str) -> dict:
    """读取对应 portfolio 的 state 文件，计算风控判定所需的组合状态。"""
    import json as _json
    if portfolio_id == 'vol180':
        from ..tools.sim_portfolio import STATE_FILE, INITIAL_CAPITAL as _ic
    else:
        from ..tools.zt_replica_portfolio import STATE_FILE, INITIAL_CAPITAL as _ic
    state_data = {}
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state_data = _json.load(f)
    except Exception:
        pass
    holdings = state_data.get('holding', {}) or {}
    ready = state_data.get('ready', {}) or {}
    positions = len(holdings) + len(ready)
    pos_val = sum(
        h.get('shares', 0) * (h.get('current_price', h.get('buy_price', 0)) or 0)
        for h in holdings.values()
    )
    init_cap = state_data.get('initial_capital', _ic)
    total_value = state_data.get('cash', init_cap) + pos_val
    hist_peak = init_cap
    for snap in state_data.get('portfolio_history', []) or []:
        hist_peak = max(hist_peak, snap.get('total', 0) or 0)
    return {'positions': positions, 'opened_today': 0,
            'total_value': total_value, 'history_peak': hist_peak}


@app.route('/api/risk/status')
def api_risk_status():
    from ..risk.evaluate import evaluate
    from ..risk.store import RiskStore
    from ..analysis.strategy_regime import live_diagnosis as _ld
    portfolio_id = request.args.get('portfolio', 'vol180')
    if portfolio_id not in ('vol180', 'zt_replica'):
        return jsonify({'error': 'invalid portfolio'}), 400
    cfg = RiskStore().get(portfolio_id)
    try:
        regime = _ld.get_regime_diagnosis().get('regime', '震荡观望') or '震荡观望'
    except Exception:
        regime = '震荡观望'
    # ── 实时组合状态（读对应 portfolio 的 state 文件） ──
    state = _portfolio_risk_state(portfolio_id)
    risk = evaluate(cfg, state, regime)
    risk['regime'] = regime
    risk['portfolio'] = portfolio_id
    risk['positions'] = state['positions']
    risk['config'] = {k: v for k, v in cfg.items() if k != 'regime_scale'}
    return jsonify(risk)
```

> 注：status API 从两个 portfolio 的 state 文件读取真实组合状态（`_portfolio_risk_state`）；state 文件缺失时回退空组合（positions=0、total=初始资金）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py -v`
Expected: 16 passed（14 + 2）

- [ ] **Step 5: 提交**

```bash
git add ashare_review/web/app.py ashare_review/tests/test_risk_engine.py
git commit -m "feat(risk): 风控 API（配置 GET/POST + 状态）"
```

---

### Task 7: 持仓页风控卡片 + 编辑弹窗

**Files:**
- Modify: `ashare_review/web/templates/sim_portfolio.html`
- Modify: `ashare_review/web/templates/zt_replica.html`

- [ ] **Step 1: 在两个持仓页模板顶部插入风控卡片**

在页面 `<div class="content-area">` 之后、首个 section 之前插入（两页相同的卡片结构，portfolio 参数按页不同）：

```html
<!-- 🛡️ 风控状态（Risk Engine） -->
<div class="card" style="margin-bottom:14px;border-left:4px solid #4f46e5;">
    <div class="card-header">
        🛡️ 风控
        <button class="btn btn-secondary btn-sm" style="margin-left:auto;" onclick="openRiskEdit()">编辑规则</button>
    </div>
    <div class="card-body" style="display:flex;gap:24px;flex-wrap:wrap;align-items:center;">
        <div>
            <div style="font-size:.85em;color:#666;">当日行情</div>
            <div id="risk-regime" style="font-weight:700;">—</div>
        </div>
        <div>
            <div style="font-size:.85em;color:#666;">仓位缩放</div>
            <div id="risk-scale" style="font-weight:700;">—</div>
        </div>
        <div>
            <div style="font-size:.85em;color:#666;">建议单票仓位</div>
            <div id="risk-size" style="font-weight:700;">—</div>
        </div>
        <div style="flex:1;min-width:180px;">
            <div style="font-size:.85em;color:#666;">组合回撤</div>
            <div class="risk-dd-bar" style="height:8px;background:#e2e8f0;border-radius:4px;overflow:hidden;margin-top:4px;">
                <div id="risk-dd-fill" style="height:100%;width:0%;background:#059669;transition:width .4s;"></div>
            </div>
            <div style="font-size:.8em;color:#666;margin-top:2px;"><span id="risk-dd-text">—</span></div>
        </div>
        <div id="risk-status" style="font-weight:700;">—</div>
    </div>
</div>

<!-- 编辑规则弹窗 -->
<div id="risk-edit-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:99;align-items:center;justify-content:center;">
    <div style="background:#fff;border-radius:14px;padding:20px;width:520px;max-width:92vw;max-height:85vh;overflow-y:auto;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <strong>编辑风控规则</strong>
            <button class="btn btn-secondary btn-sm" onclick="closeRiskEdit()">✕</button>
        </div>
        <div id="risk-edit-form" style="display:flex;flex-direction:column;gap:10px;"></div>
        <div style="margin-top:14px;display:flex;gap:10px;justify-content:flex-end;">
            <button class="btn btn-secondary" onclick="closeRiskEdit()">取消</button>
            <button class="btn btn-primary" onclick="saveRiskConfig()">保存</button>
        </div>
    </div>
</div>
```

- [ ] **Step 2: 在模板 scripts 块追加 JS**

```html
<script>
var RISK_PORTFOLIO = 'vol180';   // zt_replica.html 里改为 'zt_replica'

function loadRiskStatus() {
    fetch('/api/risk/status?portfolio=' + RISK_PORTFOLIO)
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.error) return;
            document.getElementById('risk-regime').textContent = d.regime || '—';
            document.getElementById('risk-scale').textContent = '×' + d.regime_scale;
            document.getElementById('risk-size').textContent = d.suggested_size_pct + '%';
            var dd = Math.abs(d.drawdown_pct || 0);
            var breaker = d.config ? (d.config.drawdown_breaker_pct || 8) : 8;
            document.getElementById('risk-dd-text').textContent = dd.toFixed(1) + '% / 熔断 ' + breaker + '%';
            var fill = document.getElementById('risk-dd-fill');
            fill.style.width = Math.min(100, dd / breaker * 100) + '%';
            fill.style.background = d.can_open ? '#059669' : '#dc2626';
            var st = document.getElementById('risk-status');
            if (d.can_open) { st.textContent = '✅ 可开仓'; st.style.color = '#059669'; }
            else {
                st.textContent = '⛔ ' + ((d.blocked_reasons || [])[0] || '被拦截');
                st.style.color = '#dc2626';
            }
        });
}

function openRiskEdit() {
    fetch('/api/risk/config')
        .then(function (r) { return r.json(); })
        .then(function (d) {
            var cfg = d[RISK_PORTFOLIO] || {};
            var box = document.getElementById('risk-edit-form');
            box.innerHTML = '';
            var fields = [
                ['stop_loss_pct', '止损线(%)', cfg.stop_loss_pct],
                ['per_position_pct', '单票仓位(%)', cfg.per_position_pct],
                ['max_positions', '最大持仓', cfg.max_positions],
                ['max_new_per_day', '每日最大新开', cfg.max_new_per_day],
                ['drawdown_breaker_pct', '回撤熔断(%)', cfg.drawdown_breaker_pct],
                ['drawdown_recover_pct', '恢复线(%)', cfg.drawdown_recover_pct]
            ];
            fields.forEach(function (f) {
                var row = document.createElement('div');
                row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;';
                row.innerHTML = '<span style="font-size:.9em;">' + f[1] + '</span>' +
                    '<input data-rc="' + f[0] + '" type="number" step="0.5" value="' + f[2] + '" style="width:120px;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;">';
                box.appendChild(row);
            });
            // regime 系数表
            var rs = cfg.regime_scale || {};
            var title = document.createElement('div');
            title.style.cssText = 'margin-top:6px;font-size:.9em;color:#666;';
            title.textContent = '行情仓位缩放系数（0=禁开仓）';
            box.appendChild(title);
            Object.keys(rs).forEach(function (r) {
                var row = document.createElement('div');
                row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;';
                row.innerHTML = '<span style="font-size:.9em;">' + r + '</span>' +
                    '<input data-rc="regime:' + r + '" type="number" step="0.1" value="' + rs[r] + '" style="width:120px;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;">';
                box.appendChild(row);
            });
            document.getElementById('risk-edit-modal').style.display = 'flex';
        });
}

function saveRiskConfig() {
    var cfg = {};
    document.querySelectorAll('#risk-edit-form [data-rc]').forEach(function (el) {
        var key = el.dataset.rc;
        if (key.indexOf('regime:') === 0) {
            if (!cfg.regime_scale) cfg.regime_scale = {};
            cfg.regime_scale[key.slice(7)] = Number(el.value);
        } else {
            cfg[key] = Number(el.value);
        }
    });
    fetch('/api/risk/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({portfolio_id: RISK_PORTFOLIO, config: cfg})
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { alert('保存失败: ' + (d.error || '')); return; }
        closeRiskEdit();
        loadRiskStatus();
        alert('规则已保存，下次买入/卖出生效');
      });
}

function closeRiskEdit() { document.getElementById('risk-edit-modal').style.display = 'none'; }

document.addEventListener('DOMContentLoaded', loadRiskStatus);
</script>
```

- [ ] **Step 3: 冒烟测试**

Run: `python -m pytest ashare_review/tests/test_risk_engine.py::test_risk_config_api -v` + 手动 GET /sim_portfolio 与 /zt_replica 页面（或用 Flask test_client 验证两页 200）
Expected: PASS（API 与页面正常；卡片 JS 错误不影响页面渲染）

- [ ] **Step 4: 提交**

```bash
git add ashare_review/web/templates/sim_portfolio.html ashare_review/web/templates/zt_replica.html
git commit -m "feat(risk): 持仓页风控卡片 + 编辑弹窗"
```

---

### Task 8: 全量回归 + 真实冒烟 + 推送

- [ ] **Step 1: 全量测试**

Run: `python -m pytest ashare_review/tests -q`
Expected: 全部通过（99 既有 + 16 新增 = 115 passed）

- [ ] **Step 2: 真实冒烟（默认配置行为不变）**

Run:
```bash
python -c "from ashare_review.tools.sim_portfolio import Vol180SimPortfolio; from ashare_review.tools.zt_replica_portfolio import ZTReplicaSimPortfolio; p1 = Vol180SimPortfolio(); print('vol180 cfg stop:', p1._risk.get('vol180')['stop_loss_pct']); p2 = ZTReplicaSimPortfolio(); print('zt cfg stop:', p2._risk.get('zt_replica')['stop_loss_pct'])"
python -c "from ashare_review.risk.evaluate import evaluate; from ashare_review.risk.store import RiskStore; cfg = RiskStore().get('vol180'); r = evaluate(cfg, {'positions': 0, 'opened_today': 0, 'total_value': 1000000, 'history_peak': 1000000}, '强势趋势'); print('evaluate:', r['can_open'], r['suggested_size_pct'])"
```
Expected: 配置读取正常（-6/-5），evaluate 正常开仓、建议 10%

再跑一次真实日扫描（不重建池）确认不崩：
```bash
python -c "from ashare_review.tools.sim_portfolio import Vol180SimPortfolio; r = Vol180SimPortfolio().run_daily(); print('scan ok:', {k: r.get(k) for k in ('buys','sells','watch')})"
```
Expected: 正常返回（buys/sells/watch 数值），无异常

- [ ] **Step 3: 改配置验证生效（止损线）**

Run:
```bash
python -c "from ashare_review.risk.store import RiskStore; RiskStore().set('vol180', {'stop_loss_pct': -4.0}); print('saved', RiskStore().get('vol180')['stop_loss_pct']); RiskStore().set('vol180', {'stop_loss_pct': -6.0}); print('restored', RiskStore().get('vol180')['stop_loss_pct'])"
```
Expected: 保存 -4 读取 -4；还原 -6 读取 -6（改配置→生效→还原，不残留）

- [ ] **Step 4: 提交推送**

```bash
git status   # 确认只含本功能文件（runtime 状态文件不提交）
git add ashare_review/risk ashare_review/tools/sim_portfolio.py ashare_review/tools/zt_replica_portfolio.py ashare_review/web/app.py ashare_review/web/templates/sim_portfolio.html ashare_review/web/templates/zt_replica.html ashare_review/tests/test_risk_engine.py
git commit -m "feat(risk): 风控规则层完整交付"
git push origin main
```
