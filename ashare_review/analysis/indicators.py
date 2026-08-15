"""技术指标计算"""
import pandas as pd
import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# 基础指标
# ═══════════════════════════════════════════════════════════════════════════════

def calc_ma(df: pd.DataFrame, periods: list) -> pd.DataFrame:
    """计算移动平均线"""
    for p in periods:
        df[f'ma{p}'] = df['close'].rolling(window=p).mean()
    return df


def calc_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    """MACD指标"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd_dif'] = ema_fast - ema_slow
    df['macd_dea'] = df['macd_dif'].ewm(span=signal, adjust=False).mean()
    df['macd_bar'] = 2 * (df['macd_dif'] - df['macd_dea'])
    return df


def calc_ma_converge(df: pd.DataFrame, short=60, long=89, threshold=0.03) -> pd.DataFrame:
    """检测60/89日均线是否粘合 (价差<3%)"""
    s = df[f'ma{short}']
    l = df[f'ma{long}']
    diff_pct = abs(s - l) / l
    df['ma60_89_converge'] = diff_pct < threshold
    df['ma60_89_slope_up'] = (s.diff(3) > 0) & (l.diff(3) > 0)
    return df


def calc_volume_ratio(df: pd.DataFrame, period=5) -> pd.DataFrame:
    """量比: 当日成交量 / 前N日均量"""
    df['vol_ma5'] = df['volume'].rolling(window=period).mean()
    df['volume_ratio'] = df['volume'] / df['vol_ma5']
    return df


def calc_daily_change(df: pd.DataFrame) -> pd.DataFrame:
    """涨跌幅"""
    df['change_pct'] = df['close'].pct_change() * 100
    return df


def calc_amplitude(df: pd.DataFrame) -> pd.DataFrame:
    """振幅"""
    df['amplitude'] = (df['high'] - df['low']) / df['close'].shift(1) * 100
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 通达信内置函数 Python 实现
# ═══════════════════════════════════════════════════════════════════════════════

def _tdx_sma(x: pd.Series, n: int, m: int) -> pd.Series:
    """通达信 SMA(X,N,M): X的N日加权移动平均，权重M。

    SMA = (X*M + Y'*(N-M)) / N
    其中 Y' 是前一日的SMA值。
    """
    result = pd.Series(np.nan, index=x.index)
    first_valid = x.first_valid_index()
    if first_valid is None:
        return result
    result.at[first_valid] = x.at[first_valid]
    alpha = m / n
    one_minus_alpha = (n - m) / n
    prev = result.at[first_valid]
    for i in range(x.index.get_loc(first_valid) + 1, len(x)):
        if pd.isna(x.iloc[i]):
            result.iloc[i] = np.nan
        else:
            result.iloc[i] = x.iloc[i] * alpha + prev * one_minus_alpha
            prev = result.iloc[i]
    return result


def _cross(a: pd.Series, b: pd.Series) -> pd.Series:
    """通达信 CROSS(A,B): A从下方上穿B。

    返回 bool Series: A上穿B时为True。
    """
    return (a > b) & (a.shift(1) <= b.shift(1))


# ═══════════════════════════════════════════════════════════════════════════════
# SWL/SWS 操盘线（通达信 主图指标 第1-7行）
# ═══════════════════════════════════════════════════════════════════════════════

def calc_swl_sws(df: pd.DataFrame) -> pd.DataFrame:
    """计算 SWL 操盘线和 SWS 生命线。

    通达信主图指标（主图指标.txt）:
      SWL:(EMA(CLOSE,10)*7+EMA(CLOSE,20)*3)/10;
      SWS:DMA(EMA(CLOSE,20),MAX(1,100*(SUM(VOL,5)/(3*CAPITAL)))),COLORWHITE,DOTLINE;
      主力操盘线:IF(SWL>SWS,SWL,DRAWNULL),COLORRED,LINETHICK2;
      生命线:IF(SWL<SWS,SWL,DRAWNULL),COLORGREEN,LINETHICK2;

    SWL > SWS → 主力操盘线(红色/多头)
    SWL < SWS → 生命线(绿色/防守)
    """
    close = df['close']
    volume = df['volume']

    # EMA(CLOSE,10)*7 + EMA(CLOSE,20)*3 / 10
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    df['swl'] = (ema10 * 7 + ema20 * 3) / 10.0

    # SWS = DMA(EMA(CLOSE,20), MAX(1, 100*(SUM(VOL,5)/(3*CAPITAL))))
    # DMA(X, A): A 是加权系数，需归一化到 (0,1] 区间
    # 通达信中 CAPITAL 单位为手(100股)，这里的 vol 是股，调整缩放
    sum_vol5 = volume.rolling(5).sum()
    n = len(df)
    # 用5日成交额/总市值估算权重，归一化到合理范围
    vol_factor = np.full(n, 0.01)
    for i in range(n):
        sv5 = sum_vol5.iloc[i]
        if sv5 > 0:
            # 成交量(股) / (流通股本估值的3倍 * 100) → 近似 DMA 权重
            raw = 100.0 * sv5 / (3.0 * 5.5e9)  # 5.5B股 ≈ A股平均流通盘
            vol_factor[i] = min(max(raw, 0.001), 0.95)  # 限制在 [0.001, 0.95]

    sws = np.full(n, np.nan)
    first_idx = ema20.first_valid_index()
    if first_idx is not None:
        fi = df.index.get_loc(first_idx)
        sws[fi] = ema20.iloc[fi]
        for i in range(fi + 1, n):
            a = float(vol_factor[i])
            sws[i] = ema20.iloc[i] * a + sws[i - 1] * (1.0 - a)
    df['sws'] = sws

    # 主力操盘线 = SWL > SWS
    df['swl_control'] = df['swl'] > df['sws']
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 成交量复合炮
# ═══════════════════════════════════════════════════════════════════════════════

def calc_volume_cannon(df: pd.DataFrame) -> pd.DataFrame:
    """识别成交量复合炮信号。

    成交量复合炮: 连续3根及以上放量柱(>1.5倍20日均量)。

    信号等级:
      0 — 无信号
      1 — 单根放量（量 > 20日均量 × 1.5）
      2 — 双炮（连续2根放量柱）
      3 — 复合炮（连续3根及以上放量柱）

    cannon_name: 信号名称（"双炮", "复合炮", "单炮"）
    """
    n = len(df)
    vol_ma20 = df['volume'].rolling(20).mean()
    is_burst = (df['volume'] > vol_ma20 * 1.5).astype(int)

    cannon_signal = np.zeros(n, dtype=int)
    cannon_name = [''] * n

    i = n - 1
    while i >= 0:
        if is_burst.iloc[i]:
            start = i
            while start > 0 and is_burst.iloc[start - 1]:
                start -= 1
            count = i - start + 1
            for j in range(start, i + 1):
                cannon_signal[j] = min(count, 5)
                if count >= 3:
                    cannon_name[j] = '复合炮'
                elif count == 2:
                    cannon_name[j] = '双炮'
                else:
                    cannon_name[j] = '单炮'
            i = start - 1
        else:
            i -= 1

    df['cannon_signal'] = cannon_signal.astype(int)
    df['cannon_name'] = cannon_name
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 异动资金
# ═══════════════════════════════════════════════════════════════════════════════

def calc_yicha_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """计算异动资金动量 — 量价异常检测。

    检测方法: 当日成交量超过20日均量2倍以上且涨幅>5% → 异动信号。
    同时计算异动后的5日走势评分。
    """
    vol_ma20 = df['volume'].rolling(20).mean()
    change_pct = df['close'].pct_change() * 100

    # 异动信号: 超大量 + 大涨幅
    df['yicha_signal'] = (
        (df['volume'] > vol_ma20 * 2.0) & (change_pct > 5.0)
    ).astype(int)

    # 异动强度: 量比 × 涨幅
    df['yicha_strength'] = np.where(
        df['yicha_signal'] > 0,
        (df['volume'] / vol_ma20.replace(0, np.nan) * change_pct / 100).fillna(0),
        0.0
    )
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 主力资金
# ═══════════════════════════════════════════════════════════════════════════════

def calc_main_capital(df: pd.DataFrame, index_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """计算主力资金指标。

    简化实现: 用价格 × 成交量 加权移动平均追踪资金流入流出趋势。
    返回值归一化到 0-100 区间（< 30 = 低位，> 70 = 高位）。

    Args:
        df: 个股日线数据
        index_df: 大盘指数数据（可选，用于相对强弱修正）
    """
    close = df['close']
    volume = df['volume']

    # 资金流: 价格变动 × 成交量（正=流入，负=流出）
    price_chg = close.diff()
    money_flow = price_chg * volume

    # 归一化: 用EMA平滑后转0-100标度
    mf_ema = money_flow.ewm(span=13, adjust=False).mean()
    mf_abs_ema = money_flow.abs().ewm(span=13, adjust=False).mean()

    # MFI: Money Flow Index 变体
    mfi = np.full(len(df), 50.0)
    for i in range(1, len(df)):
        if mf_abs_ema.iloc[i] > 0:
            mfi[i] = 50.0 + 50.0 * mf_ema.iloc[i] / mf_abs_ema.iloc[i]
        else:
            mfi[i] = 50.0
    mfi = np.clip(mfi, 0, 100)

    df['main_cap'] = mfi
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 一键补全
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_all(df: pd.DataFrame) -> pd.DataFrame:
    """一键补全所有技术指标。

    包含: 均线(5/10/20/60/89/250), MACD, 均线粘合, 量比, 涨跌幅, 振幅
          + SWL/SWS, 量能复合炮, 异动资金, 主力资金, KDJ, 吸筹
    """
    df = calc_ma(df, [5, 10, 20, 60, 89, 250])
    df = calc_macd(df)
    df = calc_ma_converge(df)
    df = calc_volume_ratio(df)
    df = calc_daily_change(df)
    df = calc_amplitude(df)
    df = calc_swl_sws(df)
    df = calc_volume_cannon(df)
    df = calc_yicha_momentum(df)

    # ── 主力资金 ──
    df = calc_main_capital(df, None)

    # ── KDJ ──
    low9 = df['low'].rolling(9).min()
    high9 = df['high'].rolling(9).max()
    rsv = (df['close'] - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = _tdx_sma(rsv, 3, 1)
    d = _tdx_sma(k, 3, 1)
    df['kdj_k'] = k
    df['kdj_d'] = d
    df['kdj_j'] = 3 * k - 2 * d
    df['kdj_golden'] = _cross(k, d) & (k < 20)

    # ── 吸筹信号 ──
    var2 = df['low'].shift(1)
    abs_diff = (df['low'] - var2).abs()
    pos_diff = (df['low'] - var2).clip(lower=0)
    var3 = _tdx_sma(abs_diff, 3, 1) / _tdx_sma(pos_diff, 3, 1).replace(0, np.nan) * 100
    var4 = _tdx_sma(var3 * 10, 3, 1)
    var5 = df['low'].rolling(19).min()
    var6 = var4.rolling(13).max()
    var8 = _tdx_sma(
        ((var4 + var6 * 2) / 2).where(df['low'] <= var5, 0),
        3, 1
    ) / 618
    df['accumulate'] = var8.clip(upper=150)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# Zigzag 找顶线（完整还原 通达信 主图指标 第104-155行）
# ═══════════════════════════════════════════════════════════════════════════════

def calc_zigzag_find_top_line(df: pd.DataFrame) -> pd.DataFrame:
    """完整还原通达信主图指标的 找顶线 计算。

    严格对应 主图指标.txt 第104-155行的 zigzag swing high 检测逻辑:
        HH:=REF(H,5)=HHV(H,11) → FG01 → FG02 → FG0
        → FG1 → FG → G1X → G1 → G2 → NN
        → 找顶线:DRAWLINE(NN,H,REF(NN,1),REF(H,1),1)

    核心链路:
      - 用11日窗口确认 swing high/low 顶点
      - BACKSET 精确定位峰值位置
      - 三层 zigzag 过滤 (FG0→G1→G2) 去除噪音
      - NN 选出最显著的 swing highs
      - DRAWLINE 连接最近2个 NN 点并线性外推

    Args:
        df: 含 OHLC + ma5/ma10 列的 DataFrame

    Returns:
        添加了 'find_top_line' 列的 DataFrame
    """
    n = len(df)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    ma5 = df['ma5'].values.astype(float)
    ma10 = df['ma10'].values.astype(float)

    # ── 通达信内置函数 ──

    def _ref(arr, offset):
        out = np.full(n, np.nan)
        if offset > 0:
            out[offset:] = arr[:-offset]
        elif offset == 0:
            out[:] = arr
        else:
            out[:offset] = arr[-offset:]
        return out

    def _hhv(arr, period):
        out = np.full(n, np.nan)
        for i in range(n):
            p = max(1, int(period[i] if hasattr(period, '__iter__') else period))
            s = max(0, i - p + 1)
            out[i] = np.nanmax(arr[s:i + 1])
        return out

    def _llv(arr, period):
        out = np.full(n, np.nan)
        for i in range(n):
            p = max(1, int(period[i] if hasattr(period, '__iter__') else period))
            s = max(0, i - p + 1)
            out[i] = np.nanmin(arr[s:i + 1])
        return out

    def _backset(cond, m):
        out = np.zeros(n)
        for i in range(n):
            if cond[i]:
                s = max(0, i - m + 1)
                out[s:i + 1] = 1
        return out

    def _barslast(cond):
        out = np.full(n, 1e9)
        last = -1
        for i in range(n):
            if cond[i]:
                out[i] = 0
                last = i
            elif last >= 0:
                out[i] = i - last
        return out

    def _count(cond, period):
        out = np.zeros(n)
        for i in range(n):
            p = max(1, int(period[i] if hasattr(period, '__iter__') else period))
            s = max(0, i - p + 1)
            out[i] = np.sum(cond[s:i + 1])
        return out

    # ── 第104-105行: HH/LL — 11日窗口 + 5日偏移确认 swing 顶点 ──
    hh = (_ref(high, 5) == _hhv(high, 11)).astype(float)
    ll = (_ref(low, 5) == _llv(low, 11)).astype(float)

    # ── 第106-107行: FG01/FD01 — BACKSET 精确定位峰值 bar ──
    fg01 = (_backset(hh, 6) > _backset(hh, 5)).astype(int)
    fd01 = (_backset(ll, 6) > _backset(ll, 5)).astype(int)

    # ── 第108-111行: FG02/FD02 — BARSLAST 交替过滤 ──
    bl_fg01 = _barslast(fg01)
    bl_fd01 = _barslast(fd01)
    fg02 = np.zeros(n, dtype=int)
    fd02 = np.zeros(n, dtype=int)
    for i in range(n):
        if bl_fg01[i] == bl_fd01[i] and ma5[i] > ma10[i]:
            fg02[i] = fg01[i]
        elif bl_fd01[i] > bl_fg01[i]:
            fg02[i] = fg01[i]
        if bl_fg01[i] == bl_fd01[i] and ma10[i] > ma5[i]:
            fd02[i] = fd01[i]
        elif bl_fg01[i] > bl_fd01[i]:
            fd02[i] = fd01[i]

    # ── 第112-113行: FG0/FD0 — 区间最高/最低确认 ──
    bl_fd02 = _barslast(fd02)
    bl_fg02 = _barslast(fg02)
    fg0 = np.zeros(n, dtype=int)
    fd0 = np.zeros(n, dtype=int)
    for i in range(n):
        if fg02[i]:
            period = max(1, int(bl_fd02[i]) + 1)
            if high[i] == np.max(high[max(0, i - period + 1):i + 1]):
                fg0[i] = 1
        if fd02[i]:
            period = max(1, int(bl_fg02[i]) + 1)
            if low[i] == np.min(low[max(0, i - period + 1):i + 1]):
                fd0[i] = 1

    # ── 第114-115行: GQ/DQ — 缺口检测 ──
    gq = np.zeros(n, dtype=int)
    dq = np.zeros(n, dtype=int)
    for i in range(1, n):
        if low[i] > high[i - 1]:
            gq[i] = 1
        if high[i] < low[i - 1]:
            dq[i] = 1

    # ── 第116-125行: 提取 FG0/FD0 位置的价格 ──
    bl_fg0 = _barslast(fg0)
    bl_fd0 = _barslast(fd0)
    fgh = np.full(n, np.nan)
    fgl = np.full(n, np.nan)
    fgh1 = np.full(n, np.nan)
    fgl1 = np.full(n, np.nan)
    fgl2 = np.full(n, np.nan)
    fdh = np.full(n, np.nan)
    fdl = np.full(n, np.nan)
    fdh1 = np.full(n, np.nan)
    fdl1 = np.full(n, np.nan)
    fdh2 = np.full(n, np.nan)
    for i in range(n):
        if bl_fg0[i] < 1e8:
            pos = i - int(bl_fg0[i])
            fgh[i] = high[pos]
            fgl[i] = low[pos]
            fgh1[i] = high[min(pos + 1, n - 1)]
            fgl1[i] = low[min(pos + 1, n - 1)]
            fgl2[i] = low[min(pos + 2, n - 1)]
        if bl_fd0[i] < 1e8:
            pos = i - int(bl_fd0[i])
            fdh[i] = high[pos]
            fdl[i] = low[pos]
            fdh1[i] = high[min(pos + 1, n - 1)]
            fdl1[i] = low[min(pos + 1, n - 1)]
            fdh2[i] = high[min(pos + 2, n - 1)]

    # ── 第126-127行: FGZL/FDZH — 支撑/压力验证点 ──
    fgzl = np.full(n, np.nan)
    fdzh = np.full(n, np.nan)
    for i in range(n):
        if not np.isnan(fgh1[i]) and not np.isnan(fgl[i]):
            if fgh1[i] < fgl[i]:
                fgzl[i] = fgl[i]
            elif not np.isnan(fgl1[i]) and not np.isnan(fgl2[i]):
                fgzl[i] = fgl2[i] if fgl[i] <= fgl1[i] else fgl1[i]
        if not np.isnan(fdl1[i]) and not np.isnan(fdh[i]):
            if fdl1[i] > fdh[i]:
                fdzh[i] = fdh[i]
            elif not np.isnan(fdh1[i]) and not np.isnan(fdh2[i]):
                fdzh[i] = fdh2[i] if fdh[i] >= fdh1[i] else fdh1[i]

    # ── 第128-131行: FG1/FD1 → FG/FD ──
    fg1 = np.zeros(n, dtype=int)
    fd1 = np.zeros(n, dtype=int)
    fg = np.zeros(n, dtype=int)
    fd = np.zeros(n, dtype=int)
    for i in range(n):
        if fg0[i] and not np.isnan(fgh[i]) and not np.isnan(fdzh[i]) and fgh[i] > fdzh[i]:
            fg1[i] = 1
        if fd0[i] and not np.isnan(fdl[i]) and not np.isnan(fgzl[i]) and fdl[i] < fgzl[i]:
            fd1[i] = 1
        if fg1[i]:
            if (not np.isnan(fdh[i]) and not np.isnan(fdl[i])
                    and not np.isnan(fgl1[i]) and not np.isnan(fdl[i])):
                if fgh[i] > fdh[i] and fgl[i] > fdl[i] and fgl1[i] > fdl[i]:
                    fg[i] = 1
        if fd1[i]:
            if (not np.isnan(fgl[i]) and not np.isnan(fdh[i])
                    and not np.isnan(fdh1[i])):
                if fdl[i] < fgl[i] and fdh[i] < fgh[i] and fdh1[i] < fgh[i]:
                    fd[i] = 1

    # ── 第132-138行: BH0 → BK — 包含K线计数 & 过滤阈值 ──
    bh0 = np.zeros(n, dtype=int)
    for i in range(1, n):
        if ((high[i] <= high[i - 1] and low[i] >= low[i - 1]) or
                (high[i] >= high[i - 1] and low[i] <= low[i - 1])):
            bh0[i] = 1
    bl_fd = _barslast(fd)
    bl_fg_2 = _barslast(fg)
    bhg = np.zeros(n)
    bhd = np.zeros(n)
    bgq_cnt = np.zeros(n)
    bdq_cnt = np.zeros(n)
    bk0 = np.full(n, 3.0)
    bk = np.full(n, 3.0)
    for i in range(n):
        if bl_fd0[i] < 1e8:
            p = int(bl_fd0[i]) + 1
            bhg[i] = _count(bh0, p)[i]
            bgq_cnt[i] = _count(gq, p)[i]
        if bl_fg0[i] < 1e8:
            p = int(bl_fg0[i]) + 1
            bhd[i] = _count(bh0, p)[i]
            bdq_cnt[i] = _count(dq, p)[i]
        if bhg[i] > 0:
            bk0[i] = bhg[i] + 2
        elif bhd[i] > 0:
            bk0[i] = bhd[i] + 2
        if bgq_cnt[i] > 0:
            bk[i] = bk0[i] - bgq_cnt[i]
        elif bdq_cnt[i] > 0:
            bk[i] = bk0[i] - bdq_cnt[i]
        else:
            bk[i] = bk0[i]

    # ── 第139-140行: G1X/D1X — 时间过滤后的 swing 点 ──
    g1x = np.zeros(n, dtype=int)
    d1x = np.zeros(n, dtype=int)
    for i in range(n):
        if fg[i] and bl_fd[i] < 1e8 and bl_fd[i] > bk[i]:
            g1x[i] = 1
        if fd[i] and bl_fg_2[i] < 1e8 and bl_fg_2[i] > bk[i]:
            d1x[i] = 1

    # ── 第141-145行: G1/D1 — 第一层 zigzag 确认 ──
    bl_g1x = _barslast(g1x)
    bl_d1x = _barslast(d1x)
    bl_fg1 = _barslast(fg1)
    bl_fd1 = _barslast(fd1)
    g1 = np.zeros(n, dtype=int)
    d1_2 = np.zeros(n, dtype=int)
    for i in range(n):
        if fg0[i] and bl_fg0[i] < 1e8 and bl_g1x[i] < 1e8:
            p_fg0 = i - int(bl_fg0[i])
            p_g1x = i - int(bl_g1x[i])
            if p_fg0 >= 0 and p_g1x >= 0:
                if high[p_fg0] >= high[p_g1x] and bl_d1x[i] > bl_g1x[i]:
                    g1[i] = 1
        if fg1[i] and bl_fd1[i] < 1e8 and bl_g1x[i] < 1e8:
            p_fg1 = i - int(bl_fg1[i])
            p_g1x = i - int(bl_g1x[i])
            if p_fg1 >= 0 and p_g1x >= 0:
                period = int(bl_fd1[i]) + 1
                if _count(gq, period)[i] > 0 and high[p_fg1] > high[p_g1x]:
                    g1[i] = 1
        if fd0[i] and bl_fd0[i] < 1e8 and bl_d1x[i] < 1e8:
            p_fd0 = i - int(bl_fd0[i])
            p_d1x = i - int(bl_d1x[i])
            if p_fd0 >= 0 and p_d1x >= 0:
                if low[p_fd0] <= low[p_d1x] and bl_g1x[i] > bl_d1x[i]:
                    d1_2[i] = 1
        if fd1[i] and bl_fg1[i] < 1e8 and bl_d1x[i] < 1e8:
            p_fd1 = i - int(bl_fd1[i])
            p_d1x = i - int(bl_d1x[i])
            if p_fd1 >= 0 and p_d1x >= 0:
                period = int(bl_fg1[i]) + 1
                if _count(dq, period)[i] > 0 and low[p_fd1] < high[p_d1x]:
                    d1_2[i] = 1

    # ── 第146-147行: G1H/D1L — G1/D1 位置参考价 ──
    bl_g1 = _barslast(g1)
    bl_d1_2 = _barslast(d1_2)
    g1h = np.full(n, np.nan)
    d1l = np.full(n, np.nan)
    for i in range(n):
        if bl_d1_2[i] > bl_g1[i]:
            pos = i - int(bl_g1[i]) if bl_g1[i] < 1e8 else i - int(bl_d1_2[i])
            if 0 <= pos < n:
                g1h[i] = high[pos]
        elif bl_g1[i] >= 0 and bl_d1_2[i] < 1e8:
            pos = i - int(bl_d1_2[i])
            if 0 <= pos < n:
                g1h[i] = high[pos]
        if bl_g1[i] > bl_d1_2[i]:
            pos = i - int(bl_d1_2[i]) if bl_d1_2[i] < 1e8 else i - int(bl_g1[i])
            if 0 <= pos < n:
                d1l[i] = low[pos]
        elif bl_d1_2[i] >= 0 and bl_g1[i] < 1e8:
            pos = i - int(bl_g1[i])
            if 0 <= pos < n:
                d1l[i] = low[pos]

    # ── 第148-149行: G2/D2 — 第二层 zigzag 确认（最严格） ──
    g2 = np.zeros(n, dtype=int)
    d2 = np.zeros(n, dtype=int)
    for i in range(n):
        if g1[i] and not np.isnan(g1h[i]) and bl_d1_2[i] < 1e8:
            period = int(bl_d1_2[i]) + 1
            s = max(0, i - period + 1)
            if (g1h[s:i + 1].max() == high[i] and high[i] > high[max(0, i - 1)]
                    and bl_d1_2[i] > bl_g1[i]):
                g2[i] = 1
        if d1_2[i] and not np.isnan(d1l[i]) and bl_g1[i] < 1e8:
            period = int(bl_g1[i]) + 1
            s = max(0, i - period + 1)
            if (d1l[s:i + 1].min() == low[i] and low[i] < low[max(0, i - 1)]
                    and bl_g1[i] > bl_d1_2[i]):
                d2[i] = 1

    # ── 第150-153行: NN/UU — 最终 swing high/low 信号 ──
    bl_g2 = _barslast(g2)
    bl_d2 = _barslast(d2)
    nn = np.zeros(n, dtype=int)
    uu = np.zeros(n, dtype=int)
    for i in range(n):
        if g2[i]:
            nn[i] = 1
        elif fg0[i] and bl_fg0[i] < 1e8 and bl_g2[i] < 1e8:
            p_fg0 = i - int(bl_fg0[i])
            p_g2 = i - int(bl_g2[i])
            if p_fg0 >= 0 and p_g2 >= 0:
                if high[p_fg0] > high[p_g2] and bl_d2[i] > bl_g2[i]:
                    nn[i] = 1
        if d2[i]:
            uu[i] = 1
        elif fd0[i] and bl_fd0[i] < 1e8 and bl_d2[i] < 1e8:
            p_fd0 = i - int(bl_fd0[i])
            p_d2 = i - int(bl_d2[i])
            if p_fd0 >= 0 and p_d2 >= 0:
                if low[p_fd0] < low[p_d2] and bl_g2[i] > bl_d2[i]:
                    uu[i] = 1

    # ── 第154行: 找顶线 = DRAWLINE(NN, H, REF(NN,1), REF(H,1), 1) ──
    nn_idx = np.where(nn)[0]
    find_top_line = np.full(n, np.nan)
    for k in range(1, len(nn_idx)):
        i1, i2 = nn_idx[k - 1], nn_idx[k]
        h1, h2 = high[i1], high[i2]
        if i2 <= i1:
            continue
        slope = (h2 - h1) / (i2 - i1)
        for j in range(i2, n):
            find_top_line[j] = h2 + slope * (j - i2)

    # NaN 回退: 用60日最高
    for j in range(n):
        if np.isnan(find_top_line[j]):
            s60 = max(0, j - 60)
            find_top_line[j] = float(np.max(high[s60:j + 1]))

    df['find_top_line'] = find_top_line
    df['_nn'] = nn
    df['_g2'] = g2
    return df
