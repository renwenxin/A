"""每日市场状态预计算 — 过去一年逐日的指数/广度/成交额

一次性扫描全市场 .day 文件尾部，构建每日市场快照：
  - 上证指数(000001) / 国证2000(399303) / 深证成指(399001) / 沪深300(000300)
  - 涨跌家数、涨停/跌停家数（广度 → 情绪温度/冰点检测）
  - 上证 MA20/MA60、缠论趋势/背驰（上证日线笔）

结果缓存到 parquet，供 regime 分类 + 三个战法回测 + 汇总复用。
"""
import os
import struct
import numpy as np
import pandas as pd
from datetime import date, datetime
from collections import defaultdict

from ...data.tdx_reader import TdxReader, RECORD_SIZE
from ...utils.calendar import TradingCalendar
from . import chan
from . import regime as rg


def load_state(start: date, end: date, cache_path: str = None,
               rebuild: bool = False) -> pd.DataFrame:
    """构建或加载 每日市场状态 + 行情分类。独立回测脚本的统一入口。

    Args:
        start/end: 回测区间
        cache_path: 缓存 CSV 路径；None 则只内存计算不落盘
        rebuild: 强制重建（忽略缓存）
    """
    tdx = TdxReader()
    if cache_path and not rebuild and os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
        df['date'] = pd.to_datetime(df['date']).dt.date
    else:
        df = build_market_state(tdx, start, end, cache_path=cache_path)
    return rg.compute_regime(df)

# 指数代码
SH_IDX = ('000001', 'sh', '上证指数')
GZ_IDX = ('399303', 'sz', '国证2000')
SZ_IDX = ('399001', 'sz', '深证成指')
HS300 = ('000300', 'sh', '沪深300')


def _read_index(tdx, code, mkt):
    try:
        df = tdx.read_daily(code, mkt)
        if df.empty:
            return None
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        return df
    except Exception:
        return None


def build_market_state(tdx: TdxReader, start: date, end: date,
                       cache_path: str = None) -> pd.DataFrame:
    """构建 [start, end] 逐日市场状态 DataFrame。"""
    cal = TradingCalendar()

    # 交易日序列
    all_dates = []
    d = start
    while d <= end:
        if cal.is_trading_day(d):
            all_dates.append(d)
        d = pd.Timestamp(d) + pd.Timedelta(days=1)
        d = d.date()

    # 每个交易日的前一交易日
    prev_dates = []
    for td in all_dates:
        p = cal.prev_trading_day(td, offset=1)
        prev_dates.append(p)

    # ── 指数行情 ──
    idx_data = {}
    for code, mkt, name in [SH_IDX, GZ_IDX, SZ_IDX, HS300]:
        df = _read_index(tdx, code, mkt)
        if df is None:
            continue
        df = df.set_index('trade_date')
        idx_data[name] = df

    # ── 广度（一次性扫描） ──
    # 复用: 对每个股票读尾部记录，只对目标日期做差
    breadth = _bulk_breadth(tdx, all_dates, prev_dates)

    # ── 上证缠论趋势（整段笔 → 逐日分类） ──
    sh_df = idx_data.get('上证指数')
    sh_bis = None
    sh_date_idx = {}
    sh_full = None
    if sh_df is not None:
        sh_full = sh_df.reset_index()
        sh_bis = chan.build_bi(sh_full)
        sh_date_idx = {d: i for i, d in enumerate(sh_full['trade_date'])}

    rows = []
    for i, td in enumerate(all_dates):
        row = {'date': td}
        prev = prev_dates[i]
        for name in ['上证指数', '国证2000', '深证成指', '沪深300']:
            df = idx_data.get(name)
            if df is None:
                continue
            c = df['close'].get(td)
            pc = df['close'].get(prev)
            o = df['open'].get(td)
            h = df['high'].get(td)
            l = df['low'].get(td)
            amt = df['amount'].get(td)
            if c is None or pd.isna(c):
                continue
            chg = (c - pc) / pc * 100 if (pc and not pd.isna(pc) and pc > 0) else 0.0
            prefix = 'sh' if name == '上证指数' else 'gz' if name == '国证2000' else 'sz' if name == '深证成指' else 'hs'
            row[f'{prefix}_close'] = float(c)
            row[f'{prefix}_open'] = float(o) if o is not None else float(c)
            row[f'{prefix}_high'] = float(h) if h is not None else float(c)
            row[f'{prefix}_low'] = float(l) if l is not None else float(c)
            row[f'{prefix}_chg'] = round(chg, 2)
            if amt is not None and not pd.isna(amt):
                row[f'{prefix}_amount'] = float(amt)

        # 广度
        b = breadth.get(td)
        if b:
            row['up_count'] = b[0]; row['down_count'] = b[1]
            row['flat_count'] = b[2]
            row['limit_up'] = b[3]; row['limit_down'] = b[4]
            row['scanned'] = b[5]
        else:
            row['up_count'] = row['down_count'] = row['flat_count'] = 0
            row['limit_up'] = row['limit_down'] = row['scanned'] = 0

        # 上证均线（用前 250 日窗口）
        if sh_df is not None:
            closes = sh_df['close']
            ma20 = closes.rolling(20).mean().get(td)
            ma60 = closes.rolling(60).mean().get(td)
            row['sh_ma20'] = round(float(ma20), 2) if ma20 is not None and not pd.isna(ma20) else None
            row['sh_ma60'] = round(float(ma60), 2) if ma60 is not None and not pd.isna(ma60) else None

        # 缠论趋势/背驰
        if sh_bis and td in sh_date_idx:
            idx_i = sh_date_idx[td]
            row['sh_trend'] = chan.classify_trend(sh_bis, idx_i)
            row['sh_trend_now'] = chan.current_bi_direction(sh_bis, idx_i, sh_full)
            row['sh_beichi'] = chan.detect_beichi(sh_bis, idx_i)
        else:
            row['sh_trend'] = '盘整'
            row['sh_trend_now'] = '盘整'
            row['sh_beichi'] = False

        rows.append(row)

    df_out = pd.DataFrame(rows)

    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        if cache_path.endswith('.csv'):
            df_out.to_csv(cache_path, index=False)
        else:
            df_out.to_parquet(cache_path, index=False)

    return df_out


