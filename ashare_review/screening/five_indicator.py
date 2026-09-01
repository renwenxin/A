"""通达信四大战法筛选器 — 严格对应使用指南中的四个战法

战法①: 启动+突破（核心战法）— 主图操盘线控盘 + 压力位突破
战法②: 接力（连板博弈）    — 成交量创6月新高 + 成交额>10亿 + MA10>MA20
战法③: N字龙头（回调反包） — 涨停→缩量回调→再次放量上攻
战法④: 冰点抄底            — 市场冰点检测 + 吸筹信号确认

每个战法独立运行、独立评分、独立返回结果。
来源: c:\\Users\\15195\\Desktop\\指标代码\\使用指南.txt
"""
import os
from typing import List, Optional, Dict, Tuple
from datetime import datetime, date as dt_date
import numpy as np
import pandas as pd

from .base import BaseScreener
from ..data.models import ScreeningResult
from ..analysis.indicators import (
    enrich_all, calc_swl_sws, calc_volume_cannon,
    calc_yicha_momentum, calc_main_capital, calc_zigzag_find_top_line,
)
from ..analysis.pattern import detect_n_pattern
from ..data.tdx_reader import RECORD_SIZE
from ..utils.calendar import TradingCalendar


# ═══════════════════════════════════════════════════════════════════════════════
# 共享工具
# ═══════════════════════════════════════════════════════════════════════════════

def _market(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith('6'): return 'sh'
    if code.startswith(('0', '3')): return 'sz'
    return 'bj'


def _index_for(mkt: str) -> Tuple[str, str]:
    if mkt == 'sh': return ('000001', 'sh')
    if mkt == 'bj': return ('899050', 'bj')
    return ('399001', 'sz')


def _filter_df(df: pd.DataFrame, trade_date: Optional[str]) -> pd.DataFrame:
    if not trade_date: return df
    try:
        target = datetime.strptime(trade_date, '%Y%m%d').date()
    except ValueError:
        return df
    f = df[df['trade_date'].apply(
        lambda x: (x.date() if hasattr(x, 'date') else x) <= target
    )]
    return f if len(f) >= 20 else df


def _read_stock(tdx, code: str, trade_date=None) -> Optional[pd.DataFrame]:
    mkt = _market(code)
    try:
        df = tdx.read_daily(code, mkt)
        if df.empty or len(df) < 60: return None
        df = enrich_all(df)
        if trade_date: df = _filter_df(df, trade_date)
        return df if len(df) >= 40 else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 战法①: 启动+突破（核心战法）
# ═══════════════════════════════════════════════════════════════════════════════

class StartBreakoutScreener(BaseScreener):
    """战法①: 启动+突破（核心战法）

    使用指南原文:
      选股标准：沪深主板股票，近一年内涨停超过10次，非ST、非*ST；
      寻找股价在主图压力支撑计算出的压力位相差不过5%的标的；
      形态确认：放量突破近期高点或新高
      实战口诀：放量+新高+板块效应→竞价确认后→回踩均线买

    硬性条件:
      ① 近一年涨停 ≥ 5次（放宽：非严格10次）
      ② 股价距60日最高 ≤ 5%（压力位附近）
      ③ SWL > SWS（操盘线控盘）
      ④ 今日放量（量 > 5日均量 × 1.2）
    """
    name = '启动+突破(原版)'

    def screen(self, trade_date: str = None) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool(trade_date=trade_date)
        if not limit_ups: return []
        results = []
        for lu in limit_ups:
            if lu.board_type == '一字板': continue
            r = self._check(lu, trade_date)
            if r and r.score >= 40: results.append(r)
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]

    def _check(self, lu, trade_date) -> Optional[ScreeningResult]:
        code = str(lu.code).zfill(6)
        df = _read_stock(self.tdx, code, trade_date)
        if df is None: return None
        try:
            idx = len(df) - 1
            score, reasons, detail = 30.0, [], {}

            # ① 涨停基因
            limit_count = self._count_limit_ups(code)
            if limit_count >= 10:
                score += 20; reasons.append(f'年涨停{limit_count}次·股性活跃')
            elif limit_count >= 5:
                score += 10; reasons.append(f'年涨停{limit_count}次')
            else:
                return None  # 核心: 必须有一定涨停历史
            detail['limit_count'] = limit_count

            # ② 压力位附近
            high60 = df['high'].iloc[max(0, idx-60):idx+1].max()
            close_now = float(df['close'].iloc[idx])
            dist_pct = (high60 - close_now) / high60 * 100
            near_pressure = dist_pct <= 5.0
            if near_pressure:
                score += 20; reasons.append(f'距压力位{dist_pct:.1f}%·突破在即')
            else:
                score += 5; reasons.append(f'距压力位{dist_pct:.1f}%')
            detail['pressure_dist'] = round(dist_pct, 1)

            # ③ 操盘线控盘
            df = calc_swl_sws(df)
            ctrl = bool(df['swl_control'].iloc[idx])
            if ctrl:
                score += 20; reasons.append('操盘线控盘(SWL>生命线)')
            else:
                swl_v = float(df['swl'].iloc[idx]); sws_v = float(df['sws'].iloc[idx])
                near_ctrl = sws_v > 0 and abs(swl_v - sws_v) / sws_v < 0.02
                if near_ctrl:
                    score += 10; reasons.append('操盘线即将控盘')
                else:
                    score -= 5
            detail['swl_control'] = ctrl

            # ④ 放量确认
            above_ma5 = bool(df['close'].iloc[idx] > df['ma5'].iloc[idx])
            vol_ratio = float(df['volume'].iloc[idx] / df['volume'].rolling(5).mean().iloc[idx])
            vol_ok = vol_ratio >= 1.2
            if vol_ok and above_ma5:
                score += 15; reasons.append(f'放量站上MA5(量比{vol_ratio:.1f})')
            elif vol_ok:
                score += 10; reasons.append(f'放量(量比{vol_ratio:.1f})')
            elif above_ma5:
                score += 5; reasons.append('站上MA5')
            detail['vol_ratio'] = round(vol_ratio, 1)

            # 加分: 板块效应
            sector = self._get_sector(code)
            if sector:
                detail['sector'] = sector

            if score < 40: return None
            return ScreeningResult(code=code, name=lu.name or self._get_name(code),
                                   strategy=self.name, score=min(score, 100),
                                   reasons=reasons, detail=detail)
        except Exception:
            return None


            if score < 40: return None
            return ScreeningResult(code=code, name=lu.name or self._get_name(code),
                                   strategy=self.name, score=min(score, 100),
                                   reasons=reasons, detail=detail)
        except Exception:
            return None


