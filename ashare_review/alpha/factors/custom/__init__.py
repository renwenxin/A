"""龙哥体系特色因子 — 严格按五条件文档实现

龙哥五条件（来源：竞价分析和周期转换信号.txt）：
  条件1: 近期有涨停或8%+大阳线 → CUSTOM_007 RecentBigYangActivity
  条件2: 成交额>10亿 → CUSTOM_006 AmountScale
  条件3: 成交量近6个月最大（核心）→ CUSTOM_005 VolumeBreakout
  条件4: 价格在新高或新高附近 → CUSTOM_008 NewHighProximity
  条件5: 非ST/非北交所 → 过滤层

  同花顺: 10日均线>20日均线 → CUSTOM_004 MABullAlignment

保留因子（供其他预设使用）:
  CUSTOM_001 LimitUpGene, CUSTOM_002 TurnoverIntensity, CUSTOM_003 PriceConcentration
"""


def register_custom_factors(registry):
    from .limit_up import (LimitUpGene, TurnoverIntensity,
                           VolumeBreakout, AmountScale, RecentBigYangActivity)
    from .chip_concentration import PriceConcentration
    from .ma_system import MABullAlignment, NewHighProximity

    # 龙哥特色预设专用（5因子）
    # + 保留因子（3个，供其他预设使用）
    for cls in [LimitUpGene,            # CUSTOM_001 涨停基因（保留）
                TurnoverIntensity,      # CUSTOM_002 换手率强度（保留）
                PriceConcentration,     # CUSTOM_003 价格集中度（保留）
                MABullAlignment,        # CUSTOM_004 MA10>MA20强度 ⭐同花顺条件
                VolumeBreakout,         # CUSTOM_005 爆量突破 ⭐条件3（核心）
                AmountScale,            # CUSTOM_006 成交额规模 ⭐条件2
                RecentBigYangActivity,  # CUSTOM_007 近期大阳线活跃度 ⭐条件1
                NewHighProximity,       # CUSTOM_008 新高接近度 ⭐条件4
                ]:
        registry.register(cls())