# ═══════════════════════════════════════════════════════════════════════
# 真实的广度实现（写入上面占位的 _bulk_breadth）
# ═══════════════════════════════════════════════════════════════════════
def _bulk_breadth(tdx: TdxReader, target_dates, prev_dates) -> dict:
    t_int = {d: int(d.strftime('%Y%m%d')) for d in target_dates}
    p_int = {d: int(p.strftime('%Y%m%d')) for d, p in zip(target_dates, prev_dates)}
    need = set(t_int.values()) | set(p_int.values())
    need_by_td = {int(t.strftime('%Y%m%d')): int(p.strftime('%Y%m%d'))
                  for t, p in zip(target_dates, prev_dates)}

    counts = defaultdict(lambda: [0, 0, 0, 0, 0])

    rec_dtype = np.dtype([
        ('date', '<u4'), ('open', '<u4'), ('high', '<u4'),
        ('low', '<u4'), ('close', '<u4'), ('amount', '<f4'),
        ('volume', '<u4'), ('rsv', '<u4'),
    ])

    for mkt in ['sh', 'sz', 'bj']:
        d = tdx._market_dir(mkt)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith('.day'):
                continue
            fpath = os.path.join(d, fn)
            try:
                fsize = os.path.getsize(fpath)
                if fsize < RECORD_SIZE * 2:
                    continue
                with open(fpath, 'rb') as f:
                    f.seek(max(0, fsize - RECORD_SIZE * 280))
                    data = f.read()
                nrec = len(data) // RECORD_SIZE
                if nrec < 2:
                    continue
                arr = np.frombuffer(data, dtype=rec_dtype, count=nrec)
                dts = arr['date']
                closes = arr['close'].astype(float) / 100.0
                opens = arr['open'].astype(float) / 100.0

                # 构建 需要日 的收盘价查找
                c_map = {}
                for k in range(nrec):
                    dk = int(dts[k])
                    if dk in need:
                        c_map[dk] = float(closes[k])

                for td_int, pv_int in need_by_td.items():
                    c = c_map.get(td_int)
                    pc = c_map.get(pv_int)
                    if c is None or pc is None or pc <= 0:
                        continue
                    chg = (c - pc) / pc * 100
                    cnt = counts[td_int]
                    if chg > 0:
                        cnt[0] += 1
                    elif chg < 0:
                        cnt[1] += 1
                    else:
                        cnt[2] += 1
                    if chg >= 9.9:
                        cnt[3] += 1
                    elif chg <= -9.9:
                        cnt[4] += 1
            except (OSError, struct.error):
                continue

    # 转回 date key + scanned 计数
    result = {}
    for d in target_dates:
        di = int(d.strftime('%Y%m%d'))
        cnt = counts[di]
        result[d] = (cnt[0], cnt[1], cnt[2], cnt[3], cnt[4], sum(cnt))
    return result
