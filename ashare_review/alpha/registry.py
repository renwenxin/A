"""因子注册中心 — 统一管理所有 Alpha 因子"""
import pandas as pd
from .base import AlphaFactor


class FactorRegistry:
    """因子注册中心：注册、查询、批量计算"""

    def __init__(self):
        self._factors: dict[str, AlphaFactor] = {}

    def register(self, factor: AlphaFactor):
        """注册因子（同名覆盖）"""
        self._factors[factor.id] = factor
        return self

    def register_many(self, factors: list[AlphaFactor]):
        for f in factors:
            self.register(f)
        return self

    def get(self, id: str):
        return self._factors.get(id)

    def list_all(self) -> list[AlphaFactor]:
        return list(self._factors.values())

    def list_by_zoo(self, zoo: str) -> list[AlphaFactor]:
        return [f for f in self._factors.values() if f.zoo == zoo]

    def list_by_category(self, category: str) -> list[AlphaFactor]:
        return [f for f in self._factors.values() if f.category == category]

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """对所有已注册因子在给定 DataFrame 上计算，返回因子值矩阵"""
        result = pd.DataFrame(index=df.index)
        for f in self._factors.values():
            try:
                result[f.id] = f.calculate(df)
            except Exception:
                result[f.id] = 0.0
        return result

    @property
    def count(self) -> int:
        return len(self._factors)

    def summary(self) -> list[dict]:
        """返回所有因子元数据摘要"""
        return [{
            'id': f.id, 'name': f.name, 'category': f.category,
            'zoo': f.zoo, 'horizon': f.horizon,
        } for f in self._factors.values()]


# 全局单例
_registry = FactorRegistry()

def get_registry() -> FactorRegistry:
    global _registry
    if _registry.count == 0:
        try:
            from .factors import register_all
            register_all(_registry)
        except Exception:
            pass  # 静默失败，不阻塞启动
    return _registry
