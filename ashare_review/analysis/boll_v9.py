# ashare_review/analysis/boll_v9.py
"""BOLL V9 多日期动态买点条件选股公式 — Python 逐行移植

公式原文: 项目根 BOLL_V9_超跌动态买点选股公式.txt（通达信）。
移植语义:
  - MA/EMA/STD/REF/SUM/LLV/HHV/MAX/MIN/IF 用 pandas/numpy 直接实现
  - SMA(X,N,M) 用 _tdx_sma 递推实现
  - CROSS / UPNDAY / COUNT(REF(X,1),N) 见下方原语
  - DMA(EMA20, A) 按 A>=1 → EMA20 处理（与 calc_swl_sws 结论一致）
  - WINNER(C) 用近 120 日量加权成本分布近似（无真实筹码数据）
  - INDEXC/INDEXH/INDEXL 取上证指数按日对齐

对外接口:
  calc_boll(df)                     -> df 加 boll/ub/lb
  tdx_rsi(close_series)             -> RSI(6)（SMA 法, 通达信口径）
  tdx_mfi(df)                       -> MFI(14)
  upnday(x, n)                      -> 连续 N 日上升(bool Series)
  tdx_count_ref(x, lookback)        -> COUNT(REF(x,1),lookback)>0
  tdx_cross(a, b)                   -> CROSS(A,B)
  build_signal_row(df, index_df, capital_hands) -> 尾部一行 {score,yca,ycb,xg,...}
  scan_universe(trade_date=None, tdx=None, top_n=None) -> 全市场出信号标的列表
"""
import numpy as np
import pandas as pd

from .indicators import _tdx_sma
from ..data.float_share import get_capital_hands, DEFAULT_FLOAT_SHARE_HANDS

INDEX_CODE, INDEX_MKT = '000001', 'sh'   # 上证指数（公式 INDEXC/INDEXH/INDEXL 参照系）


# ═══════════════ 通达信原语 ═══════════════

def _tdx_ref(s: pd.Series, k: int = 1) -> pd.Series:
    return s.shift(k)


def tdx_cross(a: pd.Series, b: pd.Series) -> pd.Series:
    """CROSS(A,B): A 上穿 B（前值 A<=B 且现值 A>B）"""
    return (a > b) & (a.shift(1) <= b.shift(1))


def upnday(x: pd.Series, n: int) -> pd.Series:
    """UPNDAY(X,N): X 连续 N 天（含当日）较前一日上升"""
    up = x.diff() > 0
    return up.rolling(n).sum() >= n


def tdx_count_ref(x: pd.Series, lookback: int) -> pd.Series:
    """COUNT(REF(x,1),lookback)>0: 过去 lookback 个交易日（不含当日）内 x 曾为真"""
    shifted = x.shift(1).fillna(False)
    return shifted.rolling(lookback).sum() > 0


# ═══════════════ BOLL / RSI / MFI ═══════════════

def calc_boll(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    close = df['close']
    boll = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)      # 通达信 STD 为总体标准差
    df['boll'] = boll
    df['ub'] = boll + 2 * std
    df['lb'] = boll - 2 * std
    return df


def tdx_rsi(close: pd.Series, n: int = 6) -> pd.Series:
    """通达信 RSI: SMA(MAX(C-REF(C,1),0),N,1)/SMA(ABS(C-REF(C,1)),N,1)*100"""
    delta = close.diff()
    up = _tdx_sma(delta.clip(lower=0), n, 1)
    down = _tdx_sma(delta.abs(), n, 1)
    rsi = pd.Series(np.nan, index=close.index)
    m = down > 0
    rsi[m] = (up[m] / down[m]) * 100
    return rsi


def tdx_mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """MFI(14): 以 (H+L+C)/3 为典型价, 量加权正负流 14 日累计"""
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    mf = tp * df['volume']
    pos = mf.where(tp > tp.shift(1), 0.0).rolling(n).sum()
    neg = mf.where(tp < tp.shift(1), 0.0).rolling(n).sum()
    ratio = pos / (neg + 1e-9)
    mfi = 100 - 100 / (1 + ratio)
    return mfi


