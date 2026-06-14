"""板块分歧介入筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult
from collections import Counter

class SectorDivergenceScreener(BaseScreener):
    """板块分歧介入: 涨停潮次日分歧 → 找龙头低吸机会"""
    name = '板块分歧介入'

    def screen(self) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool()
        # 按涨停连板数找热点板块
        sector_counts = Counter()
        sector_stocks = {}
        for lu in limit_ups:
            sector_counts[lu.board_type or '未知'] += 1
            if lu.board_type not in sector_stocks:
                sector_stocks[lu.board_type] = []
            sector_stocks[lu.board_type].append(lu.code)

        results = []
        for sector, count in sector_counts.items():
            if count >= 5:  # 涨停潮
                stocks = sector_stocks.get(sector, [])
                for code in stocks[:10]:
                    results.append(ScreeningResult(
                        code=code, name=self._get_name(code), strategy=self.name,
                        score=60, reasons=[f'{sector}涨停潮({count}只)', '关注分歧日低吸'],
                        detail={'sector': sector, 'total_limit_up': count}
                    ))
        return results[:20]
