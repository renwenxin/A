"""1进2筛选器 — 龙哥1进2战法 + 竞价预期体系

双模式：
- 盘后模式(night_mode=True): 筛选昨日首板，按质量打分，预判次日竞价预期
- 竞价模式(night_mode=False): 结合次日竞价数据，做超预期/符合预期/弱于预期判断

竞价预期预测（根据当日走势预判次日竞价）:
- 封板时间: 越早→次日高开预期越高
- 成交量(换手率): 缩量加速→高开7%+；放量分歧→需弱转强确认；巨量烂板→低开预期
- 封单量/封板强度: 封单巨大→一字板预期

超预期/符合预期/弱于预期框架：
- 超预期: 昨日烂板今日高开 = 弱转强买点
- 符合预期: 中规中矩，看承接
- 弱于预期: 该强不强 = 最大利空，竞价核按钮

弱转强判断（龙哥方法论）：
- 可以做: 市场上升期 + 核心人气股 + 分时转强早(30分钟内) + 带量突破
- 绝不可以: 退潮期 + 跟风股 + 尾盘偷袭 + 无量拉升 + 高位出货信号
"""
import os, struct
import pandas as pd
from datetime import datetime
from typing import List, Optional, Tuple, Dict
from .base import BaseScreener
from ..data.models import ScreeningResult, LimitUpInfo, AuctionInfo
from ..data.tdx_reader import RECORD_SIZE
from ..analysis.indicators import enrich_all


