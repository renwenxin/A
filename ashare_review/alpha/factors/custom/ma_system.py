"""均线排列因子"""
import pandas as pd
import numpy as np
from ...base import AlphaFactor


class MABullAlignment(AlphaFactor):
    """均线多头排列度: 统计MA5>MA10>MA20>MA60的确认条数"""
    def __init__(self):
        super().__init__('CUSTOM_004', '均线多头排列度', 'ma_bull', 'momentum', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        masks = []
        for p in [5, 10, 20, 60]:
            col = f'ma{p}'
            if col in df.columns:
                masks.append(df[col].notna() & (df[col] > 0))
        result = pd.Series(0.0, index=df.index)
        if len(masks) >= 3:
            # 检查 MA5 > MA10 > MA20 > MA60 的层数
            pairs = [(5, 10), (10, 20), (20, 60)]
            for p1, p2 in pairs:
                c1, c2 = f'ma{p1}', f'ma{p2}'
                if c1 in df.columns and c2 in df.columns:
                    mask = (df[c1] > df[c2]).astype(float)
                    result = result + mask
        return result / 3  # 归一化到 0-1
