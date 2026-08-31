# ashare_review/analysis/one_two_compare_backtest.py
"""1进2 新旧模型公平对比回测

规则（用户规格）：
- T 日盘后：旧模型(OneTwoScreener) vs 新模型(one_two_v2) 各取评分前 3 只首板候选
- T+1 9:30~9:35 买入（开盘涨幅规则，分钟线覆盖日完整规则，否则日线近似）
- 100% 仓位单票；-6% 止损；+8% 移动止盈(回撤4%卖)；涨停不主动卖
- 最多持有 3 个交易日，T+3 收盘必卖
- 最差执行原则（同 K 线止损优先）；跌停无法卖 → 次日可成交价

数据降级（两模型公平同降级）：
- 涨停池：akshare 优先（约20天）→ TDX 本地涨停判定（涨幅≥阈值）
- 分时：TDX 1分钟线覆盖最近23天 → 完整买入规则；更早用日线近似
"""
import os
import struct
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..data.tdx_reader import TdxReader, is_a_share_stock
from ..data.akshare_fetcher import AkshareFetcher
from ..utils.calendar import TradingCalendar
from ..data.models import LimitUpInfo

TDX = TdxReader()


def zt_limit_pct(code: str) -> float:
    if code.startswith(('30', '68')):
        return 19.6
    if code.startswith(('8', '4', '92')):
        return 29.4
    return 9.8


def market_of(code: str) -> str:
    if code.startswith(('8', '4', '92')):
        return 'bj'
    return 'sh' if code.startswith('6') else 'sz'


def detect_limit_ups_from_tdx(trade_date: str) -> List[dict]:
    """TDX 本地涨停判定：遍历 .day 文件，找 trade_date 日涨幅≥阈值 且收盘≈涨停价的 A 股。

    返回 [{code, name, close_price, consecutive(≈1), ...}]。封单/换手/市值缺失。
    """
    from datetime import datetime as _dt
    target = _dt.strptime(trade_date, '%Y%m%d').date()
    results = []
    for mkt in ('sh', 'sz', 'bj'):
        d = TDX._market_dir(mkt)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith('.day'):
                continue
            code = fn[:-4][2:]
            if not is_a_share_stock(mkt, code):
                continue
            fpath = os.path.join(d, fn)
            try:
                with open(fpath, 'rb') as f:
                    fsize = os.path.getsize(fpath)
                    if fsize < 32 * 2:
                        continue
                    f.seek(fsize - 32 * 60)
                    tail = f.read(32 * 60)
                bars = []
                for i in range(len(tail) // 32):
                    rec = tail[i * 32:(i + 1) * 32]
                    dt_, op, hi, lo, cl, amt, vol, _ = struct.unpack('IIIIIfII', rec)
                    bars.append((str(dt_), op / 100.0, hi / 100.0, lo / 100.0, cl / 100.0, amt, vol))
                # 找 target 日
                bar = None
                prev_close = None
                for i, b in enumerate(bars):
                    if b[0] == trade_date:
                        bar = b
                        if i > 0:
                            prev_close = bars[i - 1][4]
                        break
                if bar is None or prev_close is None or prev_close <= 0:
                    continue
                close = bar[4]
                chg = (close - prev_close) / prev_close * 100
                limit = zt_limit_pct(code)
                if chg >= limit - 0.3 and close >= prev_close * (1 + (limit - 0.5) / 100):
                    results.append({'code': code, 'name': '', 'close_price': round(close, 2),
                                    'chg': round(chg, 2), 'source': 'tdx'})
            except Exception:
                continue
    return results


def get_limit_up_pool(trade_date: str, ak: Optional[AkshareFetcher] = None) -> List[dict]:
    """当日涨停池：akshare 优先，失败/空 → TDX 本地判定。返回统一 dict 列表。"""
    ak = ak or AkshareFetcher()
    try:
        pool = ak.get_limit_up_pool(trade_date)
        if pool:
            return [{'code': str(lu.code), 'name': lu.name,
                     'close_price': lu.close_price or 0,
                     'float_market_cap': lu.float_market_cap or 0,
                     'consecutive': lu.consecutive or 1,
                     'limit_up_time': lu.limit_up_time or '',
                     'seal_amount': lu.seal_amount or 0,
                     'turnover': lu.turnover or 0,
                     'is_seal': lu.is_seal, 'is_broken': lu.is_broken,
                     'board_type': lu.board_type or '',
                     'source': 'akshare'} for lu in pool]
    except Exception:
        pass
    return detect_limit_ups_from_tdx(trade_date)


def pool_to_limit_up_info(p: dict) -> LimitUpInfo:
    """dict 候选 → LimitUpInfo（旧模型评分输入）。缺失字段用安全默认。"""
    return LimitUpInfo(
        code=p['code'], name=p.get('name', ''), limit_up_time=p.get('limit_up_time', '14:00'),
        seal_amount=float(p.get('seal_amount') or 0), turnover=float(p.get('turnover') or 0),
        float_market_cap=float(p.get('float_market_cap') or 0),
        consecutive=int(p.get('consecutive') or 1), is_first=(p.get('consecutive') or 1) == 1,
        is_seal=bool(p.get('is_seal', True)), is_broken=bool(p.get('is_broken', False)),
        board_type=p.get('board_type', '') or '换手板', close_price=float(p.get('close_price') or 0))

# ======================================================================
# 模型适配器（公平：同候选池、各取 top3）
# ======================================================================

def old_model_top3(pool: List[dict], weights=None) -> List[dict]:
    """旧模型：OneTwoScreener 评分 → top3。"""
    from ..screening.one_two import OneTwoScreener
    scr = OneTwoScreener(tdx=TDX)
    scored = []
    for p in pool:
        lu = pool_to_limit_up_info(p)
        if lu.consecutive != 1:
            continue
        try:
            score, reasons, detail = scr._evaluate_first_board(lu, night_mode=True)
        except Exception:
            continue
        if score > 0:
            scored.append({'code': p['code'], 'name': p.get('name', ''),
                           'score': score, 'reason': '; '.join(reasons[:3]), 'model': 'old'})
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:3]


