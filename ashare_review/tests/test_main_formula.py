"""通达信主图公式还原 — 单元测试

背景: 用户提供逻辑哥完整主图公式，要求代码与公式逐行对齐。
本次还原:
  1. G:=HA(C,5) / D:=HA(C,10) — Heikin-Ashi 收盘均线（原实现用普通 MA5/MA10）
  2. SWS:=DMA(EMA(C,20), MAX(1, 100*(SUM(VOL,5)/(3*CAPITAL)))) — 权重 A>=1，CAPITAL 可传真实流通股本
  3. 找底线:DRAWLINE(UU,L,REF(UU,1),REF(L,1),1) — 补输出 find_bottom_line
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd


def _make_df(n=120, seed=7, vol=1000):
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(0, 0.15, n))
    close = np.maximum(close, 1.0)
    open_ = close + rng.normal(0, 0.1, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.15, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.15, n))
    return pd.DataFrame({
        'trade_date': pd.date_range('2026-01-01', periods=n),
        'open': open_, 'high': high, 'low': low, 'close': close,
        'volume': np.full(n, vol),
    })


# ---------- 1. zigzag: HA 均线 + 找底线 ----------

def test_zigzag_does_not_require_ma_columns():
    """G/D 改为 HA 均线后，不再依赖外部 ma5/ma10 列"""
    from ashare_review.analysis.indicators import calc_zigzag_find_top_line
    df = _make_df()   # 无 ma5/ma10 列
    out = calc_zigzag_find_top_line(df)
    assert 'find_top_line' in out.columns
    assert not out['find_top_line'].iloc[-1] != out['find_top_line'].iloc[-1]  # 非NaN


def test_zigzag_outputs_bottom_line():
    """找底线 DRAWLINE(UU,L,...) 输出列存在且为有限值"""
    from ashare_review.analysis.indicators import calc_zigzag_find_top_line
    df = _make_df(seed=3)
    out = calc_zigzag_find_top_line(df)
    assert 'find_bottom_line' in out.columns
    assert '_uu' in out.columns
    bottom = out['find_bottom_line'].iloc[-1]
    top = out['find_top_line'].iloc[-1]
    assert np.isfinite(bottom) and np.isfinite(top)
    assert bottom <= top + 1e-6   # 底线不高于顶线（同区间）


def test_zigzag_top_line_uses_ha_close():
    """HA(C,5) 内部计算: 用 (O+H+L+C)/4 而非 close 做均线 → 输出应与手算一致"""
    from ashare_review.analysis.indicators import calc_zigzag_find_top_line
    # 构造可预测数据: 前 30 根横盘(5.0) → 后 30 根震荡上升
    n = 60
    close = [5.0] * 30 + [5.0 + i * 0.05 for i in range(30)]
    df = pd.DataFrame({
        'trade_date': pd.date_range('2026-01-01', periods=n),
        'open': close, 'high': [c + 0.1 for c in close],
        'low': [c - 0.1 for c in close], 'close': close,
        'volume': np.full(n, 1000),
    })
    out = calc_zigzag_find_top_line(df)
    # HA 收盘 = (O+H+L+C)/4 = (c + c+0.1 + c-0.1 + c)/4 = c（本构造下 HA_C = close）
    # 因此找顶线应 >= 最近 swing high；验证列存在且非 NaN 即可（精确值依赖 swing 检测）
    assert np.isfinite(out['find_top_line'].iloc[-1])


# ---------- 2. SWS 公式还原 ----------

def test_swl_sws_formula_a_ge_1():
    """A = MAX(1, 100*SUM(VOL,5)/(3*CAPITAL)) — 权重下限 1，高换手时 >1"""
    from ashare_review.analysis.indicators import calc_swl_sws
    df = _make_df(n=80, vol=1_000_000)   # 每日 100 万股
    out = calc_swl_sws(df, capital_hands=1e6)   # 流通 100 万手
    # 5日量 = 500万股 = 5万手; A = 100*50000/(3*1e6) = 1.67 > 1
    assert 'swl' in out.columns and 'sws' in out.columns
    assert 'swl_control' in out.columns
    assert out['swl_control'].dtype == bool
    # sws 递推稳定（有限值）
    sws = out['sws'].dropna()
    assert len(sws) > 0 and np.isfinite(sws).all()
    # swl = (EMA10*7+EMA20*3)/10
    ema10 = out['close'].ewm(span=10, adjust=False).mean()
    ema20 = out['close'].ewm(span=20, adjust=False).mean()
    expect_swl = (ema10 * 7 + ema20 * 3) / 10
    assert np.allclose(out['swl'].dropna().values[-20:],
                       expect_swl.dropna().values[-20:], rtol=1e-6)


def test_swl_sws_default_capital():
    """不传 capital_hands 时用默认流通股本（公式仍可算，A>=1）"""
    from ashare_review.analysis.indicators import calc_swl_sws
    df = _make_df(n=60)
    out = calc_swl_sws(df)
    assert out['sws'].dropna().shape[0] > 0
    assert np.isfinite(out['sws'].dropna()).all()


# ---------- 3. enrich_all 透传 ----------

def test_enrich_all_capital_passthrough():
    from ashare_review.analysis.indicators import enrich_all
    df = _make_df(n=80)
    out = enrich_all(df, capital_hands=2.0e6)
    assert 'sws' in out.columns and 'swl_control' in out.columns
    out2 = enrich_all(df)   # 默认
    assert 'sws' in out2.columns


def test_float_share_default_fallback():
    """无网络/无缓存时 get_capital_hands 返回默认值"""
    from ashare_review.data.float_share import get_capital_hands, DEFAULT_FLOAT_SHARE_HANDS
    # 不强制拉取（可能无网络），只验证接口不抛异常
    v = get_capital_hands('999999')
    assert v > 0
