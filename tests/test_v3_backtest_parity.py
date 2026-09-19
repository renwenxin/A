"""回测引擎与实盘引擎 V3 卖出阈值一致（防口径裂）

两层保障：
1. 常量层：-8% 止损 / 20 交易日兜底 / MA13 趋势线在三个模块取值一致；
2. 行为层：同一份合成日线 + 同一 buy_date/check_date 驱动
   sim_portfolio.Vol180SimPortfolio（实盘）、v3_backtest、v4_backtest 三个引擎，
   断言卖出原因分类一致。常量一致但分支顺序/口径改回 -6% 或移动止盈时，
   只有这一层能抓住。
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ashare_review.tools import sim_portfolio as sp          # noqa: E402
from ashare_review.analysis import v3_backtest as bt3        # noqa: E402
from ashare_review.analysis import v4_backtest as bt4        # noqa: E402
from ashare_review.utils.calendar import TradingCalendar     # noqa: E402

CODE = '600000'      # 主板 → 涨停阈值 9.5%
BUY_PRICE = 10.0
ANCHOR = date(2026, 7, 1)    # 交易日历缓存（ashare_review/data/trading_calendar.json）覆盖 2026 全年


def test_stop_loss_and_hold_days_aligned():
    assert bt3.V3_STOP_LOSS_PCT == sp.V3_STOP_LOSS_PCT == -8.0
    assert bt4.V3_STOP_LOSS_PCT == sp.V3_STOP_LOSS_PCT == -8.0
    assert bt3.V3_MAX_HOLD_DAYS == sp.V3_MAX_HOLD_DAYS == 20
    assert bt4.V3_MAX_HOLD_DAYS == sp.V3_MAX_HOLD_DAYS == 20


def test_trend_line_ma_aligned():
    assert bt3.TREND_LINE_MA == bt4.TREND_LINE_MA == sp.TREND_LINE_MA == 13


def _trading_day_offset(anchor: date, n: int) -> date:
    """anchor 起第 n 个交易日（含 anchor）。

    与三个引擎共用的 TradingCalendar.trading_days_between 同一口径，
    因此按此选取 buy_date/check_date 时各引擎的 days_held 必然可比。
    """
    cal = TradingCalendar()
    cal._load()
    d = anchor
    count = 0
    for _ in range(400):
        if cal.is_trading_day(d):
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    raise AssertionError(f'交易日历中找不到 {anchor} 之后的第 {n} 个交易日')


BUY_DATE = _trading_day_offset(ANCHOR, 1)        # 建仓日（也是首根基线K线）
EARLY_DATE = _trading_day_offset(BUY_DATE, 3)    # 短持有：3 个交易日
CAP_DATE = _trading_day_offset(BUY_DATE, 20)     # 硬兜底：正好 20 个交易日


@pytest.fixture(scope='module')
def engines():
    # 注：v4_backtest 模块内的回测类历史上沿用 V3Backtest 这一名字
    return bt3.V3Backtest(), bt4.V3Backtest()


def _frame(check_date: date, close: float, open_=None, low=None,
           volume: int = 2_000_000) -> pd.DataFrame:
    """合成日线：基线K线 + check_date 当日K线。

    三个引擎都只看「截至 check_date 的最后一根」，因此两根足够；
    日线表结构按回测的 df_full 列名给出（trade_date 为 Timestamp）。
    """
    open_ = close if open_ is None else open_
    low = min(open_, close) if low is None else low
    bars = [
        (BUY_DATE, BUY_PRICE, BUY_PRICE, BUY_PRICE, BUY_PRICE, 1_000_000),
        (check_date, open_, max(open_, close), low, close, volume),
    ]
    df = pd.DataFrame(bars, columns=['trade_date', 'open', 'high', 'low',
                                     'close', 'volume'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


def _live_signal(monkeypatch, frame, check_date, trend_line, had_zt, awaiting):
    """实盘引擎：_read_sell_df 走本地（已联网）日线，这里注入合成帧。"""
    p = sp.Vol180SimPortfolio()
    monkeypatch.setattr(p, '_read_sell_df',
                        lambda code, up_to_date=None: frame.copy())
    position = {
        'buy_date': BUY_DATE.strftime('%Y-%m-%d'),
        'buy_price': BUY_PRICE,
        'trend_line': trend_line,
        'had_zt': had_zt,
        'awaiting_reversal': awaiting,
    }
    return p._check_sell_v3(CODE, position, check_date.strftime('%Y-%m-%d'))


def _bt_signal(engine, frame, check_date, trend_line, had_zt, awaiting):
    hold = {
        'buy_date': BUY_DATE,
        'buy_price': BUY_PRICE,
        'trend_line': trend_line,
        'had_zt': had_zt,
        'awaiting_reversal': awaiting,
    }
    return engine._check_sell_v3(CODE, hold, check_date, frame.copy())


def _category(signal):
    """按前缀归一化卖出原因：破趋势线9.50 / 止损8% / 止损-8% / 持有20天到期。"""
    if not signal:
        return None
    reason = signal.get('sell_reason')
    if not reason:
        return None
    for bucket in ('破趋势线', '止损', '到期', '断板'):
        if bucket in reason:
            return bucket
    raise AssertionError(f'未归类的卖出原因: {reason!r}')


SCENARIOS = [
    pytest.param(dict(check_date=EARLY_DATE, close=9.50, trend_line=10.00),
                 '破趋势线', id='trend_break'),
    pytest.param(dict(check_date=EARLY_DATE, close=9.10, trend_line=1.00),
                 '止损', id='floor_minus9pct'),
    pytest.param(dict(check_date=CAP_DATE, close=10.05, trend_line=9.80,
                      had_zt=True, awaiting_reversal=True),
                 '到期', id='hold_cap_20d'),
    pytest.param(dict(check_date=EARLY_DATE, close=10.20, open_=10.00, low=10.00,
                      trend_line=9.90),
                 None, id='normal_hold'),
]


@pytest.mark.parametrize('scenario,expected', SCENARIOS)
def test_sell_reason_parity_across_engines(monkeypatch, engines, scenario, expected):
    bt3_engine, bt4_engine = engines
    check_date = scenario['check_date']
    frame = _frame(check_date, scenario['close'],
                   open_=scenario.get('open_'), low=scenario.get('low'))
    kwargs = dict(trend_line=scenario['trend_line'],
                  had_zt=scenario.get('had_zt', False),
                  awaiting=scenario.get('awaiting_reversal', False))

    signals = {
        'live': _live_signal(monkeypatch, frame, check_date, **kwargs),
        'v3': _bt_signal(bt3_engine, frame, check_date, **kwargs),
        'v4': _bt_signal(bt4_engine, frame, check_date, **kwargs),
    }
    cats = {name: _category(sig) for name, sig in signals.items()}

    # 三引擎结果一致，且等于该场景的预期口径
    assert cats['v3'] == cats['v4'] == cats['live'], f'口径不一致: {cats}'
    assert cats['live'] == expected, f'与预期卖出分类不符: {cats}'

    # 有信号时 days_held 也必须一致；20日硬兜底场景必须真由兜底触发
    days = {name: sig['days_held'] for name, sig in signals.items() if sig}
    if days:
        assert len(set(days.values())) == 1, f'持有天数不一致: {days}'
    if expected == '到期':
        assert all(d >= sp.V3_MAX_HOLD_DAYS for d in days.values()), days
