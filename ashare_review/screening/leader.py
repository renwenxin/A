"""龙头筛选器 — 龙哥5条件选股体系 + 连板接力

核心逻辑（逆向思维）：
龙头在诞生过程中一定会出现涨停/新高，从涨停和新高的品种里选，
选到龙头的概率大大提高。

5个必要条件（缺一不可）：
1. 近期有过涨停或8%以上大阳线 → 大资金进场
2. 成交额 > 阈值 → 市场合力，非单一资金
3. 成交量为过去6个月最大量 → 新老资金完成切换
4. 价格在新高或新高附近 → 天然吸引做多资金
5. 非ST、非北交所

在此基础上，对连板股做接力评分：N字结构、换手板、早盘封板等加分项。
"""
import os, struct
from typing import List
import numpy as np
from .base import BaseScreener
from ..data.models import ScreeningResult
from ..analysis.pattern import detect_n_pattern
from ..analysis.indicators import enrich_all
from ..data.tdx_reader import RECORD_SIZE


class LeaderScreener(BaseScreener):
    """龙头筛选: 5条件初筛 + 连板接力评分 + 筹码分析"""

    name = '龙头'

    # 5条件阈值
    MIN_TURNOVER = 50000       # 最小成交额(万元) = 5亿
    MIN_CHANGE_PCT = 8.0       # 最小涨幅(大阳线)
    LOOKBACK_MONTHS = 126      # 6个月 ≈ 126个交易日
    MIN_RECENT_LIMIT_UP = 1    # 近期至少1次涨停

    def screen(self) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool()

        # 分成两路：连板龙（consecutive >= 2）和 低位启动龙（首板但满足5条件）
        multi_board = [lu for lu in limit_ups if lu.consecutive >= 2]
        first_board = [lu for lu in limit_ups if lu.is_first]

        results = []

        # ---- 连板龙：接力评分 ----
        for lu in multi_board:
            score, reasons, detail = self._evaluate_multi_board(lu)
            if score >= 30:
                results.append(ScreeningResult(
                    code=lu.code, name=lu.name, strategy=self.name,
                    score=min(score, 100), reasons=reasons,
                    detail=detail
                ))

        # ---- 低位启动龙：5条件筛选 ----
        for lu in first_board:
            # 过滤一字板（散户买不到）
            if lu.board_type == '一字板':
                continue
            score, reasons, detail = self._evaluate_first_board_leader(lu)
            if score >= 35:
                results.append(ScreeningResult(
                    code=lu.code, name=lu.name, strategy=self.name,
                    score=min(score, 100), reasons=reasons,
                    detail=detail
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:25]

    # ------------------------------------------------------------------
    # 连板龙评分
    # ------------------------------------------------------------------
    def _evaluate_multi_board(self, lu) -> tuple:
        score = 25
        reasons = [f'{lu.consecutive}连板']
        detail = {
            'consecutive': lu.consecutive,
            'board_type': lu.board_type,
            'limit_up_time': lu.limit_up_time,
            'seal_amount': round(lu.seal_amount, 0),
        }

        try:
            market = 'sh' if lu.code.startswith('6') else 'sz'
            if lu.code.startswith('8') or lu.code.startswith('4'):
                market = 'bj'
            df = self.tdx.read_daily(lu.code, market)
            if not df.empty:
                df = enrich_all(df)
                latest = df.iloc[-1]

                # N字结构加分
                n_pattern = detect_n_pattern(df)
                if n_pattern:
                    score += 20
                    reasons.append('N字结构')
                    detail['n_pattern'] = True

                # 换手板 vs 一字板
                time_str = str(lu.limit_up_time).replace(':', '')[:4]
                is_yizi = time_str == '0925'

                if not is_yizi:
                    score += 15
                    reasons.append('换手板上位')
                else:
                    reasons.append('一字板·需等开板换手')

                # 早盘封板
                try:
                    t = int(time_str)
                    if t <= 1000 and not is_yizi:
                        score += 12
                        reasons.append('早盘封板(≤10:00)')
                    elif t <= 1030:
                        score += 6
                        reasons.append('上午封板')
                except (ValueError, TypeError):
                    pass

                # 封单强度
                if lu.float_market_cap > 0 and lu.seal_amount > 0:
                    seal_ratio = lu.seal_amount / (lu.float_market_cap * 10000)
                    if seal_ratio > 0.02:
                        score += 10
                        reasons.append(f'封单强度{seal_ratio:.3f}')
                        detail['seal_ratio'] = round(seal_ratio, 4)

                # 成交量是否近期最大（6个月内）
                vol_6m_max = self._check_volume_6m_max(df)
                if vol_6m_max:
                    score += 12
                    reasons.append('量能=6个月最大·新旧资金切换')
                    detail['vol_6m_max'] = True

                # 是否在新高附近（60日新高）
                near_high = self._check_near_high(df)
                if near_high:
                    score += 8
                    reasons.append('新高附近')
                    detail['near_high'] = True

                # 涨停次数加成
                limit_up_count = self._count_limit_ups(lu.code)
                if limit_up_count >= 10:
                    score += 8
                    reasons.append(f'年涨停{limit_up_count}次·股性活跃')
                    detail['limit_up_count'] = limit_up_count
                elif limit_up_count >= 5:
                    score += 3
                    detail['limit_up_count'] = limit_up_count

                # 筹码分析
                try:
                    from ..analysis.chip import detect_chip_patterns
                    patterns = detect_chip_patterns(df)
                    chip_signals = []
                    for p in patterns:
                        if p['signal'] in ('买入', '持有'):
                            score += 5
                            chip_signals.append(p['pattern'])
                        elif p['signal'] == '卖出':
                            score -= 10
                            chip_signals.append(f'⚠{p["pattern"]}')
                        elif p['signal'] == '警示':
                            score -= 5
                            chip_signals.append(f'⚠{p["pattern"]}')
                    if chip_signals:
                        detail['chip_signals'] = chip_signals
                except Exception:
                    pass

        except Exception:
            pass

        return score, reasons, detail

    # ------------------------------------------------------------------
    # 低位启动龙：5条件体系
    # ------------------------------------------------------------------
    def _evaluate_first_board_leader(self, lu) -> tuple:
        """对首板标的执行龙哥5条件打分"""
        score = 0
        reasons = []
        detail = {
            'consecutive': 1,
            'board_type': lu.board_type,
            'limit_up_time': lu.limit_up_time,
            'seal_amount': round(lu.seal_amount, 0),
        }
        conditions_met = 0
        total_conditions = 5

        # ---- 条件1：近期涨停或8%以上阳线 ----
        # 首板本身就是涨停，满足条件1
        conditions_met += 1
        score += 15
        reasons.append('首板涨停·大资金进场')

        # 检查是否是放量首板（更强势）
        try:
            market = 'sh' if lu.code.startswith('6') else 'sz'
            if lu.code.startswith('8') or lu.code.startswith('4'):
                market = 'bj'
            df = self.tdx.read_daily(lu.code, market)
            if len(df) >= 2:
                prev_vol = df['volume'].iloc[-2]
                today_vol = df['volume'].iloc[-1]
                if prev_vol > 0 and today_vol / prev_vol >= 1.5:
                    score += 8
                    reasons.append('放量首板(量>昨日1.5倍)')
                    detail['vol_ratio'] = round(float(today_vol / prev_vol), 1)
        except Exception:
            pass

        # ---- 条件2：成交额 > 5亿（市场合力） ----
        if lu.turnover >= self.MIN_TURNOVER:
            conditions_met += 1
            score += 15
            turnover_yi = lu.turnover / 10000
            reasons.append(f'成交额{turnover_yi:.1f}亿·市场合力')
            detail['turnover_yi'] = round(turnover_yi, 1)
        elif lu.turnover >= 20000:  # 2亿以上给部分分
            score += 8
            reasons.append(f'成交额{lu.turnover/10000:.1f}亿')

        # ---- 条件3：成交量为过去6个月最大量（核心条件） ----
        vol_6m_max = False
        try:
            market = 'sh' if lu.code.startswith('6') else 'sz'
            if lu.code.startswith('8') or lu.code.startswith('4'):
                market = 'bj'
            df = self.tdx.read_daily(lu.code, market)
            if not df.empty:
                df = enrich_all(df)
                vol_6m_max = self._check_volume_6m_max(df)
                if vol_6m_max:
                    conditions_met += 1
                    score += 20
                    reasons.append('量能=6个月最大·新旧资金切换(核心)')
                    detail['vol_6m_max'] = True
        except Exception:
            pass

        # ---- 条件4：价格在新高或新高附近 ----
        near_high = False
        try:
            market = 'sh' if lu.code.startswith('6') else 'sz'
            if lu.code.startswith('8') or lu.code.startswith('4'):
                market = 'bj'
            df = self.tdx.read_daily(lu.code, market)
            if not df.empty:
                near_high = self._check_near_high(df)
                if near_high:
                    conditions_met += 1
                    score += 15
                    reasons.append('新高附近·做多引力强')
                    detail['near_high'] = True

                # 10日均线 > 20日均线（多头排列）
                ma_check = self._check_ma_bullish(df)
                if ma_check:
                    score += 5
                    reasons.append('10日线>20日线·多头排列')
        except Exception:
            pass

        # ---- 条件5：非ST非北交所（已在base过滤） ----
        conditions_met += 1
        score += 5

        # ---- 额外加分项 ----
        # 涨停次数
        limit_up_count = self._count_limit_ups(lu.code)
        if limit_up_count >= 10:
            score += 5
            reasons.append(f'年涨停{limit_up_count}次·股性活跃')
            detail['limit_up_count'] = limit_up_count
        elif limit_up_count >= 5:
            score += 2
            detail['limit_up_count'] = limit_up_count

        # 早盘封板
        time_str = str(lu.limit_up_time).replace(':', '')[:4]
        try:
            t = int(time_str)
            if t <= 1000:
                score += 8
                reasons.append('早盘封板(≤10:00)')
            elif t <= 1030:
                score += 4
                reasons.append('上午封板')
        except (ValueError, TypeError):
            pass

        # 封单强度
        if lu.float_market_cap > 0 and lu.seal_amount > 0:
            seal_ratio = lu.seal_amount / (lu.float_market_cap * 10000)
            if seal_ratio > 0.02:
                score += 5
                reasons.append(f'封单强度{seal_ratio:.3f}')
                detail['seal_ratio'] = round(seal_ratio, 4)

        # 流通市值合适（10-150亿最适合龙头）
        if 10 <= lu.float_market_cap <= 150:
            score += 5
            reasons.append(f'流通市值{lu.float_market_cap:.0f}亿·适中')

        detail['conditions_met'] = f'{conditions_met}/{total_conditions}'
        return score, reasons, detail

    # ------------------------------------------------------------------
    # 辅助检查
    # ------------------------------------------------------------------
    def _check_volume_6m_max(self, df) -> bool:
        """检查最近5日成交量是否出现6个月新高"""
        if len(df) < self.LOOKBACK_MONTHS:
            return len(df) >= 20 and df['volume'].iloc[-1] >= df['volume'].iloc[-len(df):].max()
        recent_max = df['volume'].iloc[-5:].max()
        prior_max = df['volume'].iloc[-self.LOOKBACK_MONTHS:].max()
        return recent_max >= prior_max

    def _check_near_high(self, df, period: int = 60) -> bool:
        """价格是否在N日新高附近（距最高点5%以内）"""
        if len(df) < period:
            period = len(df) - 1
        if period < 5:
            return False
        recent_high = df['close'].iloc[-period:].max()
        latest_close = float(df['close'].iloc[-1])
        if recent_high <= 0:
            return False
        return (recent_high - latest_close) / recent_high <= 0.05

    def _check_ma_bullish(self, df) -> bool:
        """10日均线 > 20日均线（短线多头）"""
        try:
            ma10 = df['ma10'].iloc[-1]
            ma20 = df['ma20'].iloc[-1]
            return ma10 > ma20 and ma10 > 0
        except Exception:
            return False