# ============================================================================
# V2.0: evidence-driven scoring (two cross-period-validated Alphas)
# ============================================================================

class StartBreakoutScreenerV2(StartBreakoutScreener):
    """Start+Breakout V2.0 (Frozen) -- evidence-driven scoring.

    Alphas (cross-period validated):
      Alpha 1: distance to 250-day high (position) -- +20/+10
      Alpha 2: 60-day momentum (trend)            -- +10/+5
    Core thresholds (unchanged from V1).
    Observation features logged for future ML.
    """
    name = '启动+突破'

    def __init__(self, tdx=None, ak_fetcher=None):
        super().__init__(tdx, ak_fetcher)
        import json, os
        self._features_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'data', 'v2_features_log.jsonl')

    def screen(self, trade_date: str = None) -> List[ScreeningResult]:
        # 自动回退到最近交易日（非交易日查前一交易日数据）
        if trade_date is None:
            try:
                cal = TradingCalendar()
                last = cal.prev_trading_day(dt_date.today(), offset=1)
                if last:
                    trade_date = last.strftime('%Y%m%d')
            except Exception:
                pass

        # Market state
        market_state = self._get_market_state()

        # Candidates: limit-up pool + 7%+ gainers
        limit_ups = self.ak.get_limit_up_pool(trade_date=trade_date)
        candidates = {}  # code -> info
        for lu in (limit_ups or []):
            if lu.board_type == '一字板': continue
            candidates[lu.code] = {'name': lu.name, 'code': lu.code,
                                   'is_zt': True, 'board_type': lu.board_type,
                                   'consecutive': lu.consecutive,
                                   'float_market_cap': lu.float_market_cap}
        # Supplement: 7%+ non-limit-up stocks
        try:
            spot_df = self.ak.get_spot_df()
            if spot_df is not None and not spot_df.empty:
                for _, row in spot_df.iterrows():
                    code = str(row.get('代码', '')).strip().zfill(6)
                    if code in candidates or len(code) != 6: continue
                    try:
                        pct = float(row.get('涨跌幅', 0))
                    except (ValueError, TypeError):
                        continue
                    if pct >= 7.0:
                        candidates[code] = {'name': str(row.get('名称', '')),
                                            'code': code, 'is_zt': False,
                                            'board_type': '7%+', 'consecutive': 0,
                                            'float_market_cap': 0}
        except Exception:
            pass

        # Score
        results = []
        for code, info in candidates.items():
            r = self._check_v2(code, info, trade_date)
            if r and r.score >= 40: results.append(r)
        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:20]

        # Log
        for rank, r in enumerate(top, 1):
            r.detail['rank'] = rank
            self._log_features(r, trade_date, market_state, rank)
        return top

    def _get_market_state(self) -> dict:
        state = {'sh_ma60_up': 0, 'up_ratio': 0,
                 'limit_up_num': 0, 'limit_down_num': 0}
        try:
            df_sh = self.tdx.read_daily('999999', 'sh')
            if df_sh is not None and len(df_sh) >= 60:
                close = float(df_sh['close'].iloc[-1])
                ma60 = float(df_sh['close'].rolling(60).mean().iloc[-1])
                state['sh_ma60_up'] = 1 if close > ma60 else 0
                state['up_ratio'] = 1.0 if len(df_sh) >= 2 and float(df_sh['close'].iloc[-1]) > float(df_sh['close'].iloc[-2]) else 0.0
        except Exception:
            pass
        try:
            lus = self.ak.get_limit_up_pool()
            state['limit_up_num'] = len(lus) if lus else 0
        except Exception:
            pass
        try:
            bd = self.tdx.get_market_breadth(None)
            if bd: state['limit_down_num'] = bd.get('limit_down', 0)
        except Exception:
            pass
        return state

    def _log_features(self, r, trade_date, market_state, rank):
        try:
            import json
            record = dict(r.detail.get('features', {}))
            record.update({'code': r.code, 'name': r.name,
                          'v2_score': r.score, 'rank': rank,
                          'date': trade_date or ''})
            record.update(market_state)
            for k in ['ret3', 'ret5', 'ret7', 'ret10']:
                record.setdefault(k, None)
            with open(self._features_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception:
            pass

    @staticmethod
    def _is_main_board(code: str) -> bool:
        """判断是否主板（上海主板 + 深圳主板 + 中小板）"""
        return code.startswith(('600', '601', '603', '605', '000', '001', '002'))

    @staticmethod
    def _is_stock_st(name: str) -> bool:
        """判断是否ST/*ST股票"""
        return 'ST' in name or '*ST' in name

    def _find_resistance_line(self, df, idx: int, lookback: int = 60) -> float:
        """读取 zigzag 算法预计算的找顶线值。

        找顶线由 calc_zigzag_find_top_line 完整还原 通达信 主图指标:
          找顶线:DRAWLINE(NN,H,REF(NN,1),REF(H,1),1),COLORGREEN,DOTLINE

        NN 检测链路: HH/LL → FG01 → FG02 → FG0 → FG1 → FG → G1X → G1 → G2 → NN
        三层 zigzag 过滤确保只取最显著的 swing highs 连线。
        """
        if 'find_top_line' not in df.columns:
            df = calc_zigzag_find_top_line(df)
        val = float(df['find_top_line'].iloc[idx])
        if np.isnan(val) or val <= 0:
            return float(df['high'].iloc[max(0, idx - lookback):idx + 1].max())
        return val

    def _check_v2(self, code: str, info: dict, trade_date=None):
        df = _read_stock(self.tdx, code, trade_date)
        if df is None: return None
        try:
            idx = len(df) - 1
            score, reasons, detail = 30.0, [], {}
            close_now = float(df['close'].iloc[idx])
            vol_now = float(df['volume'].iloc[idx])
            features = {'code': code, 'is_zt': info.get('is_zt', 0)}

            # ── 硬性条件1: 非ST/非*ST ──
            name = info.get('name', '') or self._get_name(code)
            if not name or self._is_stock_st(name):
                return None
            detail['name'] = name

            # ── 硬性条件2: 涨停次数门槛（按板块区分） ──
            code_z = str(code).zfill(6)
            is_main = self._is_main_board(code_z)
            limit_count = self._count_limit_ups(code)
            detail['limit_count'] = limit_count
            if is_main:
                # 主板: 一年内涨停 > 10次
                if limit_count > 10:
                    score += 20; reasons.append(f'年涨停{limit_count}次·主板活跃')
                else:
                    return None
            else:
                # 非主板(创业板/科创板/北交所): 一年内涨停 > 3次
                if limit_count > 3:
                    score += 20; reasons.append(f'年涨停{limit_count}次·创科活跃')
                else:
                    return None

            # ── 硬性条件3: 在绿色找顶线下方10%以内（未突破） ──
            top_line = self._find_resistance_line(df, idx, lookback=60)
            detail['top_line'] = round(top_line, 2)
            features['top_line'] = top_line
            if top_line <= 0:
                return None
            below_pct = (top_line - close_now) / top_line * 100
            detail['dist_top_line'] = round(below_pct, 1)
            # 硬性要求: 在找顶线下方 且 距离不超过10%
            if 0 < below_pct <= 10:
                score += 20; reasons.append(f'距找顶线{below_pct:.1f}%·即将突破')
            elif below_pct <= 0:
                # 已经站上找顶线或平线，不符合"下方未突破"要求
                return None
            else:
                # 距离超过10%，不符合要求
                return None

            # ── 实盘二次校验: 排除已突破标的（找顶线外推在强势股中易虚高） ──
            # 如果近2个交易日收盘已高于前20日最高点，说明已突破而非即将突破
            high_20_before = float(df['high'].iloc[max(0, idx-20):idx-2].max()) if idx >= 3 else 0
            if high_20_before > 0:
                for offset in [1, 2]:  # 检查昨天和前天收盘
                    check_close = float(df['close'].iloc[idx - offset])
                    if check_close > high_20_before * 1.015:
                        return None  # 已提前突破，排除

            # ── 追高排除: 相对真实压力位(前60日最高高点, 排除当日)已突破超过阈值 → 放弃 ──
            # 找顶线 DRAWLINE 外推在强势股中易虚高，用真实前高兜底校验。
            # 教学: "八个点以下才做" · 突破压力位>10%的追高标的不做。
            high_60_before = float(df['high'].iloc[max(0, idx-60):idx].max()) if idx >= 1 else 0
            if high_60_before > 0:
                chase_pct = (close_now - high_60_before) / high_60_before * 100
                detail['chase_pct'] = round(chase_pct, 1)
                features['chase_pct'] = chase_pct
                chase_limit = 8.0 if is_main else 12.0  # 主板8% · 创科12%
                if chase_pct > chase_limit:
                    return None  # 已突破真实前高超过阈值 → 追高，排除

            # [Core] near 60-day high (保留，作为辅助参考)
            high60 = df['high'].iloc[max(0, idx-60):idx+1].max()
            dist_60 = (close_now - high60) / high60 * 100
            detail['dist_60d'] = round(dist_60, 1)
            if dist_60 >= -2:
                score += 5; reasons.append(f'距60日高{dist_60:.1f}%')
            features['dist_60d'] = dist_60

            # [Alpha 1] distance to 250-day high
            if len(df) >= 250:
                high250 = float(df['high'].iloc[max(0, idx-250):idx].max())
                dist_250 = (close_now - high250) / high250 * 100 if high250 > 0 else 0
                detail['dist_250d'] = round(dist_250, 1)
                if dist_250 >= 0:
                    score += 20; reasons.append('创250日新高·主升浪')
                elif dist_250 > -3:
                    score += 10; reasons.append(f'距250日高{dist_250:.1f}%·突破在即')
                features['dist_250d'] = dist_250

            # [Core] SWL control
            df = calc_swl_sws(df)
            ctrl = bool(df['swl_control'].iloc[idx])
            if ctrl:
                score += 20; reasons.append('操盘线控盘(SWL>生命线)')
            else:
                swl_v = float(df['swl'].iloc[idx]); sws_v = float(df['sws'].iloc[idx])
                if sws_v > 0 and abs(swl_v - sws_v) / sws_v < 0.02:
                    score += 10; reasons.append('操盘线即将控盘')
                else:
                    score -= 5
            features['swl_control'] = ctrl

            # [Core] volume expansion (prev-5d mean, excl today)
            vol_ma5 = float(df['volume'].rolling(5).mean().shift(1).iloc[idx])
            vol_ratio = vol_now / vol_ma5 if vol_ma5 > 0 else 1
            if 1.5 <= vol_ratio <= 3.0:
                score += 15; reasons.append(f'放量{vol_ratio:.1f}倍·温和放量')
            elif 3.0 < vol_ratio <= 5.0:
                score += 10; reasons.append(f'放量{vol_ratio:.1f}倍·显著放量')
            elif vol_ratio >= 1.2:
                score += 5; reasons.append(f'放量{vol_ratio:.1f}倍')
            detail['vol_ratio'] = round(vol_ratio, 1)
            features['vol_ratio'] = vol_ratio

            # [Alpha 2] 60-day momentum
            if idx >= 60:
                close_60d = float(df['close'].iloc[idx-60])
                chg_60d = (close_now - close_60d) / close_60d * 100
                detail['chg_60d'] = round(chg_60d, 1)
                if chg_60d > 50:
                    score += 10; reasons.append(f'60日涨幅{chg_60d:.0f}%·趋势强劲')
                elif chg_60d > 30:
                    score += 5; reasons.append(f'60日涨幅{chg_60d:.0f}%')
                features['chg_60d'] = chg_60d

            # [Observe] sector / leader (not scored)
            sector = self._get_sector(code)
            if sector:
                detail['sector'] = sector
                features['sector'] = sector
            features['is_sector_leader'] = 0
            amount = float(df['amount'].iloc[idx]) if 'amount' in df.columns else vol_now * close_now
            if amount / 1e8 > 15:
                features['is_sector_leader'] = 1
                detail['amount_yi'] = round(amount / 1e8, 1)

            detail['features'] = features
            name = info.get('name', '') or self._get_name(code)
            if score < 60: return None  # 资格过滤线（研究结论）：V2 不作为排序器，只作 pass/fail
            return ScreeningResult(code=code, name=name,
                                   strategy=self.name, score=min(round(score), 100),
                                   reasons=reasons, detail=detail)
        except Exception:
            return None


# ============================================================================
# V3.0: 板块共振 + 竞价确认 + N字反包 + 移动止盈
# ============================================================================

class StartBreakoutScreenerV3(StartBreakoutScreenerV2):
    """Start+Breakout V3.0 — 板块共振增强版

    V3 相比 V2 新增:
      - 板块共振评分: 同板块当日突破信号 ≥ 3 只 → +15 分
      - 板块龙头溢价: 成交额 > 15 亿 → 标记为板块龙头
      保留 V2 所有硬性条件、死亡区间过滤、过度放量过滤。
    """
    name = '启动+突破 V3'

    def __init__(self, tdx=None, ak_fetcher=None):
        super().__init__(tdx, ak_fetcher)
        self._v3_sectors: Dict[str, int] = {}  # sector → count (for resonance scoring)

    def screen(self, trade_date: str = None) -> List[ScreeningResult]:
        """V3 筛选: 先跑 V2 全量评分 → 再统计板块共振 → 回溯加分。"""
        # Step 1: 继承 V2 全量筛选（含所有硬性条件 + 评分）
        base_results = super().screen(trade_date=trade_date)
        if not base_results:
            return []

        # Step 2: 统计板块分布
        self._v3_sectors.clear()
        for r in base_results:
            sector = r.detail.get('sector', '')
            if sector:
                self._v3_sectors[sector] = self._v3_sectors.get(sector, 0) + 1

        # Step 3: 板块共振加分
        for r in base_results:
            sector = r.detail.get('sector', '')
            count = self._v3_sectors.get(sector, 0)
            if count >= 3:
                r.score = min(r.score + 15, 100)
                r.reasons.append(f'板块共振({sector}×{count}只)')
                r.detail['sector_resonance'] = count
            # 龙头标记
            amount_yi = r.detail.get('amount_yi', 0)
            if amount_yi >= 15:
                r.detail['is_leader'] = True
                r.reasons.append(f'板块龙头(成交{amount_yi:.0f}亿)')

        base_results.sort(key=lambda x: x.score, reverse=True)
        return base_results[:20]


# ═══════════════════════════════════════════════════════════════════════════════
# 战法②: 接力（连板博弈）
# ═══════════════════════════════════════════════════════════════════════════════

class RelayScreener(BaseScreener):
    """战法②: 接力（连板博弈）

    使用指南原文:
      条件①：成交量创6月新高
      条件②：成交额>10亿
      条件③：非ST、非*ST
      条件④：10日均线>20日均线
      手动排除: 已翻倍或涨幅过大的、股性差的、纯炒作无逻辑的
      按成交额排序，锁定≤5只候选。
      实战口诀: 首板放量换手→二板竞价高开放量→三板分歧转一致→四板以上龙头博弈

    硬性条件:
      ① 成交量创6个月新高（或近5日有6月最大量）
      ② 成交额 > 10亿
      ③ MA10 > MA20（均线多头）
      ④ 非ST/非*ST
    """
    name = '接力'

    @staticmethod
    def _is_stock_st(name: str) -> bool:
        return 'ST' in name or '*ST' in name

    def screen(self, trade_date: str = None) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool(trade_date=trade_date)
        if not limit_ups: return []
        results = []
        for lu in limit_ups:
            if lu.board_type == '一字板': continue
            r = self._check(lu, trade_date)
            if r and r.score >= 40: results.append(r)
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]

    def _check(self, lu, trade_date) -> Optional[ScreeningResult]:
        code = str(lu.code).zfill(6)
        df = _read_stock(self.tdx, code, trade_date)
        if df is None: return None
        try:
            idx = len(df) - 1
            score, reasons, detail = 25.0, [], {}

            # -- 硬性条件: 非ST/非*ST --
            name = lu.name or self._get_name(code)
            if not name or self._is_stock_st(name):
                return None
            detail['name'] = name

            # ① 成交量创6月新高 (核心)
            vol_now = float(df['volume'].iloc[idx])
            vol_6m_max = float(df['volume'].iloc[max(0, idx-126):idx+1].max())
            is_vol_max = vol_now >= vol_6m_max * 0.95
            if is_vol_max:
                score += 25; reasons.append('成交量6个月最大·新旧资金切换')
            else:
                score += 10; reasons.append('成交量接近6月高')
            detail['vol_vs_6m'] = round(vol_now / vol_6m_max * 100, 0) if vol_6m_max > 0 else 0

            # ② 成交额 > 10亿（硬性门槛）
            amount = float(df['amount'].iloc[idx]) if 'amount' in df.columns else vol_now * float(df['close'].iloc[idx])
            amount_yi = amount / 1e8
            if amount_yi >= 10:
                score += 20; reasons.append(f'成交额{amount_yi:.1f}亿·市场合力')
            else:
                return None  # 成交额不足10亿，不符合硬性条件
            detail['amount_yi'] = round(amount_yi, 1)

            # ③ MA10 > MA20
            ma10 = float(df['ma10'].iloc[idx]); ma20 = float(df['ma20'].iloc[idx])
            ma_bull = ma10 > ma20
            if ma_bull:
                score += 15; reasons.append('MA10>MA20·均线多头')
            detail['ma_bull'] = ma_bull

            # ④ 换手板（非一字板，排除买不到的情况）
            if lu.board_type != '一字板':
                score += 5
            detail['board_type'] = lu.board_type

            # 连板加分
            if lu.consecutive >= 3:
                score += 10; reasons.append(f'{lu.consecutive}连板·龙头辨识度')
            elif lu.consecutive >= 2:
                score += 5; reasons.append(f'{lu.consecutive}连板')
            detail['consecutive'] = lu.consecutive

            # 跌幅过大排除
            close_now = float(df['close'].iloc[idx])
            high_120 = df['high'].iloc[max(0, idx-120):idx+1].max()
            if (high_120 - close_now) / high_120 > 0.50:
                reasons.append('距高点腰斩·已翻倍排除')
                score -= 20

            # 量能复合炮信号加分
            df = calc_volume_cannon(df)
            cannon_sig = int(df['cannon_signal'].iloc[idx])
            if cannon_sig >= 2:
                score += 10; reasons.append(f'量能信号·{df["cannon_name"].iloc[idx]}')
            detail['cannon'] = cannon_sig

            if score < 40: return None
            return ScreeningResult(code=code, name=lu.name or self._get_name(code),
                                   strategy=self.name, score=min(score, 100),
                                   reasons=reasons, detail=detail)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# 战法③: N字龙头（回调反包）