class OneTwoScreener(BaseScreener):
    """一进二战法筛选器

    盘后模式：筛选昨日首板+预判竞价预期
    竞价模式：结合次日竞价做确认
    """

    name = '1进2'

    # 预期量化标准（龙哥体系）
    EXPECTATION_EARLY = '强势板(≤10:00封)'    # 次日预期高开5-9%
    EXPECTATION_MORNING = '上午板(10-11:00封)'  # 预期高开3-6%
    EXPECTATION_AFTERNOON = '下午板'              # 预期平开或高开0-3%
    EXPECTATION_LATE = '尾盘板'                   # 预期平开或低开

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

            score, reasons, detail = self._evaluate_first_board(
                lu, auctions.get(lu.code), night_mode
            )
            if score > 0:
                results.append(ScreeningResult(
                    code=lu.code, name=lu.name, strategy=self.name,
                    score=min(score, 100), reasons=reasons,
                    detail=detail
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:30]

    # ------------------------------------------------------------------
    # 首板评估（核心）
    # ------------------------------------------------------------------
    def _evaluate_first_board(self, lu: LimitUpInfo,
                               auction: Optional[AuctionInfo] = None,
                               night_mode: bool = True) -> Tuple[int, list, dict]:
        score = 0
        reasons = []
        detail: Dict = {
            'limit_up_time': lu.limit_up_time,
            'seal_amount': round(lu.seal_amount, 0),
            'board_type': lu.board_type,
            'float_market_cap': round(lu.float_market_cap, 0),
        }

        # ============================================
        # 一、基础质量评分（盘后+竞价共用）
        # ============================================

        # 1) 流通市值 10-100亿（接力最佳区间）
        if 10 <= lu.float_market_cap <= 100:
            score += 12
            reasons.append(f'流通市值{lu.float_market_cap:.0f}亿·接力佳')
        elif lu.float_market_cap <= 150:
            score += 6
            reasons.append(f'流通市值{lu.float_market_cap:.0f}亿')

        # 2) 涨停时间 + 次日竞价预期
        time_str = str(lu.limit_up_time).replace(':', '')[:4]
        expectation = None  # 次日竞价预期
        try:
            t = int(time_str)
            if t <= 1000:
                score += 18
                reasons.append('早盘秒板(≤10:00)')
                expectation = self.EXPECTATION_EARLY
                detail['next_day_expect'] = '次日预期高开5-9%或一字板'
            elif t <= 1030:
                score += 12
                reasons.append('上午封板(≤10:30)')
                expectation = self.EXPECTATION_EARLY
                detail['next_day_expect'] = '次日预期高开3-6%'
            elif t <= 1130:
                score += 6
                reasons.append('午前封板')
                expectation = self.EXPECTATION_MORNING
                detail['next_day_expect'] = '次日预期高开1-3%'
            elif t <= 1400:
                score += 3
                expectation = self.EXPECTATION_AFTERNOON
                detail['next_day_expect'] = '次日预期平开或小幅高开'
            else:
                expectation = self.EXPECTATION_LATE
                detail['next_day_expect'] = '次日预期平开或低开，关注弱转强'
        except (ValueError, TypeError):
            expectation = self.EXPECTATION_AFTERNOON

        # 3) 封成比（封单额/成交额）
        if lu.turnover > 0:
            seal_ratio = lu.seal_amount / lu.turnover
            detail['seal_ratio'] = round(seal_ratio, 2)
            if seal_ratio > 1.0:
                score += 18
                reasons.append(f'封成比{seal_ratio:.2f}>1.0·极强')
                detail['volume_type'] = '缩量加速'
            elif seal_ratio > 0.5:
                score += 12
                reasons.append(f'封成比{seal_ratio:.2f}>0.5·封单充足')
                detail['volume_type'] = '健康换手'
            elif seal_ratio > 0.3:
                score += 5
                reasons.append(f'封成比{seal_ratio:.2f}')
                detail['volume_type'] = '适度分歧'
            else:
                detail['volume_type'] = '分歧较大'
                # 分歧大但不一定差——可能是弱转强候补
                if lu.is_seal and not lu.is_broken:
                    reasons.append('分歧回封·关注弱转强')
        else:
            detail['volume_type'] = '数据缺失'

        # 4) 封单强度（封单额/流通市值）
        if lu.float_market_cap > 0:
            seal_strength = lu.seal_amount / (lu.float_market_cap * 10000)
            detail['seal_strength'] = round(seal_strength, 4)
            if seal_strength > 0.02:
                score += 12
                reasons.append(f'封单强度{seal_strength:.3f}·资金坚决')
            elif seal_strength > 0.01:
                score += 5
                reasons.append('封单适量')

        # 5) 封死且未炸板
        if lu.is_seal and not lu.is_broken:
            score += 8
            reasons.append('封死未炸板')
        elif lu.is_seal and lu.is_broken:
            score += 3
            reasons.append('炸板回封·分歧转一致')
            # 炸板回封是弱转强候补
            detail['weak_to_strong_candidate'] = True

        # 6) 股性（历史涨停次数）
        limit_up_count = self._count_limit_ups(lu.code)
        detail['limit_up_count'] = limit_up_count
        if limit_up_count >= 10:
            score += 8
            reasons.append(f'股性活跃({limit_up_count}次年涨停)')
        elif limit_up_count >= 5:
            score += 3
            reasons.append(f'股性尚可({limit_up_count}次)')
        elif limit_up_count <= 1:
            score -= 3
            reasons.append('股性待验证(年涨停≤1)')

        # ============================================
        # 二、盘后模式：预判次日竞价预期（不依赖竞价数据）
        # ============================================
        if night_mode:
            # 根据今日表现量化次日预期
            if expectation == self.EXPECTATION_EARLY:
                if detail.get('volume_type') == '缩量加速':
                    detail['auction_forecast'] = '次日大概率一字板或T字板，竞价涨幅+7%以上'
                    detail['action_plan'] = '若次日竞价缩量高开→持仓不动；若竞价爆量→看承接再决定'
                else:
                    detail['auction_forecast'] = '次日竞价涨幅+4~7%，竞价量需达昨日5-10%'
                    detail['action_plan'] = '竞价高开+放量=超预期→可竞价上车；缩量高开=诱多→等分时回调确认'
            elif expectation == self.EXPECTATION_MORNING:
                detail['auction_forecast'] = '次日竞价涨幅+1~3%，竞价量需达昨日3-5%'
                detail['action_plan'] = '竞价符合预期→观察前15分钟量能和分时承接再决定'
            elif expectation in (self.EXPECTATION_AFTERNOON, self.EXPECTATION_LATE):
                detail['auction_forecast'] = '次日大概率平开或低开-3%~0%'
                detail['action_plan'] = '若竞价高开3%以上→弱转强超预期=买点；平低开=符合预期→观察承接'

            # 弱转强候选标注
            if (lu.is_broken or detail.get('volume_type') in ('分歧较大',) or
                    expectation == self.EXPECTATION_LATE):
                if not detail.get('weak_to_strong_candidate'):
                    detail['weak_to_strong_candidate'] = True
                detail['action_plan'] = (
                    detail.get('action_plan', '')
                    + '【弱转强候补】若次日高开3%以上且竞价量>昨日5%，构成弱转强买点'
                )

        # ============================================
        # 三、竞价模式：结合次日竞价做确认
        # ============================================
        if auction and not night_mode:
            open_pct = auction.open_change_pct
            detail['auction_open_pct'] = round(open_pct, 1)
            detail['auction_volume'] = auction.auction_volume
            detail['auction_amount'] = round(auction.auction_amount, 0)

            # --- 判断超预期/符合预期/弱于预期 ---
            if expectation == self.EXPECTATION_EARLY:
                if open_pct >= 7:
                    score += 10
                    reasons.append(f'竞价{open_pct:.1f}%·符合预期(强势)')
                    detail['expectation_status'] = '符合预期·强'
                elif open_pct >= 3:
                    score += 5
                    reasons.append(f'竞价{open_pct:.1f}%·符合预期')
                    detail['expectation_status'] = '符合预期'
                elif open_pct < 2:
                    score -= 8
                    reasons.append(f'竞价仅{open_pct:.1f}%·强转弱!')
                    detail['expectation_status'] = '弱于预期⚠'
                    detail['action_plan'] = '该强不强=最大利空，竞价直接核按钮或开盘反抽无力止损'

            elif expectation == self.EXPECTATION_MORNING:
                if open_pct >= 5:
                    score += 12
                    reasons.append(f'竞价{open_pct:.1f}%·超预期!')
                    detail['expectation_status'] = '超预期⭐'
                elif open_pct >= 1:
                    score += 5
                    reasons.append(f'竞价{open_pct:.1f}%·符合预期')
                    detail['expectation_status'] = '符合预期'
                elif open_pct < -1:
                    score -= 5
                    reasons.append(f'竞价{open_pct:.1f}%·弱于预期')
                    detail['expectation_status'] = '弱于预期'

            elif expectation in (self.EXPECTATION_AFTERNOON, self.EXPECTATION_LATE):
                # 下午板/尾盘板：平低开是正常预期，高开是超预期（弱转强）
                if open_pct >= 3:
                    score += 15
                    reasons.append(f'竞价高开{open_pct:.1f}%·弱转强超预期!!')
                    detail['expectation_status'] = '超预期·弱转强⭐'
                    detail['weak_to_strong_confirmed'] = True
                elif open_pct >= 0:
                    score += 3
                    reasons.append(f'竞价{open_pct:.1f}%·符合预期')
                    detail['expectation_status'] = '符合预期'
                elif open_pct < -3:
                    score -= 3
                    reasons.append(f'竞价低开{open_pct:.1f}%·弱于预期')
                    detail['expectation_status'] = '弱于预期'

            # 竞价量能验证 — 直接从TDX读昨日爆量
            yesterday_max_vol = self._read_trailing_max_volume(lu.code)
            if yesterday_max_vol > 0 and auction.auction_volume > 0:
                vol_ratio = auction.auction_volume / yesterday_max_vol
                detail['auction_vol_ratio'] = round(vol_ratio, 3)
                if vol_ratio >= 0.5 and open_pct >= 3:
                    score += 8
                    reasons.append('爆量高开·增量资金进场')
                elif vol_ratio >= 0.3:
                    score += 4
                    reasons.append(f'竞价量充分({vol_ratio:.2f})')
                elif vol_ratio < 0.05 and open_pct > 3:
                    score -= 3
                    reasons.append('缩量高开·诱多嫌疑')

            # 竞价额 > 1000万（小盘标准）
            if auction.auction_amount > 1000 and open_pct >= 3:
                score += 5
                reasons.append(f'竞价额{auction.auction_amount:.0f}万·资金真实')

        # ============================================
        # 四、技术面加分（TDX数据）
        # ============================================
        try:
            market = 'sh' if lu.code.startswith('6') else 'sz'
            if lu.code.startswith('8') or lu.code.startswith('4'):
                market = 'bj'
            df = self.tdx.read_daily(lu.code, market)
            if len(df) >= 60:
                df = enrich_all(df)
                latest = df.iloc[-1]

                # MA多头排列检查
                ma10 = latest.get('ma10', 0)
                ma20 = latest.get('ma20', 0)
                if ma10 > 0 and ma20 > 0:
                    if ma10 > ma20:
                        score += 5
                        reasons.append('10日线>20日线·多头排列')
                        detail['ma_bullish'] = True

                    # 5日线不下穿10日线
                    ma5 = latest.get('ma5', 0)
                    if ma5 > 0 and ma5 > ma10:
                        score += 3
                        reasons.append('5日线上·短线强势')

                # 涨停突破60日线
                ma60 = latest.get('ma60', 0)
                close = float(latest['close'])
                if ma60 > 0 and close > ma60:
                    prev_close = float(df['close'].iloc[-2]) if len(df) >= 2 else 0
                    if prev_close > 0 and prev_close <= ma60:
                        score += 8
                        reasons.append('涨停突破60日线')
                        detail['break_ma60'] = True

        except Exception:
            pass

        return score, reasons, detail

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