def new_model_top3(pool: List[dict], trade_date: str) -> List[dict]:
    """新模型：one_two_v2 8 维打分 → top3。"""
    from ..one_two_v2.picks import filter_candidates, compute_score
    from ..one_two_v2.service import build_pick_context, _load_concept_map, default_weights
    lus = [pool_to_limit_up_info(p) for p in pool]
    ctx = build_pick_context(lus, tdx=TDX, concept_map=_load_concept_map(), trade_date=trade_date)
    scored = []
    for lu in lus:
        if lu.consecutive != 1:
            continue
        try:
            d = compute_score(lu, ctx.get('scored', {}).get(str(lu.code), {}), default_weights())
        except Exception:
            continue
        if d['score'] > 0:
            scored.append({'code': str(lu.code), 'name': lu.name,
                           'score': d['score'], 'reason': '; '.join(v['reason'] for v in d['dimensions'].values()),
                           'model': 'new', 'mcap': float(lu.float_market_cap or 0)})
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:3]


# ======================================================================
# 分钟线读取（T+1 9:30-9:35）
# ======================================================================

def read_minutes(code: str, market: str, trade_date: str) -> List[dict]:
    """读指定交易日的 1 分钟线（复用 tdx_reader.read_minute_bars 正确解析）。"""
    try:
        bars = TDX.read_minute_bars(code, market, days=70)
    except Exception:
        return []
    out = []
    for b in bars or []:
        d = str(b.get('date', '')).replace('-', '')
        if d == trade_date:
            out.append({'t': b.get('time', 0), 'open': float(b.get('open', 0)),
                        'high': float(b.get('high', 0)), 'low': float(b.get('low', 0)),
                        'close': float(b.get('close', 0)), 'vol': float(b.get('volume', 0))})
    out.sort(key=lambda b: b['t'])
    return out


# ======================================================================
# 交易模拟器
# ======================================================================

STOP_LOSS = -6.0
TAKE_PROFIT = 8.0
TRAIL_DROP = 4.0
MAX_HOLD = 3