# ═══════════════════════════════════════════════════════════════════════════════

class NPatternScreener(BaseScreener):
    """战法③: N字龙头（回调反包）

    使用指南原文:
      识别：第1天涨停 → 第2-3天缩量回调（不破关键支撑）→ 第4天再次放量上攻
      买入条件: 缩量止跌（成交量<涨停日一半）、资金再次流入、不破10日线或关键支撑

    硬性条件:
      ① 近期（10日内）有涨停日
      ② 涨停日后缩量回调（成交量萎缩到涨停日的50%以下）
      ③ 价格不破MA10（关键支撑守住）
      ④ 今日放量再次上攻（量>昨日 AND 收阳）
    """
    name = 'N字龙头'

    def screen(self, trade_date: str = None) -> List[ScreeningResult]:
        limit_ups = self.ak.get_limit_up_pool(trade_date=trade_date)
        if not limit_ups: return []
        results = []
        for lu in limit_ups:
            r = self._check(lu, trade_date)
            if r and r.score >= 40: results.append(r)
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]

    def _check(self, lu, trade_date) -> Optional[ScreeningResult]:
        code = str(lu.code).zfill(6)
        df = _read_stock(self.tdx, code, trade_date)
        if df is None: return None
        try:
            idx = len(df) - 1
            score, reasons, detail = 25.0, [], {}

            close_series = df['close']; vol_series = df['volume']

            # ① 找最近涨停日（10日内）
            pct_chg = close_series.pct_change()
            zt_mask = pct_chg >= 0.095
            recent_zt = zt_mask.iloc[max(0, idx-10):idx+1]
            if not recent_zt.any(): return None

            zt_idx = recent_zt[recent_zt].index[-1]  # 最近涨停日的位置
            zt_pos = df.index.get_loc(zt_idx) if hasattr(df.index, 'get_loc') else zt_idx
            # 涨停日距今几天
            days_since = idx - zt_pos
            if days_since < 1: return None  # 今天涨停的不算N字，等回调

            zt_vol = float(vol_series.iloc[zt_pos])
            zt_close = float(close_series.iloc[zt_pos])

            # ② 缩量回调确认
            # 涨停后最低量
            post_zt_vol = vol_series.iloc[zt_pos+1:idx+1]
            if len(post_zt_vol) < 1: return None
            min_vol = float(post_zt_vol.min())
            contraction = min_vol < zt_vol * 0.5
            if contraction:
                score += 25; reasons.append('缩量止跌(量<涨停日50%)')
            elif min_vol < zt_vol * 0.7:
                score += 15; reasons.append('缩量回调中')
            else:
                score += 5; reasons.append('回调缩量不明显')
            detail['vol_contraction'] = round(min_vol / zt_vol * 100, 0) if zt_vol > 0 else 0

            # ③ 不破10日线（关键支撑守住）
            ma10_now = float(df['ma10'].iloc[idx])
            close_now = float(df['close'].iloc[idx])
            above_ma10 = close_now > ma10_now
            if above_ma10:
                score += 20; reasons.append('守住MA10关键支撑')
            else:
                score -= 10; reasons.append('跌破MA10·支撑失守')
            detail['above_ma10'] = above_ma10

            # ④ 今日再次放量上攻
            today_vol = float(vol_series.iloc[idx])
            yesterday_vol = float(vol_series.iloc[idx-1])
            today_up = float(close_series.iloc[idx]) > float(close_series.iloc[idx-1])
            re_attack = today_vol > yesterday_vol and today_up
            if re_attack:
                score += 20; reasons.append('放量再次上攻·N字完成')
            elif today_up:
                score += 10; reasons.append('收阳但量能不足')
            detail['re_attack'] = re_attack

            # 加分: 资金再次流入 (副图3)
            df = calc_main_capital(df, None)
            main_cap_now = float(df['main_cap'].iloc[idx])
            main_cap_prev = float(df['main_cap'].iloc[idx-1]) if idx > 0 else main_cap_now
            if main_cap_now > main_cap_prev:
                score += 10; reasons.append('主力资金再次流入')
            detail['main_cap_up'] = main_cap_now > main_cap_prev

            # 加分: N字结构识别
            n_result = detect_n_pattern(df)
            if n_result:
                score += 10; reasons.append('经典N字结构')
            detail['n_pattern'] = bool(n_result)

            detail['days_since_zt'] = days_since

            if score < 40: return None
            return ScreeningResult(code=code, name=lu.name or self._get_name(code),
                                   strategy=self.name, score=min(score, 100),
                                   reasons=reasons, detail=detail)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# 战法④: 冰点抄底
