# =====================================================================
# 策略名称: MAVOL180 成交量突破 + Zigzag压力位突破 V2
# 平台:     聚宽 (JoinQuant)  — 可直接复制运行
# 回测建议:  2023-06-01 ~ 2026-07-01, 初始资金10万, 频率:天
#
# 与本地回测 vol180_breakout_backtest.py 完全对齐:
#   选股: 沪深主板 · 年涨停>10次 · 非ST · 距zigzag找顶线≤10%
#   买入: 收盘>找顶线 AND 量>MAVOL180(180日均量×1.2) AND 前日在找顶线下方
#          → 次日开盘买入 (保护:开盘涨停不追/高开>5%不追)
#   卖出: 连板持有 → 断板当日收盘卖出 → 无涨停最多3天
#         → 收盘价跌破买入价-6% → 立即止损
#   评分: 距离质量(25/20/15) + 量能(20/15/10/5) + 均线(8/5) + 涨幅(12/6/2)
#         基础20分, 满分约110
# =====================================================================

import numpy as np
import pandas as pd
import talib

# ═══════════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════════

def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    log.set_level('order', 'error')

    # ── 核心参数 (与 vol180_breakout_backtest.py 完全一致) ──
    g.MAVOL_PERIOD   = 180      # MAVOL周期
    g.MAVOL_MULT     = 1.2      # MAVOL180 = MA(vol,180) * 1.2
    g.RESIST_LOOKBACK= 60       # zigzag回溯辅助
    g.MAX_DIST_PCT   = 10.0     # 距压力位 ≤10%
    g.MIN_LIMIT_UP   = 10       # 年涨停 >10 次
    g.HOLD_DAYS      = 3        # 无涨停最大持有天数
    g.STOP_LOSS      = -0.06    # 止损线 -6%
    g.FEE            = 0.0035   # 双边手续费 + 滑点
    g.MAX_POS        = 8        # 最大同时持仓
    g.POS_PCT        = 0.12     # 单只仓位 12%
    g.TOP_N          = 5        # 每日最多买入信号数
    g.QUEUE_DAYS     = 2        # 信号保留天数(未成交则过期)

    # ── 全局状态 ──
    g.stock_pool   = []                        # 合格股池
    g.buy_queue    = []                        # 买入队列 [(code,score,sig_close,sig_date,expire_day)]
    g.holdings     = {}                        # {code: {buy_price,had_zt,days,score}}
    g.pool_date    = None                      # 股池更新日期
    g.zigzag_cache = {}                        # {code: {date_str: resistance}} 避免重复计算

    # ── 每日调度 ──
    run_daily(before_open,   '09:00')          # 盘前: 更新股池
    run_daily(execute_buy,   '09:31')          # 开盘: 执行买入队列
    run_daily(scan_and_sell, '14:55')          # 尾盘: 卖出检查 + 信号扫描

# ═══════════════════════════════════════════════════════════════════
# 盘前: 股票池维护
# ═══════════════════════════════════════════════════════════════════

def before_open(context):
    """每月重建合格股池: 沪深主板 + 年涨停>10 + 非ST"""
    if g.stock_pool and g.pool_date and (context.current_dt - g.pool_date).days < 20:
        return

    try:
        all_stocks = list(get_all_securities(['stock']).index)
        main_board = [s for s in all_stocks
                      if s[:3] in ('600','601','603','605','000','001','002')]
        current_data = get_current_data()
        pool = []
        for code in main_board:
            if current_data[code].is_st:
                continue
            if current_data[code].name and current_data[code].name.startswith('*'):
                continue
            limit_count = count_limit_ups(code, 250)
            if limit_count > g.MIN_LIMIT_UP:
                pool.append(code)
        g.stock_pool  = pool
        g.pool_date   = context.current_dt
        log.info(f'[池] 更新: {len(pool)} 只 (主板+年涨停>{g.MIN_LIMIT_UP}+非ST)')
    except Exception as e:
        log.error(f'[池] 失败: {e}')

# ═══════════════════════════════════════════════════════════════════
# 开盘: 执行买入队列
# ═══════════════════════════════════════════════════════════════════

