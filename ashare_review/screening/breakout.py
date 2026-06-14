"""突破形态筛选器"""
from typing import List
from .base import BaseScreener
from ..data.models import ScreeningResult
from ..analysis.indicators import enrich_all
from ..analysis.pattern import detect_box_breakout, detect_w_bottom, detect_n_pattern
from ..analysis.volume import detect_volume_breakout, detect_volume_cannon

class BreakoutScreener(BaseScreener):
    """突破形态筛选: 箱体/W底/N字 + 量价确认"""
    name = '突破形态'

    def screen(self, sample_size: int = 200) -> List[ScreeningResult]:
        stocks = self.tdx.list_stocks()[:sample_size]
        results = []
        for code, market in stocks:
            try:
                df = self.tdx.read_daily(code, market)
                if len(df) < 60:
                    continue
                df = enrich_all(df)
                score, reasons = 0, []

                box = detect_box_breakout(df)
                if box:
                    score += 30
                    reasons.append(f'箱体突破({box["box_period"]}天)')

                wb = detect_w_bottom(df)
                if wb:
                    score += 25
                    reasons.append('W底突破')

                n_pat = detect_n_pattern(df)
                if n_pat:
                    score += 20
                    reasons.append('N字结构')

                if detect_volume_breakout(df):
                    score += 15
                    reasons.append('放量突破')

                cannons = detect_volume_cannon(df)
                if cannons:
                    cannon = cannons[0]
                    score += 15
                    reasons.append(f'成交量{cannon["cannon_type"]}({cannon["count"]}连)')

                if score >= 30:
                    results.append(ScreeningResult(
                        code=code, name=self._get_name(code), strategy=self.name,
                        score=min(score, 100), reasons=reasons,
                        detail={'close': float(df['close'].iloc[-1])}
                    ))
            except Exception:
                pass
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:30]
