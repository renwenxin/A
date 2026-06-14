"""日度复盘报告"""
from datetime import date, datetime
from typing import Dict, List
from ..data.tdx_reader import TdxReader
from ..data.akshare_fetcher import AkshareFetcher
from collections import Counter

class DailyReport:
    def __init__(self, tdx: TdxReader = None, ak_fetcher: AkshareFetcher = None):
        self.tdx = tdx or TdxReader()
        self.ak = ak_fetcher or AkshareFetcher()

    def generate(self) -> Dict:
        limit_ups = self.ak.get_limit_up_pool()
        boards = self.ak.get_concept_boards()
        lhb = self.ak.get_lhb()

        # 涨停统计
        total_zt = len(limit_ups)
        sealed = sum(1 for lu in limit_ups if lu.is_seal)
        broken = sum(1 for lu in limit_ups if lu.is_broken)
        first_boards = [lu for lu in limit_ups if lu.is_first]
        multi_boards = [lu for lu in limit_ups if lu.consecutive >= 2]

        # 涨停时间分布
        time_dist = {'早盘(<10:30)': 0, '上午(10:30-11:30)': 0, '下午': 0}
        for lu in limit_ups:
            try:
                t = int(str(lu.limit_up_time).replace(':', '')[:4])
                if t <= 1030:
                    time_dist['早盘(<10:30)'] += 1
                elif t <= 1130:
                    time_dist['上午(10:30-11:30)'] += 1
                else:
                    time_dist['下午'] += 1
            except (ValueError, TypeError):
                time_dist['下午'] += 1

        # 连板高度
        max_consecutive = max((lu.consecutive for lu in limit_ups), default=0)

        # 板块涨停数排名
        sector_zt = Counter(lu.board_type for lu in limit_ups if lu.board_type)
        top_sectors = sector_zt.most_common(10)

        # 龙虎榜净买前十
        lhb_sorted = sorted(lhb, key=lambda x: x.net_amount, reverse=True)[:10]

        return {
            'date': date.today().isoformat(),
            'total_limit_ups': total_zt,
            'sealed': sealed,
            'broken': broken,
            'seal_rate': f'{sealed/max(total_zt,1)*100:.1f}%',
            'first_boards': len(first_boards),
            'multi_boards': len(multi_boards),
            'max_consecutive': max_consecutive,
            'time_distribution': time_dist,
            'top_sectors': [(s, c) for s, c in top_sectors],
            'top_lhb': [{
                'code': l.code, 'name': l.name, 'reason': l.reason,
                'net_amount': l.net_amount
            } for l in lhb_sorted],
            'multi_board_list': [{
                'code': lu.code, 'name': lu.name,
                'consecutive': lu.consecutive, 'board_type': lu.board_type,
                'limit_up_time': lu.limit_up_time
            } for lu in sorted(multi_boards, key=lambda x: x.consecutive, reverse=True)]
        }
