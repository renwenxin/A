"""因子筛选策略 — 继承 BaseScreener，接入选股面板"""
from typing import List
from ..data.models import ScreeningResult
from ..screening.base import BaseScreener
from .batch import batch_calculate, DEFAULT_FACTOR_PRESET


class FactorScreener(BaseScreener):
    """Alpha 因子筛选器

    使用全市场因子横截面排名选股。
    支持 4 种预设组合：momentum / reversal / quality / all。
    """

    def __init__(self, tdx=None, ak_fetcher=None, preset: str = 'momentum',
                 top_n: int = 30, max_stocks: int = 500):
        super().__init__(tdx, ak_fetcher)
        self.preset = preset
        self.top_n = top_n
        self.max_stocks = max_stocks
        self._name_map_populated = False

    @property
    def name(self) -> str:
        preset_name = DEFAULT_FACTOR_PRESET.get(self.preset, {}).get('name', '因子筛选')
        return f'Alpha因子 · {preset_name}'

    def screen(self, **kwargs) -> List[ScreeningResult]:
        preset = kwargs.get('preset', self.preset)
        top_n = kwargs.get('top_n', self.top_n)
        max_stocks = kwargs.get('max_stocks', self.max_stocks)

        raw_results = batch_calculate(
            tdx=self.tdx,
            preset=preset,
            top_n=top_n,
            max_stocks=max_stocks,
        )

        if not raw_results:
            return []

        # 确保名称映射已加载
        if not self._name_map_loaded:
            self._load_name_map()

        results = []
        for r in raw_results:
            code = r['code']
            name = r['name'] or self._get_name(code)
            if not name:
                name = self._get_name_from_auction(code)
            if not name:
                name = code

            results.append(ScreeningResult(
                code=code,
                name=name,
                strategy=self.name,
                score=r['score'],
                reasons=r.get('reasons', []),
                detail=r.get('detail', {}),
            ))

        return results