def execute_buy(context):
    """遍历买入队列, 执行未过期的买入信号"""
    if not g.buy_queue:
        return

    today = context.current_dt.date()
    executed  = []
    cd = get_current_data()

    for item in g.buy_queue[:]:
        code, score, signal_close, signal_date, expire_day = item

        # 信号过期 (>2天未成交)
        if today > expire_day:
            g.buy_queue.remove(item)
            continue

        # 已在持仓
        if code in context.portfolio.positions:
            g.buy_queue.remove(item)
            continue

        # 持仓上限
        if len(context.portfolio.positions) >= g.MAX_POS:
            break

        if cd[code].paused:
            continue

        try:
            open_price = cd[code].last_price
            df_prev = attribute_history(code, 1, '1d', ['close'], skip_paused=True)
            if df_prev.empty: continue

            # ── 买入保护 (与本地回测 603-619行 一致) ──
            if open_price >= signal_close * 1.095:   # 开盘涨停 → 买不到, 过期
                g.buy_queue.remove(item)
                continue
            if open_price > signal_close * 1.05:     # 高开 >5% → 追高风险, 跳过本日(保留队列)
                continue

            order_value(code, context.portfolio.total_value * g.POS_PCT)

            g.holdings[code] = {
                'buy_price':    open_price,
                'had_zt':       False,
                'days':         0,
                'score':        score,
                'signal_close': signal_close,
                'signal_date':  signal_date,
                'buy_date':     context.current_dt,
            }
            log.info(f'[买] {code} 评{score} @{open_price:.2f} 信号日{signal_date}')
            g.buy_queue.remove(item)

        except Exception as e:
            log.error(f'[买] {code} 异常: {e}')

# ═══════════════════════════════════════════════════════════════════
# 尾盘: 卖出检查 + 信号扫描
# ═══════════════════════════════════════════════════════════════════

def scan_and_sell(context):
    """PART1: 检查持仓卖出条件 | PART2: 扫描突破信号并入队"""
    check_positions(context)
    scan_signals(context)

# ── PART 1: 持仓卖出 ──

def check_positions(context):
    for code in list(context.portfolio.positions.keys()):
        if code not in g.holdings:
            g.holdings[code] = {'buy_price': 0, 'had_zt': False, 'days': 0,
                                'score': 0, 'signal_close': 0,
                                'signal_date': '', 'buy_date': context.current_dt}

        h = g.holdings[code]
        h['days'] += 1

        try:
            df = attribute_history(code, 3, '1d', ['close'], skip_paused=True)
            if df.empty: continue

            today_c  = float(df['close'].iloc[-1])
            prev_c   = float(df['close'].iloc[-2]) if len(df) > 1 else today_c
            bp       = h['buy_price'] if h['buy_price'] > 0 else today_c
            if prev_c <= 0: continue

            chg      = (today_c - prev_c) / prev_c
            loss_pct = (today_c - bp) / bp
            lim_th   = limit_threshold(code)
            is_zt    = (chg >= lim_th)
            reason   = None

            if loss_pct <= g.STOP_LOSS:                     # 止损-6%
                reason = '止损-6%'
            elif is_zt:                                      # 连板持有
                h['had_zt'] = True
                continue
            elif h['had_zt']:                                # 断板卖出
                reason = '断板'
            elif h['days'] >= g.HOLD_DAYS:                   # 到期卖出
                reason = f'到期D{h["days"]}'

            if reason:
                order_target_value(code, 0)
                gross = (today_c - bp) / bp * 100 if bp > 0 else 0
                net   = gross - g.FEE * 100
                log.info(f'[卖] {code} {reason} 持{h["days"]}天 毛{gross:+.1f}% 净{net:+.1f}%')
                del g.holdings[code]

        except Exception as e:
            log.error(f'[卖] {code} 异常: {e}')

# ── PART 2: 信号扫描 ──

def scan_signals(context):
    """扫描今日突破信号, 入队 (保留2天)"""
    if not g.stock_pool:
        return

    today    = context.current_dt
    today_d  = today.date()
    pool     = g.stock_pool[:400]
    new_list = []

    for code in pool:
        # 已在持仓或队列中 → 跳过
        if code in context.portfolio.positions: continue
        if any(item[0] == code for item in g.buy_queue): continue

        signal = check_signal(context, code)
        if signal:
            new_list.append(signal)

    if not new_list:
        return

    # 评分排序
    new_list.sort(key=lambda x: x['score'], reverse=True)

    # 入队 (保留2天过期)
    expire_day = today_d + pd.Timedelta(days=g.QUEUE_DAYS)
    for sig in new_list[:g.TOP_N]:
        # 去重
        if any(item[0] == sig['code'] for item in g.buy_queue):
            continue
        g.buy_queue.append((
            sig['code'],
            sig['score'],
            sig['close'],
            today.strftime('%Y%m%d'),
            expire_day,
        ))
        log.info(f'[信号] {sig["code"]} 评{sig["score"]} 价{sig["close"]} '
                f'量{sig["vol_ratio"]}x 距{sig["prev_dist_pct"]}% '
                f'理由:{sig["reasons"]}')

    # 清理过期队列
    g.buy_queue = [item for item in g.buy_queue if item[4] > today_d]

    log.info(f'[扫描] 今日{len(new_list)}信号 → 买入队列{len(g.buy_queue)}只')

