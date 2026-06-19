"""Alpha 因子实现集合"""

def register_all(registry=None):
    """注册所有因子到全局注册中心"""
    if registry is None:
        from ..registry import get_registry
        registry = get_registry()
    from .gtja191.momentum import register_gtja_momentum
    from .custom import register_custom_factors
    register_gtja_momentum(registry)
    register_custom_factors(registry)
