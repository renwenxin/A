"""突破形态筛选器 — 龙哥均线体系 + 量价验证

硬性条件：
- 近一年涨停次数 > 10
- 非 ST / *ST

形态检测（满足任一即可）：
- 箱体突破 / W底 / N字结构
- 放量突破 / 成交量复合炮

均线体系增强（龙哥均线口诀）：
- 5/10日线：短线生命线，5日破位必减仓
- 20日线：中线导航标，走平横→观望，向上翘→建仓
- 60/89日线粘合再发散 → 资金进场要大涨
- 均线背离 → 涨跌反转预警

量能验证（龙哥强调）：
- 均线向好但缩量 → 假突破，涨幅受限
- 均线支撑且放量 → 上涨有持续性
- 均线破位后反抽 → 不是反转，等重新站稳+放量
"""
from typing import List
import numpy as np
from .base import BaseScreener
from ..data.models import ScreeningResult
from ..data.akshare_fetcher import AkshareFetcher


class BreakoutScreener(BaseScreener):
    """突破形态筛选: 涨停活跃 + 形态/均线/量价三重确认"""

    name = '突破形态'

    def screen(self) -> List[ScreeningResult]:
        # 获取股票列表
        try:
            spot_df = self.ak.get_spot_df()
        except Exception:
            spot_df = None

        stocks = self.tdx.list_stocks()
        stocks = [(c, m) for c, m in stocks
                  if m != 'bj' and AkshareFetcher._is_a_stock(c)]

        results = []
        for code, market in stocks:
            # --- 名称和 ST 过滤 ---
            name = ''
            if spot_df is not None and not spot_df.empty:
                match = spot_df[spot_df['代码'] == code]
                if not match.empty:
                    name = str(match.iloc[0].get('名称', ''))
            if not name:
                name = self._get_name(code)
            if not name:
                name = self._get_name_from_auction(code)
            if name.startswith(('ST', '*ST', 'SST', 'S*ST', 'NST')):
                continue

            # --- 近一年涨停 > 10 次（前置快速过滤） ---
            limit_up_count = self._count_limit_ups(code)
            if limit_up_count <= 10:
                continue

            # --- 加载日线 ---
            try:
                df = self.tdx.read_daily(code, market)
                if len(df) < 60:
                    continue
            except Exception:
                continue

            score, reasons, detail = self._evaluate_breakout(
                df, code, name, limit_up_count
            )
            if score >= 20:
                results.append(ScreeningResult(
                    code=code, name=name, strategy=self.name,
                    score=min(round(score), 100), reasons=reasons,
                    detail=detail
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:50]

    # ------------------------------------------------------------------
    # 突破评估
    # ------------------------------------------------------------------
    def _evaluate_breakout(self, df, code: str, name: str,
                            limit_up_count: int) -> tuple:
        score = 0.0
        reasons = []
        detail = {
            'limit_up_count': limit_up_count,
            'close': float(df['close'].iloc[-1]) if len(df) > 0 else 0,
        }

        reasons.append(f'年涨停{limit_up_count}次')

        # 涨停次数基础分
        lu_score = min(limit_up_count / 30 * 40, 40)
        score += lu_score

        # --- 技术指标计算 ---
        try:
            from ..analysis.indicators import enrich_all
            from ..analysis.pattern import (
                detect_box_breakout, detect_w_bottom, detect_n_pattern
            )
            from ..analysis.volume import detect_volume_breakout, detect_volume_cannon

            df = enrich_all(df)
            latest = df.iloc[-1]
            close = float(latest['close'])

            # ============================================
            # 一、均线体系（龙哥口诀）
            # ============================================
            ma_signals = self._eval_ma_system(df)

            # 5日线/10日线（短线生命线）
            if ma_signals.get('ma5_above_ma10'):
                score += 8
                reasons.append('5日线>10日线·短线多头')

            # 20日线向上翘（中线趋势要起跳）
            if ma_signals.get('ma20_slope_up'):
                score += 8
                reasons.append('20日线向上翘·中线趋势启动')

            # 三线粘合再发散（5/10/20）
            if ma_signals.get('triple_converge'):
                score += 15
                reasons.append('⭐三线(5/10/20)粘合发散·资金进场')
                detail['triple_converge'] = True

            # 60/89日线粘合（大级别行情）
            if ma_signals.get('ma60_89_converge'):
                if ma_signals.get('ma60_89_slope_up'):
                    score += 12
                    reasons.append('60/89日线粘合向上·大级别行情')
                else:
                    score += 5
                    reasons.append('60/89日线粘合·关注方向选择')

            # 季线以上级别（中线趋势核心）
            ma60 = float(latest.get('ma60', 0))
            if ma60 > 0 and close > ma60:
                score += 5
                reasons.append('站上季线(60日)')
            else:
                score -= 3
                reasons.append('季线压制')

            ma250 = float(latest.get('ma250', 0))
            if ma250 > 0:
                if close > ma250:
                    score += 5
                    reasons.append('站上年线')
                else:
                    score -= 5
                    reasons.append('年线压制·谨慎')

            # 均线背离检查
            if ma_signals.get('ma_divergence'):
                score -= 10
                reasons.append('⚠均线背离·涨跌反转预警')
                detail['ma_divergence'] = True

            # ============================================
            # 二、量价验证（均线信号需量能验证）
            # ============================================
            vol_verified = False

            # 检查量能状态
            vol_ratio = float(latest.get('volume_ratio', 1))
            detail['vol_ratio'] = round(vol_ratio, 1)

            if vol_ratio >= 2.0:
                score += 10
                reasons.append(f'量比{vol_ratio:.1f}·爆量突破')
                vol_verified = True
            elif vol_ratio >= 1.5:
                score += 6
                reasons.append(f'量比{vol_ratio:.1f}·温和放量')
                vol_verified = True
            elif vol_ratio >= 1.0:
                score += 2
            elif vol_ratio < 0.7:
                # 均线信号好但缩量 = 假突破嫌疑
                if score > 40:
                    score -= 5
                    reasons.append('缩量·警惕假突破')
                vol_verified = False

            volume_breakout = detect_volume_breakout(df)
            if volume_breakout:
                score += 10
                reasons.append('近期最大量·倍量突破')
                detail['volume_breakout'] = True
                vol_verified = True

            # 成交量复合炮
            cannons = detect_volume_cannon(df)
            if cannons:
                cannon = cannons[0]
                if cannon['count'] >= 4:
                    score += 12
                    reasons.append(f'量复合炮({cannon["count"]}连)')
                else:
                    score += 8
                    reasons.append(f'量炮({cannon["count"]}连)')
                detail['volume_cannon'] = cannon['count']

            # 均线+量能综合验证
            if ma_signals.get('triple_converge') and not vol_verified:
                score -= 3
                reasons.append('均线信号需量能验证')
                detail['needs_vol_confirm'] = True

            # ============================================
            # 三、形态检测
            # ============================================
            box = detect_box_breakout(df)
            if box:
                box_score = min(18, (box['breakout_pct'] / 3) * 18)
                score += box_score
                reasons.append(f'箱体突破({box["box_period"]}天·突破{box["breakout_pct"]:.1f}%)')
                detail['box_breakout'] = True

            wb = detect_w_bottom(df)
            if wb:
                score += 18
                reasons.append('W底突破')
                detail['w_bottom'] = True

            n_pat = detect_n_pattern(df)
            if n_pat:
                score += 12
                reasons.append('N字结构')
                detail['n_pattern'] = True

            # ============================================
            # 四、新高附近判断
            # ============================================
            if len(df) >= 60:
                recent_60_high = float(df['close'].iloc[-60:].max())
                if recent_60_high > 0:
                    pct_from_high = (recent_60_high - close) / recent_60_high * 100
                    if pct_from_high <= 3:
                        score += 8
                        reasons.append(f'距60日新高仅{pct_from_high:.1f}%')
                        detail['near_high'] = True

            # ============================================
            # 五、筹码分析
            # ============================================
            try:
                from ..analysis.chip import detect_chip_patterns
                patterns = detect_chip_patterns(df)
                for p in patterns:
                    if p['signal'] == '买入' and p['confidence'] == '高':
                        score += 10
                        reasons.append(f'筹码·{p["pattern"]}')
                        detail['chip_buy_signal'] = p['pattern']
                    elif p['signal'] == '持有':
                        score += 5
                        reasons.append(f'筹码·{p["pattern"]}')
            except Exception:
                pass

            detail['sector'] = self._get_sector(code)

        except Exception:
            # 形态检测失败不影响基本评分
            pass

        return score, reasons, detail

    # ------------------------------------------------------------------
    # 均线体系评估
    # ------------------------------------------------------------------
    def _eval_ma_system(self, df) -> dict:
        """评估均线体系，返回各项信号"""
        signals = {}
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest
        close = float(latest['close'])

        # 5>10>20 多头排列
        ma5 = float(latest.get('ma5', 0))
        ma10 = float(latest.get('ma10', 0))
        ma20 = float(latest.get('ma20', 0))
        if ma5 > 0 and ma10 > 0:
            signals['ma5_above_ma10'] = ma5 > ma10
        else:
            signals['ma5_above_ma10'] = False

        # 20日线走平横→观望，向上翘→建仓
        ma20_prev = float(prev.get('ma20', 0)) if len(df) > 2 else 0
        if ma20 > 0 and ma20_prev > 0:
            ma20_slope = (ma20 - ma20_prev) / ma20_prev * 100
            signals['ma20_slope_up'] = ma20_slope > 0.1
        else:
            signals['ma20_slope_up'] = False

        # 三线粘合（5/10/20日线间距<2%）且发散
        if ma5 > 0 and ma10 > 0 and ma20 > 0:
            max_ma = max(ma5, ma10, ma20)
            min_ma = min(ma5, ma10, ma20)
            spread = (max_ma - min_ma) / max_ma
            was_converge = spread < 0.03
            # 检查前10天是否更粘合（从粘合到发散）
            if len(df) >= 15:
                prev_ma5 = float(df['ma5'].iloc[-10]) if 'ma5' in df.columns else ma5
                prev_ma10 = float(df['ma10'].iloc[-10]) if 'ma10' in df.columns else ma10
                prev_ma20 = float(df['ma20'].iloc[-10]) if 'ma20' in df.columns else ma20
                if prev_ma5 > 0 and prev_ma20 > 0:
                    prev_max = max(prev_ma5, prev_ma10, prev_ma20)
                    prev_min = min(prev_ma5, prev_ma10, prev_ma20)
                    prev_spread = (prev_max - prev_min) / prev_max if prev_max > 0 else 0
                    signals['triple_converge'] = (prev_spread < 0.03 and spread < 0.06
                                                   and ma5 > ma10 > ma20)
                else:
                    signals['triple_converge'] = False
            else:
                signals['triple_converge'] = False
        else:
            signals['triple_converge'] = False

        # 60/89日线粘合+向上
        ma60 = float(latest.get('ma60', 0))
        ma89 = float(latest.get('ma89', 0))
        if ma60 > 0 and ma89 > 0:
            ma_diff = abs(ma60 - ma89) / ma89
            signals['ma60_89_converge'] = ma_diff < 0.03
            # 向上：ma60 和 ma89 都比3天前高
            if signals['ma60_89_converge'] and len(df) >= 5:
                ma60_3d = float(df['ma60'].iloc[-4])
                ma89_3d = float(df['ma89'].iloc[-4])
                signals['ma60_89_slope_up'] = (
                    ma60 > ma60_3d and ma89 > ma89_3d
                )
            else:
                signals['ma60_89_slope_up'] = False
        else:
            signals['ma60_89_converge'] = False
            signals['ma60_89_slope_up'] = False

        # 均线背离：价格创新高但均线不跟
        if len(df) >= 20 and close > 0:
            recent_high = float(df['close'].iloc[-20:].max())
            ma10_high = float(df['ma10'].iloc[-20:].max()) if 'ma10' in df.columns else 0
            if recent_high == close and ma10_high > 0:
                # 价格新高但MA10没有对应的新高
                ma10_prev_high = float(df['ma10'].iloc[-40:-20].max()) if len(df) >= 40 else 0
                if ma10_high < ma10_prev_high * 0.98:
                    signals['ma_divergence'] = True
                else:
                    signals['ma_divergence'] = False
            else:
                signals['ma_divergence'] = False
        else:
            signals['ma_divergence'] = False

        return signals
