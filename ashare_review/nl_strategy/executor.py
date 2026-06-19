# ashare_review/nl_strategy/executor.py
"""策略执行器 — 将 StrategySpec 转为实际的筛选操作"""
import pandas as pd
from typing import List
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..data.models import ScreeningResult
from ..screening.base import BaseScreener
from .spec import StrategySpec, StrategyCondition

_tdx = TdxReader()
_ak = AkshareFetcher()


def execute_strategy(spec: StrategySpec) -> list[dict]:
    """执行策略筛选，返回结果列表

    流程：
    1. 加载全市场行情快照
    2. 逐个条件过滤（低成本优先）
    3. 加权评分
    4. 排序取 Top N
    """
    # 加载行情数据
    try:
        spot_df = _ak.get_spot_df()
    except Exception:
        return []

    if spot_df is None or spot_df.empty:
        return []

    # 条件过滤顺序：先执行低成本条件
    low_cost_types = {'exclude_st', 'exclude_bj', 'change_pct', 'float_market_cap',
                      'market_cap', 'consecutive'}
    ordered_conditions = sorted(spec.conditions,
                                key=lambda c: (0 if c.type in low_cost_types else 1))

    passed = spot_df.copy()
    for cond in ordered_conditions:
        passed = _apply_condition(passed, cond)
        if passed.empty:
            return []

    # 评分
    scores = pd.Series(0.0, index=passed.index)
    for cond in spec.conditions:
        cond_score = _calc_condition_score(passed, cond)
        scores = scores + cond_score * cond.weight

    passed['_score'] = scores

    # 排序
    sort_col = '_score'
    ascending = False
    if spec.sort_by == 'market_cap' and 'float_market_cap' in passed.columns:
        sort_col = 'float_market_cap'
        ascending = True
    elif spec.sort_by == 'turnover' and '换手率' in passed.columns:
        sort_col = '换手率'
        ascending = False

    passed = passed.sort_values(sort_col, ascending=ascending)
    top = passed.head(spec.max_results)

    # 格式化结果
    results = []
    for _, row in top.iterrows():
        code = str(row.get('代码', '')).zfill(6)
        name = str(row.get('名称', ''))
        score = float(row.get('_score', 0))
        results.append({
            'code': code, 'name': name,
            'score': round(min(score, 100), 1),
            'reasons': [f'{spec.name}策略匹配'],
            'detail': {'strategy': spec.name, 'conditions': len(spec.conditions)},
        })

    return results


def _apply_condition(df: pd.DataFrame, cond: StrategyCondition) -> pd.DataFrame:
    """对 DataFrame 应用单个过滤条件"""
    p = cond.params
    t = cond.type

    try:
        if t == 'exclude_st':
            if '名称' in df.columns:
                return df[~df['名称'].str.contains('ST', na=False)]
            return df
        if t == 'exclude_bj':
            if '代码' in df.columns:
                return df[~df['代码'].astype(str).str.startswith(('8', '4'))]
            return df
        if t in ('market_cap', 'float_market_cap'):
            col = '流通市值' if '流通市值' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', 0)
            max_v = p.get('max', float('inf'))
            return df[(df[col] >= min_v) & (df[col] <= max_v)]
        if t == 'change_pct':
            col = '涨跌幅' if '涨跌幅' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', -100)
            max_v = p.get('max', 100)
            return df[(df[col] >= min_v) & (df[col] <= max_v)]
        # 高成本条件：在评分阶段处理，此处不过滤
        return df
    except Exception:
        return df


def _calc_condition_score(df: pd.DataFrame, cond: StrategyCondition) -> pd.Series:
    """计算单个条件的评分"""
    p = cond.params
    t = cond.type
    result = pd.Series(0.0, index=df.index)

    try:
        if t == 'volume_ratio':
            col = '量比' if '量比' in df.columns else None
            if col is not None:
                min_v = p.get('min', 0)
                result = df[col].clip(0, 10) / max(min_v, 0.1)
                result = result.clip(0, 3) / 3
        if t == 'change_pct':
            col = '涨跌幅' if '涨跌幅' in df.columns else None
            if col is not None:
                # 涨得越多分越高（0-9%范围线性）
                result = df[col].clip(-5, 10) / 10
                result = result.clip(0, 1)
    except Exception:
        pass

    return result.fillna(0)