# ═══════════════════════════════════════════════════════════════════════════════

class IceBottomScreener(BaseScreener):
    """战法④: 冰点抄底

    使用指南原文:
      使用情况：上涨家数不过800家时 → 普通冰点
      涨停家数≤20，连板高度压到2板 → 极冰点
      冰点反转确认三条件:
        ① 大盘放量大阳线
        ② 涨停家数明显增加
        ③ 新题材出现且有持续性
      满足三条 → 冰点反转，可积极入场；不满足 → 还在下跌通道，继续等。

    硬性条件（市场级别):
      ① 上涨家数 ≤ 1200（普通冰点）/ ≤ 800（深度冰点）
      ② 涨停家数 ≤ 30

    硬性条件（个股级别):
      ③ 吸筹信号（VAR9 > 0）或 低位洗盘
      ④ 主流资金处于低位（< 30）且开始拐头向上
    """
    name = '冰点抄底'

    def screen(self, trade_date: str = None) -> List[ScreeningResult]:
        # ── 市场冰点检测 ──
        breadth = self._get_breadth(trade_date)
        up_count = breadth.get('up', 9999)
        zt_count = breadth.get('limit_up', 99)
        is_ice = up_count <= 1200 or zt_count <= 30
        is_deep_ice = up_count <= 800 or zt_count <= 20

        if not is_ice:
            # 非冰点: 仍可跑个股筛选，但降权
            market_note = f'非冰点期(上涨{up_count}家/涨停{zt_count}家)'
        elif is_deep_ice:
            market_note = f'极冰点(上涨{up_count}家/涨停{zt_count}家)'
        else:
            market_note = f'普通冰点(上涨{up_count}家/涨停{zt_count}家)'

        limit_ups = self.ak.get_limit_up_pool(trade_date=trade_date)
        if not limit_ups: return []

        results = []
        for lu in limit_ups:
            r = self._check(lu, market_note, is_ice, is_deep_ice, trade_date)
            if r and r.score >= 25: results.append(r)

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:20]

    def _get_breadth(self, trade_date) -> dict:
        try:
            return self.tdx.get_market_breadth(
                trade_date=dt_date.today() if not trade_date else
                datetime.strptime(trade_date, '%Y%m%d').date()
            )
        except Exception:
            return {}

    def _check(self, lu, market_note, is_ice, is_deep_ice, trade_date) -> Optional[ScreeningResult]:
        code = str(lu.code).zfill(6)
        df = _read_stock(self.tdx, code, trade_date)
        if df is None: return None
        try:
            idx = len(df) - 1
            score, reasons, detail = 15.0, [market_note], {}

            # 冰点加分
            if is_deep_ice:
                score += 20; reasons.append('极冰点·反转机会')
            elif is_ice:
                score += 10; reasons.append('冰点期·关注反转')

            # ── 个股: 主力资金低位 ──
            df = calc_main_capital(df, None)
            main_cap = float(df['main_cap'].iloc[idx])
            is_low = main_cap < 30

            # 资金拐头向上
            main_series = df['main_cap']
            turning_up = False
            if len(main_series) >= 4:
                turning_up = (
                    float(main_series.iloc[idx]) > float(main_series.iloc[idx-1]) and
                    float(main_series.iloc[idx-1]) <= float(main_series.iloc[idx-2])
                )
            detail['main_cap'] = round(main_cap, 1); detail['turning_up'] = turning_up

            if is_low and turning_up:
                score += 30; reasons.append('低位拐头向上·抄底信号')
            elif is_low:
                score += 15; reasons.append('处于低位')
            elif turning_up:
                score += 10; reasons.append('资金拐头向上')

            # ── 吸筹信号 ──
            acc = float(df['accumulate'].iloc[idx]) if not pd.isna(df['accumulate'].iloc[idx]) else 0
            if acc > 0:
                score += 20; reasons.append(f'吸筹信号(强度{acc:.1f})')
            detail['accumulate'] = round(acc, 2)

            # ── KDJ 金叉（低位金叉加分） ──
            k_val = float(df['kdj_k'].iloc[idx]) if not pd.isna(df['kdj_k'].iloc[idx]) else 50
            d_val = float(df['kdj_d'].iloc[idx]) if not pd.isna(df['kdj_d'].iloc[idx]) else 50
            golden = (k_val > d_val) and (float(df['kdj_k'].iloc[idx-1]) <= float(df['kdj_d'].iloc[idx-1]))
            if golden and k_val < 30:
                score += 15; reasons.append('KDJ低位金叉')
            detail['kdj_golden'] = golden; detail['kdj_k'] = round(k_val, 1)

            # ── 抄底B信号 (主图: CROSS(支撑, 现价)) ──
            # 简化为: close 刚突破 MA5
            above_ma5 = bool(df['close'].iloc[idx] > df['ma5'].iloc[idx])
            was_below = bool(df['close'].iloc[idx-1] < df['ma5'].iloc[idx-1])
            if above_ma5 and was_below:
                score += 15; reasons.append('站上MA5·底分型')
            detail['above_ma5'] = above_ma5

            if score < 25: return None
            return ScreeningResult(code=code, name=lu.name or self._get_name(code),
                                   strategy=self.name, score=min(score, 100),
                                   reasons=reasons, detail=detail)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# 综合共振（一键四战法）
