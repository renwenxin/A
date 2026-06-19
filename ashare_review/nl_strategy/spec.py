"""策略数据模型"""
from dataclasses import dataclass, field

# 支持的条件类型及其参数 schema
VALID_CONDITIONS = {
    'market_cap': {'min': float, 'max': float},
    'ma_breakout': {'period': int},
    'ma_position': {'period': int, 'above': bool},
    'sector_limit_up': {'min_count': int},
    'limit_up_time': {'before': str},
    'seal_amount': {'min': float},
    'turnover': {'min': float, 'max': float},
    'volume_ratio': {'min': float},
    'consecutive': {'min': int, 'max': int},
    'institution_count': {'min': int},
    'float_market_cap': {'min': float, 'max': float},
    'exclude_st': {},
    'exclude_bj': {},
    'pattern': {'type': str},
    'chip': {'signal': str},
    'change_pct': {'min': float, 'max': float},
}


@dataclass
class StrategyCondition:
    type: str
    params: dict = field(default_factory=dict)
    weight: float = 1.0


@dataclass
class StrategySpec:
    name: str = ''
    description: str = ''
    conditions: list[StrategyCondition] = field(default_factory=list)
    universe: str = 'all'
    max_results: int = 20
    sort_by: str = 'score'

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'description': self.description,
            'conditions': [{'type': c.type, 'params': c.params, 'weight': c.weight}
                          for c in self.conditions],
            'universe': self.universe,
            'max_results': self.max_results,
            'sort_by': self.sort_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'StrategySpec':
        conditions = [StrategyCondition(
            type=c['type'], params=c.get('params', {}),
            weight=c.get('weight', 1.0),
        ) for c in d.get('conditions', [])]
        return cls(
            name=d.get('name', ''), description=d.get('description', ''),
            conditions=conditions, universe=d.get('universe', 'all'),
            max_results=d.get('max_results', 20), sort_by=d.get('sort_by', 'score'),
        )
