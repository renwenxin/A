"""Alpha 因子实现集合"""
from .gtja191.momentum import register_gtja_momentum
from .custom import register_custom_factors

def register_all():
    """注册所有因子到全局注册中心"""
    from ..registry import get_registry
    r = get_registry()
    register_gtja_momentum(r)
    register_custom_factors(r)