# ═══════════════ WINNER 近似 ═══════════════

def _winner_approx(close: pd.Series, volume: pd.Series, price: float,
                   lookback: int = 120) -> float:
    """近 lookback 日量加权成本分布中成本 <= price 的筹码占比（WINNER 近似）。

    每根 K 线以收盘价为成本、当日成交量为权重，线性衰减（越近权重越高）。
    返回 0..1；样本不足返回 0.5（中性，避免信号误触发）。
    """
    n = min(lookback, len(close))
    if n < 30:
        return 0.5
    c = close.iloc[-n:].astype(float).values
    v = volume.iloc[-n:].astype(float).values
    w = np.arange(1, n + 1, dtype=float)          # 越近权重越高
    w /= w.sum()
    win = float(np.sum(w[c <= price]))
    return float(win)


# ═══════════════ 主扫描行（单只股票 + 指数） ═══════════════

def build_signal_row(df: pd.DataFrame, index_df: pd.DataFrame = None,
                     capital_hands: float = None) -> dict:
    """按公式逐段计算，返回最后一行的信号字典。

    df: 至少 ~230 根日线（open/high/low/close/volume + trade_date, 升序）
    index_df: 上证指数日线（trade_date/idx_close 等）；None 时指数项用自身近似（弱化）
    """
    # 复用 indicators 里已论证的通达信原语风格：全部用 pandas 向量化 + 递推 SMA
    df = df.reset_index(drop=True).copy()
    calc_boll(df)                                   # BOLL/UB/LB
    close, vol = df['close'], df['volume'].astype(float)
    n = len(df)

    # 5. BOLL 波动率 X1/X2/X3
    x1 = (df['ub'] - df['lb']) / df['boll'].replace(0, np.nan) * 100
    x2 = x1.rolling(60).mean()
    x3 = pd.Series(np.where((x2 > 0) & x2.notna(), x1 / x2.replace(0, np.nan), 1.0), index=df.index)

    # 6/7. RSI(6) / MFI(14)
    x4 = tdx_rsi(close, 6)
    x9 = tdx_mfi(df, 14)

    # 8. 成交量环境 XA..XD
    xa = vol.rolling(20).mean()
    xb = pd.Series(np.where(xa > 0, vol / xa.replace(0, np.nan), 1.0), index=df.index)
    xc = x3.clip(lower=0.70, upper=1.60)
    xd = xb.clip(lower=0.60, upper=1.80)

    # 9. 自适应阈值 XE/XF（45 基准 ± 修正, 夹 35..52）
    xe = (45 - (xc - 1) * 8 - (xd - 1) * 4).clip(lower=35, upper=52)
    xf = (45 - (xc - 1) * 7 - (xd - 1) * 3).clip(lower=35, upper=52)

    # 10. 自适应下轨 XI / XK / XL
    xi = (0.012 * xc).clip(lower=0.008, upper=0.025)
    low = df['low']
    xk = low <= df['lb']
    xl = close <= df['lb'] * (1 + xi)

    # 11. RSI/MFI 修复 XM..XP
    xm = x4 < xe
    xn = x4 > x4.shift(1)
    xo = x9 < xf
    xp = x9 > x9.shift(1)

    # 12. 价格修复 XQ..XS
    xq = close > df['open']
    xr = close >= close.shift(1)
    xs = close > df['lb']

    # 13. 趋势结构 XV（SWL>=SWS 或 MA5>=MA10）
    from .indicators import calc_swl_sws
    tdf = calc_swl_sws(df.assign(close=close, volume=vol), capital_hands=capital_hands)
    xt = tdf['swl'] >= tdf['sws']
    xu = close.rolling(5).mean() >= close.rolling(10).mean()
    xv = xt | xu

    # 14. 成交量控制 XW/XX/XY
    xw = vol <= xa * 1.80
    xx = vol < vol.shift(1) * 2.00
    xy = x3 < 1.35

    # 15. 主流/非主流资金 ZA/ZB（个股 34 日位置 vs 上证指数 34 日位置）
    def _fund_pos(h, l, c):
        lo34 = l.rolling(34).min()
        hi34 = h.rolling(34).max()
        rng = (hi34 - lo34).replace(0, np.nan)
        return ((c - lo34) / rng * 100).ewm(span=3, adjust=False).mean()

    za = _fund_pos(df['high'], df['low'], close)
    if index_df is not None and not index_df.empty:
        idx = index_df.reset_index(drop=True).copy()
        idx_close = idx['idx_close'] if 'idx_close' in idx else idx['close']
        idx_high = idx['idx_high'] if 'idx_high' in idx else idx['high']
        idx_low = idx['idx_low'] if 'idx_low' in idx else idx['low']
        if 'trade_date' in idx.columns and 'trade_date' in df.columns:
            # 按 trade_date 精确对齐（防停牌/上市日差异造成位置错位 → 未来数据污染）
            idx_dates = pd.to_datetime(idx['trade_date'])
            df_dates = pd.to_datetime(df['trade_date'])
            idx_by_date = idx.set_index(idx_dates)
            # 每只个股行取同日期指数值；查不到（指数无该日）→ NaN → zn=za>=zb 判 False
            def _align(col: pd.Series) -> pd.Series:
                aligned = idx_by_date[col.name].reindex(df_dates).reset_index(drop=True)
                return pd.Series(aligned.astype(float).values, index=df.index)
            ic_al = _align(idx_close)
            ih_al = _align(idx_high)
            il_al = _align(idx_low)
            zb = _fund_pos(ih_al, il_al, ic_al)
        else:
            # 退化：无日期信息 → 尾部位置对齐（仅测试/构造数据用）
            zb = _fund_pos(idx_high, idx_low, idx_close)
            zb = pd.Series(zb.values[-n:], index=df.index)
    else:
        zb = za.copy()   # 无指数数据时退化为中性（资金不弱化）

    # 16. 吸筹（/618 系, 原样移植）
    zc = df['low'].shift(1)
    zd_den = (df['low'] - zc).clip(lower=0)
    zd = _tdx_sma((df['low'] - zc).abs(), 3, 1) / _tdx_sma(zd_den, 3, 1).replace(0, np.nan) * 100
    # ZE:=EMA(IF(CLOSE*13,ZD*10,ZD/10),3) — np.where 输出需包成 Series 才能喂 _tdx_sma
    ze = _tdx_sma(pd.Series(np.where(close * 13 > 0, zd * 10, zd / 10),
                            index=df.index).astype(float), 3, 1)
    zf = df['low'].rolling(19).min()
    zg = pd.Series(ze).rolling(13).max()
    zh = pd.Series(np.where(df['close'].rolling(34).mean() > 0, 1.0, 0.0), index=df.index)
    # ZI:=EMA(IF(LOW<=ZF,(ZE+ZG*2)/2,0),3)/618*ZH — IF 作用于表达式整体：
    # 不吸筹（LOW>ZF）时输入必须是 0，不能把 (0+2*ZG)/2=ZG 送进 EMA
    zi_raw = (pd.Series(ze).astype(float) + zg * 2) / 2.0
    zi = _tdx_sma(zi_raw.where(df['low'] <= zf, 0.0), 3, 1) / 618.0 * zh
    zj = pd.Series(np.where(zi > 190, 150.0, zi), index=df.index)
    zk = pd.Series(zj).rolling(20).mean()
    zl = zj > zk * 0.85
    zm = zj > pd.Series(zj).shift(1)
    zn = za >= zb
    zo = zn | zm
    zp = zl & zm

    # 17. XYS 动量 ZQ..ZZ
    zq = (2 * close + df['high'] + df['low']) / 4.0
    zr = zq.ewm(span=4, adjust=False).mean()
    zs = zr.ewm(span=4, adjust=False).mean()
    zt1 = zs.ewm(span=4, adjust=False).mean()
    zu = (zt1 - zt1.shift(1)) / zt1.shift(1).replace(0, np.nan) * 100
    zv = zu.rolling(2).mean()
    zw = zu
    zx = tdx_cross(zw, zv)
    zy = zu > zu.shift(1)
    zz = zv > zv.shift(1)

    # 18. 量能复合系统 YAA..YAL（含 WINNER 近似的 YAJ / KDJ 式 YAK/YAL）
    yaa = vol * 1.2
    yab = vol.rolling(2).mean() * 1.2
    yac = vol.rolling(13).mean() * 1.2
    yad = vol.rolling(21).mean() * 1.2
    yae = vol.rolling(34).mean() * 1.2
    yaf = vol.rolling(43).mean() * 1.2
    yag = vol.rolling(55).mean() * 1.2
    yah = vol.rolling(120).mean() * 1.2
    yai = vol.rolling(180).mean() * 1.2
    # YAJ = EMA(WINNER(C)*70, 180)*10 —— WINNER 用量加权成本近似
    win_s = pd.Series([_winner_approx(close, vol, float(p)) for p in close], index=df.index)
    yaj = (win_s * 70).ewm(span=180, adjust=False).mean() * 10.0
    # YAK 的 LLV 取 LOW 的 27 日最低（公式: (CLOSE-LLV(LOW,27))/(HHV(HIGH,27)-LLV(LOW,27))）
    llv27 = df['low'].rolling(27).min()
    hhv27 = df['high'].rolling(27).max()
    kdj_raw = ((close - llv27) / (hhv27 - llv27).replace(0, np.nan) * 100)
    yak = 3 * _tdx_sma(kdj_raw, 5, 1) - 2 * _tdx_sma(_tdx_sma(kdj_raw, 5, 1), 3, 1)
    yal = pd.Series(yak).rolling(180).mean()

    # 19-22. 剑 / 箭 / 炮 / 出刀
    yam = tdx_cross(yaa, yab) & (yab < yai * 0.90) & upnday(pd.Series(close).ewm(span=13, adjust=False).mean(), 2)
    yan = tdx_cross(yaa, yai) & upnday(yai, 2)
    yao = (upnday(yac, 3) & upnday(yad, 2) & upnday(yae, 2) & upnday(yaf, 1)
           & upnday(yag, 1) & upnday(yah, 1) & upnday(yaj, 3) & upnday(yal, 3))
    yap = (tdx_cross(yaa, yai) & tdx_cross(yab, yai)
           & upnday(yac, 2) & upnday(yad, 2) & upnday(yae, 2))

    # 23. 量能综合
    yaq = yam | yan | yao | yap
    yar = yao | yap
    yas = yaq | tdx_count_ref(yaq, 3)
    yat = yar | tdx_count_ref(yar, 3)

    # 24. 评分
    pts = {
        'YAU': xk | xl, 'YAV': xm, 'YAW': xn, 'YAX': xo, 'YAY': xp,
        'YAZ': xv, 'YBA': zn, 'YBB': zm, 'YBC': zl, 'YBD': zm,
        'YBE': zx, 'YBF': zy, 'YBG': zz, 'YBH': yam, 'YBI': yan,
        'YBJ': yao, 'YBK': yap, 'YBL': yas, 'YBM': yat, 'YBN': xq,
        'YBO': xr, 'YBP': xs, 'YBQ': xw, 'YBR': xx, 'YBS': xy,
    }
    weight = {'YAU': 3, 'YAV': 2, 'YAW': 2, 'YAX': 2, 'YAY': 2, 'YAZ': 2,
              'YBA': 2, 'YBB': 2, 'YBC': 2, 'YBD': 2, 'YBE': 2, 'YBF': 1,
              'YBG': 1, 'YBH': 1, 'YBI': 2, 'YBJ': 3, 'YBK': 4, 'YBL': 2,
              'YBM': 3, 'YBN': 1, 'YBO': 1, 'YBP': 2, 'YBQ': 1, 'YBR': 1, 'YBS': 1}
    ybt = pd.Series(0.0, index=df.index)
    for k, w in weight.items():
        ybt = ybt + pts[k].astype(float) * w

    # 26. 核心买入条件
    ybu = xk | xl
    ybv = (xm & xn) | (xo & xp)
    ybw = xq | xr | xs
    ybx = zo
    yby = zx | zy | yas
    # 27. 动态门槛
    ybz = pd.Series(np.where(x3 < 0.85, 8.0, np.where(x3 < 1.10, 9.0,
                       np.where(x3 < 1.35, 10.0, 11.0))), index=df.index)
    # 28. 基础买点
    yca = (ybu & ybv & ybw & ybx & yby & (ybt >= ybz)).fillna(False)
    # 29. 历史首次（前 5 日无 YCA）
    ycb = yca & ~(yca.shift(1).fillna(False).rolling(5).sum() > 0)
    xg = ycb.iloc[-1]

    return {
        'score': float(ybt.iloc[-1]), 'xg': bool(xg), 'yca': bool(yca.iloc[-1]),
        'ycb': bool(ycb.iloc[-1]),
        'close': float(close.iloc[-1]), 'lb': float(df['lb'].iloc[-1]),
        'dist_to_lb': float((close.iloc[-1] - df['lb'].iloc[-1]) / df['lb'].iloc[-1] * 100),
        'rsi': float(x4.iloc[-1]) if pd.notna(x4.iloc[-1]) else None,
        'mfi': float(x9.iloc[-1]) if pd.notna(x9.iloc[-1]) else None,
    }


