"""竞价抢筹筛选器 — 龙哥竞价四维分析体系

核心公式："量价齐升→最强做多，量价齐跌→最强做空，
          高价低量→诱多嫌疑，低价高量→可能止跌"

四维分析框架：
1. 量：竞价成交量 + 量比（竞价量/昨日爆量）→ 市场关注度
2. 价：高开/低开幅度 → 多空态度
3. 形：竞价图形态（9:20-9:25抢筹/抛压）→ 资金意图
4. 势：板块强度 + 龙头表现 + 昨日涨停表现 → 市场情绪

辅助分析：
- 板块强度：竞价时板块涨幅前3 + 涨停≥3家
- 龙头竞价形态：一字涨停/高开7%+封单巨大
- 昨日涨停表现：高开率≥3%→情绪好，低开→情绪差
- 资金流向：买盘封单+量能验证
"""
import os, struct
from typing import List, Dict
from collections import Counter
from .base import BaseScreener
from ..data.models import ScreeningResult, AuctionInfo
from ..data.tdx_reader import TdxReader, RECORD_SIZE


class AuctionScreener(BaseScreener):
    """竞价抢筹: 四维量价形势分析 + 板块联动"""

    name = '竞价抢筹'

    _MIN_SCORE = 25

    def screen(self) -> List[ScreeningResult]:
        auctions = self.ak.get_auction_data()
        limit_ups = self.ak.get_limit_up_pool()

        # ---- 预计算板块强度和昨日涨停表现（所有标的共用） ----
        sector_strength = self._calc_sector_strength(auctions, limit_ups)
        yesterday_mood = self._calc_yesterday_mood(limit_ups)

        results = []
        for a in auctions:
            if a.auction_volume == 0 and a.auction_amount == 0:
                continue

            score = 0
            reasons = []
            detail: Dict = {}

            # ============================================
            # 一维·量：成交量与量比
            # ============================================
            # 竞价量能
            if a.auction_amount >= 3000:  # 竞价成交额 > 3000万
                score += 20
                reasons.append(f'竞价爆量{a.auction_amount:.0f}万')
            elif a.auction_amount >= 1000:
                score += 12
                reasons.append(f'竞价放量{a.auction_amount:.0f}万')
            elif a.auction_amount >= 500:
                score += 6
                reasons.append(f'竞价量{a.auction_amount:.0f}万')

            # 量比：竞价量 / 昨日爆量
            yesterday_max = self._read_trailing_max_volume(a.code)
            a.preclose_volume = yesterday_max
            detail['yesterday_max_vol'] = yesterday_max

            vol_ratio_to_yesterday = 0
            if yesterday_max > 0:
                vol_ratio_to_yesterday = a.auction_volume / yesterday_max
                if vol_ratio_to_yesterday >= 0.5:
                    score += 25
                    reasons.append(f'竞价量/昨爆量={vol_ratio_to_yesterday:.2f}(巨量)')
                elif vol_ratio_to_yesterday >= 0.3:
                    score += 12
                    reasons.append(f'竞价量/昨爆量={vol_ratio_to_yesterday:.2f}(放量)')
                elif vol_ratio_to_yesterday >= 0.15:
                    score += 5
            detail['vol_ratio_to_yesterday'] = round(vol_ratio_to_yesterday, 3)

            # ============================================
            # 二维·价：高开幅度
            # ============================================
            open_pct = a.open_change_pct

            if open_pct >= 7:
                score += 20
                reasons.append(f'超高开{open_pct:.1f}%')
                detail['open_level'] = '超高开'
            elif open_pct >= 5:
                score += 16
                reasons.append(f'强势高开{open_pct:.1f}%')
                detail['open_level'] = '强势高开'
            elif open_pct >= 3:
                score += 12
                reasons.append(f'高开{open_pct:.1f}%')
                detail['open_level'] = '高开'
            elif open_pct >= 1:
                score += 5
                reasons.append(f'小高开{open_pct:.1f}%')
                detail['open_level'] = '小高开'
            elif open_pct >= 0:
                detail['open_level'] = '平开'
            elif open_pct >= -2:
                score += 3
                reasons.append(f'小幅低开{open_pct:.1f}%')
                detail['open_level'] = '小幅低开'
            else:
                detail['open_level'] = '低开'
                # 低开但有爆量可能是弱转强机会
                if vol_ratio_to_yesterday >= 0.5:
                    score += 10
                    reasons.append('低开爆量·关注弱转强')

            # 量价组合判断（核心口诀）
            if open_pct >= 2 and vol_ratio_to_yesterday >= 0.3:
                score += 10
                reasons.append('量价齐升·最强做多信号')
            elif open_pct >= 2 and vol_ratio_to_yesterday < 0.1:
                score -= 5
                reasons.append('高价低量·诱多嫌疑')
            elif open_pct < -2 and vol_ratio_to_yesterday >= 0.3:
                reasons.append('低价高量·可能止跌·关注承接')

            # ============================================
            # 三维·形：竞价图形态(9:20-9:25)
            # ============================================
            if a.vol_0924 > 0 and a.vol_0925 > 0:
                vol_25_to_24 = a.vol_0925 / a.vol_0924

                if vol_25_to_24 >= 2.0:
                    score += 20
                    reasons.append(f'9:25尾盘抢筹·阶梯上扬(25量/24量={vol_25_to_24:.1f})')
                    detail['shape'] = '阶梯上扬·抢筹'
                elif vol_25_to_24 >= 1.3:
                    score += 12
                    reasons.append(f'9:25抢筹加速({vol_25_to_24:.1f}倍)')
                    detail['shape'] = '温和抢筹'
                elif vol_25_to_24 >= 0.5:
                    score += 5
                    detail['shape'] = '平盘整理'
                elif vol_25_to_24 < 0.5:
                    # 最后时刻抛压涌出
                    score -= 3
                    reasons.append(f'9:25尾盘抛压·阶梯下挫(25量/24量={vol_25_to_24:.1f})')
                    detail['shape'] = '阶梯下挫·抛压'
            else:
                detail['shape'] = '数据不足'

            # ============================================
            # 四维·势：板块+龙头+情绪联动
            # ============================================
            # 4.1 板块强度
            sector = self._get_sector(a.code)
            detail['board_type'] = sector

            if sector and sector in sector_strength:
                sec_info = sector_strength[sector]
                if sec_info['zt_count'] >= 5:
                    score += 12
                    reasons.append(f'{sector}板块强势({sec_info["zt_count"]}只涨停)')
                elif sec_info['zt_count'] >= 3:
                    score += 6
                    reasons.append(f'{sector}板块活跃({sec_info["zt_count"]}只涨停)')
                detail['sector_zt_count'] = sec_info['zt_count']

            # 4.2 个股是否是板块内龙头（竞价涨停/高开最高的）
            if sector and sector in sector_strength:
                sec_info = sector_strength[sector]
                if a.code == sec_info.get('leader_code'):
                    score += 8
                    reasons.append(f'{sector}板块竞价龙头')
                elif open_pct >= 5 and sec_info.get('leader_open_pct', 0) < open_pct:
                    score += 5
                    reasons.append('竞价表现超板块龙头')

            # 4.3 昨日涨停整体表现（市场情绪晴雨表）
            if yesterday_mood == '火爆':
                score += 8
                reasons.append('昨日涨停股普遍高开·情绪火爆')
            elif yesterday_mood == '低迷':
                score -= 5
                reasons.append('⚠昨日涨停股多低开·情绪低迷')

            # ============================================
            # 最终组装
            # ============================================
            detail['open_change_pct'] = open_pct
            detail['auction_volume'] = a.auction_volume
            detail['auction_amount'] = a.auction_amount

            if score >= self._MIN_SCORE:
                results.append(ScreeningResult(
                    code=a.code, name=a.name, strategy=self.name,
                    score=min(score, 100), reasons=reasons,
                    detail=detail
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:25]

    # ------------------------------------------------------------------
    # 板块强度计算
    # ------------------------------------------------------------------
    def _calc_sector_strength(self, auctions: List,
                               limit_ups: List) -> Dict[str, Dict]:
        """计算各板块在竞价中的强度

        返回 {sector_name: {'zt_count': N, 'leader_code': code, 'leader_open_pct': pct}}
        """
        sector_info: Dict[str, Dict] = {}
        lu_code_to_sector = {lu.code: (lu.board_type or '') for lu in limit_ups}

        for a in auctions:
            sec = self._get_sector(a.code) or lu_code_to_sector.get(a.code, '')
            if not sec:
                continue
            if sec not in sector_info:
                sector_info[sec] = {
                    'zt_count': 0, 'auction_stocks': [],
                    'leader_code': '', 'leader_open_pct': 0.0,
                }
            sector_info[sec]['auction_stocks'].append(a)
            if a.open_change_pct > sector_info[sec]['leader_open_pct']:
                sector_info[sec]['leader_code'] = a.code
                sector_info[sec]['leader_open_pct'] = a.open_change_pct

        # 从涨停池统计板块涨停数
        for lu in limit_ups:
            sec = lu.board_type or ''
            if sec and sec in sector_info:
                sector_info[sec]['zt_count'] += 1
            elif sec:
                sector_info[sec] = {
                    'zt_count': 1, 'auction_stocks': [],
                    'leader_code': lu.code, 'leader_open_pct': 0.0,
                }

        return sector_info

    # ------------------------------------------------------------------
    # 昨日涨停表现（市场情绪晴雨表）
    # ------------------------------------------------------------------
    def _calc_yesterday_mood(self, limit_ups: List) -> str:
        """根据今日涨停池中昨日涨停股的表现判断情绪

        注：实时竞价模式下，昨日涨停表现来自竞价数据中的高开/低开比例。
        这里用涨停池中连板≥2的标的（昨日涨停今日继续涨停）来近似。
        """
        if not limit_ups:
            return '中性'

        multi_board = [lu for lu in limit_ups if lu.consecutive >= 2]
        total_zt = len(limit_ups)

        if total_zt >= 100 and len(multi_board) >= 30:
            return '火爆'
        elif total_zt >= 60 and len(multi_board) >= 15:
            return '偏强'
        elif total_zt >= 30 and len(multi_board) >= 5:
            return '中性'
        elif total_zt < 15 or len(multi_board) < 2:
            return '低迷'
        return '中性'

    # ------------------------------------------------------------------
    # TDX 数据读取
    # ------------------------------------------------------------------
    def _read_trailing_max_volume(self, code: str) -> int:
        """快速读取 TDX .day 文件尾部 20 条记录的成交量最大值"""
        market = 'sh' if code.startswith('6') else 'sz'
        if code.startswith('8') or code.startswith('4'):
            market = 'bj'
        fpath = os.path.join(self.tdx._market_dir(market), f'{market}{code}.day')
        if not os.path.exists(fpath):
            return 0
        fsize = os.path.getsize(fpath)
        if fsize < RECORD_SIZE * 2:
            return 0
        read_size = min(RECORD_SIZE * 20, fsize)
        with open(fpath, 'rb') as f:
            f.seek(fsize - read_size)
            tail = f.read(read_size)
        max_vol = 0
        for i in range(len(tail) // RECORD_SIZE):
            offset = i * RECORD_SIZE
            vol = struct.unpack('I', tail[offset+24:offset+28])[0]
            if vol > max_vol:
                max_vol = vol
        return max_vol
