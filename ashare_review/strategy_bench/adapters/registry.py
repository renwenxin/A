"""适配器注册表"""
from typing import Dict, List

from .base import StrategyAdapter
from .v3 import V3Adapter
from .one_two import OneTwoAdapter
from .ice import IceAdapter
from .tail import TailAdapter
from .zt_replica import ZTReplicaAdapter


def _build() -> Dict[str, StrategyAdapter]:
    adapters = [V3Adapter(), OneTwoAdapter(), IceAdapter(), TailAdapter(), ZTReplicaAdapter()]
    return {a.strategy_id: a for a in adapters}


def get_adapter(strategy_id: str) -> StrategyAdapter:
    return _build().get(strategy_id)


def list_adapters() -> List[StrategyAdapter]:
    return list(_build().values())