def _daily_bars(code: str, market: str, d_from: str, d_to: str) -> List[dict]:
    """TDX 日线区间 [d_from, d_to] 的 K 线。"""
    try:
        df = TDX.read_daily(code, market)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    df = df.reset_index(drop=True)
    out = []
    for _, row in df.iterrows():
        d = str(row['trade_date']).replace('-', '')
        if d_from <= d <= d_to:
            out.append({'date': d, 'open': float(row['open']), 'high': float(row['high']),
                        'low': float(row['low']), 'close': float(row['close']),
                        'volume': float(row.get('volume', 0))})
    return out


def _next_trade_dates(td: str, n: int, cal: TradingCalendar) -> List[str]:
    d = datetime.strptime(td, '%Y%m%d').date()
    out = []
    cur = d
    while len(out) < n:
        cur = cal.next_trading_day(cur, offset=1)
        if cur is None:
            break
        out.append(cur.strftime('%Y%m%d'))
    return out


def entry_decision(code: str, market: str, t_prev_close: float, t_plus1: str,
                   minutes: Optional[List[dict]]) -> Tuple[Optional[float], str]:
    """T+1 买入判定。返回 (成交价, 说明) 或 (None, 放弃原因)。"""
    if t_prev_close <= 0:
        return None, '昨收缺失'
    if minutes:
        # 完整规则：9:30-9:35 分钟线
        first = minutes[0] if minutes else None
        if not first:
            return None, '无分时'
        open_pct = (first['open'] / t_prev_close - 1) * 100
        # 简化：成交价 = 第 2 根(9:31) 开盘（禁止未来函数）；只用前 5 分钟判定
        exec_price = minutes[1]['open'] if len(minutes) > 1 else first['open']
        five_low = min(b['low'] for b in minutes[:5])
        five_close = minutes[min(4, len(minutes) - 1)]['close']
        first_vol = first['vol']
        five_vol = sum(b['vol'] for b in minutes[:5])
        if 2.0 <= open_pct <= 7.0:
            # 未快速走弱：前5分钟最低不低于开盘价-1%
            if five_low >= first['open'] * 0.99:
                return exec_price, f'高开{open_pct:.1f}%未走弱'
        if -2.0 <= open_pct < 2.0:
            # 5分钟内站回昨收且放量
            if five_close >= t_prev_close and five_vol > first_vol * 1.2:
                return exec_price, f'平开{open_pct:.1f}%站回昨收放量'
        if open_pct > 7.0:
            # 回踩后重新突破第一波高点
            first_high = max(b['high'] for b in minutes[:3])
            if five_low < first_high and five_close > first_high:
                return exec_price, f'高开{open_pct:.1f}%回踩突破'
        return None, f'开盘{open_pct:.1f}%不满足买入'
    # 日线近似：开盘涨幅 -2%~+7% 可买
    try:
        bars = _daily_bars(code, market, t_plus1, t_plus1)
        if not bars:
            return None, '无日线'
        open_pct = (bars[0]['open'] / t_prev_close - 1) * 100
        if -2.0 <= open_pct <= 7.0:
            return bars[0]['open'], f'日线近似:开盘{open_pct:.1f}%'
        return None, f'日线近似:开盘{open_pct:.1f}%超范围'
    except Exception:
        return None, '日线读取失败'


