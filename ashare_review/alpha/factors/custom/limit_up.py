"""龙哥五条件因子 — 严格按文档选股逻辑实现

条件1: 近期有涨停或8%+大阳线 → CUSTOM_007
条件2: 成交额>10亿 → CUSTOM_006
条件3: 成交量近6个月最大（核心）→ CUSTOM_005
条件4: 价格在新高或新高附近 → CUSTOM_008 (在 ma_system.py)
条件5: 非ST/非北交所 → 过滤层，非因子

同花顺: 10日均线>20日均线 → CUSTOM_004 (在 ma_system.py)
"""

import pandas as pd
import numpy as np
from ...base import AlphaFactor

# 各板块涨停阈值
_BOARD_LIMIT = {
    'main': 0.099,   # 主板 10% → 涨幅>=9.9%视为涨停
    'gem': 0.199,    # 创业板 20%
    'star': 0.199,   # 科创板 20%
    'bj': 0.299,     # 北交所 30%
}


def _get_limit_threshold(code: str) -> float:
    """根据股票代码前缀返回涨停阈值"""
    if not code:
        return _BOARD_LIMIT['main']
    code = str(code).zfill(6)
    if code.startswith(('300', '301')):
        return _BOARD_LIMIT['gem']
    if code.startswith('688'):
        return _BOARD_LIMIT['star']
    if code.startswith(('8', '4')):
        return _BOARD_LIMIT['bj']
    return _BOARD_LIMIT['main']


# ═══════════════════════════════════════════════════════════════════════════
# 保留因子（不在龙哥特色预设中，但供其他预设/全因子组合使用）
# ═══════════════════════════════════════════════════════════════════════════

class LimitUpGene(AlphaFactor):
    """涨停基因: 近250日涨停次数指数加权（保留供均值回归/全因子预设使用）"""
    def __init__(self):
        super().__init__('CUSTOM_001', '涨停基因', 'limit_up_gene', 'liquidity', 10, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        code = str(df.attrs.get('code', '')) if hasattr(df, 'attrs') else ''
        threshold = _get_limit_threshold(code)
        up_days = (df['close'].pct_change() >= threshold).astype(float)
        ewm = up_days.ewm(halflife=60, min_periods=1).mean()
        return ewm.clip(0, 1).fillna(0)


class TurnoverIntensity(AlphaFactor):
    """换手率强度: 5日均换手 / 20日均换手（保留供均值回归/全因子预设使用）"""
    def __init__(self):
        super().__init__('CUSTOM_002', '换手率强度', 'turnover_intensity', 'liquidity', 5, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        if 'turnover' in df.columns:
            t5 = df['turnover'].rolling(5).mean()
            t20 = df['turnover'].rolling(20).mean()
            return t5 / t20.replace(0, 1)
        v5 = df['volume'].rolling(5).mean()
        v20 = df['volume'].rolling(20).mean()
        return v5 / v20.replace(0, 1)


# ═══════════════════════════════════════════════════════════════════════════
# 龙哥特色预设专用因子 — 严格对应文档五个条件
# ═══════════════════════════════════════════════════════════════════════════

class VolumeBreakout(AlphaFactor):
    """条件3（核心）: 成交量为过去6个月最大量

    文档原话：「成交量为过去6个月最大量，确保目标品种新资金和旧资金
    完成切换，新的做多资金入场」
    """
    def __init__(self):
        super().__init__('CUSTOM_005', '爆量突破', 'volume_breakout', 'volume', 5, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        max_vol_120 = df['volume'].rolling(120).max()
        result = df['volume'] / max_vol_120.replace(0, np.nan)
        return result.clip(0, 1.5)


class AmountScale(AlphaFactor):
    """条件2: 成交额大于10亿

    文档原话：「成交额大于10亿，确保目标品种异动不是单一资金进场，
    而是市场合力」
    成交额 < 10亿 → 低分；成交额越大 → 越接近1（上限50亿）
    """
    def __init__(self):
        super().__init__('CUSTOM_006', '成交额规模', 'amount_scale', 'liquidity', 5, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        # 优先用 amount 列，否则用 volume * close 估算（单位：元）
        if 'amount' in df.columns:
            amount = df['amount']
        else:
            amount = df['volume'] * df['close']

        # 10亿正好达标→0.5分；20亿+→1.0分
        yi = 1e8  # 1亿
        score = amount / (20 * yi)  # 0~20亿线性映射到0~1
        return score.clip(0, 1).fillna(0)


class RecentBigYangActivity(AlphaFactor):
    """条件1: 近期有涨停或8%以上大阳线

    文档原话：「近期必须有过涨停，或者8%以上的大阳线，确保目标品种
    大资金进场」
    近10日×0.5 + 近30日×0.3 + 近60日×0.2，自适应板块阈值。
    """
    def __init__(self):
        super().__init__('CUSTOM_007', '近期大阳线活跃度', 'recent_big_yang', 'liquidity', 5, 'custom')

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        code = str(df.attrs.get('code', '')) if hasattr(df, 'attrs') else ''
        limit_threshold = _get_limit_threshold(code)
        # 8%大阳线 和 涨停 都算（取两者中的低阈值，即8%）
        threshold = min(0.08, limit_threshold)

        pct_chg = df['close'].pct_change()
        # 涨停 或 ≥8%大阳线
        is_big_yang = (pct_chg >= threshold).astype(float)

        count_10 = is_big_yang.rolling(10).sum()
        count_30 = is_big_yang.rolling(30).sum()
        count_60 = is_big_yang.rolling(60).sum()

        score = (count_10 / 10) * 0.5 + (count_30 / 30) * 0.3 + (count_60 / 60) * 0.2
        return score.fillna(0)
