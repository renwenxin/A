"""板块分歧介入筛选器 — 龙哥板块分歧战法

核心逻辑：
涨停潮次日板块会出现分歧（部分个股回调），此时找抗跌标的低吸。
"别人恐慌时我贪婪"的前提是：找到真正有资金承接的标的。

五维评分体系（各20分，满分100）：
1. 价格韧性：今日涨跌幅 vs 板块中位数（逆势或抗跌加分）
2. 均线支撑：是否站上MA10/MA20（主力护盘迹象）
3. 量价形态：放量但跌幅小 = 吸筹，缩量回调 = 抛压衰竭
4. 昨日封板质量：封死、早盘、封单强（主力做多意愿）
5. MACD趋势：金叉+零轴上（中期趋势未坏）
"""
from typing import List, Dict, Optional
from collections import Counter, defaultdict
import numpy as np
from .base import BaseScreener
from ..data.models import ScreeningResult, LimitUpInfo
from ..utils.calendar import TradingCalendar
from ..analysis.indicators import enrich_all


class SectorDivergenceScreener(BaseScreener):
    """板块分歧介入: 昨日涨停潮 → 今日分歧抗跌 → 低吸机会"""

    name = '板块分歧介入'

    # 阈值
    MIN_HOT_SECTOR_COUNT = 5     # 昨日板块此数以上才算涨停潮
    TOP_RESULTS = 25             # 返回结果数
    SCORE_PER_DIMENSION = 20     # 每维度满分

    def screen(self) -> List[ScreeningResult]:
        # ---- 1. 获取昨日交易日 ----
        calendar = TradingCalendar()
        yesterday = calendar.prev_trading_day()
        yesterday_str = yesterday.strftime('%Y%m%d')

        # ---- 2. 获取昨日涨停池 ----
        yesterday_ups = self.ak.get_limit_up_pool(trade_date=yesterday_str)
        if not yesterday_ups:
            return []

        # ---- 3. 按板块分组，找出热点板块 ----
        sector_stocks: Dict[str, List[LimitUpInfo]] = defaultdict(list)
        for lu in yesterday_ups:
            sec = lu.board_type or '未分类'
            sector_stocks[sec].append(lu)

        hot_sectors = {
            sec: stocks for sec, stocks in sector_stocks.items()
            if len(stocks) >= self.MIN_HOT_SECTOR_COUNT
        }
        if not hot_sectors:
            return []

        # 构建昨日涨停股票代码集合 + 昨日信息映射
        yesterday_map: Dict[str, LimitUpInfo] = {}
        for sec, stocks in hot_sectors.items():
            for lu in stocks:
                yesterday_map[lu.code] = lu

        # ---- 4. 获取今日实时行情（盘中补充） ----
        today_spot: Dict[str, dict] = {}
        try:
            spot_df = self.ak.get_spot_df()
            if spot_df is not None and not spot_df.empty:
                for _, row in spot_df.iterrows():
                    c = str(row.get('代码', '')).zfill(6)
                    try:
                        pct = float(row.get('涨跌幅', 0))
                    except (ValueError, TypeError):
                        pct = 0
                    try:
                        price = float(row.get('最新价', 0))
                    except (ValueError, TypeError):
                        price = 0
                    today_spot[c] = {'price': price, 'change_pct': pct}
        except Exception:
            pass

        # ---- 5. 对每个热点板块的股票做抗跌分析 ----
        all_results = []
        for sector_name, yesterday_stocks in hot_sectors.items():
            # 收集板块内所有股票的今日数据
            sector_data = []  # list of (code, df, latest_row)
            for lu in yesterday_stocks:
                try:
                    market = 'sh' if lu.code.startswith('6') else 'sz'
                    if lu.code.startswith('8') or lu.code.startswith('4'):
                        market = 'bj'
                    df = self.tdx.read_daily(lu.code, market)
                    if len(df) < 20:
                        continue
                    df = enrich_all(df)
                    latest = df.iloc[-1]
                    if latest.get('close', 0) <= 0:
                        continue
                    sector_data.append((lu.code, df, latest))
                except Exception:
                    continue

            if len(sector_data) < 2:
                continue

            # 计算板块涨跌幅中位数
            sector_changes = []
            for _, _, latest in sector_data:
                chg = latest.get('change_pct', 0)
                if not (chg is None or (isinstance(chg, float) and np.isnan(chg))):
                    sector_changes.append(float(chg))
            sector_median = float(np.median(sector_changes)) if sector_changes else 0.0

            # ---- 5. 对每只股票打分 ----
            for code, df, latest in sector_data:
                yesterday_lu = yesterday_map.get(code)
                spot = today_spot.get(code, {})
                score, reasons, detail = self._score_anti_falling(
                    code, latest, df, yesterday_lu, sector_median, sector_name,
                    len(yesterday_stocks), spot
                )
                if score >= 30:
                    name = self._get_name(code) or (yesterday_lu.name if yesterday_lu else '')
                    all_results.append(ScreeningResult(
                        code=code, name=name, strategy=self.name,
                        score=min(score, 100), reasons=reasons,
                        detail=detail
                    ))

        # ---- 6. 排序返回 ----
        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results[:self.TOP_RESULTS]

    # ------------------------------------------------------------------
    # 五维抗跌评分
    # ------------------------------------------------------------------
    def _score_anti_falling(self, code: str, latest, df,
                             yesterday_lu: Optional[LimitUpInfo],
                             sector_median: float, sector_name: str,
                             yesterday_zt_count: int,
                             spot: dict = None) -> tuple:
        score = 0
        reasons = []
        detail = {
            'sector': sector_name,
            'total_limit_up': yesterday_zt_count,
            'sector_median_change': round(sector_median, 1),
        }

        close = float(latest.get('close', 0))
        if close <= 0:
            return 0, [], detail

        # 优先使用当日实时涨跌幅（盘中），回退到TDX的change_pct（盘后）
        spot_change = (spot or {}).get('change_pct')
        change_pct = spot_change if spot_change is not None else float(latest.get('change_pct', 0))
        if change_pct is None or (isinstance(change_pct, float) and np.isnan(change_pct)):
            change_pct = 0.0
        detail['change_pct'] = round(change_pct, 1)

        # 当日实时价格
        spot_price = (spot or {}).get('price', 0)
        if spot_price and spot_price > 0:
            detail['today_price'] = spot_price

        # ============================================
        # 维度一：价格韧性（20分）
        # ============================================
        better_than_median = change_pct - sector_median
        if change_pct > 0 and sector_median < 0:
            # 板块整体下跌但该股逆势上涨 — 最强
            score += 20
            reasons.append(f'逆势上涨{change_pct:.1f}%(板块中位数{sector_median:.1f}%)')
            detail['price_resilience'] = '逆势上涨'
        elif better_than_median > 1:
            score += 18
            reasons.append(f'抗跌(跌幅{change_pct:.1f}% < 板块中位{sector_median:.1f}%)')
            detail['price_resilience'] = '显著抗跌'
        elif better_than_median > 0:
            score += 14
            reasons.append(f'略强于板块(跌幅{change_pct:.1f}%)')
            detail['price_resilience'] = '略抗跌'
        elif better_than_median > -1:
            score += 8
            reasons.append(f'与板块同步(跌幅{change_pct:.1f}%)')
            detail['price_resilience'] = '同步'
        elif better_than_median > -3:
            score += 3
            detail['price_resilience'] = '弱于板块'
        else:
            detail['price_resilience'] = '领跌'
            # 领跌标的可能还有更低，不加分

        # ============================================
        # 维度二：均线支撑（20分）
        # ============================================
        ma10 = float(latest.get('ma10', 0))
        ma20 = float(latest.get('ma20', 0))
        ma_support = 0

        if ma10 > 0 and close > ma10:
            score += 10
            reasons.append('站上MA10')
            ma_support += 1
            detail['ma10_above'] = True
        elif ma10 > 0:
            detail['ma10_above'] = False
            pct_below = (close - ma10) / ma10 * 100
            if pct_below > -2:  # 跌破不多
                score += 4
                reasons.append(f'MA10附近({pct_below:.1f}%)')

        if ma20 > 0 and close > ma20:
            score += 10
            reasons.append('站上MA20')
            ma_support += 1
            detail['ma20_above'] = True
        elif ma20 > 0:
            detail['ma20_above'] = False
            pct_below = (close - ma20) / ma20 * 100
            if pct_below > -3:
                score += 3
                reasons.append(f'MA20附近({pct_below:.1f}%)')

        detail['ma_support'] = ma_support

        # ============================================
        # 维度三：量价形态（20分）
        # ============================================
        vol_ratio = float(latest.get('volume_ratio', 1))
        if vol_ratio is None or (isinstance(vol_ratio, float) and np.isnan(vol_ratio)):
            vol_ratio = 1.0
        detail['volume_ratio'] = round(vol_ratio, 1)

        if vol_ratio > 1.5 and change_pct > -2:
            score += 20
            reasons.append(f'放量吸筹(量比{vol_ratio:.1f}·拒绝下跌)')
            detail['vol_pattern'] = '放量吸筹'
        elif vol_ratio > 1.2 and change_pct > -3:
            score += 15
            reasons.append(f'温和吸筹(量比{vol_ratio:.1f})')
            detail['vol_pattern'] = '温和吸筹'
        elif vol_ratio > 1.0:
            score += 10
            reasons.append('有量承接')
            detail['vol_pattern'] = '有量承接'
        elif vol_ratio > 0.6 and change_pct > -2:
            score += 6
            reasons.append('缩量抗跌·抛压衰竭')
            detail['vol_pattern'] = '缩量抗跌'
        else:
            score += 2
            detail['vol_pattern'] = '缩量回调'

        # ============================================
        # 维度四：昨日封板质量（20分）
        # ============================================
        if yesterday_lu:
            # 封死且未炸板
            if yesterday_lu.is_seal and not yesterday_lu.is_broken:
                score += 10
                reasons.append('昨日封死涨停')
                detail['yesterday_sealed'] = True
            elif yesterday_lu.is_seal and yesterday_lu.is_broken:
                score += 4
                reasons.append('昨日炸板回封·分歧转一致')
                detail['yesterday_broken'] = True
            else:
                detail['yesterday_sealed'] = False

            # 封板时间
            time_str = str(yesterday_lu.limit_up_time).replace(':', '')[:4]
            try:
                t = int(time_str)
                if t <= 1000:
                    score += 6
                    reasons.append('昨日早盘封板')
                    detail['yesterday_time_early'] = True
                elif t <= 1030:
                    score += 3
                    reasons.append('昨日上午封板')
            except (ValueError, TypeError):
                pass

            # 封单强度
            if yesterday_lu.float_market_cap > 0 and yesterday_lu.seal_amount > 0:
                seal_strength = yesterday_lu.seal_amount / (yesterday_lu.float_market_cap * 10000)
                if seal_strength > 0.01:
                    score += 4
                    reasons.append(f'昨日封单强({seal_strength:.3f})')
                    detail['yesterday_seal_strong'] = True
            detail['yesterday_time'] = yesterday_lu.limit_up_time
        else:
            # 不在涨停池内但板块热点中有提及（可能是ST或非A股）
            score += 3

        # ============================================
        # 维度五：MACD趋势（20分）
        # ============================================
        dif = float(latest.get('macd_dif', 0))
        dea = float(latest.get('macd_dea', 0))

        if dif is None or (isinstance(dif, float) and np.isnan(dif)):
            dif = 0.0
        if dea is None or (isinstance(dea, float) and np.isnan(dea)):
            dea = 0.0

        detail['macd_dif'] = round(dif, 2)

        if dif > dea:
            score += 10
            reasons.append('MACD金叉')
            detail['macd_golden'] = True
        else:
            detail['macd_golden'] = False
            # 死叉但收敛中也是好的
            if len(df) >= 5:
                dif_prev = float(df['macd_dif'].iloc[-5])
                if not np.isnan(dif_prev) and dif > dif_prev:
                    score += 4
                    reasons.append('MACD收敛中')

        if dif > 0:
            score += 10
            reasons.append('MACD零轴上')
            detail['macd_above_zero'] = True
        elif dif > -0.05:
            score += 5
            reasons.append('MACD零轴附近')
            detail['macd_above_zero'] = False
        else:
            detail['macd_above_zero'] = False

        return score, reasons, detail
