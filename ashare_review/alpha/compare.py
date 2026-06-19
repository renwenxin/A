"""因子对比 — 排名和对比"""
import pandas as pd
from .base import AlphaFactor, FactorReport
from .registry import get_registry
from .evaluator import evaluate_factor


def rank_factors(df: pd.DataFrame, zoo: str = None,
                 sort_by: str = 'ir') -> list[FactorReport]:
    """对已注册因子进行排名"""
    registry = get_registry()
    factors = registry.list_by_zoo(zoo) if zoo else registry.list_all()
    reports = []
    for f in factors:
        try:
            report = evaluate_factor(f, df)
            reports.append(report)
        except Exception as e:
            print(f'[{f.id}] eval failed: {e}')
    if sort_by == 'ir':
        reports.sort(key=lambda r: r.ir, reverse=True)
    elif sort_by == 'ic_mean':
        reports.sort(key=lambda r: r.ic_mean, reverse=True)
    elif sort_by == 'stars':
        reports.sort(key=lambda r: r.stars, reverse=True)
    return reports


def compare_factors(factor_ids: list[str], df: pd.DataFrame) -> list[dict]:
    """对比指定因子，返回排名表"""
    registry = get_registry()
    reports = []
    for fid in factor_ids:
        f = registry.get(fid)
        if f is None:
            continue
        r = evaluate_factor(f, df)
        reports.append(r.to_dict())
    reports.sort(key=lambda r: r.get('ir', 0), reverse=True)
    return reports
