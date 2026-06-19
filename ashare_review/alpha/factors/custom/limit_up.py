"""涨停基因因子"""
import os
import struct
import pandas as pd
import numpy as np
from ...base import AlphaFactor


class LimitUpGene(AlphaFactor):
    """涨停基因: 近250日涨停次数 / 250"""
    def __init__(self):
        super().__init__('CUSTOM_001', '涨停基因', 'limit_up_gene', 'liquidity', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        # 此因子需要涨停次数数据，通常由 BaseScreener._count_limit_ups 提供
        # 这里返回基于日线的简化计算
        threshold = 0.099  # 默认主板10%
        up_days = (df['close'].pct_change() >= threshold).astype(int)
        return up_days.rolling(250).sum() / 250


class TurnoverIntensity(AlphaFactor):
    """换手率强度: 5日均换手 / 20日均换手"""
    def __init__(self):
        super().__init__('CUSTOM_002', '换手率强度', 'turnover_intensity', 'liquidity', 5, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        if 'turnover' in df.columns:
            t5 = df['turnover'].rolling(5).mean()
            t20 = df['turnover'].rolling(20).mean()
            return t5 / t20.replace(0, 1)
        # fallback: 用 volume 估算
        v5 = df['volume'].rolling(5).mean()
        v20 = df['volume'].rolling(20).mean()
        return v5 / v20.replace(0, 1)