# ═══════════════════════════════════════════════════════════════════
# 核心: 突破信号检查 (与本地 _check_signal 逐行一致)
# ═══════════════════════════════════════════════════════════════════

def check_signal(context, code):
    """检查单只股票当日是否触发买入信号。返回 dict 或 None。"""
    NEED = g.MAVOL_PERIOD + 15

    try:
        df = attribute_history(code, NEED, '1d',
                               ['open','close','high','low','volume'], skip_paused=True)
        if df.empty or len(df) < g.MAVOL_PERIOD:
            return None

        close = df['close'].values.astype(float)
        opn   = df['open'].values.astype(float)
        high  = df['high'].values.astype(float)
        low   = df['low'].values.astype(float)
        vol   = df['volume'].values.astype(float)
        idx   = len(close) - 1

        c = float(close[idx])
        o = float(opn[idx])
        v = float(vol[idx])

        # ── 计算 zigzag 找顶线 ──
        _, resistance = calc_zigzag_resistance(high, close, low, context, code)
        _, prev_res   = calc_zigzag_resistance(high[:idx], close[:idx], low[:idx],
                                                context, code)

        # ── MAVOL180 ──
        mavol180 = float(np.mean(vol[-g.MAVOL_PERIOD:]) * g.MAVOL_MULT)

        # ===== 硬性条件1: 收盘突破找顶线 =====
        if np.isnan(resistance) or resistance <= 0:
            return None
        if c <= resistance:
            return None

        # ===== 硬性条件2: 成交量 > MAVOL180 =====
        if np.isnan(mavol180) or mavol180 <= 0:
            return None
        if v <= mavol180:
            return None

        # ===== 硬性条件3: 非一字板 =====
        if o >= c * 1.095:
            return None

        # ===== 硬性条件4: 前一日收盘在找顶线下方0~10% (真突破) =====
        prev_c = float(close[idx-1])
        if np.isnan(prev_res) or prev_res <= 0 or prev_c > prev_res:
            return None

        prev_dist = (prev_res - prev_c) / prev_res * 100
        if not (0 < prev_dist <= g.MAX_DIST_PCT):
            return None

        # ===== 评分 =====
        vr     = v / mavol180
        bp_pct = (c - resistance) / resistance * 100
        ma5    = float(np.mean(close[-5:]))
        ma10   = float(np.mean(close[-10:]))
        chg    = (c - prev_c) / prev_c * 100 if prev_c > 0 else 0

        score = compute_score(prev_dist, vr, bp_pct, ma5, ma10, c, chg)
        reasons = build_reasons(prev_dist, vr, bp_pct, chg, ma5, ma10, c)

        return {
            'code':              code,
            'score':             score,
            'close':             round(c, 2),
            'volume':            int(v),
            'resistance':        round(float(resistance), 2),
            'mavol180':          round(float(mavol180), 0),
            'vol_ratio':         round(float(vr), 2),
            'breakthrough_pct':  round(float(bp_pct), 2),
            'change_pct':        round(float(chg), 1),
            'reasons':           '; '.join(reasons),
            'ma5':               round(ma5, 2),
            'ma10':              round(ma10, 2),
            'prev_dist_pct':     round(float(prev_dist), 1),
        }
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════════
# 统一评分接口 (后续可直接替换为 Logistic 打分)
# ═══════════════════════════════════════════════════════════════════

def compute_score(prev_dist, vol_ratio, break_pct, ma5, ma10, close, chg):
    """与 vol180_breakout_backtest.py _check_signal 评分逐行一致"""
    score = 20  # 基础分

    # 距压力位距离
    if prev_dist <= 3:
        score += 25
    elif prev_dist <= 5:
        score += 20
    else:
        score += 15

    # 量能级别
    if vol_ratio >= 3.0:
        score += 20
    elif vol_ratio >= 2.0:
        score += 15
    elif vol_ratio >= 1.5:
        score += 10
    else:
        score += 5

    # 均线状态
    if ma5 > 0 and ma10 > 0:
        if ma5 > ma10:
            score += 8
        if close > ma5:
            score += 5

    # 当日涨幅
    if chg >= 9.5:
        score += 12
    elif chg >= 7:
        score += 6
    else:
        score += 2

    return round(score)

