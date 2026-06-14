import pandas as pd
import numpy as np
from ashare_review.analysis.indicators import calc_ma, calc_macd, calc_ma_converge

def make_df(prices):
    return pd.DataFrame({'close': prices, 'open': prices, 'high': prices, 'low': prices, 'volume': [100000]*len(prices)})

def test_calc_ma():
    df = make_df([10, 12, 11, 13, 14, 15, 16, 15, 14, 13])
    df = calc_ma(df, [5])
    assert 'ma5' in df.columns
    assert abs(df['ma5'].iloc[-1] - 14.6) < 0.01

def test_calc_macd():
    closes = [10.0] * 30 + [10.5]*5 + [11.0]*5  # 上涨趋势
    df = make_df(closes)
    df = calc_macd(df)
    assert 'macd_dif' in df.columns
    assert 'macd_dea' in df.columns
    assert 'macd_bar' in df.columns

def test_calc_ma_converge():
    closes = [10.0]*30 + [10.05]*10 + [10.02]*5  # 横盘
    df = make_df(closes)
    df = calc_ma(df, [60, 89])
    df['ma60'] = [10.0]*35 + [10.03]*10
    df['ma89'] = [10.0]*35 + [10.04]*10
    df = calc_ma_converge(df)
    assert 'ma60_89_converge' in df.columns
