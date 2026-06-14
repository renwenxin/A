"""技术指标计算"""
import pandas as pd
import numpy as np

def calc_ma(df: pd.DataFrame, periods: list) -> pd.DataFrame:
    """计算移动平均线"""
    for p in periods:
        df[f'ma{p}'] = df['close'].rolling(window=p).mean()
    return df

def calc_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    """MACD指标"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd_dif'] = ema_fast - ema_slow
    df['macd_dea'] = df['macd_dif'].ewm(span=signal, adjust=False).mean()
    df['macd_bar'] = 2 * (df['macd_dif'] - df['macd_dea'])
    return df

def calc_ma_converge(df: pd.DataFrame, short=60, long=89, threshold=0.03) -> pd.DataFrame:
    """检测60/89日均线是否粘合 (价差<3%)"""
    s = df[f'ma{short}']
    l = df[f'ma{long}']
    diff_pct = abs(s - l) / l
    df['ma60_89_converge'] = diff_pct < threshold
    df['ma60_89_slope_up'] = (s.diff(3) > 0) & (l.diff(3) > 0)
    return df

def calc_volume_ratio(df: pd.DataFrame, period=5) -> pd.DataFrame:
    """量比: 当日成交量 / 前N日均量"""
    df['vol_ma5'] = df['volume'].rolling(window=period).mean()
    df['volume_ratio'] = df['volume'] / df['vol_ma5']
    return df

def calc_daily_change(df: pd.DataFrame) -> pd.DataFrame:
    """涨跌幅"""
    df['change_pct'] = df['close'].pct_change() * 100
    return df

def calc_amplitude(df: pd.DataFrame) -> pd.DataFrame:
    """振幅"""
    df['amplitude'] = (df['high'] - df['low']) / df['close'].shift(1) * 100
    return df

def enrich_all(df: pd.DataFrame) -> pd.DataFrame:
    """一键补全所有指标"""
    df = calc_ma(df, [5, 10, 20, 60, 89, 250])
    df = calc_macd(df)
    df = calc_ma_converge(df)
    df = calc_volume_ratio(df)
    df = calc_daily_change(df)
    df = calc_amplitude(df)
    return df
