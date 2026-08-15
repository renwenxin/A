"""策略执行器 — 将 StrategySpec 转为实际的筛选操作"""
import logging
import pandas as pd
import numpy as np
from typing import List, Optional

from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from ..data.models import ScreeningResult
from ..screening.base import BaseScreener
from .spec import StrategySpec, StrategyCondition

logger = logging.getLogger(__name__)

_tdx = TdxReader()
_ak = AkshareFetcher()


def _enrich_limit_up_data(df: pd.DataFrame) -> pd.DataFrame:
    """用今日涨停池数据丰富 spot DataFrame"""
    try:
        limit_ups = _ak.get_limit_up_pool()
        if not limit_ups:
            return df

        # 建立 code → LimitUpInfo 映射
        lu_map = {}
        for lu in limit_ups:
            lu_map[lu.code] = lu

        # 添加涨停相关列
        df['_is_zt'] = df['代码'].astype(str).str.zfill(6).map(
            lambda c: c in lu_map).fillna(False)
        df['_consecutive'] = df['代码'].astype(str).str.zfill(6).map(
            lambda c: lu_map[c].consecutive if c in lu_map else 0).fillna(0)
        df['_seal_amount'] = df['代码'].astype(str).str.zfill(6).map(
            lambda c: lu_map[c].seal_amount if c in lu_map else 0).fillna(0)
        df['_limit_up_time'] = df['代码'].astype(str).str.zfill(6).map(
            lambda c: str(lu_map[c].limit_up_time) if c in lu_map else '').fillna('')

        return df
    except Exception as e:
        logger.warning(f"涨停池数据丰富失败: {e}")
        return df


def _enrich_institution_data(df: pd.DataFrame) -> pd.DataFrame:
    """用机构持仓数据丰富 spot DataFrame"""
    try:
        inst_df = _ak.get_institution_holdings()
        if inst_df is None or inst_df.empty:
            return df

        # 建立 code → holder_count 映射
        if '代码' in inst_df.columns and '持有机构数' in inst_df.columns:
            inst_map = dict(zip(
                inst_df['代码'].astype(str).str.zfill(6),
                inst_df['持有机构数']
            ))
            df['_institution_count'] = df['代码'].astype(str).str.zfill(6).map(
                inst_map).fillna(0)
        return df
    except Exception as e:
        logger.warning(f"机构持仓数据丰富失败: {e}")
        return df


def execute_strategy(spec: StrategySpec) -> list[dict]:
    """执行策略筛选，返回结果列表

    流程：
    1. 加载全市场行情快照
    2. 丰富涨停池 + 机构持仓数据
    3. 逐个条件过滤（低成本优先）
    4. 加权评分
    5. 排序取 Top N
    """
    # 加载行情数据
    try:
        spot_df = _ak.get_spot_df()
    except Exception as e:
        logger.error(f"获取行情数据失败: {e}")
        return []

    if spot_df is None or spot_df.empty:
        return []

    # 标准化代码列
    if '代码' in spot_df.columns:
        spot_df['代码'] = spot_df['代码'].astype(str).str.zfill(6)

    # 丰富数据
    spot_df = _enrich_limit_up_data(spot_df)
    spot_df = _enrich_institution_data(spot_df)

    # 条件过滤顺序：先执行低成本条件
    low_cost_types = {'exclude_st', 'exclude_bj', 'change_pct', 'float_market_cap',
                      'market_cap', 'consecutive', 'turnover', 'limit_up_time'}
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
    if spec.sort_by == 'market_cap' and '流通市值' in passed.columns:
        sort_col = '流通市值'
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
        # ---- 低成本基础过滤 ----
        if t == 'exclude_st':
            if '名称' in df.columns:
                return df[~df['名称'].str.contains('ST', na=False)]
            return df

        if t == 'exclude_bj':
            if '代码' in df.columns:
                return df[~df['代码'].astype(str).str.startswith(('8', '4'))]
            return df

        # ---- 市值过滤 ----
        if t in ('market_cap', 'float_market_cap'):
            col = '流通市值' if '流通市值' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', 0)
            max_v = p.get('max', float('inf'))
            return df[(df[col] >= min_v) & (df[col] <= max_v)]

        # ---- 涨跌幅过滤 ----
        if t == 'change_pct':
            col = '涨跌幅' if '涨跌幅' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', -100)
            max_v = p.get('max', 100)
            return df[(df[col] >= min_v) & (df[col] <= max_v)]

        # ---- 换手率过滤 ----
        if t == 'turnover':
            col = '换手率' if '换手率' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', 0)
            max_v = p.get('max', float('inf'))
            result = df[(df[col] >= min_v) & (df[col] <= max_v)]
            return result

        # ---- 连板数过滤 ----
        if t == 'consecutive':
            if '_consecutive' not in df.columns:
                return df
            min_v = p.get('min', 0)
            max_v = p.get('max', float('inf'))
            return df[(df['_consecutive'] >= min_v) & (df['_consecutive'] <= max_v)]

        # ---- 涨停时间过滤 ----
        if t == 'limit_up_time':
            if '_limit_up_time' not in df.columns:
                return df
            before = p.get('before', '1500')
            # 格式 HHMM 比较
            before_int = int(before.replace(':', '')[:4])
            def _time_filter(t_str):
                try:
                    t_int = int(str(t_str).replace(':', '')[:4])
                    return 0 < t_int <= before_int
                except (ValueError, TypeError):
                    return False
            mask = df['_limit_up_time'].apply(_time_filter)
            return df[mask]

        # ---- 封单金额过滤 ----
        if t == 'seal_amount':
            if '_seal_amount' not in df.columns:
                return df
            min_v = p.get('min', 0)
            return df[df['_seal_amount'] >= min_v]

        # ---- 机构持仓过滤 ----
        if t == 'institution_count':
            if '_institution_count' not in df.columns:
                return df
            min_v = p.get('min', 0)
            return df[df['_institution_count'] >= min_v]

        # ---- 量比过滤（低成本） ----
        if t == 'volume_ratio':
            col = '量比' if '量比' in df.columns else None
            if col is None:
                return df
            min_v = p.get('min', 0)
            return df[df[col] >= min_v]

        # ---- 需要 TDX 数据的高成本条件（暂无数据时不过滤） ----
        if t in ('ma_breakout', 'ma_position', 'pattern', 'chip', 'sector_limit_up'):
            # 这些条件需要逐股读取K线数据，在 spot 快筛阶段跳过过滤
            # 在评分阶段会处理
            return df

        # 未识别的条件类型 → 不过滤
        logger.debug(f"未知条件类型 {t}，跳过过滤")
        return df

    except Exception as e:
        logger.warning(f"条件 {t} 过滤异常: {e}")
        return df


