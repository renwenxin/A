"""GTJA191 动量类因子 — 精选6个"""
import pandas as pd
import numpy as np
from ...base import AlphaFactor


class GTJA_Momentum_5D(AlphaFactor):
    """5日动量: (close - close_5d_ago) / close_5d_ago"""
    def __init__(self):
        super().__init__('GTJA_001', '5日动量', 'mom_5d', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df['close'].pct_change(5)


class GTJA_Momentum_10D(AlphaFactor):
    """10日动量"""
    def __init__(self):
        super().__init__('GTJA_002', '10日动量', 'mom_10d', 'momentum', 10, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df['close'].pct_change(10)


class GTJA_Momentum_20D(AlphaFactor):
    """20日动量"""
    def __init__(self):
        super().__init__('GTJA_003', '20日动量', 'mom_20d', 'momentum', 20, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df['close'].pct_change(20)


class GTJA_MA_Deviation(AlphaFactor):
    """均线偏离度: (close - ma20) / ma20"""
    def __init__(self):
        super().__init__('GTJA_005', '均线偏离度(20日)', 'ma_dev_20', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        ma20 = df['close'].rolling(20).mean()
        return (df['close'] - ma20) / ma20


class GTJA_Price_Position(AlphaFactor):
    """价格相对位置: (close - low_20d) / (high_20d - low_20d)"""
    def __init__(self):
        super().__init__('GTJA_012', '价格相对位置(20日)', 'price_pos_20', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        h = df['high'].rolling(20).max()
        l = df['low'].rolling(20).min()
        rng = h - l
        return np.where(rng > 0, (df['close'] - l) / rng, 0.5)


class GTJA_RSI(AlphaFactor):
    """RSI指标: 14日相对强弱"""
    def __init__(self):
        super().__init__('GTJA_018', 'RSI(14日)', 'rsi_14', 'momentum', 5, 'gtja191')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - (100 / (1 + rs))


def register_gtja_momentum(registry):
    for cls in [GTJA_Momentum_5D, GTJA_Momentum_10D, GTJA_Momentum_20D,
                 GTJA_MA_Deviation, GTJA_Price_Position, GTJA_RSI]:
        registry.register(cls())