def build_reasons(prev_dist, vol_ratio, break_pct, chg, ma5, ma10, close):
    """生成信号理由文本"""
    reasons = []
    if prev_dist <= 3:
        reasons.append(f'紧贴找顶线{prev_dist:.1f}%→突破{break_pct:+.1f}%')
    elif prev_dist <= 5:
        reasons.append(f'距找顶线{prev_dist:.1f}%→突破{break_pct:+.1f}%')
    else:
        reasons.append(f'距找顶线{prev_dist:.1f}%→突破{break_pct:+.1f}%')

    if vol_ratio >= 3.0:
        reasons.append(f'爆量{vol_ratio:.1f}倍MAVOL180')
    elif vol_ratio >= 2.0:
        reasons.append(f'显著放量{vol_ratio:.1f}倍MAVOL180')
    elif vol_ratio >= 1.5:
        reasons.append(f'放量{vol_ratio:.1f}倍MAVOL180')
    else:
        reasons.append(f'突破MAVOL180({vol_ratio:.1f}倍)')

    if ma5 > 0 and ma10 > 0:
        if ma5 > ma10:
            reasons.append('MA5>MA10多头')
        if close > ma5:
            reasons.append('站上MA5')

    if chg >= 9.5:
        reasons.append(f'涨停突破{chg:.1f}%')
    elif chg >= 7:
        reasons.append(f'大阳突破{chg:.1f}%')
    else:
        reasons.append(f'涨幅{chg:.1f}%')

    return reasons

# ═══════════════════════════════════════════════════════════════════
# Zigzag 找顶线 — 通达信主图指标等价实现
# ═══════════════════════════════════════════════════════════════════

def calc_zigzag_resistance(high, close, low, context, code):
    """
    通达信 zigzag 找顶线算法。

    核心: REF(H,5) == HHV(H,11) 识别 swing high →
    收集交替 swing highs/lows → 取最近2个 swing high 连线外推。

    返回: (adjusted_resistance, raw_resistance)
    """
    n = len(high)
    if n < 20:
        return np.nan, np.nan

    # ── Step 1: 识别 swing highs and lows ──
    def _ref(arr, offset):
        out = np.full(n, np.nan)
        out[max(0, offset):] = arr[:n - offset] if offset >= 0 else arr[-offset:]
        return out

    def _hhv(arr, period):
        out = np.full(n, np.nan)
        for i in range(n):
            p = max(1, period)
            out[i] = np.nanmax(arr[max(0, i-p+1):i+1])
        return out

    def _llv(arr, period):
        out = np.full(n, np.nan)
        for i in range(n):
            p = max(1, period)
            out[i] = np.nanmin(arr[max(0, i-p+1):i+1])
        return out

    hh = (_ref(high, 5) == _hhv(high, 11))
    ll = (_ref(low,  5) == _llv(low,  11))

    # ── Step 2: 收集 swing points ──
    all_points = []
    for i in range(n):
        if hh[i]:
            all_points.append((i, high[i], 'H'))
        if ll[i]:
            all_points.append((i, low[i], 'L'))

    all_points.sort(key=lambda x: x[0])

    # ── Step 3: 交替过滤 ──
    filtered = []
    last_type = None
    for pos, price, ptype in all_points:
        if ptype != last_type and not np.isnan(price):
            filtered.append((pos, price, ptype))
            last_type = ptype

    # ── Step 4: 取最近2个 swing highs ──
    recent_highs = [(pos, price) for pos, price, t in filtered[-10:] if t == 'H']

    if len(recent_highs) < 2:
        fallback = float(np.max(high[-60:]))
        return fallback, fallback

    p1, v1 = recent_highs[-2]
    p2, v2 = recent_highs[-1]

    if p2 <= p1:
        return float(v2), float(v2)

    # ── Step 5: 连线外推 ──
    slope = (v2 - v1) / (p2 - p1)
    raw_resistance = v2 + slope * (n - 1 - p2)

    # ── 边界保护 ──
    recent_high = float(np.max(high[-5:]))
    if raw_resistance < recent_high:
        raw_resistance = recent_high

    return float(raw_resistance), float(raw_resistance)

# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def limit_threshold(code):
    """涨停阈值 — 按板块区分"""
    s = str(code)
    if s[:3] in ('300','301','688'):
        return 0.195
    if s[:1] in ('8','4'):
        return 0.295
    return 0.095

def count_limit_ups(code, lookback=250):
    """近N日涨停次数 (≥9.5% threshold)"""
    try:
        df = attribute_history(code, lookback, '1d', ['close'], skip_paused=True)
        if df.empty or len(df) < 5:
            return 0
        c   = df['close'].values.astype(float)
        pct = (c[1:] - c[:-1]) / c[:-1]
        return int(np.sum(pct >= limit_threshold(code)))
    except:
        return 0
