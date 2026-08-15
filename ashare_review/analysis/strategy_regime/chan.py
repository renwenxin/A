"""简化缠论工具 — 分型 / 笔 / 走势分类 / 背驰

用于:
  1. 上证指数日线走势分类（上涨/下跌/盘整）→ 行情 regime 判断
  2. 冰点反转确认（下跌段创新低 + MACD 背驰 = 下跌衰竭 → 缠论一买）
  3. 个股超跌底部特征（底分型 + 背驰）

核心思想（缠论）:
  - 不对市场做预测，只对走势做完全分类并制定对应策略
  - 卖点永远在下跌中产生；买点买在"下跌衰竭"，不买"上涨加速"
  - 分型需要 2 根 K 线确认（避免用未确认的当下分型 → 无未来函数）

实现为简化的 O(n) 版本：完整缠论笔段/中枢的 BACKSET 链在这里被
"局部极值分型 + 顶底交替过滤"近似，精度足够用于 regime 分类。
"""
import numpy as np
import pandas as pd


def ema(series, n):
    """EMA"""
    return series.ewm(span=n, adjust=False).mean()


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    """MACD 快线/慢线/柱面积"""
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    macd_hist = (dif - dea) * 2
    return pd.DataFrame({'dif': dif, 'dea': dea, 'macd': macd_hist})


def detect_fractals(df: pd.DataFrame) -> pd.Series:
    """检测顶/底分型。返回 1=顶分型, -1=底分型, 0=无。

    顶分型: 中间 K 线高点最高、低点也最高（相对左右各 1 根）
    底分型: 中间 K 线低点最低、高点也最低
    """
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    n = len(df)
    out = np.zeros(n, dtype=int)
    for i in range(1, n - 1):
        h_i, h_l, h_r = high[i], high[i - 1], high[i + 1]
        l_i, l_l, l_r = low[i], low[i - 1], low[i + 1]
        if h_i > h_l and h_i > h_r and l_i > l_l and l_i > l_r:
            out[i] = 1
        elif l_i < l_l and l_i < l_r and h_i < h_l and h_i < h_r:
            out[i] = -1
    return pd.Series(out, index=df.index)


def build_bi(df: pd.DataFrame, min_gap: int = 4) -> list:
    """从分型构建笔序列。

    规则（简化缠论）:
      - 顶分型 / 底分型 必须交替出现
      - 相邻分型索引差 >= min_gap（顶底之间至少隔 1 根不共用 K 线，
        含分型三根共至少 4 根）
      - 同类分型（连续两个顶/底）只保留更极端的一个

    返回: [{start, end, direction('up'/'down'), start_price, end_price,
            start_date, end_date, macd_area, pct}, ...]
      start_price/end_price: 分型的极值（顶=最高价 / 底=最低价），
                              即缠论笔的摆动点，比收盘价更稳定
      macd_area: 笔内 MACD 柱面积（背驰用）
      pct: 笔内价格涨跌幅%（背驰用）
    """
    frac = detect_fractals(df)
    idxs = np.where(frac.values != 0)[0]
    if len(idxs) < 2:
        return []

    # 过滤相邻分型：间隔不足 / 同类只留更极端
    kept = []  # (index, type)
    for p in idxs:
        if kept and p - kept[-1][0] < min_gap:
            pt, tt = kept[-1]
            if frac.values[p] == tt:
                # 同类：顶取更高，底取更低
                if tt == 1 and df['high'].values[p] >= df['high'].values[pt]:
                    kept[-1] = (p, tt)
                elif tt == -1 and df['low'].values[p] <= df['low'].values[pt]:
                    kept[-1] = (p, tt)
            # 异类且间隔不足 → 跳过（不满足笔的独立性）
            continue
        kept.append((p, int(frac.values[p])))

    # 再强制顶底交替（删掉连续同向）
    alternated = []
    for p, t in kept:
        if alternated and alternated[-1][1] == t:
            # 保留更极端
            pt, tt = alternated[-1]
            if t == 1 and df['high'].values[p] >= df['high'].values[pt]:
                alternated[-1] = (p, t)
            elif t == -1 and df['low'].values[p] <= df['low'].values[pt]:
                alternated[-1] = (p, t)
            continue
        alternated.append((p, t))
    kept = alternated

    if len(kept) < 2:
        return []

    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    macd = calc_macd(df['close'])
    macd_arr = macd['macd'].values

    bis = []
    for i in range(len(kept) - 1):
        s, t_s = kept[i]
        e, t_e = kept[i + 1]
        if t_s == t_e:
            continue
        direction = 'up' if t_s == -1 else 'down'  # 底→顶 为上涨笔
        # 摆动点用分型极值：顶=最高价，底=最低价
        s_price = low[s] if t_s == -1 else high[s]
        e_price = high[e] if t_e == 1 else low[e]
        area = float(np.nansum(macd_arr[s:e + 1]))
        pct = (e_price - s_price) / s_price * 100 if s_price > 0 else 0.0
        bis.append({
            'start': s, 'end': e,
            'direction': direction,
            'start_price': float(s_price), 'end_price': float(e_price),
            'start_date': df['trade_date'].iloc[s],
            'end_date': df['trade_date'].iloc[e],
            'macd_area': area,
            'pct': pct,
        })
    return bis


