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
    p = _p(monkeypatch, _df(close=9.1, low=9.0))     # -9% ≤ -8%
    sig = p._check_sell_v3('600001', _pos(trend_line=8.0), '2026-09-02')
    assert sig and '止损8%' in sig['sell_reason']


def test_no_trailing_take_profit_anymore():
    """取消移动止盈：从最高 12 回落到 11 仍不卖。"""
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


def test_twenty_day_cap_applies_to_zt_positions():
    """连板票同样受 20 日兜底（防死拿）：had_zt 且已持有 20 交易日 → 到期卖出。"""
    import pytest
    monkeypatch = pytest.MonkeyPatch()
    p = _p(monkeypatch, _df(close=11.0, low=10.9))
    pos = _pos(had_zt=True, awaiting_reversal=True, trend_line=9.5,
               reversal_day_close=10.5, reversal_day_vol=100, buy_date='2026-08-04')
    sig = p._check_sell_v3('600001', pos, '2026-09-02')
    assert sig and '到期' in sig['sell_reason']