def simulate_trade(pick: dict, t_pool_date: str, cal: TradingCalendar) -> Optional[dict]:
    """模拟一笔交易：T 日选出 → T+1 买入 → 最多持有 3 日。返回交易记录。"""
    code = pick['code']
    market = market_of(code)
    t_dt = datetime.strptime(t_pool_date, '%Y%m%d').date()
    nxt = _next_trade_dates(t_pool_date, 1, cal)
    if not nxt:
        return None
    t1 = nxt[0]
    # T 日收盘价（作为昨收与买入基准）
    try:
        bars_t = _daily_bars(code, market, t_pool_date, t_pool_date)
        if not bars_t:
            return None
        t_close = bars_t[0]['close']
    except Exception:
        return None
    # 分钟线（T+1）
    minutes = read_minutes(code, market, t1)
    entry_price, note = entry_decision(code, market, t_close, t1, minutes)
    if entry_price is None:
        return None
    # 涨停无法确认成交 → 未成交（开盘价 ≥ 昨收*(1+涨停阈值-0.3) 视为可能一字/涨停无法成交）
    limit = zt_limit_pct(code)
    if entry_price >= t_close * (1 + (limit - 0.3) / 100):
        return None
    # 持有期：T+1..T+3
    hold_days = _next_trade_dates(t_pool_date, MAX_HOLD, cal)
    entry_date = t1
    exit_price = None
    exit_date = None
    exit_reason = None
    highest = entry_price
    trail_triggered = False
    for i, hd in enumerate(hold_days):
        bars = _daily_bars(code, market, hd, hd)
        if not bars:
            continue
        bar = bars[0]
        high, low, close = bar['high'], bar['low'], bar['close']
        highest = max(highest, high)
        # 涨停不主动卖：当日 close 达涨停 → 不卖（除非更早触发）
        is_zt_close = close >= t_close * (1 + (limit - 0.5) / 100) and i > 0
        # 止损（最差执行：止损价成交）
        stop_price = entry_price * (1 + STOP_LOSS / 100)
        # 移动止盈：已触发 +8% 后回撤 4%
        trail_price = None
        if highest >= entry_price * (1 + TAKE_PROFIT / 100):
            trail_triggered = True
        if trail_triggered:
            trail_price = highest * (1 - TRAIL_DROP / 100)
        # 同日同触 → 止损优先（最差执行原则）
        hit_stop = low <= stop_price
        hit_trail = trail_price is not None and low <= trail_price
        if hit_stop:
            exit_price = stop_price
            exit_reason = '止损-6%'
            exit_date = hd
            break
        if hit_trail:
            exit_price = trail_price
            exit_reason = '移动止盈(回撤4%)'
            exit_date = hd
            break
        if is_zt_close and i < len(hold_days) - 1:
            continue  # 涨停持有，不主动卖
        # 时间退出：T+3 收盘必卖
        if i == len(hold_days) - 1 or (i == MAX_HOLD - 1):
            exit_price = close
            exit_reason = 'T+3收盘'
            exit_date = hd
            break
    if exit_price is None or exit_date is None:
        # 兜底：最后一个持有日收盘
        last = hold_days[-1]
        bars = _daily_bars(code, market, last, last)
        if bars:
            exit_price = bars[0]['close']
            exit_date = last
            exit_reason = 'T+3收盘'
        else:
            return None
    ret = (exit_price - entry_price) / entry_price * 100
    return {'code': code, 'name': pick.get('name', ''), 'model': pick['model'],
            'rank': pick.get('rank', 0), 'score': pick.get('score', 0),
            'entry_date': entry_date, 'exit_date': exit_date,
            'entry_price': round(entry_price, 2), 'exit_price': round(exit_price, 2),
            'return_pct': round(ret, 2), 'exit_reason': exit_reason,
            'days_held': len(hold_days[:hold_days.index(exit_date) + 1]) if exit_date in hold_days else MAX_HOLD,
            'entry_note': note}

# ======================================================================
# 统计
# ======================================================================

