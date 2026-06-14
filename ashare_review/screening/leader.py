"""龙头筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult, LimitUpInfo
from ..analysis.pattern import detect_n_pattern
from ..analysis.indicators import enrich_all
import pandas as pd

class LeaderScreener(BaseScreener):
    """龙头筛选: 连板+换手+N字结构"""
    name = '龙头'

    def screen(self) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool()
        # 只看连板 >= 2
        leaders = [lu for lu in limit_ups if lu.consecutive >= 2]
        results = []
        for lu in leaders:
            score = 30  # 连板基础分
            reasons = [f'{lu.consecutive}连板']
            try:
                market = 'sh' if lu.code.startswith('6') else 'sz'
                if lu.code.startswith('8') or lu.code.startswith('4'):
                    market = 'bj'
                df = self.tdx.read_daily(lu.code, market)
                if not df.empty:
                    df = enrich_all(df)
                    n_pattern = detect_n_pattern(df)
                    if n_pattern:
                        score += 25
                        reasons.append('N字结构')
                    # 换手龙 vs 一字龙
                    if lu.board_type != '一字板':
                        score += 20
                        reasons.append('换手板上位')
                    # 涨停时间
                    try:
                        t = int(str(lu.limit_up_time).replace(':', '')[:4])
                        if t <= 1000:
                            score += 15
                            reasons.append('早盘封板')
                    except ValueError:
                        pass
            except Exception:
                pass
            results.append(ScreeningResult(
                code=lu.code, name=lu.name, strategy=self.name,
                score=score, reasons=reasons,
                detail={'consecutive': lu.consecutive, 'board_type': lu.board_type}
            ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]
