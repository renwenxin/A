"""筹码集中度因子"""
import pandas as pd
import numpy as np
from ...base import AlphaFactor


class PriceConcentration(AlphaFactor):
    """价格集中度: 1 - (high_20d - low_20d) / close，值越大越集中"""
    def __init__(self):
        super().__init__('CUSTOM_003', '价格集中度', 'price_concentration', 'volatility', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        h = df['high'].rolling(20).max()
        l = df['low'].rolling(20).min()
        rng_ratio = (h - l) / df['close'].replace(0, 1)
        return 1 - rng_ratio.clip(0, 2)
