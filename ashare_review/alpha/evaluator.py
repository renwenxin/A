"""因子评估 — 计算 IC/IR/分层收益"""
import pandas as pd
import numpy as np
from .base import AlphaFactor, FactorReport


def evaluate_factor(factor: AlphaFactor, df: pd.DataFrame,
                    universe: pd.DataFrame = None,
                    forward_period: int = 5) -> FactorReport:
    """评估单个因子的预测能力

    Args:
        factor: AlphaFactor 实例
        df: 单只股票的日线DataFrame
        universe: 可选，多只股票的面板数据
        forward_period: 前瞻收益周期（天）
    """
    factor_values = factor.calculate(df)
    if len(factor_values.dropna()) < 60:
        return FactorReport(factor_id=factor.id)

    # 前向收益
    fwd_ret = df['close'].pct_change(forward_period).shift(-forward_period)

    # IC 序列: 因子值与前瞻收益的秩相关系数
    valid = factor_values.notna() & fwd_ret.notna()
    if valid.sum() < 30:
        return FactorReport(factor_id=factor.id)

    fv = factor_values[valid]
    fr = fwd_ret[valid]

    # 滚动 IC（20日窗口）
    ic_series = []
    for i in range(20, len(fv)):
        ic_series.append(fv.iloc[i-20:i].corr(fr.iloc[i-20:i]))

    ic_series = pd.Series(ic_series)
    ic_mean = ic_series.mean()
    ic_std = ic_series.std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    ic_positive_ratio = (ic_series > 0).mean()

    # 分层收益: Top 20% vs Bottom 20%
    q = pd.qcut(fv, 5, labels=False, duplicates='drop')
    long_mask = q == 4  # Top quintile
    short_mask = q == 0  # Bottom quintile
    long_ret = fr[long_mask].mean() * (252 / forward_period) if long_mask.any() else 0
    short_ret = fr[short_mask].mean() * (252 / forward_period) if short_mask.any() else 0

    # 星级: 基于IR
    if ir >= 0.7:
        stars = 5
    elif ir >= 0.5:
        stars = 4
    elif ir >= 0.3:
        stars = 3
    elif ir >= 0.1:
        stars = 2
    else:
        stars = 1

    return FactorReport(
        factor_id=factor.id,
        ic_mean=ic_mean, ic_std=ic_std, ir=ir,
        ic_positive_ratio=ic_positive_ratio,
        long_ret=long_ret, short_ret=short_ret,
        turnover=0.1,  # 简化：日均换手
        stars=stars,
    )
