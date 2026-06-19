"""Alpha 因子基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


class AlphaFactor(ABC):
    """量化 Alpha 因子 — 输入日线 DataFrame，输出因子值 Series"""

    def __init__(self, id: str, name: str, name_en: str = '',
                 category: str = '', horizon: int = 5, zoo: str = 'custom'):
        self.id = id
        self.name = name
        self.name_en = name_en or id.lower()
        self.category = category       # 'momentum'|'reversal'|'volume'|'volatility'|'liquidity'
        self.horizon = horizon         # 预测周期（天）
        self.zoo = zoo                 # 'gtja191'|'alpha101'|'custom'

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """输入日线DataFrame（至少含 open/high/low/close/volume），输出因子值序列"""
        ...

    def __repr__(self):
        return f'AlphaFactor({self.id}: {self.name})'


@dataclass
class FactorReport:
    factor_id: str
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ir: float = 0.0           # Information Ratio = ic_mean / ic_std
    ic_positive_ratio: float = 0.0
    long_ret: float = 0.0     # 多头年化收益
    short_ret: float = 0.0    # 空头年化收益
    turnover: float = 0.0     # 日均换手
    stars: int = 0            # 1-5

    def to_dict(self) -> dict:
        return {
            'factor_id': self.factor_id,
            'ic_mean': round(self.ic_mean, 4),
            'ic_std': round(self.ic_std, 4),
            'ir': round(self.ir, 3),
            'ic_positive_ratio': round(self.ic_positive_ratio, 3),
            'long_ret': round(self.long_ret, 4),
            'short_ret': round(self.short_ret, 4),
            'turnover': round(self.turnover, 4),
            'stars': self.stars,
        }
