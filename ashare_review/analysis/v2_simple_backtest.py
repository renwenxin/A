"""启动+突破 V2 回测 — 连板持有到断板 / 没连板最多3天

优化版: zigzag 预计算缓存，每只票只算一次。
"""
import sys, os, argparse, json, time, struct
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE
from ashare_review.analysis.indicators import (
    enrich_all, calc_swl_sws, calc_zigzag_find_top_line,
)

FEE = 0.0015; SLIPPAGE = 0.001; TOTAL_COST = FEE + SLIPPAGE * 2
MAX_HOLD_NO_CHAIN = 3; SIM_BUFFER = 30
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')


def limit_threshold(code: str) -> float:
    code = str(code).zfill(6)
    if code.startswith(('300', '301', '688')): return 0.199
    if code.startswith(('8', '4')): return 0.299
    return 0.095


def board_limit_threshold(code: str) -> float:
    """涨停阈值(%) — 对应 _board_limit_threshold 用于计数"""
    code = str(code).zfill(6)
    if code.startswith(('300', '301')): return 19.9
    if code.startswith('688'): return 19.9
    if code.startswith(('8', '4')): return 29.9
    return 9.9


def trade_dates(n: int, end_date: date = None) -> List[date]:
    d = end_date or (date.today() - timedelta(days=1))
    dates = []
    while len(dates) < n + SIM_BUFFER:
        if d.weekday() < 5: dates.append(d)
        d -= timedelta(days=1)
    return list(reversed(dates))


def _is_main_board(code: str) -> bool:
    return code.startswith(('600', '601', '603', '605', '000', '001', '002'))


def _is_stock_st(name: str) -> bool:
    return 'ST' in name or '*ST' in name


def _count_limit_ups(code: str, tdx, cache: dict, lookback: int = 250) -> int:
    """统计近一年涨停次数（从TDX文件直接读，不依赖akshare）"""
    if code in cache: return cache[code]
    threshold = board_limit_threshold(code)
    market = 'sh' if code.startswith('6') else 'sz'
    if code.startswith(('8', '4')): market = 'bj'
    fpath = os.path.join(tdx._market_dir(market), f'{market}{code}.day')
    if not os.path.exists(fpath):
        cache[code] = 0; return 0
    fsize = os.path.getsize(fpath)
    if fsize < RECORD_SIZE * 2: cache[code] = 0; return 0
    read_size = min(RECORD_SIZE * lookback, fsize)
    with open(fpath, 'rb') as f:
        f.seek(fsize - read_size)
        tail = f.read(read_size)
    records = len(tail) // RECORD_SIZE
    cnt, prev_close = 0, None
    for i in range(records):
        off = i * RECORD_SIZE
        cl = struct.unpack('I', tail[off+16:off+20])[0] / 100.0
        if prev_close and prev_close > 0 and (cl/prev_close-1)*100 >= threshold:
            cnt += 1
        prev_close = cl
    cache[code] = cnt
    return cnt


# ═══════════════════════════════════════════════════════════════════
# Zigzag 预计算缓存
# ═══════════════════════════════════════════════════════════════════

def _build_zigzag_cache(codes: List[str], tdx, max_bars: int = 200):
    """预计算所有候选股票的 zigzag find_top_line。返回 {code: df_with_zigzag}.

    max_bars=200: 覆盖 zigzag 所需 + 60日高 + 30日缓冲，约 0.8s/只。
    """
    zz_cache = {}
    n = len(codes)
    t0 = time.time()
    for i, code in enumerate(codes):
        if (i + 1) % 100 == 0:
            e = time.time() - t0
            eta = e / (i + 1) * (n - i - 1) if i > 0 else 0
            print(f'  zigzag: {i+1}/{n} ({e:.0f}s, ETA {eta:.0f}s)...', flush=True)
        mkt = 'sh' if code.startswith('6') else 'sz'
        if code.startswith(('8', '4')): mkt = 'bj'
        try:
            df = tdx.read_daily(code, mkt)
            if df is None or df.empty or len(df) < 60: continue
            # 只保留最近 max_bars 根K线以加速 zigzag 计算
            if len(df) > max_bars:
                df = df.iloc[-max_bars:].reset_index(drop=True)
            df = enrich_all(df)
            if 'ma5' not in df.columns or 'ma10' not in df.columns: continue
            df = calc_zigzag_find_top_line(df)
            zz_cache[code] = df
        except Exception:
            continue
    print(f'  zigzag 完成: {len(zz_cache)}/{n} 只 ({time.time()-t0:.0f}s)')
    return zz_cache


