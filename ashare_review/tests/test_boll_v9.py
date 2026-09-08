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
    # BOLL 头部 period-1 行为滚动窗口预热 NaN（通达信同样无值）→ 只在有效行断言上下轨关系
    valid = out['boll'].notna()
    assert (out.loc[valid, 'ub'] >= out.loc[valid, 'boll']).all()
    assert (out.loc[valid, 'lb'] <= out.loc[valid, 'boll']).all()


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
