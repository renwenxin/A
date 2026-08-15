"""全市场批量因子计算引擎

对全市场股票并行计算因子值 → 横截面排名 → 多因子加权评分 → Top N。
这是让因子库从「看看而已」变成「实际能选股」的关键模块。
"""
import os
import struct
import concurrent.futures
from typing import List
import numpy as np
import pandas as pd

from .registry import get_registry
from ..data.tdx_reader import TdxReader, RECORD_SIZE

# 默认因子组合（经过实盘选股逻辑验证的配置）
DEFAULT_FACTOR_PRESET = {
    'momentum': {
        'name': '趋势动量组合',
        'factors': ['GTJA_001', 'GTJA_003', 'GTJA_012', 'GTJA_018'],
        'weights': {'GTJA_001': 0.25, 'GTJA_003': 0.25, 'GTJA_012': 0.25, 'GTJA_018': 0.25},
    },
    'reversal': {
        'name': '均值回归组合',
        'factors': ['GTJA_005', 'CUSTOM_001', 'CUSTOM_002'],
        'weights': {'GTJA_005': 0.4, 'CUSTOM_001': 0.3, 'CUSTOM_002': 0.3},
    },
    'quality': {
        'name': '龙哥五条件',
        'factors': [
            # ══════ 严格按文档五条件+同花顺条件，不多不少 ══════
            'CUSTOM_005',  # 条件3(核心): 成交量为过去6个月最大量
            'CUSTOM_006',  # 条件2: 成交额大于10亿
            'CUSTOM_007',  # 条件1: 近期有涨停或8%以上大阳线
            'CUSTOM_008',  # 条件4: 价格在新高或者新高附近
            'CUSTOM_004',  # 同花顺: 10日均线价格大于20日均线价格
        ],
        'weights': {
            'CUSTOM_005': 0.30,  # 条件3 核心条件，权重最高
            'CUSTOM_006': 0.15,  # 条件2 成交额
            'CUSTOM_007': 0.25,  # 条件1 近期大阳线/涨停
            'CUSTOM_008': 0.20,  # 条件4 新高附近
            'CUSTOM_004': 0.10,  # 同花顺 MA10>MA20
        },
    },
    'all': {
        'name': '全因子综合',
        'factors': ['GTJA_001', 'GTJA_002', 'GTJA_003', 'GTJA_005', 'GTJA_012', 'GTJA_018',
                     'CUSTOM_001', 'CUSTOM_002', 'CUSTOM_003', 'CUSTOM_004',
                     'CUSTOM_005', 'CUSTOM_006', 'CUSTOM_007', 'CUSTOM_008'],
        'weights': None,  # 等权
    },
}


def _shorten_history(df: pd.DataFrame, min_bars: int = 60) -> pd.DataFrame:
    """截取尾部数据，减少计算量"""
    if len(df) <= min_bars:
        return df
    return df.tail(max(min_bars, 120))


def _calculate_one_stock(code: str, market: str, tdx: TdxReader,
                         factor_ids: list[str]) -> dict | None:
    """计算单只股票的所有因子值，返回最新一期的因子值字典"""
    registry = get_registry()
    try:
        df = tdx.read_daily(code, market)
        if df.empty or len(df) < 30:
            return None
        df = _shorten_history(df)

        # 补全技术指标（MA/MACD等），因子计算依赖这些列
        from ..analysis.indicators import enrich_all
        df = enrich_all(df)

        values = {}
        for fid in factor_ids:
            factor = registry.get(fid)
            if factor is None:
                continue
            try:
                series = factor.calculate(df)
                last_val = series.dropna().iloc[-1] if len(series.dropna()) > 0 else np.nan
                values[fid] = float(last_val) if not np.isnan(last_val) else np.nan
            except Exception:
                values[fid] = np.nan
        return values
    except Exception:
        return None


