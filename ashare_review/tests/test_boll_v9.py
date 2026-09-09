# ashare_review/tests/test_boll_v9.py
"""BOLL V9 移植 — 通达信原语与 XG 信号测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
import pytest

from ashare_review.analysis.boll_v9 import (
    calc_boll, tdx_rsi, tdx_mfi, upnday, tdx_count_ref,
    tdx_cross, build_signal_row, scan_universe, _read_index,
    _calc_absorption_zi, _calc_kdj_like,
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


def test_signal_row_works_with_misaligned_index_dates():
    """fix-3 回归：index_df 与 df 日期不完全重叠（错位/停牌）时不崩溃且输出正常。

    指数只覆盖个股后半段（模拟上市早于指数采样、停牌错位）→ 缺失日对齐为 NaN，
    zn=za>=zb 对 NaN 为 False，不抛错、不把未来数据算进去。
    """
    df = _mk(n=260, seed=7)
    # 指数日期从个股中间开始（前 40 根对齐不到），长度与个股不同
    idx = _mk(n=220, seed=3).rename(columns={'close': 'idx_close',
                                             'high': 'idx_high',
                                             'low': 'idx_low'})
    idx['trade_date'] = pd.date_range('2026-03-01', periods=220).strftime('%Y-%m-%d')
    row = build_signal_row(df, index_df=idx, capital_hands=5.5e7)
    for k in ('score', 'yca', 'ycb', 'xg'):
        assert k in row
    assert isinstance(row['score'], float)
    assert row['xg'] in (True, False)


def test_read_index_truncates_by_trade_date():
    """fix-3 回归：_read_index 按 trade_date 截断指数，历史回扫不掺未来数据。"""
    class _FakeTdx:
        def read_daily(self, code, market):
            df = _mk(n=120, seed=3)
            return df.rename(columns={'close': 'idx_close', 'high': 'idx_high',
                                      'low': 'idx_low'})

    idx = _read_index(_FakeTdx(), trade_date='2026-03-01')
    assert idx is not None
    max_date = idx['trade_date'].astype(str).str[:10].max()
    assert max_date <= '2026-03-01'
    # 无 trade_date → 不截断、原样返回（_mk(n=120) 生成 120 行）
    idx_all = _read_index(_FakeTdx())
    assert idx_all is not None
    assert len(idx_all) == 120


def test_read_index_failure_returns_none():
    """指数读取异常 → None（调用方中性化，不崩溃）"""
    class _BrokenTdx:
        def read_daily(self, code, market):
            raise RuntimeError('no data dir')

    assert _read_index(_BrokenTdx(), trade_date='2026-03-01') is None


# ═══════════════ O2: scan_universe 冒烟（mock 小宇宙） ═══════════════

class _FakeScanTdx:
    """3 只股票小宇宙：2 只正常（各 260 根日线），1 只 read_daily 抛异常。"""

    def __init__(self):
        self._ok = {'600001': _mk(n=260, seed=1), '600002': _mk(n=260, seed=2)}

    def list_stocks(self):
        return [('600001', 'sh'), ('600002', 'sh'), ('600003', 'sz')]

    def read_daily(self, code, market):
        if code == '000001':      # 指数（INDEX 参照系）
            df = _mk(n=260, seed=99)
            return df.rename(columns={'close': 'idx_close', 'high': 'idx_high',
                                      'low': 'idx_low'})
        if code not in self._ok:
            raise FileNotFoundError(code)   # 600003 读不到 → 验证异常被吞
        return self._ok[code]


def test_scan_universe_smoke_no_crash_and_structure(monkeypatch):
    """扫描不因单股异常崩溃；返回 list 且元素含 code/score/close。

    确定性: monkeypatch build_signal_row —— 第一只正常股(600001)给 xg=True，
    其余给 xg=False → 断言恰好 1 条输出、字段映射正确、异常股被吞。
    """
    import ashare_review.analysis.boll_v9 as bv
    monkeypatch.setattr(bv, 'get_capital_hands', lambda code, default=None: 5.5e7)
    monkeypatch.setattr(bv, '_name_of', lambda tdx, code: f'股票{code}')
    calls = {'n': 0}

    def _fake_build(df, index_df=None, capital_hands=None):
        calls['n'] += 1
        if calls['n'] == 1:      # 第一只（600001）出信号
            return {'xg': True, 'score': 80.0, 'close': 10.5, 'lb': 9.0,
                    'dist_to_lb': -1.0, 'rsi': 30.0, 'mfi': 25.0}
        return {'xg': False, 'score': 0.0, 'close': 10.0, 'lb': 9.0,
                'dist_to_lb': 0.0, 'rsi': 50.0, 'mfi': 50.0}

    monkeypatch.setattr(bv, 'build_signal_row', _fake_build)

    out = scan_universe(trade_date=None, tdx=_FakeScanTdx())
    assert isinstance(out, list)
    assert len(out) == 1
    r = out[0]
    assert r['code'] == '600001'
    assert r['name'] == '股票600001'
    assert r['score'] == 80.0 and r['close'] == 10.5
    for key in ('sector', 'dist_to_lb', 'rsi', 'mfi'):
        assert key in r


def test_scan_universe_empty_universe_returns_empty(monkeypatch):
    """空股票池 → 返回空 list 不炸。"""
    import ashare_review.analysis.boll_v9 as bv

    class _EmptyTdx:
        def list_stocks(self):
            return []

        def read_daily(self, code, market):
            raise FileNotFoundError(code)

    monkeypatch.setattr(bv, 'get_capital_hands', lambda code, default=None: 5.5e7)
    assert scan_universe(trade_date=None, tdx=_EmptyTdx()) == []


# ═══════════════ O3: ZI / KDJ 抽出单元定向测试 ═══════════════

def test_absorption_zi_zero_without_new_lows():
    """无 19 日新低（LOW 单调上行）→ 吸筹 ZI 段门控归零，不误触发。

    公式段: ZF:=LLV(LOW,19); ZI:=EMA(IF(LOW<=ZF,(ZE+ZG*2)/2,0),3)/618*ZH。
    """
    n = 120
    df = _mk(n=n)
    df['low'] = np.linspace(5.0, 15.0, n)          # LOW 严格上行 → LOW>ZF 恒成立
    df['high'] = df['low'] + 1.0
    ze = pd.Series(np.full(n, 100.0), index=df.index)    # 高吸筹强度输入
    zg = pd.Series(ze).rolling(13).max()
    zh = pd.Series(np.where(df['close'].rolling(34).mean() > 0, 1.0, 0.0), index=df.index)
    zi = _calc_absorption_zi(df, ze, zg, zh)
    assert abs(float(zi.iloc[-1])) < 1e-6          # EMA 残留随递推衰减至 ~0


def test_kdj_like_uses_low_llv_not_close():
    """YAK 的 27 日随机位置用 LOW 的 LLV（公式 CLOSE-LLV(LOW,27)），不是 CLOSE 的 LLV。"""
    n = 120
    close = np.linspace(10.0, 20.0, n)
    df = pd.DataFrame({
        'trade_date': pd.date_range('2026-01-01', periods=n).strftime('%Y-%m-%d'),
        'open': close,
        'high': close + 1.0,
        'low': close - 1.0,
        'close': close,
        'volume': np.full(n, 200000.0),
    })
    kdj = _calc_kdj_like(df)
    # 错误语义（LLV 取 CLOSE 而非 LOW）的结果应与正确实现不同
    llv_close = pd.Series(close).rolling(27).min()
    hhv27 = pd.Series(df['high']).rolling(27).max()
    wrong = (pd.Series(close) - llv_close) / (hhv27 - llv_close).replace(0, np.nan) * 100
    tail = kdj.iloc[-30:].dropna().values
    tail_wrong = wrong.iloc[-30:].dropna().values
    assert not np.allclose(tail, tail_wrong)
