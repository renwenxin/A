"""
V4 启动突破 — 实盘候选池监控模块

复用 VOL180 量价突破引擎(Vol180SimPortfolio) 的候选池与买卖信号（与启动突破共用同一候选池），
额外提供：
- 市场情绪判断：基于上一交易日涨停池 + 上证指数 MA 状态
- 竞价确认：9:25 后对昨日选出的买入信号标的，按今日开盘价筛选（低开>3% / 开盘跌停 → 放弃）
- 预估买入 / 卖出价格区间

对应 V4 手册下方新增模块：
- 运行扫描 = 清空候选池并重新全量筛选（force_rebuild_pool）
- 刷新状态 = 竞价确认昨日选出的标的（不重建池，9:25 后点击生效）
- 买入信号 / 卖出信号两栏（含预估价格区间）
"""
from datetime import date, datetime, timedelta
from typing import Dict, List

from .sim_portfolio import Vol180SimPortfolio


def _today_str() -> str:
    return date.today().strftime('%Y-%m-%d')


def _chase_ratio(code: str) -> float:
    """追高上限系数：10cm 6% / 20cm 8% / 30cm 30%（对应启动突破 V3 规则）。"""
    c = str(code).zfill(6)
    if c.startswith(('300', '301', '688')):
        return 0.08
    if c.startswith(('8', '4')):
        return 0.30
    return 0.06