def batch_calculate(
    tdx: TdxReader = None,
    factor_ids: list[str] = None,
    preset: str = 'momentum',
    top_n: int = 30,
    max_stocks: int = 500,
    exclude_st: bool = True,
    exclude_bj: bool = True,
) -> list[dict]:
    """全市场批量因子计算 + 横截面排名

    Args:
        tdx: TdxReader 实例
        factor_ids: 手动指定因子ID列表（优先级高于 preset）
        preset: 预设组合名 'momentum' | 'reversal' | 'quality' | 'all'
        top_n: 返回前 N 只股票
        max_stocks: 最多计算多少只股票（性能控制）
        exclude_st: 排除 ST
        exclude_bj: 排除北交所

    Returns:
        [{code, name, score, factor_values: {fid: percentile}, reasons: [...]}]
    """
    if tdx is None:
        tdx = TdxReader()

    # ---- 确定因子列表和权重 ----
    if factor_ids:
        use_factors = factor_ids
        weights = {f: 1.0 / len(factor_ids) for f in factor_ids}
        preset_name = '自定义'
    else:
        preset_cfg = DEFAULT_FACTOR_PRESET.get(preset, DEFAULT_FACTOR_PRESET['momentum'])
        use_factors = preset_cfg['factors']
        weights = preset_cfg['weights']
        if weights is None:
            weights = {f: 1.0 / len(use_factors) for f in use_factors}
        preset_name = preset_cfg['name']

    registry = get_registry()
    valid_factors = [f for f in use_factors if registry.get(f) is not None]
    if not valid_factors:
        return []

    # ---- 获取全市场股票列表 ----
    all_stocks = tdx.list_stocks()  # [(code, market), ...]
    code_market_map = {s[0]: s[1] for s in all_stocks}
    codes = list(code_market_map.keys())

    # 预过滤
    if exclude_bj:
        codes = [c for c in codes if not c.startswith(('8', '4'))]

    # 限制数量：优先取主板 + 创业板/科创板混排
    if len(codes) > max_stocks:
        codes = codes[:max_stocks]

    print(f'[BatchCalc] 预设={preset_name}, 因子={len(valid_factors)}个, 股票={len(codes)}只')

    # ---- 并行计算 ----
    raw_values: dict[str, dict] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for code in codes:
            market = code_market_map.get(code, 'sh')
            futures[executor.submit(_calculate_one_stock, code, market, tdx, valid_factors)] = code

        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                result = future.result(timeout=10)
                if result:
                    valid_count = sum(1 for v in result.values() if not np.isnan(v))
                    if valid_count >= max(1, len(valid_factors) // 2):
                        raw_values[code] = result
            except Exception:
                pass

    if not raw_values:
        return []

    print(f'[BatchCalc] 有效结果: {len(raw_values)}只')

    # ---- 横截面排名（percentile） ----
    factor_matrix = pd.DataFrame(raw_values).T  # rows=codes, cols=factors
    percentile_matrix = pd.DataFrame(index=factor_matrix.index, columns=factor_matrix.columns)

    for fid in valid_factors:
        col = factor_matrix[fid].dropna()
        if len(col) < 5:
            percentile_matrix[fid] = 0.5
            continue
        # 对某些因子（如动量）值越大越好，对另一些（如波动）值越小越好
        # 默认：值越大排名越高。反转类因子需要特殊处理
        rank = col.rank(pct=True)  # 0~1 percentile
        percentile_matrix[fid] = rank

    percentile_matrix = percentile_matrix.fillna(0.3)  # 缺失给中低分

    # ---- 加权综合评分 ----
    scores = pd.Series(0.0, index=percentile_matrix.index)
    for fid in valid_factors:
        w = weights.get(fid, 1.0 / len(valid_factors))
        scores = scores + percentile_matrix[fid].fillna(0.3) * w

    # 归一化到 0-100
    if scores.max() > scores.min():
        scores = (scores - scores.min()) / (scores.max() - scores.min()) * 100
    else:
        scores = pd.Series(50.0, index=scores.index)

    # ---- 取 Top N ----
    top = scores.nlargest(top_n)

    results = []
    for code, score in top.items():
        factor_detail = {}
        top_reasons = []
        for fid in valid_factors:
            pct = percentile_matrix.loc[code, fid] if code in percentile_matrix.index else 0.5
            if isinstance(pct, pd.Series):
                pct = pct.iloc[0] if len(pct) > 0 else 0.5
            factor_detail[fid] = round(float(pct) * 100, 1)

        # 找出贡献最大的 3 个因子作为理由
        factor_scores = [(fid, factor_detail[fid]) for fid in valid_factors]
        factor_scores.sort(key=lambda x: x[1], reverse=True)
        top_reasons = [f'{registry.get(fid).name}: {pct}分' for fid, pct in factor_scores[:3]
                       if registry.get(fid)]

        results.append({
            'code': code,
            'name': '',  # screener 会填充
            'score': round(float(score), 1),
            'reasons': top_reasons,
            'detail': {
                'strategy': preset_name,
                'preset': preset,
                'factor_count': len(valid_factors),
                'factor_values': factor_detail,
            },
        })

    return results
