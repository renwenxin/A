"""机构票筛选器"""
import pandas as pd
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult

class InstitutionScreener(BaseScreener):
    """机构票筛选: 底部放量反弹 + 市值过滤"""
    name = '机构票'

    def screen(self) -> List[ScreeningResult]:
        try:
            spot_df = self.ak.get_spot_df()
        except Exception:
            return []
        results = []
        for _, row in spot_df.iterrows():
            code = str(row.get('代码', '')).zfill(6)
            name = str(row.get('名称', ''))
            change_pct = float(row.get('涨跌幅', 0))
            float_mcap = float(row.get('流通市值', 0)) / 1e8
            score = 0
            reasons = []
            # 流通市值>20亿 (机构票通常偏大)
            if float_mcap > 20:
                score += 10
            # 日内涨幅>8% (底部反弹信号)
            if change_pct > 8:
                score += 30
                reasons.append(f'日内涨{change_pct:.1f}%')
            elif change_pct > 5:
                score += 15
                reasons.append(f'日内涨{change_pct:.1f}%')
            # 非ST
            if 'ST' not in name and '*ST' not in name:
                score += 5
            if score >= 20:
                results.append(ScreeningResult(
                    code=code, name=name, strategy=self.name,
                    score=score, reasons=reasons,
                    detail={'change_pct': change_pct, 'float_market_cap': float_mcap}
                ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:50]