class V4PoolMonitor:
    """V4 实盘候选池监控器。"""

    def __init__(self):
        self.sp = Vol180SimPortfolio()

    # ── 运行扫描: 清空候选池，重新全量筛选 ──
    def scan(self, trade_date: str = None) -> dict:
        """清空之前扫描的候选池并重新筛选（同启动突破 运行扫描）。"""
        self.sp.run_daily(trade_date=trade_date, mode='v3', force_rebuild_pool=True)
        self.sp._save()
        return self.refresh(trade_date=trade_date)

    # ── 刷新状态: 竞价确认昨日选出的标的 ──
    def refresh(self, trade_date: str = None) -> dict:
        """不重建候选池，仅对昨日选出的标的做竞价确认 + 附价格区间/情绪。"""
        td = trade_date or _today_str()
        summary = self.sp.get_summary()
        buy = self._enrich_buy(summary.get('sim_buy_today', []), td)
        sell = self._enrich_sell(summary.get('sim_sell_today', []))
        return {
            'date': td,
            'last_update': summary.get('last_update', ''),
            'market_mood': self.market_mood(td),
            'sim_buy_today': buy,
            'sim_sell_today': sell,
            'summary': summary.get('summary', {}),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # 市场情绪判断（基于上一交易日）
    # ═══════════════════════════════════════════════════════════════════════

    def market_mood(self, trade_date: str = None) -> dict:
        td = trade_date or _today_str()
        prev = self._prev_trading_day(td)
        mood = self._mood_for(prev)
        mood['date'] = prev
        return mood

    def _prev_trading_day(self, d_str: str) -> str:
        from ..utils.calendar import TradingCalendar
        cal = TradingCalendar()
        d = datetime.strptime(d_str, '%Y-%m-%d').date()
        for i in range(1, 15):
            c = d - timedelta(days=i)
            if cal.is_trading_day(c):
                return c.strftime('%Y-%m-%d')
        return (d - timedelta(days=1)).strftime('%Y-%m-%d')

    def _mood_for(self, prev_date: str) -> dict:
        """基于上一交易日涨停池 + 上证指数 MA 状态判断市场情绪。"""
        zt_count = multi_count = max_cons = 0
        data_ok = True
        note = ''
        try:
            from ..data.akshare_fetcher import AkshareFetcher
            pool = AkshareFetcher().get_limit_up_pool(prev_date.replace('-', ''))
            zt_count = len(pool)
            multi = [p for p in pool if (getattr(p, 'consecutive', 0) or 0) >= 2]
            multi_count = len(multi)
            if pool:
                max_cons = max((getattr(p, 'consecutive', 0) or 0) for p in pool)
        except Exception as e:
            data_ok = False
            note = f'涨停数据获取失败: {e}'

        sh = self.sp._get_market_state(prev_date)
        is_bull = sh.get('is_bull', True)

        if not data_ok:
            mood, advice, color = '未知', '数据不可用，请手动判断', '#9ca3af'
        elif zt_count >= 100 and multi_count >= 30:
            mood, advice, color = '火爆', '积极出击 · 可放宽仓位', '#dc2626'
        elif zt_count >= 60 and multi_count >= 15:
            mood, advice, color = '偏强', '正常开仓 · 优选强势板块', '#f59e0b'
        elif zt_count >= 30 and multi_count >= 5:
            mood, advice, color = '中性', '谨慎开仓 · 精选核心标的', '#4f46e5'
        else:
            mood, advice, color = '低迷', '降低仓位或空仓观望', '#16a34a'

        return {
            'date': prev_date,
            'zt_count': zt_count,
            'multi_board': multi_count,
            'max_consecutive': max_cons,
            'mood': mood,
            'advice': advice,
            'color': color,
            'note': note,
            'sh_close': sh.get('sh_close', 0),
            'sh_ma60': sh.get('sh_ma60', 0),
            'is_bull': is_bull,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # 竞价确认（9:25 后按今日开盘价筛选昨日选出的标的）
    # ═══════════════════════════════════════════════════════════════════════

    def _auction_check(self, code: str, buy_date: str) -> dict:
        """竞价确认：返回 {status: passed|rejected|pending, reason: str}。"""
        td_fmt = (buy_date or _today_str()).replace('-', '')
        try:
            df = self.sp._read_stock(code, up_to_date=td_fmt)
        except Exception:
            df = None
        if df is None or df.empty or len(df) < 2:
            return {'status': 'pending', 'reason': '数据不足 · 9:25后刷新'}

        last = df.iloc[-1]
        # 判断最后一根 K 线是否即今日（竞价数据是否已写入）
        try:
            ld = last['trade_date']
            ld = ld.date() if hasattr(ld, 'date') else ld
            today_d = datetime.strptime(td_fmt, '%Y%m%d').date()
            if str(ld) != str(today_d):
                return {'status': 'pending', 'reason': '今日竞价未写入 · 9:25后刷新'}
        except Exception:
            pass

        open_p = float(last['open'])
        prev_close = float(df.iloc[-2]['close'])
        open_chg = (open_p - prev_close) / prev_close * 100 if prev_close > 0 else 0
        limit_pct = self.sp._limit_threshold(code)

        # 开盘跌停 → 放弃
        if prev_close > 0 and open_p <= prev_close * (1 - limit_pct):
            return {'status': 'rejected', 'reason': '开盘跌停'}
        # 低开 > 3% → 放弃
        if open_chg < -3:
            return {'status': 'rejected', 'reason': f'低开{open_chg:.1f}% > 3%'}
        return {'status': 'passed',
                'reason': f'高开{open_chg:.1f}%' if open_chg >= 0
                else f'低开{abs(open_chg):.1f}%'}

    # ═══════════════════════════════════════════════════════════════════════
    # 信号增强（预估价格区间）
    # ═══════════════════════════════════════════════════════════════════════

    def _enrich_buy(self, items: List[dict], td: str) -> List[dict]:
        out = []
        for it in items:
            item = {**it}
            code = item.get('code', '')
            close = item.get('close') or item.get('buy_price') or 0
            ratio = _chase_ratio(code)
            # 预估买入区间：低开3%内可接 ~ 追高上限
            item['buy_range_low'] = round(close * (1 - 0.03), 2)
            item['buy_range_high'] = round(close * (1 + ratio), 2)
            item['buy_range_pct'] = ratio * 100
            item['auction'] = self._auction_check(code, item.get('buy_date') or td)
            out.append(item)
        return out

    def _enrich_sell(self, items: List[dict]) -> List[dict]:
        out = []
        for it in items:
            item = {**it}
            bp = item.get('buy_price', 0)
            est = item.get('sell_price_today', 0) or bp
            highest = item.get('highest_close', 0) or bp
            reason = item.get('exit_reason', '')
            if '止损' in reason and bp > 0:
                center = bp * 0.94          # -6% 硬止损
                low, high = center * 0.99, center * 1.01
                item['sell_range_basis'] = '止损-6%附近'
            elif '移动止盈' in reason and highest > 0:
                center = highest * 0.95     # 从最高回落5%触发
                low, high = center * 0.99, center * 1.01
                item['sell_range_basis'] = '最高回落5%触发'
            else:
                low, high = est * 0.98, est * 1.02
                item['sell_range_basis'] = '开盘/收盘附近'
            item['sell_range_low'] = round(low, 2)
            item['sell_range_high'] = round(high, 2)
            out.append(item)
        return out
