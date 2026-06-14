"""竞价抢筹筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult, AuctionInfo
from ..data.tdx_reader import TdxReader

class AuctionScreener(BaseScreener):
    """竞价抢筹: 9:25竞价异动检测"""
    name = '竞价抢筹'

    def screen(self) -> List[ScreeningResult]:
        auctions = self.ak.get_auction_data()
        results = []
        for a in auctions:
            if a.auction_volume == 0:
                continue
            score, reasons = 0, []
            # 竞价量 vs 昨日爆量
            try:
                market = 'sh' if a.code.startswith('6') else 'sz'
                df = self.tdx.read_daily(a.code, market)
                if not df.empty:
                    yesterday_max = df['volume'].iloc[-20:].max()
                    a.preclose_volume = yesterday_max
                    if yesterday_max > 0:
                        ratio = a.auction_volume / yesterday_max
                        if ratio >= 0.5:
                            score += 35
                            reasons.append(f'竞价量/昨日爆量={ratio:.2f}')
                        elif ratio >= 0.3:
                            score += 15
            except Exception:
                pass
            # 高开
            if a.open_change_pct >= 3:
                score += 25
                reasons.append(f'高开{a.open_change_pct:.1f}%')
            elif a.open_change_pct >= 0:
                score += 10
            # 竞价额
            if a.auction_amount > 500:
                score += 15
                reasons.append(f'竞价额{a.auction_amount:.0f}万')
            if score >= 25:
                results.append(ScreeningResult(
                    code=a.code, name=a.name, strategy=self.name,
                    score=min(score, 100), reasons=reasons,
                    detail={'open_change_pct': a.open_change_pct, 'auction_volume': a.auction_volume}
                ))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]