def scan_universe(trade_date: str = None, tdx=None, top_n: int = None,
                  industry_map: dict = None) -> list:
    """全市场扫描 BOLL V9 XG 信号（T 日收盘后）。

    复用 tail_adapter 的全市场循环范式（sh/sz/bj 遍历 + ST 剔除）。
    返回 list[dict]: {code,name,sector,score,close,dist_to_lb,rsi,mfi}，按 score 降序。
    """
    from ..data.tdx_reader import TdxReader
    from .indicators import enrich_all as _unused  # noqa: 保持 enrich 依赖可见性
    tdx = tdx or TdxReader()
    industry_map = industry_map or {}

    # 上证指数（INDEX 参照系）— 按 trade_date 截断，历史回扫不掺未来数据
    index_df = _read_index(tdx, trade_date)

    out = []
    for code, mkt in tdx.list_stocks():
        if not str(code).startswith(('60', '00', '30')):     # 只扫主板+创业板
            continue
        try:
            df = tdx.read_daily(code, mkt)
            if df is None or len(df) < 230:
                continue
            if trade_date:
                df = df[df['trade_date'].astype(str).str[:10] <= str(trade_date)]
            df = df.tail(260).reset_index(drop=True)
            cap = get_capital_hands(code, default=DEFAULT_FLOAT_SHARE_HANDS)
            row = build_signal_row(df, index_df=index_df, capital_hands=cap)
            if row['xg']:
                name = _name_of(tdx, code)
                if 'ST' in name:
                    continue
                out.append({
                    'code': code, 'name': name,
                    'sector': str(industry_map.get(code, '') or ''),
                    'score': row['score'], 'close': row['close'],
                    'dist_to_lb': row['dist_to_lb'],
                    'rsi': row['rsi'], 'mfi': row['mfi'],
                })
        except Exception:
            continue
    out.sort(key=lambda x: -x['score'])
    return out if top_n is None else out[:top_n]


def _read_index(tdx, trade_date: str = None):
    """读上证指数日线并重命名为 idx_close/idx_high/idx_low。

    历史回扫（trade_date 给定）时先按日期截断，避免把 trade_date 之后的指数
    数据（未来函数）喂进 build_signal_row。读不到/异常返回 None（调用方中性化）。
    """
    try:
        idx = tdx.read_daily(INDEX_CODE, INDEX_MKT)
        if idx is None or idx.empty:
            return None
        index_df = idx.rename(columns={'close': 'idx_close', 'high': 'idx_high',
                                       'low': 'idx_low'})
        if trade_date:
            index_df = index_df[index_df['trade_date'].astype(str).str[:10] <= str(trade_date)]
        return index_df.reset_index(drop=True)
    except Exception:
        return None


def _name_of(tdx, code: str) -> str:
    try:
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'data', 'stock_name_map.json')
        import json
        if os.path.exists(p):
            m = json.load(open(p, encoding='utf-8'))
            nm = str(m.get(code, '') or '')
            if nm and not nm.isdigit():
                return nm
    except Exception:
        pass
    # 兜底：TDX 名称在 list_stocks 不返回 → 用涨停池等外部补名在 collector 层做
    return code