# ═══════════════════════════════════════════════════════════════════════════════

class FiveIndicatorScreener(BaseScreener):
    """四大战法综合共振 — 同时运行四个战法，按命中战法数综合排名"""
    name = '四大战法综合共振'

    def screen(self, trade_date: str = None) -> List[ScreeningResult]:
        subs = [
            StartBreakoutScreener(self.tdx, self.ak),
            RelayScreener(self.tdx, self.ak),
            NPatternScreener(self.tdx, self.ak),
            IceBottomScreener(self.tdx, self.ak),
        ]
        all_results: Dict[str, dict] = {}
        for sub in subs:
            try:
                for r in sub.screen(trade_date=trade_date):
                    code = r.code
                    if code not in all_results:
                        all_results[code] = {'name': r.name, 'score': 0,
                            'strategies': [], 'reasons': [], 'detail': {}}
                    entry = all_results[code]
                    entry['strategies'].append(sub.name)
                    entry['reasons'].append('[{s}] {r}'.format(s=sub.name, r='; '.join(r.reasons)))
                    entry['detail'][sub.name] = r.detail
                    entry['score'] = max(entry['score'], r.score)
            except Exception:
                pass
        combined = []
        for code, entry in all_results.items():
            n = len(entry['strategies'])
            combined.append(ScreeningResult(
                code=code, name=entry['name'], strategy=self.name,
                score=min(n * 25 + entry['score'] * 0.25, 100),
                reasons=['命中{n}/4战法: [{s}]'.format(n=n, s=','.join(entry['strategies']))] + entry['reasons'],
                detail=entry['detail'],
            ))
        combined.sort(key=lambda x: x.score, reverse=True)
        return combined[:30]