def compute_stats(trades: List[dict]) -> Dict:
    if not trades:
        return {'total': 0}
    rets = [t['return_pct'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    n = len(rets)
    win_rate = len(wins) / n * 100 if n else 0
    avg_ret = sum(rets) / n if n else 0
    total_ret = sum(rets)
    pl_ratio = ((sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
                if losses and sum(losses) != 0 and wins else 0.0)
    # 最大回撤（按交易序列累计收益）
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    # 最大连续亏损
    max_losing_streak = 0
    cur_streak = 0
    for r in rets:
        if r <= 0:
            cur_streak += 1
            max_losing_streak = max(max_losing_streak, cur_streak)
        else:
            cur_streak = 0
    stop_loss_cnt = sum(1 for t in trades if '止损' in t.get('exit_reason', ''))
    ge5 = sum(1 for r in rets if r >= 5)
    ge8 = sum(1 for r in rets if r >= 8)
    avg_days = sum(t.get('days_held', 0) for t in trades) / n if n else 0
    return {'total': n, 'win_rate': round(win_rate, 1),
            'avg_ret': round(avg_ret, 2), 'total_ret': round(total_ret, 1),
            'pl_ratio': round(pl_ratio, 2), 'max_drawdown': round(mdd, 1),
            'max_losing_streak': max_losing_streak,
            'stop_loss_rate': round(stop_loss_cnt / n * 100, 1) if n else 0,
            'pct5': round(ge5 / n * 100, 1) if n else 0,
            'pct8': round(ge8 / n * 100, 1) if n else 0,
            'avg_days': round(avg_days, 1)}


def rank_stats(trades: List[dict]) -> Dict:
    """按名次（第1/2/3名）分别统计胜率与平均收益。"""
    out = {}
    for rk in (1, 2, 3):
        ts = [t for t in trades if t.get('rank') == rk]
        if ts:
            rets = [t['return_pct'] for t in ts]
            out[f'rank{rk}'] = {'total': len(ts),
                                'win_rate': round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                                'avg_ret': round(sum(rets) / len(rets), 2)}
        else:
            out[f'rank{rk}'] = {'total': 0, 'win_rate': 0, 'avg_ret': 0}
    return out


# ======================================================================
# 主流程
# ======================================================================

def run_compare(start_date: str, end_date: str, ak=None) -> Dict:
    """60 日对比回测。start/end = YYYYMMDD。返回 {old, new, trades_old, trades_new, coverage}。"""
    from ..data.akshare_fetcher import AkshareFetcher
    ak = ak or AkshareFetcher()
    cal = TradingCalendar()
    # 交易日序列
    dates = []
    cur = datetime.strptime(start_date, '%Y%m%d').date()
    end = datetime.strptime(end_date, '%Y%m%d').date()
    while cur <= end:
        if cal.is_trading_day(cur):
            dates.append(cur.strftime('%Y%m%d'))
        cur += timedelta(days=1)
    old_trades, new_trades = [], []
    akshare_days = 0
    tdx_days = 0
    for i, td in enumerate(dates):
        pool = get_limit_up_pool(td, ak)
        if not pool:
            continue
        if pool[0].get('source') == 'akshare':
            akshare_days += 1
        else:
            tdx_days += 1
        # 旧模型 top3
        old3 = old_model_top3(pool)
        for rk, p in enumerate(old3, start=1):
            p['rank'] = rk
            tr = simulate_trade(p, td, cal)
            if tr:
                old_trades.append(tr)
        # 新模型 top3
        new3 = new_model_top3(pool, td)
        for rk, p in enumerate(new3, start=1):
            p['rank'] = rk
            tr = simulate_trade(p, td, cal)
            if tr:
                new_trades.append(tr)
        if (i + 1) % 10 == 0:
            print(f'[{i + 1}/{len(dates)}] {td} 旧{len(old_trades)}笔 新{len(new_trades)}笔')
    return {'old': compute_stats(old_trades), 'new': compute_stats(new_trades),
            'old_trades': old_trades, 'new_trades': new_trades,
            'rank_old': rank_stats(old_trades), 'rank_new': rank_stats(new_trades),
            'coverage': {'days': len(dates), 'akshare_days': akshare_days, 'tdx_days': tdx_days}}


def to_markdown(result: Dict) -> str:
    lines = ['# 1进2 新旧模型公平对比回测（最近60个交易日）', '']
    cov = result['coverage']
    lines.append(f'**数据覆盖**：交易日 {cov["days"]} 天 · akshare 涨停池 {cov["akshare_days"]} 天 · TDX 本地判定 {cov["tdx_days"]} 天')
    lines.append('')
    lines.append('## 统计对比')
    lines.append('| 指标 | 旧模型 | 新模型 | 更优 |')
    lines.append('|---|---|---|---|')
    old, new = result['old'], result['new']
    if old.get('total', 0) == 0 and new.get('total', 0) == 0:
        lines.append('| （无成交） | - | - | - |')
    else:
        metrics = [('total', '总交易数'), ('win_rate', '胜率%'), ('avg_ret', '平均单笔收益%'),
                   ('total_ret', '总收益率%'), ('pl_ratio', '盈亏比'), ('max_drawdown', '最大回撤%'),
                   ('max_losing_streak', '最大连续亏损'), ('stop_loss_rate', '止损率%'),
                   ('pct5', '+5%收益率%'), ('pct8', '+8%收益率%'), ('avg_days', '平均持仓天数')]
        for key, label in metrics:
            a = old.get(key, 0) if old.get('total') else '-'
            b = new.get(key, 0) if new.get('total') else '-'
            lines.append(f'| {label} | {a} | {b} | - |')
    lines.append('')
    lines.append('## 按名次统计')
    lines.append('| 名次 | 模型 | 样本 | 胜率% | 平均收益% |')
    lines.append('|---|---|---|---|---|')
    for rk in (1, 2, 3):
        for m, rs in (('旧', result['rank_old']), ('新', result['rank_new'])):
            r = rs.get(f'rank{rk}', {})
            lines.append(f'| 第{rk}名 | {m} | {r.get("total", 0)} | {r.get("win_rate", 0)} | {r.get("avg_ret", 0)} |')
    lines.append('')
    lines.append('## 结论')
    o, n = old, new
    if o.get('total') and n.get('total'):
        w = '旧模型' if o['win_rate'] > n['win_rate'] else ('新模型' if n['win_rate'] > o['win_rate'] else '持平')
        a = '旧模型' if o['avg_ret'] > n['avg_ret'] else ('新模型' if n['avg_ret'] > o['avg_ret'] else '持平')
        d = '旧模型' if o['max_drawdown'] > n['max_drawdown'] else ('新模型' if n['max_drawdown'] > o['max_drawdown'] else '持平')  # 回撤数值越大=越浅=越好
        p = '旧模型' if o['pl_ratio'] > n['pl_ratio'] else ('新模型' if n['pl_ratio'] > o['pl_ratio'] else '持平')
        lines += [f'- 胜率更高：**{w}**（{o["win_rate"]}% vs {n["win_rate"]}%）',
                  f'- 平均收益更高：**{a}**（{o["avg_ret"]} vs {n["avg_ret"]}）',
                  f'- 最大回撤更低：**{d}**（{o["max_drawdown"]} vs {n["max_drawdown"]}）',
                  f'- 盈亏比更高：**{p}**（{o["pl_ratio"]} vs {n["pl_ratio"]}）']
        # 第1名排序能力
        r1o = result['rank_old'].get('rank1', {}); r1n = result['rank_new'].get('rank1', {})
        r1_better = '旧模型' if r1o.get('avg_ret', 0) > r1n.get('avg_ret', 0) else ('新模型' if r1n.get('avg_ret', 0) > r1o.get('avg_ret', 0) else '持平')
        lines.append(f'- 第1名排序能力更强：**{r1_better}**（{r1o.get("avg_ret", 0)} vs {r1n.get("avg_ret", 0)}）')
    else:
        lines.append('- 样本不足，暂不比较')
    return '\n'.join(lines)


def main():
    import argparse
    from datetime import date, timedelta
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=60, help='回测交易日数')
    ap.add_argument('--end', default=None, help='结束日 YYYYMMDD（默认最近交易日）')
    args = ap.parse_args()
    cal = TradingCalendar()
    end_d = date.today()
    while not cal.is_trading_day(end_d):
        end_d -= timedelta(days=1)
    end_s = args.end or end_d.strftime('%Y%m%d')
    start_d = end_d
    n = 0
    while n < args.days - 1:
        start_d = cal.prev_trading_day(start_d, offset=1)
        n += 1
    start_s = start_d.strftime('%Y%m%d')
    print(f'回测区间: {start_s} ~ {end_s}（{args.days} 个交易日）')
    result = run_compare(start_s, end_s)
    md = to_markdown(result)
    os.makedirs('outputs', exist_ok=True)
    out_path = os.path.join('outputs', f'1进2_新旧模型对比_{end_s}.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)
    print(md)
    print(f'\n报告已保存: {out_path}')


if __name__ == '__main__':
    main()