# ═══════════════════════════════════════════════════════════════════
# 涨停池缓存（从TDX本地扫描）
# ═══════════════════════════════════════════════════════════════════

def _build_limit_up_cache(dates: List[date]) -> Dict[str, List[dict]]:
    """从 TDX 本地文件扫描所有股票的历史涨停数据。

    返回 {ds: [{code, name, board_type, consecutive, change_pct, close, ...}, ...]}
    """
    cache_path = os.path.join(CACHE_DIR, 'zt_pool_cache.json')
    ds_set = {d.strftime('%Y%m%d') for d in dates}

    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            if ds_set.issubset(set(cached.keys())):
                print(f'已加载涨停池缓存: {len(cached)}天')
                return {k: v for k, v in cached.items() if k in ds_set}
            print(f'缓存日期不足 ({len(cached)}天), 需要 {len(ds_set)}天, 重新扫描...')
        except Exception:
            pass

    tdx = TdxReader()
    stocks = tdx.list_stocks()
    print(f'扫描 {len(stocks)} 只股票的历史涨停...')
    ds_sorted = sorted(ds_set)
    result: Dict[str, List[dict]] = {ds: [] for ds in ds_set}
    processed, t0 = 0, time.time()

    for code, mkt in stocks:
        try:
            df = tdx.read_daily(code, mkt)
            if df is None or df.empty or len(df) < 2: continue
        except Exception: continue

        close = df['close'].values
        open_p = df['open'].values
        dates_arr = df['trade_date'].values
        threshold = limit_threshold(code)
        prev_close = close[0]

        for i in range(1, len(df)):
            chg = (close[i] / prev_close - 1.0) if prev_close > 0 else 0
            ds_str = (dates_arr[i].strftime('%Y%m%d') if hasattr(dates_arr[i], 'strftime')
                      else str(dates_arr[i])[:10].replace('-', ''))
            if ds_str not in ds_set or chg < (threshold - 0.001):
                prev_close = close[i]; continue

            consecutive = 0; j = i - 1
            for _ in range(20):
                if j < 0: break
                pp = close[max(0, j-1)]
                pc = (close[j]/pp-1.0) if pp > 0 else 0
                if pc >= (threshold - 0.001): consecutive += 1; j -= 1
                else: break

            board_type = '换手板' if open_p[i] < close[i] * 0.98 else '一字板'
            result[ds_str].append({
                'code': code, 'name': '',
                'board_type': board_type, 'consecutive': consecutive,
                'change_pct': round(chg * 100, 1),
                'close': round(float(close[i]), 2),
                'volume': int(df['volume'].values[i]),
                'float_market_cap': 0,
            })
            prev_close = close[i]

        processed += 1
        if processed % 1000 == 0:
            e = time.time() - t0
            print(f'  {processed}/{len(stocks)} ({e:.0f}s, ETA {e/processed*(len(stocks)-processed):.0f}s)...')

    n_dates = sum(1 for v in result.values() if v)
    print(f'扫描完成: {processed}只, {n_dates}天有涨停 ({time.time()-t0:.0f}s)')
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = cache_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False)
        os.replace(tmp, cache_path)
        print(f'已缓存到 {cache_path}')
    except Exception as e:
        print(f'缓存写入失败: {e}')
    return result


# ═══════════════════════════════════════════════════════════════════
# 主回测类
# ═══════════════════════════════════════════════════════════════════

