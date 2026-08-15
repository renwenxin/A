"""
V2 每日选股池管理器 — 状态机驱动

维护三个池：
  ① 启动池 (SIGNAL):  今日刚满足启动条件的股票
  ② 观察池 (WATCH):   正在等待回踩确认 (T+1~T+5)
  ③ 买入池 (BUY):     今日确认回踩，明日开盘买入

每只股票的状态机：
  NEW_SIGNAL → WATCHING → PULLBACK_CONFIRMED(READY_TO_BUY) → HOLDING → FINISHED
                 ↓(T+5未回踩)
               EXPIRED
"""
import json, os, sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import pandas as pd

# 确保能找到项目
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ashare_review.screening.five_indicator import StartBreakoutScreenerV2
from ashare_review.data.tdx_reader import TdxReader
from ashare_review.data.akshare_fetcher import AkshareFetcher

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
POOL_FILE = os.path.join(DATA_DIR, 'v2_pool_state.json')
TRADE_DB_FILE = os.path.join(DATA_DIR, 'v2_trades.json')  # 实际交易记录数据库
MAX_WATCH_DAYS = 5  # T+5 内未回踩则过期
HOLD_DAYS = 7       # 持有天数
ZHONGJUN_AMOUNT = 2_000_000_000  # 中军成交额阈值: 20亿(元)（backtest一致）
SECTOR_CACHE_FILE = os.path.join(DATA_DIR, 'sector_daily_stats.json')  # 板块统计缓存

# 状态常量
S_NEW = 'signal'       # 刚启动
S_WATCH = 'watch'      # 等待回踩
S_READY = 'ready'      # 回踩确认，可买入
S_HOLDING = 'holding'  # 已买入持有
S_EXPIRED = 'expired'  # 超时未回踩
S_FINISHED = 'finished' # 完成


def _today_str() -> str:
    return date.today().strftime('%Y-%m-%d')


def _date_from_str(s: str) -> date:
    if isinstance(s, date):
        return s
    return datetime.strptime(s, '%Y-%m-%d').date() if s else date.today()