def _calc_condition_score(df: pd.DataFrame, cond: StrategyCondition) -> pd.Series:
    """计算单个条件的评分（0-1 归一化）"""
    p = cond.params
    t = cond.type
    result = pd.Series(0.0, index=df.index)

    try:
        # ---- 量比评分 ----
        if t == 'volume_ratio':
            col = '量比' if '量比' in df.columns else None
            if col is not None:
                min_v = p.get('min', 0)
                # 量比越大越好（0-5范围线性，封顶1.0）
                result = df[col].clip(0, 10) / max(min_v, 0.1)
                result = (result.clip(0, 3) / 3).fillna(0)

        # ---- 涨跌幅评分 ----
        elif t == 'change_pct':
            col = '涨跌幅' if '涨跌幅' in df.columns else None
            if col is not None:
                # 涨得越多分越高（-5到+10范围线性映射到0-1）
                result = (df[col].clip(-5, 10) + 5) / 15
                result = result.clip(0, 1).fillna(0)

        # ---- 换手率评分 ----
        elif t == 'turnover':
            col = '换手率' if '换手率' in df.columns else None
            if col is not None:
                min_v = p.get('min', 1)
                # 适度换手最优（过高=分歧大，过低=无人气）
                # 最优区间 min_v ~ min_v*3
                optimal = max(min_v, 1)
                result = 1.0 - abs(df[col] - optimal) / max(optimal * 3, 1)
                result = result.clip(0, 1).fillna(0)

        # ---- 连板数评分 ----
        elif t == 'consecutive':
            if '_consecutive' in df.columns:
                # 1-3连板最优（接力阶段）
                cons = df['_consecutive'].fillna(0)
                result = cons.apply(
                    lambda x: 1.0 if 1 <= x <= 3 else
                    0.7 if x == 0 else 0.3 if x <= 5 else 0.1
                )

        # ---- 封单金额评分 ----
        elif t == 'seal_amount':
            if '_seal_amount' in df.columns:
                # 封单越大越好（对数尺度）
                amt = df['_seal_amount'].clip(0, 1e9)
                result = np.log1p(amt) / np.log1p(1e9)
                result = pd.Series(result, index=df.index).fillna(0)

        # ---- 涨停时间评分 ----
        elif t == 'limit_up_time':
            if '_limit_up_time' in df.columns:
                # 越早封板分越高
                def _time_score(t_str):
                    try:
                        t_int = int(str(t_str).replace(':', '')[:4])
                        if t_int <= 930:
                            return 1.0
                        elif t_int <= 1000:
                            return 0.8
                        elif t_int <= 1030:
                            return 0.6
                        elif t_int <= 1130:
                            return 0.4
                        elif t_int <= 1400:
                            return 0.3
                        else:
                            return 0.1
                    except (ValueError, TypeError):
                        return 0
                result = df['_limit_up_time'].apply(_time_score).fillna(0)

        # ---- 机构持仓评分 ----
        elif t == 'institution_count':
            if '_institution_count' in df.columns:
                # 机构越多越好（对数尺度，100家 = 1.0）
                cnt = df['_institution_count'].clip(0, 500)
                result = np.log1p(cnt) / np.log1p(100)
                result = pd.Series(result.clip(0, 1), index=df.index).fillna(0)

        # ---- 流通市值评分 ----
        elif t in ('market_cap', 'float_market_cap'):
            col = '流通市值' if '流通市值' in df.columns else None
            if col is not None:
                min_v = p.get('min', 10)
                max_v = p.get('max', 500)
                # 在 min-max 区间内的得高分
                optimal = (min_v + max_v) / 2
                half_range = (max_v - min_v) / 2 or 1
                result = 1.0 - abs(df[col] - optimal) / half_range
                result = result.clip(0, 1).fillna(0)

        # ---- 需要 TDX 数据的条件（暂无数据时给中性分） ----
        elif t in ('ma_breakout', 'ma_position', 'pattern', 'chip',
                   'sector_limit_up'):
            # 这些条件需要逐股K线数据，spot 快筛阶段给中性分
            result = pd.Series(0.5, index=df.index)

    except Exception as e:
        logger.warning(f"条件 {t} 评分异常: {e}")

    return result.fillna(0)
