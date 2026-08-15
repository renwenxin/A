"""龙哥五条件因子 - MA/价格位置

条件4: 价格在新高或新高附近 → CUSTOM_008
同花顺: 10日均线>20日均线 → CUSTOM_004
"""

import pandas as pd
import numpy as np
from ...base import AlphaFactor


class MABullAlignment(AlphaFactor):
    """同花顺条件: 10日均线价格大于20日均线价格

    文档原话：「10日均线价格大于20日均线价格」
    直接用 MA10/MA20 - 1 量化，正值=多头，负值=空头。
    """
    def __init__(self):
        super().__init__('CUSTOM_004', 'MA10>MA20强度', 'ma_bull', 'momentum', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        close = df['close']
        ma10 = df['ma10'] if 'ma10' in df.columns else close.rolling(10).mean()
        ma20 = df['ma20'] if 'ma20' in df.columns else close.rolling(20).mean()
        ratio = ma10 / ma20.replace(0, np.nan) - 1  # 正=MA10在上, 负=MA10在下
        # 映射到0~1: -5%→0, 0%→0.5, +5%→1.0
        return (ratio / 0.1 + 0.5).clip(0, 1).fillna(0.5)


class NewHighProximity(AlphaFactor):
    """条件4: 价格在新高或者新高附近

    文档原话：「价格在新高或者新高附近，这样的图形天然的会吸引做多资金关注」
    close / 近120日最高价，值越接近1.0 → 越接近新高。
    """
    def __init__(self):
        super().__init__('CUSTOM_008', '新高接近度', 'new_high_prox', 'momentum', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        close = df['close']
        max_high_120 = df['high'].rolling(120).max()
        ratio = close / max_high_120.replace(0, np.nan)
        # 0.9以下低分，0.95~1.0高分
        return ratio.clip(0.8, 1.0).pipe(lambda x: (x - 0.8) / 0.2).fillna(0.5)