class V2PoolManager:
    """V2 选股池管理器 — JSON 持久化"""

    def __init__(self):
        self.screener = StartBreakoutScreenerV2(TdxReader(), AkshareFetcher())
        os.makedirs(DATA_DIR, exist_ok=True)
        self._state = self._load()

    # ── 持久化 ──

    def _load(self) -> dict:
        if os.path.exists(POOL_FILE):
            try:
                with open(POOL_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            'signals': {},    # code -> {signal_date, score, v2_factors, name, ...}
            'watch': {},      # code -> {signal_date, watch_start, days_watched, ...}
            'ready': {},      # code -> {signal_date, confirm_date, score, ...}
            'holding': {},
            'finished': {},
            'last_update': '',
        }

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(POOL_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    # ── 板块环境过滤（backtest完整版4条件） ──

    def _load_industry_map(self) -> dict:
        """加载行业映射缓存 industry_map.json。"""
        imap_path = os.path.join(DATA_DIR, 'industry_map.json')
        if os.path.exists(imap_path):
            try:
                with open(imap_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _compute_sector_stats(self, trade_date: str) -> dict:
        """从实时行情计算板块统计（替代backtest的TDX全量扫描）。

        Returns:
            {industry: {avg_gain, count, zt_count, has_zhongjun, best_zj_amt}}
        """
        try:
            spot_df = self.screener.ak.get_spot_df()
            if spot_df is None or spot_df.empty:
                return {}

            limit_ups = self.screener.ak.get_limit_up_pool(trade_date=trade_date)
            zt_codes = set()
            for lu in (limit_ups or []):
                if lu.code:
                    zt_codes.add(lu.code)

            industry_map = self._load_industry_map()
            if not industry_map:
                return {}

            sector_stats = {}
            for _, row in spot_df.iterrows():
                code = str(row.get('代码', '')).strip().zfill(6)
                if not code or len(code) != 6 or code not in industry_map:
                    continue
                industry = industry_map[code]
                if industry not in sector_stats:
                    sector_stats[industry] = {
                        'sum_gain': 0.0, 'count': 0, 'zt_count': 0,
                        'best_zj_amt': 0.0, 'has_zhongjun': False,
                    }
                s = sector_stats[industry]
                try:
                    chg = float(row.get('涨跌幅', 0))
                except (ValueError, TypeError):
                    chg = 0.0
                s['sum_gain'] += chg
                s['count'] += 1
                if code in zt_codes:
                    s['zt_count'] += 1
                # 中军检查: 涨幅≥5% 且 成交额≥20亿
                try:
                    amount = float(row.get('成交额', 0))
                except (ValueError, TypeError):
                    amount = 0
                if chg >= 5.0 and amount >= ZHONGJUN_AMOUNT:
                    if amount > s['best_zj_amt']:
                        s['best_zj_amt'] = amount
                        s['has_zhongjun'] = True

            # 后处理: 计算平均涨幅
            for ind, s in sector_stats.items():
                s['avg_gain'] = round(s['sum_gain'] / s['count'], 2) if s['count'] > 0 else 0.0

            # 缓存当日板块统计（供条件4使用）
            self._save_sector_cache(trade_date, sector_stats)

            return sector_stats
        except Exception:
            return {}

    def _save_sector_cache(self, trade_date: str, stats: dict):
        """缓存当日板块统计（仅保存avg_gain用于条件4的持续性判断）。"""
        try:
            # 统一为 YYYYMMDD 格式
            key = trade_date.replace('-', '')
            cache = {}
            if os.path.exists(SECTOR_CACHE_FILE):
                with open(SECTOR_CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
            # 只存轻量版本: {industry: avg_gain}
            light = {ind: round(s['avg_gain'], 2) for ind, s in stats.items()}
            cache[key] = light
            # 只保留最近30天
            keys = sorted(cache.keys(), reverse=True)[:30]
            cache = {k: cache[k] for k in keys}
            with open(SECTOR_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_prev_sector_gains(self, trade_date: str, n_days: int = 3) -> dict:
        """加载前N个交易日的板块avg_gain缓存。

        Returns:
            {industry: [gain_d1, gain_d2, ...]} 从最近到最远
        """
        try:
            from ashare_review.utils.calendar import TradingCalendar
            cal = TradingCalendar()
            td = _date_from_str(trade_date)
            prev_dates = []
            d = cal.prev_trading_day(td, offset=1)
            while d and len(prev_dates) < n_days:
                ds = d.strftime('%Y%m%d')  # 统一 YYYYMMDD 格式
                prev_dates.append(ds)
                d = cal.prev_trading_day(d, offset=1)
            if not prev_dates:
                return {}
            cache = {}
            if os.path.exists(SECTOR_CACHE_FILE):
                with open(SECTOR_CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
            result = {}
            for ds in prev_dates:
                day_data = cache.get(ds, {})
                for ind, gain in day_data.items():
                    # 兼容旧版full格式{avg_gain: x}和新版light格式(float)
                    if isinstance(gain, dict):
                        gain = gain.get('avg_gain', 0)
                    if isinstance(gain, (int, float)):
                        if ind not in result:
                            result[ind] = []
                        result[ind].append(gain)
            return result
        except Exception:
            return {}

    def _sector_pass(self, industry: str, sector_stats: dict,
                     prev_gains: dict = None) -> bool:
        """板块环境过滤（4条件），对应backtest的 _sector_pass。

        条件:
          1. 板块当日平均涨幅 ≥ 2%
          2. 板块内涨停家数 ≥ 3只
          3. 板块内有中军（成交额≥20亿 且 涨幅≥5%）
          4. 近3日板块涨幅≥0的天数 ≥ 2天（持续走强）

        无行业数据时放行（避免误拦新股/数据缺失）。
        """
        if not industry or not sector_stats:
            return True

        today = sector_stats.get(industry)
        if today is None:
            return True  # 行业不在当日统计中时放行

        # 条件1: 板块涨幅 ≥ 2%
        if today.get('avg_gain', 0) < 2.0:
            return False

        # 条件2: 涨停 ≥ 3只
        if today.get('zt_count', 0) < 3:
            return False

        # 条件3: 有中军
        if not today.get('has_zhongjun', False):
            return False

        # 条件4: 近3日 ≥2天涨幅为正（使用缓存数据）
        if prev_gains and industry in prev_gains:
            gains = prev_gains[industry]
            pos_days = sum(1 for g in gains if g > 0)
            if pos_days < 2:
                return False

        return True

    # ── 公开方法 ──

    def _is_trading_day(self, d: date = None) -> bool:
        """检查是否为交易日。"""
        check = d or date.today()
        try:
            from ashare_review.utils.calendar import TradingCalendar
            cal = TradingCalendar()
            return cal.is_trading_day(check)
        except Exception:
            return check.weekday() < 5  # 回退：周一至周五

    def run_daily_scan(self, trade_date: str = None) -> dict:
        """每日自动执行：
        0. 计算板块环境统计（4条件过滤）
        1. 扫今天启动信号（板块过滤后）
        2. 更新观察池 (移除过期)
        3. 检查回踩确认（backtest完整版）
        4. 输出买入名单
        """
        td = trade_date or _today_str()
        td_dt = _date_from_str(td)

        # ── 非交易日跳过扫描，仅更新状态 ──
        if not self._is_trading_day(td_dt):
            self._update_watch_pool(td, {})
            self._update_holding_finished()
            self._state['last_update'] = td
            self._save()
            return {'new_signals': 0, 'watch_added': 0, 'pullbacks_today': 0,
                    'watch_expired': 0, 'buy_tomorrow': len(self._state['ready']),
                    'note': f'{td} 非交易日，跳过全市场扫描，仅更新池状态'}

        stats = {'new_signals': 0, 'watch_added': 0, 'pullbacks_today': 0,
                 'watch_expired': 0, 'buy_tomorrow': 0}

        # ── Step 0: 计算板块统计（backtest Layer 1） ──
        sector_stats = self._compute_sector_stats(td)
        prev_gains = self._load_prev_sector_gains(td, n_days=3)
        stats['sector_stats'] = len(sector_stats)

        # ── Step 1: 运行 V2 筛选器，获取今日启动信号（板块过滤后） ──
        new_signals = self._scan_today_signals(td, sector_stats, prev_gains)
        stats['new_signals'] = len(new_signals)
        for sig in new_signals:
            code = sig['code']
            if code not in self._state['watch'] and code not in self._state['ready']:
                self._state['signals'][code] = sig
                self._state['watch'][code] = {
                    'code': code, 'name': sig['name'],
                    'signal_date': td, 'watch_start': td,
                    'score': sig['score'],
                    'v2_factors': sig.get('v2_factors', {})
                }
                stats['watch_added'] += 1

        # ── Step 2: 更新观察池状态 ──
        self._update_watch_pool(td, stats)

        # ── Step 3: 更新持仓到期状态 ──
        self._update_holding_finished()

        # ── Step 4: 统计 ──
        stats['buy_tomorrow'] = len(self._state['ready'])

        self._state['last_update'] = td
        self._save()
        return stats

    def _scan_today_signals(self, trade_date: str, sector_stats: dict = None,
                            prev_gains: dict = None) -> List[dict]:
        """运行 V2 筛选器 + 板块过滤，获取今日启动信号列表。"""
        results = self.screener.screen(trade_date=trade_date)
        signals = []
        filtered_by_sector = 0
        for r in results:
            # 板块环境过滤（backtest Layer 1）
            sector = (r.detail.get('sector', '') or
                      r.detail.get('features', {}).get('sector', ''))
            if not self._sector_pass(sector, sector_stats or {}, prev_gains):
                filtered_by_sector += 1
                continue

            signals.append({
                'code': r.code,
                'name': r.name,
                'score': r.score,
                'reasons': r.reasons,
                'v2_factors': r.detail.get('features', {}),
                'sector': sector,
                'signal_date': trade_date,
            })
        if filtered_by_sector > 0:
            pass  # 静默过滤，stats中记录
        return signals

    def _update_watch_pool(self, today: str, stats: dict):
        """检查观察池中每只股票的回踩状态（使用backtest完整版）。"""
        today_dt = _date_from_str(today)
        expired_codes = []

        for code, info in list(self._state['watch'].items()):
            signal_dt = _date_from_str(info['signal_date'])
            days_since = (today_dt - signal_dt).days

            # 超过 T+5 未回踩 → 过期
            if days_since > MAX_WATCH_DAYS:
                expired_codes.append(code)
                stats['watch_expired'] += 1
                continue

            # 检查是否出现回踩确认（完整版）
            pb = self._find_pullback(code, info, today)
            if pb:
                # V2综合评分（信号+位置+回踩质量）
                v2_result = self._calc_v2_score_at_pullback(info, pb)

                # 回踩确认 → 移入买入池
                self._state['ready'][code] = {
                    'code': code,
                    'name': info['name'],
                    'signal_date': info['signal_date'],
                    'confirm_date': today,
                    'score': v2_result['score'],
                    'v2_tier': v2_result.get('tier', ''),
                    'v2_factors': v2_result.get('factors', {}),
                    'pullback_pct': v2_result.get('pullback_pct'),
                    'vol_shrink_ratio': v2_result.get('vol_shrink_ratio'),
                    'trigger_type': pb.get('trigger_type', ''),
                    'buy_price': pb.get('buy_price'),
                    'buy_date': pb.get('buy_date'),
                    'sector': info.get('sector', ''),
                }
                del self._state['watch'][code]
                stats['pullbacks_today'] += 1

            # 更新观察天数
            self._state['watch'][code]['days_watched'] = days_since

        for code in expired_codes:
            info = self._state['watch'].pop(code, {})
            self._state['finished'][code] = {**info, 'status': 'expired'}

    def _find_pullback(self, code: str, signal_info: dict, today: str) -> Optional[dict]:
        """完整版回踩确认 — 对应 backtest start_breakout_backtest._find_pullback。

        增强（相对简化版 _check_pullback）:
          1. 双缩量条件: vol≤信号日60% AND vol≤前5日均量80%
          2. 买入日开盘价检查: 不涨停开盘、不高开超信号日2%
          3. 使用 TradingCalendar 正确定位交易日
        """
        try:
            tdx = TdxReader()
            market = 'sh' if code.startswith('6') else 'sz'
            if code.startswith(('8', '4')):
                market = 'bj'

            signal_date = signal_info['signal_date']
            signal_score = signal_info.get('score', 60)

            df = tdx.read_daily(code, market, up_to_date=today)
            if df is None or df.empty or len(df) < 2:
                return None

            from ashare_review.analysis.indicators import calc_ma
            df = calc_ma(df, [5, 10])

            idx = len(df) - 1
            close = float(df['close'].iloc[idx])
            low = float(df['low'].iloc[idx])
            open_p = float(df['open'].iloc[idx])
            high = float(df['high'].iloc[idx])
            vol = float(df['volume'].iloc[idx])
            ma5 = float(df['ma5'].iloc[idx])
            ma10 = float(df['ma10'].iloc[idx])

            # 获取信号日的收盘价和量
            signal_idx = self._find_date_index(df, signal_date)
            if signal_idx is None:
                return None
            signal_close = float(df['close'].iloc[signal_idx])
            signal_vol = float(df['volume'].iloc[signal_idx])

            # 信号日的close也用作涨停阈值
            board_limit = 1.095 if code.startswith(('0', '6')) else 1.199

            # 条件1: 最低价触及 MA5 或 MA10，收盘站回（与backtest一致）
            touched_ma = (low <= ma5 * 1.01) or (low <= ma10 * 1.01)
            above_ma = close > ma5 or close > ma10
            if not (touched_ma and above_ma):
                return None

            # 条件2: 双缩量 — vol <= 信号日vol*60% AND vol <= 前5日均量*80%（backtest增强）
            vol_ma5_series = df['volume'].rolling(5).mean().shift(1)
            vol_ma5_now = float(vol_ma5_series.iloc[idx])
            vol_shrink = (vol <= signal_vol * 0.6)
            if vol_ma5_now > 0:
                vol_shrink = vol_shrink and (vol <= vol_ma5_now * 0.8)
            if not vol_shrink:
                return None

            # 条件3: K 线止跌（与backtest一致）
            body = abs(close - open_p)
            lower_shadow = min(close, open_p) - low
            is_hammer = lower_shadow >= body * 0.5 if body > 0 else lower_shadow > 0
            is_doji = body <= close * 0.005
            engulf = False
            if idx >= 1:
                prev_c = float(df['close'].iloc[idx - 1])
                prev_o = float(df['open'].iloc[idx - 1])
                prev_body = prev_c - prev_o
                today_body = close - open_p
                engulf = (prev_body < 0) and (today_body > 0) and (close > prev_o) and (open_p < prev_c)

            if not (is_hammer or is_doji or engulf):
                return None

            # 确定买入日 = 下一交易日（backtest使用TradingCalendar）
            all_dates = self._get_trade_dates_after(today)
            buy_date = None
            for d in all_dates:
                if d > today:
                    buy_date = d
                    break
            if not buy_date:
                return None

            # ── 买入日开盘价检查（backtest增强） ──
            df2 = tdx.read_daily(code, market, up_to_date=buy_date)
            if df2 is not None and not df2.empty:
                buy_open = float(df2['open'].iloc[-1])
            else:
                buy_open = close * 1.001  # 近似
            # 开盘涨停(买不进)跳过
            if buy_open >= signal_close * board_limit:
                return None
            # 买入价 > 信号日收盘+2% 跳过（避免追高）
            if buy_open > signal_close * 1.02:
                return None

            # 回踩幅度
            pullback_pct = (signal_close - low) / signal_close * 100

            return {
                'pullback_pct': round(pullback_pct, 2),
                'vol_shrink_ratio': round(vol / signal_vol, 2) if signal_vol > 0 else 0,
                'vol_vs_ma5': round(vol / vol_ma5_now, 2) if vol_ma5_now > 0 else 0,
                'trigger_type': 'engulf' if engulf else ('hammer' if is_hammer else 'doji'),
                'buy_price': round(buy_open, 2),
                'buy_date': buy_date,
                'pb_low': round(low, 2),
                'pb_close': round(close, 2),
                'pb_open': round(open_p, 2),
                'pb_high': round(high, 2),
                'pb_vol': vol,
                'signal_close': signal_close,
                'signal_vol': signal_vol,
                'signal_score': signal_score,
            }
        except Exception:
            return None

    def _calc_v2_score_at_pullback(self, signal_info: dict, pb: dict) -> dict:
        """回踩确认后V2综合评分 — 对应 backtest _calc_v2_score_at_pullback。

        V2 = 信号强度(0-40) + 位置优势(0-30,距年高+60日动量+共振) + 回踩质量(0-30,幅度+缩量+K线+天数)
        满分100，输出 S/A/B/C/D 层级。
        """
        score = 0
        factors = {}

        # ── 信号强度 (0-40) — 基于V2筛选器原始分换算 ──
        signal_score = signal_info.get('score', 60)
        signal_strength = min(signal_score / 100 * 40, 40)
        score += signal_strength
        factors['信号强度'] = round(signal_strength, 1)

        # ── 位置优势 (0-30) — 从 v2_factors 读取 dist_250d + chg_60d ──
        v2f = signal_info.get('v2_factors', {})

        # Alpha 1: 距250日高点
        dist_250d = v2f.get('dist_250d', -100)
        if isinstance(dist_250d, str):
            dist_250d = float(dist_250d)
        if dist_250d >= 0:
            score += 15
            factors['距年高'] = 15
        elif dist_250d > -3:
            score += 10
            factors['距年高'] = 10
        elif dist_250d > -8:
            score += 5
            factors['距年高'] = 5
        else:
            factors['距年高'] = 0

        # Alpha 2: 60日动量
        chg_60d = v2f.get('chg_60d', 0)
        if isinstance(chg_60d, str):
            chg_60d = float(chg_60d)
        if chg_60d > 50:
            score += 10
            factors['60日涨'] = 10
        elif chg_60d > 30:
            score += 8
            factors['60日涨'] = 8
        elif chg_60d > 10:
            score += 3
            factors['60日涨'] = 3
        else:
            factors['60日涨'] = 0

        # 共振加分：新高+高动量
        if chg_60d > 50 and dist_250d >= 0:
            score += 5
            factors['位置共振'] = 5
        else:
            factors['位置共振'] = 0

        # ── 回踩质量 (0-30) ──
        pullback_quality = 0

        # 1. 回踩幅度 (0-10)
        pullback_pct = pb.get('pullback_pct', 0)
        if 1.0 <= pullback_pct <= 3.0:
            pullback_quality += 10
            factors['回踩幅度'] = 10
        elif 3.0 < pullback_pct <= 5.0:
            pullback_quality += 7
            factors['回踩幅度'] = 7
        elif 0.5 <= pullback_pct < 1.0:
            pullback_quality += 5
            factors['回踩幅度'] = 5
        elif 5.0 < pullback_pct <= 8.0:
            pullback_quality += 4
            factors['回踩幅度'] = 4
        else:
            factors['回踩幅度'] = 0

        # 2. 缩量程度 (0-8)
        vol_shrink = pb.get('vol_shrink_ratio', 1)
        if vol_shrink <= 0.40:
            pullback_quality += 8
            factors['缩量'] = 8
        elif vol_shrink <= 0.50:
            pullback_quality += 6
            factors['缩量'] = 6
        elif vol_shrink <= 0.60:
            pullback_quality += 4
            factors['缩量'] = 4
        elif vol_shrink <= 0.80:
            pullback_quality += 2
            factors['缩量'] = 2
        else:
            factors['缩量'] = 0

        # 3. K线止跌形态 (0-7)
        tt = pb.get('trigger_type', '')
        if tt == 'engulf':
            pullback_quality += 7
            factors['止跌K线'] = 7
        elif tt == 'hammer':
            pullback_quality += 5
            factors['止跌K线'] = 5
        elif tt == 'doji':
            pullback_quality += 3
            factors['止跌K线'] = 3
        else:
            factors['止跌K线'] = 0

        # 4. 回踩天数 (0-5) — T+2/T+3最佳
        today = _today_str()
        signal_date = signal_info.get('signal_date', '')
        try:
            from ashare_review.utils.calendar import TradingCalendar
            cal = TradingCalendar()
            sd = _date_from_str(signal_date)
            td = _date_from_str(today)
            pb_days = abs((td - sd).days)
        except Exception:
            pb_days = 3  # 默认
        if 2 <= pb_days <= 3:
            pullback_quality += 5
            factors['回踩天数'] = 5
        elif pb_days == 1 or pb_days == 4:
            pullback_quality += 3
            factors['回踩天数'] = 3
        else:
            pullback_quality += 1
            factors['回踩天数'] = 1

        score += pullback_quality

        # ── 综合 ──
        score = min(round(score), 100)
        if score >= 90:
            tier = 'S'
        elif score >= 80:
            tier = 'A'
        elif score >= 70:
            tier = 'B'
        elif score >= 60:
            tier = 'C'
        else:
            tier = 'D'

        return {
            'score': score,
            'tier': tier,
            'factors': factors,
            'pullback_pct': round(pullback_pct, 2),
            'vol_shrink_ratio': round(vol_shrink, 2),
        }

    def _find_date_index(self, df, date_str: str) -> Optional[int]:
        """在 DataFrame 中找到指定日期的索引。"""
        date_str = date_str[:10]
        dates = df.index if isinstance(df.index, pd.Index) else None
        if hasattr(df, 'index'):
            try:
                if isinstance(df.index[0], (str, date, datetime)):
                    for i in range(len(df) - 1, -1, -1):
                        d = str(df.index[i])[:10]
                        if d == date_str:
                            return i
            except Exception:
                pass
        # 回退：按行搜索
        for i in range(len(df) - 1, -1, -1):
            try:
                d = str(df.index[i])[:10]
                if d == date_str:
                    return i
            except Exception:
                pass
        return None

    def _get_trade_dates_after(self, after: str) -> List[str]:
        """获取 after 日之后的交易日列表。"""
        from ashare_review.utils.calendar import TradingCalendar
        try:
            cal = TradingCalendar()
            start_dt = _date_from_str(after)
            end_dt = start_dt + timedelta(days=30)
            dates = []
            d = cal.next_trading_day(start_dt, offset=1)
            while d and d <= end_dt and len(dates) < 10:
                dates.append(d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10])
                d = cal.next_trading_day(d, offset=1)
            return dates
        except Exception:
            # 回退：简单往前推算
            d = _date_from_str(after) + timedelta(days=1)
            dates = []
            while len(dates) < 10:
                if d.weekday() < 5:
                    dates.append(d.strftime('%Y-%m-%d'))
                d += timedelta(days=1)
            return dates

    # ── 查询方法 ──

    def get_signals(self) -> list:
        """获取今日启动信号（含板块信息）。"""
        today = _today_str()
        signals = []
        for v in self._state['signals'].values():
            if v.get('signal_date') == today:
                signals.append({
                    'code': v.get('code', ''),
                    'name': v.get('name', ''),
                    'score': v.get('score', 0),
                    'sector': v.get('sector', ''),
                    'signal_date': v.get('signal_date', ''),
                    'reasons': v.get('reasons', []),
                })
        return signals

    def get_watch_list(self) -> list:
        """获取当前观察池 (按剩余天数排序)。"""
        today_dt = date.today()
        items = []
        for code, info in self._state['watch'].items():
            signal_dt = _date_from_str(info['signal_date'])
            days_since = (today_dt - signal_dt).days
            remaining = MAX_WATCH_DAYS - days_since
            items.append({
                'code': code, 'name': info.get('name', ''),
                'signal_date': info['signal_date'],
                'score': info.get('score', 0),
                'days_watched': days_since,
                'remaining_days': max(remaining, 0),
            })
        items.sort(key=lambda x: x['remaining_days'])
        return items

    def get_buy_list(self) -> list:
        """获取明日买入列表。"""
        today = _today_str()
        items = []
        for code, info in list(self._state['ready'].items()):
            # 如果 buy_date 已过期，移出
            buy_dt = info.get('buy_date', '')
            if buy_dt and buy_dt <= today:
                self._state['finished'][code] = {**info, 'status': 'bought'}
                del self._state['ready'][code]
                continue
            items.append({
                'code': code,
                'name': info.get('name', ''),
                'score': info.get('score', 0),
                'v2_tier': info.get('v2_tier', ''),
                'trigger_type': info.get('trigger_type', ''),
                'sector': info.get('sector', ''),
                'signal_date': info['signal_date'],
                'confirm_date': info['confirm_date'],
                'buy_price': info.get('buy_price'),
                'buy_date': info.get('buy_date'),
                'pullback_pct': info.get('pullback_pct'),
                'vol_shrink_ratio': info.get('vol_shrink_ratio'),
            })
        return items

    def get_status_summary(self) -> dict:
        """获取池状态摘要。"""
        now = _today_str()
        return {
            'date': now,
            'signals': len([v for v in self._state['signals'].values()
                            if v.get('signal_date') == now]),
            'watch': len(self._state['watch']),
            'ready': len(self._state['ready']),
            'holding': len(self._state['holding']),
            'last_update': self._state.get('last_update', ''),
        }

    def mark_bought(self, code: str, buy_date: str = None):
        """将 ready → holding，记录买入日和卖出日（T+7）。"""
        if code in self._state['ready']:
            info = self._state['ready'].pop(code)
            info['status'] = 'holding'
            info['buy_date'] = info.get('buy_date', buy_date or _today_str())
            # 计算卖出日：买入后第7个交易日
            sell_date = self._calc_sell_date(info['buy_date'])
            info['sell_date'] = sell_date
            self._state['holding'][code] = info
            self._save()

    def _calc_sell_date(self, buy_date: str) -> str:
        """计算卖出日 = 买入日 + 7 个交易日。"""
        return self._get_nth_trade_date(buy_date, 7)

    def _get_nth_trade_date(self, after: str, n: int) -> str:
        """获取 after 之后的第 n 个交易日。"""
        dates = self._get_trade_dates_after(after)
        if len(dates) >= n:
            return dates[n - 1]
        # 回退：自然日推算
        import pandas as pd
        d = _date_from_str(after)
        count = 0
        while count < n:
            d += timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return d.strftime('%Y-%m-%d')

    def get_holding_list(self) -> list:
        """获取持仓列表，含 MA10 状态。"""
        today_dt = date.today()
        items = []
        for code, info in list(self._state['holding'].items()):
            buy_dt = _date_from_str(info.get('buy_date', ''))
            days_held = (today_dt - buy_dt).days if buy_dt else 0

            # 获取当前价格和 MA10（通过 TDX 实时读取）
            ma10_status = self._get_ma10_status(code)
            should_sell = ma10_status.get('below_ma10', False) if ma10_status else False

            items.append({
                'code': code, 'name': info.get('name', ''),
                'buy_date': info.get('buy_date', ''),
                'days_held': days_held,
                'score': info.get('score', 0),
                'price': ma10_status.get('price'),
                'ma10': ma10_status.get('ma10'),
                'below_ma10': should_sell,
                'sell_signal': '🔴 跌破MA10' if should_sell else '✅ 持有',
            })
        return items

    def _get_ma10_status(self, code: str) -> Optional[dict]:
        """读取股票当前价和 MA10，返回 {'price': x, 'ma10': y, 'below_ma10': bool}。"""
        try:
            tdx = TdxReader()
            market = 'sh' if code.startswith('6') else 'sz'
            if code.startswith(('8', '4')): market = 'bj'
            df = tdx.read_daily(code, market, up_to_date=_today_str())
            if df is None or df.empty or len(df) < 15: return None
            from ashare_review.analysis.indicators import calc_ma
            df = calc_ma(df, [10])
            idx = len(df) - 1
            price = float(df['close'].iloc[idx])
            ma10 = float(df['ma10'].iloc[idx])
            return {'price': round(price, 2), 'ma10': round(ma10, 2),
                    'below_ma10': price < ma10}
        except Exception:
            return None

    def _update_holding_finished(self):
        """检查持仓股票是否到期 → 移入 FINISHED。"""
        today_dt = date.today()
        for code, info in list(self._state['holding'].items()):
            sell_dt = _date_from_str(info.get('sell_date', ''))
            if sell_dt and sell_dt <= today_dt:
                info['sell_date'] = sell_dt.strftime('%Y-%m-%d') if hasattr(sell_dt, 'strftime') else str(sell_dt)[:10]
                info['status'] = 'finished'
                self._state['finished'][code] = self._state['holding'].pop(code)

    def get_finished_list(self) -> list:
        """获取已完成交易列表。"""
        items = []
        for code, info in self._state['finished'].items():
            items.append({
                'code': code, 'name': info.get('name', ''),
                'signal_date': info.get('signal_date', ''),
                'buy_date': info.get('buy_date', ''),
                'sell_date': info.get('sell_date', ''),
                'score': info.get('score', 0),
                'status': info.get('status', 'finished'),
            })
        return items

    def get_pool_summary(self) -> dict:
        """获取完整池数据（供 API 返回）。"""
        return {
            'status': self.get_status_summary(),
            'watch_list': self.get_watch_list(),
            'buy_list': self.get_buy_list(),
            'holding_list': self.get_holding_list(),
            'finished_list': self.get_finished_list(),
            'signals_today': self.get_signals(),
            'daily_ops': self.get_daily_ops(),
            'performance': self.get_performance_stats(),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # 实盘交易记录
    # ═════════════════════════════════════════════════════════════════════════

    def _load_trade_db(self) -> list:
        """加载实盘交易数据库。"""
        if os.path.exists(TRADE_DB_FILE):
            try:
                with open(TRADE_DB_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_trade_db(self, trades: list):
        """保存实盘交易数据库。"""
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TRADE_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(trades, f, ensure_ascii=False, indent=2)

    def record_buy(self, code: str, actual_price: float, buy_date: str = None) -> dict:
        """记录实际买入 → 从 READY 移入 HOLDING，写入交易数据库。

        Args:
            code: 股票代码
            actual_price: 实际买入价格
            buy_date: 买入日期 (默认今天)

        Returns:
            dict: 更新后的 pool_summary
        """
        bd = buy_date or _today_str()

        if code in self._state['ready']:
            info = self._state['ready'].pop(code)
            info['status'] = 'holding'
            info['buy_date'] = bd
            info['actual_buy_price'] = actual_price
            sell_date = self._calc_sell_date(bd)
            info['sell_date'] = sell_date
            self._state['holding'][code] = info
            self._save()

            # 写入交易数据库（开盘记录）
            trades = self._load_trade_db()
            trades.append({
                'code': code,
                'name': info.get('name', ''),
                'buy_date': bd,
                'buy_price': actual_price,
                'sell_date': None,
                'sell_price': None,
                'actual_ret': None,
                'score': info.get('score', 0),
                'signal_date': info.get('signal_date', ''),
                'confirm_date': info.get('confirm_date', ''),
                'pullback_pct': info.get('pullback_pct'),
                'vol_shrink_ratio': info.get('vol_shrink_ratio'),
                'status': 'holding',
            })
            self._save_trade_db(trades)

        return self.get_pool_summary()

    def record_sell(self, code: str, sell_price: float, sell_date: str = None) -> dict:
        """记录实际卖出 → 从 HOLDING 移入 FINISHED，计算实际收益。

        Args:
            code: 股票代码
            sell_price: 实际卖出价格
            sell_date: 卖出日期 (默认今天)

        Returns:
            dict: 更新后的 pool_summary
        """
        sd = sell_date or _today_str()

        if code not in self._state['holding']:
            return self.get_pool_summary()

        info = self._state['holding'].pop(code)
        buy_price = info.get('actual_buy_price') or info.get('buy_price', 0)
        actual_ret = (sell_price - buy_price) / buy_price * 100 - 0.35  # 扣除佣金滑点

        info['status'] = 'finished'
        info['sell_date'] = sd
        info['actual_sell_price'] = sell_price
        info['actual_ret'] = round(actual_ret, 2)
        self._state['finished'][code] = info
        self._save()

        # 更新交易数据库
        trades = self._load_trade_db()
        for t in trades:
            if t['code'] == code and t['status'] == 'holding':
                t['sell_date'] = sd
                t['sell_price'] = sell_price
                t['actual_ret'] = round(actual_ret, 2)
                t['status'] = 'finished'
                t['buy_price_actual'] = buy_price
                # 计算 MFE / MAE（持仓期间最高最低）
                try:
                    mfe, mae = self._calc_mfe_mae(code, info.get('buy_date', ''), sd)
                    t['mfe'] = mfe
                    t['mae'] = mae
                except Exception:
                    pass
                break
        self._save_trade_db(trades)

        return self.get_pool_summary()

    def _calc_mfe_mae(self, code: str, buy_date: str, sell_date: str) -> Tuple[Optional[float], Optional[float]]:
        """计算持仓期间的最大浮盈(MFE)和最大回撤(MAE)。"""
        try:
            tdx = TdxReader()
            market = 'sh' if code.startswith('6') else 'sz'
            if code.startswith(('8', '4')):
                market = 'bj'
            df = tdx.read_daily(code, market, up_to_date=sell_date)
            if df is None or df.empty:
                return None, None

            # 找到买入日和卖出日之间的数据
            dates = [str(d)[:10] if hasattr(d, 'strftime') else str(d)[:10] for d in df.index]
            start_i, end_i = None, None
            for i, d in enumerate(dates):
                if d == buy_date[:10]:
                    start_i = i
                if d == sell_date[:10]:
                    end_i = i
            if start_i is None or end_i is None or end_i <= start_i:
                return None, None

            buy_p = float(df['open'].iloc[start_i])
            holding_high = float(df['high'].iloc[start_i:end_i + 1].max())
            holding_low = float(df['low'].iloc[start_i:end_i + 1].min())
            mfe = round((holding_high - buy_p) / buy_p * 100, 2)
            mae = round((holding_low - buy_p) / buy_p * 100, 2)
            return mfe, mae
        except Exception:
            return None, None

    # ═════════════════════════════════════════════════════════════════════════
    # 每日操作面板
    # ═════════════════════════════════════════════════════════════════════════

    def get_daily_ops(self) -> dict:
        """获取今日操作列表。

        Returns:
            {'buy_today': [今日需要买入的股票],
             'sell_today': [今日需要卖出的股票 (MA10跌破)],
             'buy_count': N,
             'sell_count': N}
        """
        today = _today_str()
        today_dt = date.today()

        # 今日需要买入：READY 池，buy_date <= today
        buy_today = []
        for info in self._state['ready'].values():
            bd = info.get('buy_date', '')
            if bd and bd <= today:
                buy_today.append({
                    'code': info.get('code', ''),
                    'name': info.get('name', ''),
                    'score': info.get('score', 0),
                    'v2_tier': info.get('v2_tier', ''),
                    'trigger_type': info.get('trigger_type', ''),
                    'sector': info.get('sector', ''),
                    'buy_price': info.get('buy_price'),
                    'buy_date': bd,
                    'pullback_pct': info.get('pullback_pct'),
                    'vol_shrink_ratio': info.get('vol_shrink_ratio'),
                })

        # 今日需要卖出：HOLDING 池，跌破 MA10
        sell_today = []
        for code, info in self._state['holding'].items():
            ma10_info = self._get_ma10_status(code)
            if ma10_info and ma10_info.get('below_ma10', False):
                buy_dt = _date_from_str(info.get('buy_date', ''))
                days_held = (today_dt - buy_dt).days if buy_dt else 0
                sell_today.append({
                    'code': code,
                    'name': info.get('name', ''),
                    'buy_date': info.get('buy_date', ''),
                    'days_held': days_held,
                    'buy_price': info.get('actual_buy_price') or info.get('buy_price', 0),
                    'price': ma10_info.get('price'),
                    'ma10': ma10_info.get('ma10'),
                    'sell_signal': '跌破MA10',
                })

        return {
            'buy_today': buy_today,
            'sell_today': sell_today,
            'buy_count': len(buy_today),
            'sell_count': len(sell_today),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # 绩效统计
    # ═════════════════════════════════════════════════════════════════════════

    def get_performance_stats(self) -> dict:
        """从交易数据库计算累计绩效统计。"""
        trades = self._load_trade_db()
        closed = [t for t in trades if t.get('status') == 'finished' and t.get('actual_ret') is not None]

        if not closed:
            return {'total_trades': 0, 'closed_trades': 0}

        rets = [t['actual_ret'] for t in closed]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]

        n = len(rets)
        wr = len(wins) / n * 100
        avg_ret = sum(rets) / n

        # 累计收益（每笔独立1万）
        cum = sum(rets)

        # 最大回撤
        cum_max = 0
        max_dd = 0
        running_cum = 0
        for r in rets:
            running_cum += r
            cum_max = max(cum_max, running_cum)
            dd = (cum_max - running_cum) / max(abs(cum_max), 1) * 100
            max_dd = max(max_dd, dd)

        # 平均盈亏比
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        # 大赚概率 (>10%)
        big_wins = len([r for r in rets if r > 10])
        big_win_prob = big_wins / n * 100

        # Sharpe (简化：假设交易频率约每周一次)
        ret_std = (sum((r - avg_ret) ** 2 for r in rets) / n) ** 0.5
        sharpe = avg_ret / ret_std if ret_std > 0 else 0

        # 按时间顺序排列的近 N 笔
        recent = [{
            'code': t['code'], 'name': t.get('name', ''),
            'buy_date': t.get('buy_date', ''), 'sell_date': t.get('sell_date', ''),
            'actual_ret': t.get('actual_ret'),
            'score': t.get('score', 0),
        } for t in closed[-10:]]
        recent.reverse()

        return {
            'total_trades': len(trades),
            'closed_trades': n,
            'win_rate': round(wr, 1),
            'avg_ret': round(avg_ret, 2),
            'avg_win': round(avg_win, 2) if wins else 0,
            'avg_loss': round(avg_loss, 2) if losses else 0,
            'cum_ret': round(cum, 2),
            'max_dd': round(max_dd, 2),
            'pl_ratio': round(pl_ratio, 2),
            'big_win_prob': round(big_win_prob, 1),
            'sharpe': round(sharpe, 2),
            'recent_trades': recent,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 独立使用
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='V2 每日选股池')
    ap.add_argument('--scan', action='store_true', help='运行每日扫描')
    ap.add_argument('--status', action='store_true', help='查看池状态')
    ap.add_argument('--date', default=_today_str(), help='指定日期')
    args = ap.parse_args()

    pm = V2PoolManager()

    if args.scan:
        print(f'Running daily scan for {args.date}...')
        stats = pm.run_daily_scan(trade_date=args.date)
        print(f'  New signals:  {stats["new_signals"]}')
        print(f'  Watch added:  {stats["watch_added"]}')
        print(f'  Pullbacks:    {stats["pullbacks_today"]}')
        print(f'  Watch expired:{stats["watch_expired"]}')
        print(f'  Buy tomorrow: {stats["buy_tomorrow"]}')

    if args.status or not args.scan:
        s = pm.get_status_summary()
        print(f'\nPool Status ({s["date"]}):')
        print(f'  Signals today: {s["signals"]}')
        print(f'  Watch pool:    {s["watch"]}')
        print(f'  Buy ready:     {s["ready"]}')
        print(f'  Holding:       {s["holding"]}')
        print(f'  Last update:   {s["last_update"]}')

        watches = pm.get_watch_list()
        if watches:
            print(f'\nWatch List:')
            for w in watches:
                print(f'  {w["code"]} {w["name"]} (T+{w["days_watched"]}, remaining {w["remaining_days"]}d) score={w["score"]}')

        buys = pm.get_buy_list()
        if buys:
            print(f'\nBuy Tomorrow:')
            for b in buys:
                pb = f' pb={b["pullback_pct"]}%' if b.get('pullback_pct') else ''
                print(f'  {b["code"]} {b["name"]} V2={b["score"]}{pb}')
