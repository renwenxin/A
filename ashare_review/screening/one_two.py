"""1进2筛选器"""
import pandas as pd
from datetime import datetime
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult, LimitUpInfo
from ..analysis.indicators import enrich_all

class OneTwoScreener(BaseScreener):
    """一进二战法筛选器

    盘后模式(night_mode=True): 筛选昨日首板，按质量打分
    竞价模式(night_mode=False): 结合次日竞价数据确认
    """
    name = '1进2'

    def screen(self, night_mode: bool = True) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool()
        auctions = {}
        if not night_mode:
            auctions = {a.code: a for a in self.ak.get_auction_data()}
        results = []
        for lu in limit_ups:
            if not lu.is_first:
                continue
            if lu.board_type == '一字板':
                continue
            score, reasons = self._evaluate_first_board(lu, auctions.get(lu.code))
            if score > 0:
                results.append(ScreeningResult(
                    code=lu.code, name=lu.name, strategy=self.name,
                    score=score, reasons=reasons,
                    detail={'limit_up_time': lu.limit_up_time, 'seal_amount': lu.seal_amount}
                ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:30]

    def _evaluate_first_board(self, lu: LimitUpInfo, auction=None) -> tuple:
        score = 0
        reasons = []

        # 1) 流通市值 10-100亿
        if 10 <= lu.float_market_cap <= 100:
            score += 15
            reasons.append(f'流通市值{lu.float_market_cap:.0f}亿，合适')
        elif lu.float_market_cap < 200:
            score += 5

        # 2) 涨停时间越早越好
        time_str = str(lu.limit_up_time).replace(':', '')[:4]
        try:
            t = int(time_str)
            if t <= 1000:
                score += 20
                reasons.append('10点前涨停')
            elif t <= 1100:
                score += 10
                reasons.append('11点前涨停')
            elif t <= 1400:
                score += 5
        except ValueError:
            pass

        # 3) 封成比 > 0.5
        if lu.turnover > 0:
            seal_ratio = lu.seal_amount / (lu.turnover / 10000)
            if seal_ratio > 0.5:
                score += 20
                reasons.append(f'封成比{seal_ratio:.2f}>0.5')
            elif seal_ratio > 0.3:
                score += 10

        # 4) 封单额/流通市值 > 0.015
        if lu.float_market_cap > 0:
            seal_strength = lu.seal_amount / (lu.float_market_cap * 10000)
            if seal_strength > 0.015:
                score += 15
                reasons.append(f'封单强度{seal_strength:.3f}>0.015')

        # 5) 封死且未炸板
        if lu.is_seal and not lu.is_broken:
            score += 10
            reasons.append('封死未炸板')

        # 6) 竞价确认 (次日模式)
        if auction:
            if auction.open_change_pct >= 3:
                score += 15
                reasons.append(f'竞价高开{auction.open_change_pct:.1f}%')
            if auction.auction_volume > auction.preclose_volume * 0.5:
                score += 10
                reasons.append('竞价量>昨日爆量50%')

        return score, reasons