def completed_bi(bis: list, day_idx: int, confirm: int = 2) -> list:
    """返回截至 day_idx（且端点已确认）的笔。

    confirm: 分型需要后 2 根 K 线确认，笔端点索引 <= day_idx - confirm
    """
    return [b for b in bis if b['end'] <= day_idx - confirm]


def classify_trend(bis: list, day_idx: int, lookback: int = 5) -> str:
    """用最近已完成笔判断当前走势类型: '上涨' / '下跌' / '盘整'

    规则（简化）:
      - 无已确认笔 → 盘整
      - 最近一笔向上 且 底部整体抬高（近期向下笔的低点抬升）→ 上涨
      - 最近一笔向下 且 顶部整体降低 → 下跌
      - 高低点交错无递进 → 盘整
    """
    comp = completed_bi(bis, day_idx)
    if len(comp) < 2:
        return '盘整'
    recent = comp[-lookback:]
    if len(recent) < 2:
        return '盘整'

    last = recent[-1]
    ups = [b for b in recent if b['direction'] == 'up']
    downs = [b for b in recent if b['direction'] == 'down']

    if last['direction'] == 'up':
        # 上涨结构：最近向上笔创新高，且最近回调不破前低
        if ups and downs:
            new_high = last['end_price'] >= max(b['end_price'] for b in ups)
            low_hold = recent[-1]['start_price'] >= recent[-2]['start_price'] if len(recent) >= 2 else True
            if new_high:
                return '上涨'
        elif len(ups) == len(recent) and recent[-1]['end_price'] > recent[-2]['end_price']:
            return '上涨'
        return '盘整'
    else:
        if downs:
            new_low = last['end_price'] <= min(b['end_price'] for b in downs)
            if new_low:
                return '下跌'
        return '盘整'


def detect_beichi(bis: list, day_idx: int, confirm: int = 2) -> bool:
    """背驰检测（缠论一买/一卖的核心）。

    规则: 当前下跌笔创新低（end_price < 前一下跌笔），但
          - MACD 柱面积更小（动能衰竭）或
          - 笔内跌幅更小（pct 更浅）
      → 背驰成立，下跌衰竭，可能反转。

    用于: 上证指数冰点反转确认 + 个股超跌底部。
    """
    comp = completed_bi(bis, day_idx)
    downs = [b for b in comp if b['direction'] == 'down']
    if len(downs) < 2:
        return False
    cur = downs[-1]
    prev = downs[-2]
    if cur['end_price'] >= prev['end_price']:
        return False  # 未创新低，不是背驰，是正常回调
    # 创新低但动能衰减
    area_shrink = cur['macd_area'] > prev['macd_area']  # 柱面积更大(更弱) → 背驰
    depth_shrink = cur['pct'] > prev['pct']  # 跌幅更浅
    return area_shrink or depth_shrink


def bottom_fractal(df: pd.DataFrame, idx: int) -> bool:
    """个股当前是否刚形成底分型（缠论底分型 + 2根确认）。"""
    if idx < 3:
        return False
    return detect_fractals(df).iloc[idx - 2] == -1


def current_bi_direction(bis: list, day_idx: int, df: pd.DataFrame,
                         confirm: int = 2) -> str:
    """最近一笔（含未完成的当下笔）方向 — 更及时的缠论趋势。

    规则: 取 day_idx 前最后一个已确认分型（需 confirm 根确认）：
      - 底分型 → 现价高于该底 → 上涨笔进行中
      - 顶分型 → 现价低于该顶 → 下跌笔进行中
      - 否则 → 盘整

    比 classify_trend（只认已完成笔）更早识别拐点，适合 regime 分类。
    """
    frac = detect_fractals(df)
    values = frac.values
    n = len(values)
    if day_idx < confirm + 1:
        return '盘整'
    confirmed = [i for i in range(day_idx - confirm) if values[i] != 0]
    if not confirmed:
        return '盘整'
    last = confirmed[-1]
    last_type = int(values[last])
    close_now = float(df['close'].values[day_idx])
    close_last = float(df['close'].values[last])
    if last_type == -1:
        return '上涨' if close_now > close_last else '盘整'
    else:
        return '下跌' if close_now < close_last else '盘整'


def channel_trend(df: pd.DataFrame, window: int = 60) -> str:
    """简单通道趋势（MA 视角），供冰点抄底个股过滤参考。"""
    close = df['close'].iloc[-window:] if len(df) >= window else df['close']
    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    if ma5 > ma20:
        return 'up'
    if ma5 < ma20:
        return 'down'
    return 'flat'
