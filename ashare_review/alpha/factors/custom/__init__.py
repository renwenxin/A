"""龙哥体系特色因子"""


def register_custom_factors(registry):
    from .limit_up import LimitUpGene, TurnoverIntensity
    from .chip_concentration import PriceConcentration
    from .ma_system import MABullAlignment
    for cls in [LimitUpGene, TurnoverIntensity, PriceConcentration, MABullAlignment]:
        registry.register(cls())