class V2SimpleBacktest:
    def __init__(self):
        self.tdx = TdxReader()
        self._lu_count_cache: Dict[str, int] = {}
        self._zz_cache: Dict[str, pd.DataFrame] = {}
        self._stock_cache: Dict[str, pd.DataFrame] = {}

    def _find_bar_idx(self, df, ds):
        try:
            target = datetime.strptime(ds, '%Y%m%d').date()
        except ValueError:
            return None
        dates = df['trade_date'].apply(lambda x: x.date() if hasattr(x, 'date') else x)
        mask = dates <= target
        if not mask.any(): return None
        return int(mask[mask].index[-1])

    def _v2_check_cached(self, code: str, info: dict, ds: str) -> Optional[dict]:
        """V2 逻辑 — 使用预计算的 zigzag 缓存。

        与 StartBreakoutScreenerV2._check_v2 逻辑一致，但：
        - 从 zz_cache 取数据（含 zigzag），不重新计算
        - 从 TDX 直接读原始数据（_read_stock 不变，但 find_resistance_line 从缓存取值）
        """
        df = self._zz_cache.get(code)
        if df is None: return None

        # 找到信号日对应的 bar
        sig_bar = self._find_bar_idx(df, ds)
        if sig_bar is None or sig_bar < 40: return None

        idx = sig_bar
        close_now = float(df['close'].iloc[idx])
        vol_now = float(df['volume'].iloc[idx])

        score, reasons, detail = 30.0, [], {}
        features = {'code': code, 'is_zt': info.get('is_zt', 0)}

        # ── 硬性条件1: 非ST ──
        name = info.get('name', '') or f'stock{code}'
        if _is_stock_st(name): return None
        detail['name'] = name

        # ── 硬性条件2: 涨停次数 ──
        is_main = _is_main_board(code)
        limit_count = _count_limit_ups(code, self.tdx, self._lu_count_cache)
        detail['limit_count'] = limit_count
        if is_main:
            if limit_count > 10:
                score += 20; reasons.append(f'年涨停{limit_count}次·主板活跃')
            else: return None
        else:
            if limit_count > 3:
                score += 20; reasons.append(f'年涨停{limit_count}次·创科活跃')
            else: return None

        # ── 硬性条件3: 找顶线下方10%以内 ──
        top_line = float(df['find_top_line'].iloc[idx])
        detail['top_line'] = round(top_line, 2)
        features['top_line'] = top_line
        if top_line <= 0: return None

        below_pct = (top_line - close_now) / top_line * 100
        detail['dist_top_line'] = round(below_pct, 1)
        if 0 < below_pct <= 10:
            score += 20; reasons.append(f'距找顶线{below_pct:.1f}%·即将突破')
        elif below_pct <= 0: return None
        else: return None

        # ── 实盘校验: 已突破过滤 ──
        high_20_before = float(df['high'].iloc[max(0, idx-20):idx-2].max()) if idx >= 3 else 0
        if high_20_before > 0:
            for offset in [1, 2]:
                check_close = float(df['close'].iloc[idx - offset])
                if check_close > high_20_before * 1.015:
                    return None

        # [Core] 60-day high
        high60 = df['high'].iloc[max(0, idx-60):idx+1].max()
        dist_60 = (close_now - high60) / high60 * 100
        detail['dist_60d'] = round(dist_60, 1)
        if dist_60 >= -2:
            score += 5; reasons.append(f'距60日高{dist_60:.1f}%')
        features['dist_60d'] = dist_60

        # [Alpha 1] 250-day high
        if idx >= 250:
            high250 = float(df['high'].iloc[max(0, idx-250):idx].max())
            dist_250 = (close_now - high250) / high250 * 100 if high250 > 0 else 0
            detail['dist_250d'] = round(dist_250, 1)
            if dist_250 >= 0:
                score += 20; reasons.append('创250日新高·主升浪')
            elif dist_250 > -3:
                score += 10; reasons.append(f'距250日高{dist_250:.1f}%·突破在即')
            features['dist_250d'] = dist_250

        # [Core] SWL control
        df2 = calc_swl_sws(df)
        ctrl = bool(df2['swl_control'].iloc[idx])
        if ctrl:
            score += 20; reasons.append('操盘线控盘(SWL>生命线)')
        else:
            swl_v = float(df2['swl'].iloc[idx]); sws_v = float(df2['sws'].iloc[idx])
            if sws_v > 0 and abs(swl_v - sws_v) / sws_v < 0.02:
                score += 10; reasons.append('操盘线即将控盘')
            else: score -= 5
        features['swl_control'] = ctrl

        # [Core] volume expansion
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

        score = min(round(score), 100)
        if score < 60: return None
        return {
            'code': code, 'name': name, 'score': score,
            'reasons': '; '.join(reasons), 'detail': detail,
            'close_now': close_now,
        }

    def _simulate_exit(self, code: str, df: pd.DataFrame, sig_bar: int,
                       buy_price: float) -> Optional[dict]:
        threshold = limit_threshold(code) - 0.001
        close_vals = df['close'].values
        chain_bars, hold_days = 0, 0
        exit_reason, sell_price = '', buy_price
        prev_close = buy_price

        for offset in range(1, SIM_BUFFER + 1):
            check_bar = sig_bar + offset
            if check_bar >= len(df):
                exit_reason = '数据结束·强制卖出'
                sell_price = float(close_vals[-1])
                hold_days = offset; break

            today_close = float(close_vals[check_bar])
            today_chg = (today_close / prev_close - 1.0) if prev_close > 0 else 0
            is_zt = today_chg >= threshold

            if is_zt:
                chain_bars += 1; prev_close = today_close; continue
            else:
                if chain_bars > 0:
                    exit_reason = f'断板({chain_bars}连板后)'
                    sell_price = today_close; hold_days = offset; break
                elif offset >= MAX_HOLD_NO_CHAIN:
                    exit_reason = f'无连板·持有{offset}天'
                    sell_price = today_close; hold_days = offset; break
                else:
                    prev_close = today_close; continue

        if not exit_reason:
            exit_reason = f'强制卖出({SIM_BUFFER}天)'
            hold_days = SIM_BUFFER
            sell_price = float(close_vals[min(sig_bar + SIM_BUFFER, len(df) - 1)])

        if sell_price <= 0: return None
        gross = (sell_price - buy_price) / buy_price
        net = gross - TOTAL_COST
        return {
            'sell_price': round(sell_price, 2), 'hold_days': hold_days,
            'chain_bars': chain_bars,
            'gross_ret': round(gross * 100, 2),
            'net_ret': round(net * 100, 2),
            'is_win': net > 0, 'exit_reason': exit_reason,
        }

    def _fast_prefilter(self, code: str, name: str, df: pd.DataFrame, sig_bar: int) -> bool:
        """快速预筛选，排除明显不符合条件的股票（不依赖 zigzag）。

        检查: 非ST、涨停次数、SWL 控盘。
        """
        # ST 检查
        if _is_stock_st(name or ''):
            return False
        # 涨停次数
        is_main = _is_main_board(code)
        limit_count = _count_limit_ups(code, self.tdx, self._lu_count_cache)
        if is_main and limit_count <= 10: return False
        if not is_main and limit_count <= 3: return False
        # SWL 控盘（快速 check，不扣分只看是否完全不符合）
        try:
            df2 = calc_swl_sws(df)
            ctrl = bool(df2['swl_control'].iloc[sig_bar])
            swl_v = float(df2['swl'].iloc[sig_bar])
            sws_v = float(df2['sws'].iloc[sig_bar])
            near_ctrl = sws_v > 0 and abs(swl_v - sws_v) / sws_v < 0.02
            if not ctrl and not near_ctrl:
                return False  # 完全不控盘，直接排除
        except Exception:
            pass
        return True

    def run(self, lookback: int = 250, end_date: date = None, min_score: int = 60):
        all_dates_raw = trade_dates(lookback, end_date=end_date)
        signal_dates = all_dates_raw[:lookback]

        print(f'回测区间: {signal_dates[0]} ~ {signal_dates[-1]} ({len(signal_dates)}天)')

        # ── 步骤1: 涨停池 ──
        zt_pool = _build_limit_up_cache(signal_dates)

        # ── 步骤2: 收集候选 + 预加载 ──
        all_codes = set()
        for ds, stocks in zt_pool.items():
            for s in stocks:
                if s['board_type'] != '一字板':
                    all_codes.add(s['code'])
        codes_sorted = sorted(all_codes)
        needed_bars = min(len(signal_dates) + SIM_BUFFER + 60, 350)
        print(f'候选股票: {len(all_codes)} 只, K线数: {needed_bars}')

        # ── 步骤3: 预加载 + 快速预筛（无 zigzag）──
        print('Phase 1: 预加载 + 快速筛选...')
        t0 = time.time()
        enriched_cache = {}
        passed_codes = set()
        for i, code in enumerate(codes_sorted):
            if (i + 1) % 500 == 0:
                e = time.time() - t0
                print(f'  预筛: {i+1}/{len(codes_sorted)} ({e:.0f}s) 通过{len(passed_codes)}只...', flush=True)
            mkt = 'sh' if code.startswith('6') else 'sz'
            if code.startswith(('8', '4')): mkt = 'bj'
            try:
                df = self.tdx.read_daily(code, mkt)
                if df is None or df.empty or len(df) < 60: continue
                if len(df) > needed_bars:
                    df = df.iloc[-needed_bars:].reset_index(drop=True)
                df = enrich_all(df)
                enriched_cache[code] = df
            except Exception:
                continue

        print(f'  预加载: {len(enriched_cache)}/{len(codes_sorted)} 只 ({time.time()-t0:.0f}s)')

        # 逐日快速筛选
        for ds_key in sorted(zt_pool.keys()):
            candidates_raw = zt_pool.get(ds_key, [])
            for c in candidates_raw:
                code = c['code']
                if code in passed_codes or c['board_type'] == '一字板':
                    continue
                df = enriched_cache.get(code)
                if df is None: continue
                sig_bar = self._find_bar_idx(df, ds_key)
                if sig_bar is None or sig_bar < 40: continue
                name = c['name'] or f'stock{code}'
                if self._fast_prefilter(code, name, df, sig_bar):
                    passed_codes.add(code)

        print(f'  预筛通过: {len(passed_codes)}/{len(enriched_cache)} 只 ({time.time()-t0:.0f}s)')

        # ── 步骤4: zigzag 预计算（仅通过预筛的股票）──
        print(f'Phase 2: zigzag 预计算 ({len(passed_codes)}只)...')
        passed_list = sorted(passed_codes)
        for i, code in enumerate(passed_list):
            if (i + 1) % 50 == 0 or i == 0:
                e = time.time() - t0
                eta = e / (i + 1) * (len(passed_list) - i - 1) if i > 0 else 0
                print(f'  zigzag: {i+1}/{len(passed_list)} ({e:.0f}s, ETA {eta:.0f}s)...', flush=True)
            df = enriched_cache.get(code)
            if df is None: continue
            try:
                df = calc_zigzag_find_top_line(df)
                self._zz_cache[code] = df
            except Exception:
                continue
        print(f'  zigzag 完成: {len(self._zz_cache)}/{len(passed_list)} 只 ({time.time()-t0:.0f}s)')

        # ── 步骤5: 逐日完整筛选 + 模拟 ──
        print(f'Phase 3: 逐日回测...')
        trades = []
        t_backtest = time.time()
        for i, td in enumerate(signal_dates):
            ds = td.strftime('%Y%m%d')
            if (i + 1) % 25 == 0 or i == 0:
                e = time.time() - t_backtest
                eta = e / (i + 1) * (len(signal_dates) - i - 1) if i > 0 else 0
                msg = f'[{i+1}/{len(signal_dates)}] {ds} ({e:.0f}s ETA {eta:.0f}s)'
                if trades: msg += f' 已{len(trades)}笔'
                print(msg, flush=True)

            candidates_raw = zt_pool.get(ds, [])
            if not candidates_raw: continue

            for c in candidates_raw:
                code = c['code']
                if c['board_type'] == '一字板' or code not in self._zz_cache:
                    continue
                info = {
                    'name': c['name'] or f'stock{code}', 'code': code,
                    'is_zt': c['change_pct'] >= 9.5,
                    'board_type': c['board_type'],
                    'consecutive': c['consecutive'],
                    'float_market_cap': c.get('float_market_cap', 0),
                }
                r = self._v2_check_cached(code, info, ds)
                if r is None or r['score'] < min_score: continue

                df = self._zz_cache.get(code)
                sig_bar = self._find_bar_idx(df, ds)
                if sig_bar is None: continue
                buy_price = float(df['close'].iloc[sig_bar])
                if buy_price <= 0: continue

                exit_r = self._simulate_exit(code, df, sig_bar, buy_price)
                if exit_r is None: continue

                trades.append({
                    'signal_date': ds,
                    'code': code, 'name': r['name'], 'score': r['score'],
                    'reasons': r['reasons'],
                    'buy_price': round(buy_price, 2),
                    **exit_r,
                })

        total_t = time.time() - t0
        print(f'回测完成: {len(trades)} 笔 ({total_t:.0f}s = {total_t/60:.1f}min)')
        return trades

    @staticmethod
    def summarize(trades: List[dict]) -> dict:
        if not trades: return {'trades': 0}
        df = pd.DataFrame(trades)
        n = len(trades); wc = len(df[df['is_win']])
        wr = wc / n * 100 if n else 0
        aw = df[df['is_win']]['net_ret'].mean() if wc else 0
        al = df[~df['is_win']]['net_ret'].mean() if n - wc else 0
        cumsum = df['net_ret'].cumsum()
        dd = (cumsum - cumsum.cummax()).min()
        ch = df[df['chain_bars'] > 0]
        exit_dist = df['exit_reason'].value_counts().to_dict()
        df['score_bin'] = pd.cut(df['score'], bins=[60,70,80,90,101],
                                  labels=['60-69','70-79','80-89','90-100'])
        score_stats = df.groupby('score_bin', observed=False).agg(
            count=('net_ret', 'count'),
            win_rate=('is_win', 'mean'),
            avg_ret=('net_ret', 'mean'),
        )
        return {
            'trades': n, 'win_rate': round(wr, 1),
            'avg_ret': round(df['net_ret'].mean(), 2),
            'avg_win': round(aw, 2), 'avg_loss': round(al, 2),
            'total_ret': round(df['net_ret'].sum(), 2),
            'median_ret': round(df['net_ret'].median(), 2),
            'pl_ratio': round(abs(aw/al), 2) if al else 0,
            'max_ret': round(df['net_ret'].max(), 2),
            'min_ret': round(df['net_ret'].min(), 2),
            'max_drawdown': round(dd, 2),
            'avg_hold': round(df['hold_days'].mean(), 1),
            'chain_rate': round(len(ch)/n*100, 1) if n else 0,
            'exit_dist': exit_dist,
            'score_stats': score_stats,
            'df': df,
        }


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=250)
    p.add_argument('--min-score', type=int, default=60)
    p.add_argument('--output', type=str, default=None)
    a = p.parse_args()

    bt = V2SimpleBacktest()
    trades = bt.run(lookback=a.days, min_score=a.min_score)
    if not trades: print('无交易'); sys.exit(0)

    s = V2SimpleBacktest.summarize(trades)
    df = s.pop('df')

    print(f'\n{"="*70}')
    print(f'  启动+突破 V2 回测 — 连板持有到断板 / 没连板至多3天')
    print(f'{"="*70}')
    print(f'  总交易: {s["trades"]}笔  胜率: {s["win_rate"]:.1f}%')
    print(f'  均收益: {s["avg_ret"]:+.2f}%  中位数: {s["median_ret"]:+.2f}%')
    print(f'  累计:   {s["total_ret"]:+.2f}%')
    print(f'  均盈/均亏: {s["avg_win"]:+.2f}% / {s["avg_loss"]:+.2f}%')
    print(f'  盈亏比: {s["pl_ratio"]:.2f}')
    print(f'  最盈/最亏: {s["max_ret"]:+.2f}% / {s["min_ret"]:+.2f}%')
    print(f'  最大回撤: {s["max_drawdown"]:+.2f}%')
    print(f'  均持有: {s["avg_hold"]:.1f}天  连板率: {s["chain_rate"]:.1f}%')

    print(f'\n  退出原因分布:')
    for reason, cnt in sorted(s['exit_dist'].items(), key=lambda x: -x[1]):
        print(f'    {reason}: {cnt}笔 ({cnt/s["trades"]*100:.0f}%)')

    print(f'\n  评分分层:')
    for bin_label in ['60-69','70-79','80-89','90-100']:
        cnt = s['score_stats']['count'].get(bin_label, 0)
        if not cnt: continue
        wr = s['score_stats']['win_rate'].get(bin_label, 0) * 100
        ar = s['score_stats']['avg_ret'].get(bin_label, 0)
        print(f'    {bin_label}分: {cnt}笔  胜率{wr:.0f}%  均{ar:+.1f}%')

    if a.output:
        df.to_excel(a.output, index=False)
        print(f'\n  已导出: {a.output}')
