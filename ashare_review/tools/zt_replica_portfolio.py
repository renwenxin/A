"""
涨停复制战法 — 模拟持仓管理器

选股: 沪深主板 · 年涨停>=10 · 非ST · 近20日有过涨停 · 处于缩量回调企稳
买入: 回调后再次放量突破 → 三类信号(N字反包/双响炮/缩量回踩不破) → 次日开盘买入
卖出: -5%止损 · 涨停持有断板卖 · 移动止盈 · 最多5天

状态机:
  WATCH → BUY_SIGNAL → HOLDING → FINISHED
"""
import json, os, struct, time as _time, threading
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE
from ashare_review.analysis.indicators import calc_ma
from ashare_review.utils.calendar import TradingCalendar

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
STATE_FILE = os.path.join(DATA_DIR, 'sim_zt_replica_state.json')

# 进程内互斥锁：串行化状态文件的读-改-写（配合原子替换防止并发损坏）
_STATE_LOCK = threading.Lock()
LIMIT_UP_POOL_FILE = os.path.join(DATA_DIR, 'limit_up_pool.json')

FEE = 0.0015; SLIPPAGE = 0.002; TOTAL_COST = FEE + SLIPPAGE
MAX_HOLD_DAYS = 5; MIN_LIMIT_UP_COUNT = 10
MAVOL_PERIOD = 180; MAVOL_MULTIPLIER = 1.2
MAX_LOOKBACK_ZT = 20; MAX_PULLBACK_DAYS = 10
INITIAL_CAPITAL = 1_000_000.0; MAX_POSITIONS = 10; MAX_NEW_PER_DAY = 3
PER_POSITION_PCT = 0.10; BUY_COMMISSION = 0.0003; SELL_COST = 0.0008
# ── 龙哥体系参数 ──
PULLBACK_VOL_RATIO = 0.6         # 回调缩量阈值: 回调期最大量 < 涨停量×0.6
STALL_DAYS = 2                   # 持有N天不涨就警告
MA5_BREAK_WARN = True            # 5日线破位警告

def _today_str() -> str: return date.today().strftime('%Y-%m-%d')
def _date_from_str(s: str) -> date:
    if isinstance(s, date): return s
    return datetime.strptime(s[:10], '%Y-%m-%d').date()

# ═══════════════════════════════════════════════════════════════════════════
class ZTReplicaSimPortfolio:
    """涨停复制战法模拟持仓"""

    def __init__(self):
        self.tdx = TdxReader()
        from ..risk.store import RiskStore
        self._risk = RiskStore()
        self.cal = TradingCalendar()
        os.makedirs(DATA_DIR, exist_ok=True)
        self._state = self._load()
        self._name_cache: Dict[str, str] = {}

    def _load(self) -> dict:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    s = json.load(f)
                s.setdefault('pending_sell', {}); s.setdefault('cash', INITIAL_CAPITAL)
                s.setdefault('initial_capital', INITIAL_CAPITAL)
                s.setdefault('portfolio_history', [])
                return s
            except Exception: pass
        return {'watch': {}, 'ready': {}, 'pending_sell': {}, 'holding': {},
                'finished': {}, 'last_update': '', 'cache_date': '',
                'total_trades': 0, 'total_wins': 0,
                'cash': INITIAL_CAPITAL, 'initial_capital': INITIAL_CAPITAL,
                'portfolio_history': []}

    def _save(self):
        with _STATE_LOCK:
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)  # 原子替换，避免半写文件

    NAME_CACHE_FILE = os.path.join(DATA_DIR, 'stock_name_map.json')

    def _load_name_map(self) -> dict:
        if os.path.exists(self.NAME_CACHE_FILE):
            try:
                with open(self.NAME_CACHE_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                return d if isinstance(d, dict) and len(d) > 1000 else {}
            except Exception: pass
        return {}

    def _get_name(self, code: str) -> str:
        if code in self._name_cache: return self._name_cache[code]
        nm = self._load_name_map()
        name = nm.get(code, code)
        self._name_cache[code] = name
        return name

    # ── 行业板块映射 ──
    INDUSTRY_MAP_FILE = os.path.join(DATA_DIR, 'industry_map.json')

    def _load_sector_map(self) -> dict:
        if os.path.exists(self.INDUSTRY_MAP_FILE):
            try:
                with open(self.INDUSTRY_MAP_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception: pass
        return {}

    def _get_sector(self, code: str) -> str:
        """获取股票所属行业。"""
        if not hasattr(self, '_sector_cache'):
            self._sector_cache = self._load_sector_map()
        return self._sector_cache.get(str(code).zfill(6), '')

    def _get_sector_linkage(self, candidates: List[dict]) -> dict:
        """统计各板块的候选数和信号数，用于板块联动加分。

        返回 {sector: {'total': N, 'buy_signals': N, 'top_score': N}}
        """
        sectors = {}
        for c in candidates:
            sec = self._get_sector(c['code'])
            if not sec: continue
            if sec not in sectors:
                sectors[sec] = {'total': 0, 'buy_signals': 0, 'top_score': 0}
            sectors[sec]['total'] += 1
            if c['status'] == 'buy_signal':
                sectors[sec]['buy_signals'] += 1
            sectors[sec]['top_score'] = max(sectors[sec]['top_score'], c.get('score', 0))
        return sectors

    @staticmethod
    def _limit_threshold(code: str) -> float:
        code = str(code).zfill(6)
        if code.startswith(('300','301','688')): return 0.199
        if code.startswith(('8','4')): return 0.299
        return 0.095

    @staticmethod
    def _is_main_board(code: str) -> bool:
        code = str(code).zfill(6)
        return code.startswith(('60','00','001','002'))

    @staticmethod
    def _fast_count_limit_ups(code: str, tdx, lookback: int = 250) -> int:
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8','4')): market = 'bj'
        try:
            fpath = os.path.join(tdx._market_dir(market), f'{market}{code}.day')
            if not os.path.exists(fpath): return 0
            fsize = os.path.getsize(fpath)
            read_bytes = min(RECORD_SIZE * (lookback + 10), fsize)
            with open(fpath, 'rb') as f:
                f.seek(fsize - read_bytes); raw = f.read(read_bytes)
            n = len(raw) // RECORD_SIZE
            if n < 20: return 0
            count = 0; prev_close = 0
            for j in range(n):
                offset = j * RECORD_SIZE
                cl = struct.unpack_from('I', raw, offset + 16)[0] / 100.0
                if prev_close > 0 and (cl - prev_close) / prev_close >= 0.093:
                    count += 1
                prev_close = cl
            return count
        except Exception: return 0

    def _read_stock(self, code: str, up_to_date: str = None) -> Optional[pd.DataFrame]:
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8','4')): market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if df is None or df.empty or len(df) < MAVOL_PERIOD: return None
            df = calc_ma(df, [5, 10, 20])
            df['mavol180'] = df['volume'].rolling(MAVOL_PERIOD).mean()
            if up_to_date:
                try: target = datetime.strptime(up_to_date, '%Y-%m-%d').date()
                except ValueError:
                    try: target = datetime.strptime(up_to_date, '%Y%m%d').date()
                    except ValueError: return df
                df = df[df['trade_date'].apply(lambda x: (x.date() if hasattr(x, 'date') else x) <= target)]
            return df if len(df) >= 60 else None
        except Exception: return None

    def _read_sell_df(self, code: str, up_to_date: str = None) -> Optional[pd.DataFrame]:
        """卖出检查用日线：读原始日线并按 up_to_date 过滤，不强制 MAVOL/最小 60 根。

        卖出规则只用 close/open/volume，不依赖均线；放宽长度约束后，
        既能省去历史均线计算，也支持测试注入的少量日线。过滤逻辑与
        _read_stock 保持一致（up_to_date 双格式解析 + trade_date 过滤）。
        """
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8','4')): market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if df is None or df.empty: return None
            if up_to_date:
                try: target = datetime.strptime(up_to_date, '%Y-%m-%d').date()
                except ValueError:
                    try: target = datetime.strptime(up_to_date, '%Y%m%d').date()
                    except ValueError: return df
                df = df[df['trade_date'].apply(lambda x: (x.date() if hasattr(x, 'date') else x) <= target)]
            return df if not df.empty else None
        except Exception: return None

    def _read_latest(self, code: str) -> Optional[dict]:
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8','4')): market = 'bj'
        try:
            fpath = os.path.join(self.tdx._market_dir(market), f'{market}{code}.day')
            if not os.path.exists(fpath): return None
            fsize = os.path.getsize(fpath)
            if fsize < RECORD_SIZE * MAVOL_PERIOD: return None
            read_bytes = min(RECORD_SIZE * 250, fsize)
            with open(fpath, 'rb') as f:
                f.seek(fsize - read_bytes); raw = f.read(read_bytes)
            n = len(raw) // RECORD_SIZE
            if n < 2: return None
            closes = []; volumes = []; opens = []; highs = []; lows = []
            for j in range(n):
                offset = j * RECORD_SIZE
                dt, op, hi, lo, cl_int, amt, vol, _ = struct.unpack('IIIIIfII', raw[offset:offset + RECORD_SIZE])
                closes.append(cl_int / 100.0); volumes.append(float(vol))
                opens.append(op / 100.0); highs.append(hi / 100.0); lows.append(lo / 100.0)
            mavol180 = float(np.mean(volumes[-MAVOL_PERIOD:])) if len(volumes) >= MAVOL_PERIOD else 0
            return {'close': round(closes[-1], 2), 'vol': int(volumes[-1]),
                    'open': round(opens[-1], 2), 'high': round(highs[-1], 2),
                    'low': round(lows[-1], 2), 'mavol180': round(mavol180, 0),
                    'closes': closes, 'volumes': volumes, 'opens': opens,
                    'highs': highs, 'lows': lows}
        except Exception: return None

    # ─── 候选池（与V3共用） ───
    def _get_eligible_pool(self) -> list:
        if os.path.exists(LIMIT_UP_POOL_FILE):
            try:
                with open(LIMIT_UP_POOL_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                pool = data.get('pool', [])
                if pool and (date.today() - _date_from_str(data.get('updated', '2000-01-01'))).days < 7:
                    print(f'[ZTReplica] 使用候选池缓存: {len(pool)} 只')
                    return pool
            except Exception: pass
        return self._build_limit_up_pool()

    def _build_limit_up_pool(self) -> list:
        print('[ZTReplica] 构建候选池...')
        name_map = self._load_name_map()
        stocks = self.tdx.list_stocks()
        pool = []; t0 = _time.time()
        for si, (code, market) in enumerate(stocks):
            if code.startswith(('8','4')) or not self._is_main_board(code): continue
            name = name_map.get(code, '')
            if not name or 'ST' in name: continue
            if (si + 1) % 500 == 0:
                print(f'  池构建 {si+1}/{len(stocks)} ({_time.time()-t0:.0f}s, 已{len(pool)}只)...')
            try:
                if self._fast_count_limit_ups(code, self.tdx, 250) < MIN_LIMIT_UP_COUNT: continue
                pool.append({'code': code, 'market': market,
                             'name': name if name != code else self._get_name(code),
                             'limit_count': self._fast_count_limit_ups(code, self.tdx, 250)})
            except Exception: continue
        with open(LIMIT_UP_POOL_FILE, 'w', encoding='utf-8') as f:
            json.dump({'pool': pool, 'updated': _today_str(), 'count': len(pool)}, f, ensure_ascii=False)
        print(f'[ZTReplica] 候选池构建完成: {len(pool)} 只 ({_time.time()-t0:.0f}s)')
        return pool

    # ─── 市场环境 ───
    def _get_market_state(self, trade_date: str = None) -> dict:
        td = trade_date or _today_str()
        try:
            sh = self.tdx.read_daily('999999', 'sh')
            if sh is not None and len(sh) >= 60:
                sh['ma60'] = sh['close'].rolling(60).mean()
                target = datetime.strptime(td.replace('-',''), '%Y%m%d').date()
                mask = sh['trade_date'].apply(lambda x: (x.date() if hasattr(x,'date') else x) <= target)
                sub = sh[mask]
                if len(sub) >= 60:
                    close = float(sub['close'].iloc[-1]); ma60 = float(sub['ma60'].iloc[-1])
                    return {'is_bull': close > ma60 and not pd.isna(ma60),
                            'sh_close': round(close,2), 'sh_ma60': round(ma60,2)}
        except Exception: pass
        return {'is_bull': True, 'sh_close': 0, 'sh_ma60': 0}

    # ═══════════════════════════════════════════════════════════════════════
    # 核心: 涨停复制选股 + 信号检测
    # ═══════════════════════════════════════════════════════════════════════

    def _find_recent_zt(self, closes, opens, highs, lows, volumes, idx, code) -> Optional[dict]:
        """在idx位置往前找最近一次非一字板涨停（20日内）。
        附加涨停时段评估: 早盘板(开盘涨3%+) > 换手板 > 尾盘板。
        """
        limit_pct = self._limit_threshold(code)
        lookback = min(MAX_LOOKBACK_ZT, idx)
        if lookback < 2: return None
        for j in range(idx - 1, max(idx - lookback - 1, 0), -1):
            prev_close = closes[j - 1] if j > 0 else 0
            if prev_close <= 0: continue
            chg = (closes[j] - prev_close) / prev_close
            if chg >= limit_pct:
                if abs(opens[j] - closes[j]) / max(closes[j], 0.01) < 0.005: continue  # 一字板跳过

                # 涨停时段评估
                open_chg = (opens[j] - prev_close) / prev_close
                if open_chg >= 0.03:
                    zt_timing = '早盘强势板'  # 高开3%+直接封板
                elif open_chg >= 0:
                    zt_timing = '换手板'
                else:
                    zt_timing = '低开拉板'

                return {'zt_idx': j, 'zt_close': closes[j], 'zt_open': opens[j],
                        'zt_high': highs[j], 'zt_low': lows[j], 'zt_vol': volumes[j],
                        'change_pct': round(chg * 100, 1), 'days_since': idx - j,
                        'zt_timing': zt_timing}
        return None

    def _check_buy_signal(self, closes, opens, highs, lows, volumes, mavol180, idx,
                          code, zt_info) -> Optional[dict]:
        """检测涨停复制买入信号（龙哥体系完整版）。

        四类信号:
          🔥涨停双响炮: 缩量回调后再次涨停（最强）
          📈N字反包: 缩量回调 → 放量突破回调高点
          🔍缩量回踩不破: 回踩均线不破 + 放量企稳
          📊蓄势待发: 今日大涨5-9.5%但未涨停（龙哥"没涨停反而更好"）

        龙哥加分项:
          - 均线支撑: 回踩MA5不破 +10分 / MA10 +6分 / MA20 +3分
          - 涨停日时段: 早盘强势板 +3分
          - 前日抗跌(大盘跌但个股涨) +10分
          - 回调2-4天最佳窗口 +8分
        """
        zt_idx = zt_info['zt_idx']; pb_start = zt_idx + 1; pb_end = idx
        if pb_end < pb_start: return None
        pb_days = pb_end - pb_start + 1
        if pb_days > MAX_PULLBACK_DAYS: return None

        zt_vol = zt_info['zt_vol']; zt_low = zt_info['zt_low']
        zt_open = zt_info.get('zt_open', 0); zt_close = zt_info.get('zt_close', 0)

        # 回调数据
        pb_vols = [volumes[i] for i in range(pb_start, pb_end + 1)]
        pb_vol_max = max(pb_vols) if pb_vols else 0
        pb_high = max(highs[i] for i in range(pb_start, pb_end + 1))
        pb_low = min(lows[i] for i in range(pb_start, pb_end + 1))

        # 涨停日强度
        zt_timing = zt_info.get('zt_timing', '换手板')
        zt_strength = '强' if zt_timing == '早盘强势板' else '中等'

        # ── 回调缩量判断 ──
        is_shrinking = pb_vol_max < zt_vol * PULLBACK_VOL_RATIO  # 严格: 0.6x
        is_moderate_shrink = pb_vol_max < zt_vol * 0.8

        # ── 不破涨停最低价 ──
        is_above_zt_low = pb_low >= zt_low * 0.98

        # ── 前一日抗跌 ──
        prev_day_resilient = False
        if idx >= 1 and closes[idx-1] > 0 and closes[idx-2] > 0:
            stock_prev_chg = (closes[idx-1] - closes[idx-2]) / closes[idx-2]
            prev_day_resilient = stock_prev_chg > 0

        close = closes[idx]; vol = volumes[idx]; open_today = opens[idx]
        if mavol180 <= 0: return None
        vol_ratio = vol / mavol180

        # ── 均线支撑识别（龙哥核心：5日线/10日线是生命线） ──
        ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else 0
        ma10 = float(np.mean(closes[-10:])) if len(closes) >= 10 else 0
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else 0

        ma_support = ''
        ma_score = 0
        dist_to_ma5 = (close - ma5) / ma5 * 100 if ma5 > 0 else 0
        dist_to_ma10 = (close - ma10) / ma10 * 100 if ma10 > 0 else 0

        if ma5 > 0 and dist_to_ma5 >= -1 and dist_to_ma5 <= 3:
            ma_support = '回踩MA5'; ma_score = 10  # 紧贴5日线=最强支撑
        elif ma10 > 0 and dist_to_ma10 >= -1 and dist_to_ma10 <= 4:
            ma_support = '回踩MA10'; ma_score = 6
        elif ma20 > 0 and close > ma20:
            ma_support = '站稳MA20'; ma_score = 3

        # 今日是否涨停
        is_zt_today = False; today_chg = 0
        if idx >= 1 and closes[idx-1] > 0:
            limit_pct = self._limit_threshold(code)
            today_chg = (close - closes[idx-1]) / closes[idx-1]
            is_zt_today = today_chg >= limit_pct
        is_yizi = abs(open_today - close) / max(close, 0.01) < 0.005

        # ── 四类信号判定 ──
        break_pb = close > pb_high and vol_ratio >= MAVOL_MULTIPLIER
        sig_a = is_shrinking and break_pb and close > 0  # N字反包

        sig_b = is_zt_today and not is_yizi and (is_shrinking or is_moderate_shrink)  # 双响炮

        sig_c = ((is_shrinking or is_moderate_shrink) and is_above_zt_low
                 and vol_ratio >= MAVOL_MULTIPLIER
                 and close > zt_info['zt_close'] * 0.98 and not break_pb)  # 缩量回踩

        # D: 蓄势待发 — 大涨5-9.5%但未涨停（龙哥"没涨停反而更好"）
        sig_d = (not is_zt_today and today_chg >= 0.05 and today_chg < 0.095
                 and vol_ratio >= MAVOL_MULTIPLIER * 1.25  # 比标准多25%量能
                 and close > ma5 and ma_support != ''  # 必须站在均线上方
                 and (is_shrinking or is_moderate_shrink))

        if not (sig_a or sig_b or sig_c or sig_d): return None
        if vol_ratio >= 5.0: return None

        # ── 信号命名与基础分 ──
        if sig_b:
            sig_type = '🔥涨停双响炮'; score = 45 + 25
        elif sig_a:
            sig_type = '📈N字反包'; score = 45 + 18
        elif sig_d:
            sig_type = '📊蓄势待发'; score = 40 + 12  # 基础分较低，靠加分项
        else:
            sig_type = '🔍缩量回踩企稳'; score = 45 + 10

        # ── 通用加分项 ──
        if is_shrinking: score += 12
        elif is_moderate_shrink: score += 6

        if vol_ratio >= 2.0: score += 10
        elif vol_ratio >= 1.5: score += 5

        if 2 <= pb_days <= 4: score += 8
        elif pb_days <= 6: score += 4

        if zt_strength == '强': score += 3

        if prev_day_resilient: score += 10

        score += ma_score

        return {'sig_type': sig_type, 'score': min(100, score),
                'vol_ratio': round(vol_ratio, 1), 'zt_days_ago': zt_info['days_since'],
                'pb_days': pb_days, 'is_shrinking': is_shrinking,
                'break_pct': round((close - pb_high) / pb_high * 100, 1) if pb_high > 0 else 0,
                'zt_strength': zt_strength, 'prev_resilient': prev_day_resilient,
                'ma_support': ma_support, 'today_chg': round(today_chg * 100, 1),
                'zt_timing': zt_timing}

    def screen_candidates(self, trade_date: str) -> List[dict]:
        """从候选池筛选涨停复制观察池（近20日有涨停+处于回调）。

        加入板块联动检测：同板块多只信号 → 板块确认加分。
        """
        eligible = self._get_eligible_pool()
        candidates = []; total = len(eligible)
        print(f'[ZTReplica] 从 {total} 只候选池中筛选涨停复制候选...')

        for si, stock in enumerate(eligible):
            if (si + 1) % 100 == 0: print(f'  筛选 {si+1}/{total}...')
            code = stock['code']
            latest = self._read_latest(code)
            if latest is None or latest['mavol180'] <= 0: continue

            closes = latest['closes']; opens = latest['opens']
            highs = latest['highs']; lows = latest['lows']
            volumes = latest['volumes']; idx = len(closes) - 1

            zt_info = self._find_recent_zt(closes, opens, highs, lows, volumes, idx, code)
            if zt_info is None: continue

            sig = self._check_buy_signal(closes, opens, highs, lows, volumes,
                                         latest['mavol180'], idx, code, zt_info)
            status = 'buy_signal' if sig else 'watching'

            # 观察池基础评分
            if sig:
                watch_score = sig['score']
            else:
                watch_score = 40 + min(10, 10 - zt_info['days_since'])

            candidates.append({
                'code': code, 'close': latest['close'],
                'vol': latest['vol'], 'mavol180': latest['mavol180'],
                'vol_ratio': round(latest['vol'] / latest['mavol180'], 1),
                'limit_count': stock.get('limit_count', 0),
                'zt_days_ago': zt_info['days_since'],
                'zt_change_pct': zt_info['change_pct'],
                'status': status,
                'sig_type': sig['sig_type'] if sig else '回调企稳中',
                'score': watch_score,
                'break_pct': sig.get('break_pct', 0) if sig else 0,
                'ma_support': sig.get('ma_support', '') if sig else '',
                'zt_timing': zt_info.get('zt_timing', ''),
                'today_chg': sig.get('today_chg', 0) if sig else 0,
            })

        # ── 板块联动加分（龙哥：当天板块有联动时做最强股） ──
        sector_info = self._get_sector_linkage(candidates)
        for c in candidates:
            sec = self._get_sector(c['code'])
            if sec and sec in sector_info:
                info = sector_info[sec]
                if info['buy_signals'] >= 2:
                    c['score'] = min(100, c['score'] + 10)  # 板块确认
                    if c['sig_type'] == '回调企稳中':
                        c['sig_type'] = '回调企稳中·板块联动'
                c['sector_name'] = sec
                c['sector_signals'] = info['buy_signals']

        return candidates

    # ═══════════════════════════════════════════════════════════════════════
    # 卖出检查
    # ═══════════════════════════════════════════════════════════════════════

    def _check_sell(self, code: str, pos: dict, today: str) -> Optional[dict]:
        """卖出检查（龙哥体系）。

        规则:
          0. 硬止损（读风控配置，默认 -5%）— 无条件离场
          1. 移动止盈 — 从最高回落>5% → 锁利
          2. 涨停后断板 → 等N字反包，失败则离场
          3. 持有≥2天无明显涨幅（<2%）→ 警惕信号 → 提示减持
          4. 跌破5日线 → 卖出警告
          5. 持有≥5天到期 → 兜底卖出
        """
        td_fmt = today.replace('-', '')
        df = self._read_sell_df(code, up_to_date=td_fmt)
        if df is None or df.empty: return None

        idx = len(df) - 1; close = float(df['close'].iloc[idx])
        buy_price = pos.get('buy_price', 0); had_zt = pos.get('had_zt', False)
        highest = max(pos.get('highest_close', buy_price), close)
        pos['highest_close'] = round(highest, 2)

        try:
            bd = _date_from_str(pos.get('buy_date', today))
            td = _date_from_str(today)
            trading_days = self.cal.trading_days_between(bd, td)
        except Exception: trading_days = 3

        # ── 0. 硬止损（读风控配置，默认 -5%） ──
        from ..risk.evaluate import stop_loss_pct
        stop = stop_loss_pct(self._risk.get('zt_replica')) / 100.0
        if buy_price > 0 and (close - buy_price) / buy_price <= stop:
            return {'sell_price': round(close, 2), 'sell_reason': f'🛑止损{abs(stop*100):.0f}%',
                    'days_held': trading_days}

        # ── 1. 移动止盈 ──
        if highest > buy_price * 1.03:
            if (close - highest) / highest <= -0.05:
                return {'sell_price': round(close, 2),
                        'sell_reason': f'📉移动止盈-5%(高{highest:.2f})', 'days_held': trading_days}

        # ── 2. 涨停相关检查 ──
        is_zt_today = False
        if idx >= 1:
            prev_c = float(df['close'].iloc[idx - 1])
            is_zt_today = (close - prev_c) / prev_c >= self._limit_threshold(code) if prev_c > 0 else False

        if is_zt_today:
            pos['had_zt'] = True; pos['awaiting_reversal'] = False
            return None  # 涨停就继续持有

        # 涨停后断板 → N字反包等待
        if had_zt:
            awaiting = pos.get('awaiting_reversal', False)
            if not awaiting:
                pos['awaiting_reversal'] = True
                pos['reversal_day_close'] = round(close, 2)
                pos['reversal_day_vol'] = int(float(df['volume'].iloc[idx]))
                return None  # 第一天断板→等反包
            else:
                rev_vol = pos.get('reversal_day_vol', 0)
                today_vol = int(float(df['volume'].iloc[idx]))
                today_open = float(df['open'].iloc[idx])
                if close > today_open and today_vol > rev_vol:
                    pos['awaiting_reversal'] = False; pos['had_zt'] = True
                    return None  # N字反包成功！
                return {'sell_price': round(close, 2), 'sell_reason': '🔴涨停后断板离场',
                        'days_held': trading_days}

        # ── 3. 持有2天不涨就撤 ──
        if trading_days >= STALL_DAYS and buy_price > 0:
            ret_so_far = (close - buy_price) / buy_price
            if ret_so_far < 0.02:  # 2天后收益不足2%
                # 不是硬卖而是强烈建议
                pos['stall_warning'] = True

        # ── 4. 5日线破位 ──
        ma5_break = False
        if 'ma5' in df.columns:
            ma5_val = float(df['ma5'].iloc[idx])
            if not pd.isna(ma5_val) and close < ma5_val:
                ma5_break = True
                if idx >= 1 and float(df['close'].iloc[idx-1]) < float(df['ma5'].iloc[idx-1]) if not pd.isna(df['ma5'].iloc[idx-1]) else False:
                    return {'sell_price': round(close, 2), 'sell_reason': '📊连续跌破5日线',
                            'days_held': trading_days}

        # ── 5. 持有≥5天到期 ──
        if trading_days >= MAX_HOLD_DAYS:
            return {'sell_price': round(close, 2),
                    'sell_reason': f'⏰持有{trading_days}天到期', 'days_held': trading_days}

        # ── 6. 移动止盈（没涨停的情况也适用） ──
        if highest > buy_price * 1.05 and (close - highest) / highest <= -0.05:
            return {'sell_price': round(close, 2),
                    'sell_reason': f'📉移动止盈-5%(高{highest:.2f})', 'days_held': trading_days}

        return None

    # ═══════════════════════════════════════════════════════════════════════
    # 对外 API（与 V3 完全一致接口）
    # ═══════════════════════════════════════════════════════════════════════

    def run_daily(self, trade_date: str = None, force_rebuild_pool: bool = False) -> dict:
        td = trade_date or _today_str(); td_dt = _date_from_str(td)
        if td_dt.weekday() >= 5:
            self._state['last_update'] = td; self._save()
            return {'date': td, 'note': '非交易日', 'watch': 0, 'buys': 0, 'sells': 0}

        market = self._get_market_state(td)
        is_bull = market.get('is_bull', True)

        if force_rebuild_pool: self._build_limit_up_pool()

        # 执行挂单卖出
        for code in list(self._state.get('pending_sell', {}).keys()):
            ps = self._state['pending_sell'][code]
            if ps.get('sell_date', '') <= td:
                pos = ps['hold_info']
                try:
                    df_t = self._read_stock(code, up_to_date=td.replace('-',''))
                    sell_price = float(df_t['open'].iloc[-1]) if df_t is not None and not df_t.empty else ps.get('close_price', pos.get('buy_price', 0))
                except Exception: sell_price = ps.get('close_price', pos.get('buy_price', 0))
                bp = pos.get('buy_price', 0); shares = pos.get('shares', 0)
                gr = (sell_price - bp) / bp if bp > 0 else 0; nr = gr - TOTAL_COST
                if shares > 0:
                    self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) + shares * sell_price * (1 - SELL_COST)
                self._state['finished'][code] = {**pos, 'status': 'finished',
                    'sell_date': td, 'sell_price': round(sell_price, 2),
                    'gross_ret': round(gr * 100, 2), 'net_ret': round(nr * 100, 2),
                    'is_win': nr > 0, 'exit_reason': ps.get('sell_reason', ''),
                    'days_held': ps.get('days_held', 0)}
                self._state['total_trades'] = self._state.get('total_trades', 0) + 1
                if nr > 0: self._state['total_wins'] = self._state.get('total_wins', 0) + 1
                del self._state['pending_sell'][code]
                if code in self._state['holding']: del self._state['holding'][code]

        # 刷新候选池 — 每次扫描清空旧信号，强制重新评估
        candidates = self.screen_candidates(td)
        watch = [c for c in candidates if c['status'] == 'watching']
        buy_sigs = [c for c in candidates if c['status'] == 'buy_signal']

        self._state['watch'] = {}
        self._state['ready'] = {}  # ← 清空旧买入信号，防止 7/28 的过期信号残留
        for c in watch:
            code = c['code']
            self._state['watch'][code] = {'code': code, 'name': self._get_name(code),
                'close': c['close'], 'vol_ratio': c['vol_ratio'],
                'zt_days_ago': c['zt_days_ago'], 'sig_type': c['sig_type'],
                'score': c['score'], 'limit_count': c['limit_count'],
                'ma_support': c.get('ma_support', ''),
                'sector_name': c.get('sector_name', ''),
                'sector_signals': c.get('sector_signals', 0),
                'zt_timing': c.get('zt_timing', ''),
                'update_date': td}

        # 买入信号
        available_slots = max(0, MAX_POSITIONS - len(self._state['holding']) - len(self._state['ready']))
        cfg = self._risk.get('zt_replica')
        holdings_val = sum(
            h.get('shares', 0) * (h.get('current_price', h.get('buy_price', 0)) or 0)
            for h in self._state['holding'].values()
        )
        hist_peak = INITIAL_CAPITAL
        for snap in self._state.get('portfolio_history', []):
            hist_peak = max(hist_peak, snap.get('total', 0) or 0)
        from ..risk.evaluate import evaluate
        from ..analysis.strategy_regime import live_diagnosis as _ld
        try:
            regime = _ld.get_regime_diagnosis().get('regime', '震荡观望') or '震荡观望'
        except Exception:
            regime = '震荡观望'
        risk = evaluate(cfg, {
            'positions': len(self._state['holding']) + len(self._state['ready']),
            'opened_today': 0,
            'total_value': self._state.get('cash', INITIAL_CAPITAL) + holdings_val,
            'history_peak': hist_peak,
            'breaker_tripped': (self._state.get('last_risk') or {}).get('breaker_tripped', False),
        }, regime)
        if risk['blocked_reasons']:
            print(f"[ZTReplica] 风控拦截开仓: {'；'.join(risk['blocked_reasons'])}")
        self._state['last_risk'] = risk
        max_new = min(cfg['max_new_per_day'], available_slots)
        buy_sigs.sort(key=lambda x: -x['score'])
        new_buys = 0

        for sig in buy_sigs[:max_new]:
            code = sig['code']
            if code in self._state['holding'] or code in self._state['ready']: continue
            if not is_bull and sig['score'] < 70: continue
            name = self._get_name(code)
            try:
                next_day = self.cal.next_trading_day(td_dt, offset=1)
                buy_date = next_day.strftime('%Y-%m-%d') if next_day else (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
            except Exception: buy_date = (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
            shares = int(INITIAL_CAPITAL * (risk['suggested_size_pct'] / 100.0) / max(sig['close'], 0.01) / 100) * 100
            if shares < 100: shares = 100
            self._state['ready'][code] = {'code': code, 'name': name,
                'signal_date': td, 'buy_date': buy_date, 'buy_price': sig['close'],
                'shares': shares, 'close': sig['close'],
                'vol_ratio': sig['vol_ratio'], 'score': sig['score'],
                'sig_type': sig['sig_type'], 'zt_days_ago': sig['zt_days_ago'],
                'break_pct': sig.get('break_pct', 0), 'limit_count': sig['limit_count'],
                'ma_support': sig.get('ma_support', ''),
                'sector_name': sig.get('sector_name', ''),
                'mode': 'zt_replica', 'market_bull': is_bull}
            new_buys += 1

        # 自动执行买入
        auto_buys = 0
        for code in list(self._state['ready'].keys()):
            rd = self._state['ready'][code]
            if rd['buy_date'] <= td:
                skip_reason = None
                try:
                    df_ck = self._read_stock(code, up_to_date=td.replace('-',''))
                    if df_ck is not None and not df_ck.empty:
                        idx = len(df_ck) - 1; op = float(df_ck['open'].iloc[idx])
                        if idx >= 1:
                            pc = float(df_ck['close'].iloc[idx-1])
                            if op <= pc * (1 - self._limit_threshold(code)): skip_reason = '开盘跌停'
                except Exception: pass
                # 竞价确认
                if skip_reason is None:
                    try:
                        df_au = self._read_stock(code, up_to_date=td.replace('-',''))
                        if df_au is not None and not df_au.empty and len(df_au) >= 2:
                            idx_a = len(df_au) - 1
                            op_a = float(df_au['open'].iloc[idx_a])
                            prev_c = float(df_au['close'].iloc[idx_a - 1])
                            if (op_a - prev_c) / prev_c * 100 < -3: skip_reason = f'竞价低开'
                            if float(df_au['volume'].iloc[idx_a]) < float(df_au['volume'].iloc[idx_a-1]) * 0.5:
                                skip_reason = '竞价缩量>50%'
                    except Exception: pass
                if skip_reason:
                    self._state['finished'][code] = {**rd, 'status': 'skipped',
                        'skip_reason': skip_reason, 'skip_date': td}
                    del self._state['ready'][code]; continue
                # 执行买入
                bp_actual = rd.get('buy_price', rd.get('close', 0))
                try:
                    df_b = self._read_stock(code, up_to_date=td.replace('-',''))
                    if df_b is not None and not df_b.empty: bp_actual = float(df_b['open'].iloc[-1])
                except Exception: pass
                actual_shares = int(INITIAL_CAPITAL * (risk['suggested_size_pct'] / 100.0) / max(bp_actual, 0.01) / 100) * 100
                if actual_shares < 100: actual_shares = 100
                buy_cost = actual_shares * bp_actual * (1 + BUY_COMMISSION)
                if buy_cost > self._state.get('cash', INITIAL_CAPITAL):
                    self._state['finished'][code] = {**rd, 'status': 'skipped',
                        'skip_reason': '资金不足', 'skip_date': td}
                    del self._state['ready'][code]; continue
                self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) - buy_cost
                self._state['holding'][code] = {**rd, 'status': 'holding',
                    'buy_date': td, 'buy_price': round(bp_actual, 2),
                    'shares': actual_shares, 'had_zt': False,
                    'highest_close': bp_actual, 'awaiting_reversal': False}
                del self._state['ready'][code]; auto_buys += 1

        # 检查卖出
        sells_today = 0
        for code in list(self._state['holding'].keys()):
            if code in self._state.get('pending_sell', {}): continue
            pos = self._state['holding'][code]
            sell_sig = self._check_sell(code, pos, td)
            if sell_sig:
                try:
                    nsd = self.cal.next_trading_day(td_dt, offset=1)
                    sell_date = nsd.strftime('%Y-%m-%d') if nsd else (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                except Exception: sell_date = (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                self._state['pending_sell'][code] = {'hold_info': {**pos},
                    'sell_date': sell_date, 'sell_reason': sell_sig['sell_reason'],
                    'close_price': sell_sig['sell_price'], 'days_held': sell_sig.get('days_held', 0)}
                sells_today += 1

        # 组合快照
        pos_val = sum(self._state['holding'].get(c, {}).get('shares', 0) *
                      (self._read_latest(c) or {}).get('close', self._state['holding'][c].get('buy_price', 0))
                      for c in self._state['holding'])
        total_val = self._state.get('cash', INITIAL_CAPITAL) + pos_val
        self._state.setdefault('portfolio_history', []).append(
            {'date': td, 'cash': round(self._state.get('cash', INITIAL_CAPITAL), 2),
             'positions_value': round(pos_val, 2), 'total': round(total_val, 2),
             'market_bull': is_bull})
        if len(self._state['portfolio_history']) > 500:
            self._state['portfolio_history'] = self._state['portfolio_history'][-500:]

        self._state['last_update'] = td; self._state['cache_date'] = td; self._save()
        print(f'[ZTReplica] 完成: 观察{len(self._state["watch"])} 买入{new_buys}(+{auto_buys}) '
              f'卖出{sells_today} 持仓{len(self._state["holding"])}')
        return {'date': td, 'watch_count': len(self._state['watch']),
                'buy_count': new_buys, 'sell_count': sells_today,
                'holding_count': len(self._state['holding']),
                'ready_count': len(self._state['ready']),
                'portfolio_value': round(total_val, 2), 'market_bull': is_bull}

    def refresh_daily_status(self, trade_date: str = None) -> dict:
        """轻量刷新状态：只执行挂单卖出+检查持仓卖出条件，不重新选股。

        与 run_daily() 的区别：
        - run_daily(): 重建候选池 → 全量选股 → 生成买入信号 → 完整流程
        - refresh_daily_status(): 仅更新已有持仓/观察池价格，执行到期操作

        这样可以保证"明日开盘买入"列表在一次扫描后保持稳定，
        不会因为刷新而改变筛选结果。
        """
        td = trade_date or _today_str(); td_dt = _date_from_str(td)
        if td_dt.weekday() >= 5:
            self._state['last_update'] = td; self._save()
            return {'date': td, 'note': '非交易日', 'watch': len(self._state.get('watch', {})),
                    'buys': len(self._state.get('ready', {})), 'sells': 0}

        # 1) 执行挂单卖出
        sells_executed = 0
        for code in list(self._state.get('pending_sell', {}).keys()):
            ps = self._state['pending_sell'][code]
            if ps.get('sell_date', '') <= td:
                pos = ps['hold_info']
                try:
                    df_t = self._read_stock(code, up_to_date=td.replace('-',''))
                    sell_price = float(df_t['open'].iloc[-1]) if df_t is not None and not df_t.empty else ps.get('close_price', pos.get('buy_price', 0))
                except Exception: sell_price = ps.get('close_price', pos.get('buy_price', 0))
                bp = pos.get('buy_price', 0); shares = pos.get('shares', 0)
                gr = (sell_price - bp) / bp if bp > 0 else 0; nr = gr - TOTAL_COST
                if shares > 0:
                    self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) + shares * sell_price * (1 - SELL_COST)
                self._state['finished'][code] = {**pos, 'status': 'finished',
                    'sell_date': td, 'sell_price': round(sell_price, 2),
                    'gross_ret': round(gr * 100, 2), 'net_ret': round(nr * 100, 2),
                    'is_win': nr > 0, 'exit_reason': ps.get('sell_reason', ''),
                    'days_held': ps.get('days_held', 0)}
                self._state['total_trades'] = self._state.get('total_trades', 0) + 1
                if nr > 0: self._state['total_wins'] = self._state.get('total_wins', 0) + 1
                del self._state['pending_sell'][code]
                if code in self._state['holding']: del self._state['holding'][code]
                sells_executed += 1

        # 2) 自动执行到期的买入
        auto_buys = 0
        for code in list(self._state.get('ready', {}).keys()):
            rd = self._state['ready'][code]
            if rd.get('buy_date', '') <= td:
                skip_reason = None
                try:
                    df_ck = self._read_stock(code, up_to_date=td.replace('-',''))
                    if df_ck is not None and not df_ck.empty:
                        idx = len(df_ck) - 1; op = float(df_ck['open'].iloc[idx])
                        if idx >= 1:
                            pc = float(df_ck['close'].iloc[idx-1])
                            if op <= pc * (1 - self._limit_threshold(code)): skip_reason = '开盘跌停'
                except Exception: pass
                if skip_reason is None:
                    try:
                        df_au = self._read_stock(code, up_to_date=td.replace('-',''))
                        if df_au is not None and not df_au.empty and len(df_au) >= 2:
                            idx_a = len(df_au) - 1
                            op_a = float(df_au['open'].iloc[idx_a])
                            prev_c = float(df_au['close'].iloc[idx_a - 1])
                            if (op_a - prev_c) / prev_c * 100 < -3: skip_reason = '竞价低开'
                            if float(df_au['volume'].iloc[idx_a]) < float(df_au['volume'].iloc[idx_a-1]) * 0.5:
                                skip_reason = '竞价缩量>50%'
                    except Exception: pass
                if skip_reason:
                    self._state['finished'][code] = {**rd, 'status': 'skipped',
                        'skip_reason': skip_reason, 'skip_date': td}
                    del self._state['ready'][code]; continue
                bp_actual = rd.get('buy_price', rd.get('close', 0))
                try:
                    df_b = self._read_stock(code, up_to_date=td.replace('-',''))
                    if df_b is not None and not df_b.empty: bp_actual = float(df_b['open'].iloc[-1])
                except Exception: pass
                actual_shares = int(INITIAL_CAPITAL * PER_POSITION_PCT / max(bp_actual, 0.01) / 100) * 100
                if actual_shares < 100: actual_shares = 100
                buy_cost = actual_shares * bp_actual * (1 + BUY_COMMISSION)
                if buy_cost > self._state.get('cash', INITIAL_CAPITAL):
                    self._state['finished'][code] = {**rd, 'status': 'skipped',
                        'skip_reason': '资金不足', 'skip_date': td}
                    del self._state['ready'][code]; continue
                self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) - buy_cost
                self._state['holding'][code] = {**rd, 'status': 'holding',
                    'buy_date': td, 'buy_price': round(bp_actual, 2),
                    'shares': actual_shares, 'had_zt': False,
                    'highest_close': bp_actual, 'awaiting_reversal': False}
                del self._state['ready'][code]; auto_buys += 1

        # 3) 检查持仓卖出条件
        sells_today = 0
        for code in list(self._state.get('holding', {}).keys()):
            if code in self._state.get('pending_sell', {}): continue
            pos = self._state['holding'][code]
            sell_sig = self._check_sell(code, pos, td)
            if sell_sig:
                try:
                    nsd = self.cal.next_trading_day(td_dt, offset=1)
                    sell_date = nsd.strftime('%Y-%m-%d') if nsd else (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                except Exception: sell_date = (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                self._state['pending_sell'][code] = {'hold_info': {**pos},
                    'sell_date': sell_date, 'sell_reason': sell_sig['sell_reason'],
                    'close_price': sell_sig['sell_price'], 'days_held': sell_sig.get('days_held', 0)}
                sells_today += 1

        self._state['last_update'] = td; self._save()
        return {'date': td,
                'watch_count': len(self._state.get('watch', {})),
                'buy_count': len(self._state.get('ready', {})),  # 保持不变
                'sell_count': sells_today + sells_executed,
                'holding_count': len(self._state.get('holding', {})),
                'ready_count': len(self._state.get('ready', {})),
                'note': '轻量刷新-未重新选股'}

    def get_summary(self) -> dict:
        today = _today_str()
        def _ensure_name(item, code):
            if not item.get('name') or item['name'] == code:
                item['name'] = self._get_name(code)

        watch = []
        for code, w in self._state['watch'].items():
            _ensure_name(w, code)
            latest = self._read_latest(code)
            if latest: w['close'] = latest['close']; w['vol_ratio'] = round(latest['vol'] / max(latest['mavol180'], 1), 1)
            watch.append({**w})

        buy_today = []
        for code, rd in self._state['ready'].items():
            _ensure_name(rd, code); buy_today.append({**rd})

        sell_today = []
        for code, pos in self._state['holding'].items():
            _ensure_name(pos, code)
            sell_sig = self._check_sell(code, pos, today)
            if sell_sig:
                bp = pos.get('buy_price', 0); sp = sell_sig['sell_price']
                nr = (sp - bp) / bp - TOTAL_COST if bp > 0 else 0
                sell_today.append({**pos, 'sell_price_today': sp,
                    'estimated_net_ret': round(nr * 100, 2),
                    'exit_reason': sell_sig['sell_reason'],
                    'days_held': sell_sig.get('days_held', 0)})

        holdings = []
        for code, pos in self._state['holding'].items():
            _ensure_name(pos, code)
            df = self._read_stock(code)
            cp = pos.get('buy_price', 0); is_zt = False
            if df is not None and not df.empty:
                idx = len(df) - 1; cp = float(df['close'].iloc[idx])
                if idx >= 1:
                    prev_c = float(df['close'].iloc[idx - 1])
                    is_zt = (cp - prev_c) / prev_c >= self._limit_threshold(code) if prev_c > 0 else False
            pnl = (cp - pos.get('buy_price', 0)) / max(pos.get('buy_price', 1), 0.01) * 100
            try:
                bd = _date_from_str(pos.get('buy_date', today))
                td = _date_from_str(today)
                days_held = self.cal.trading_days_between(bd, td)
            except Exception: days_held = 0
            highest = max(pos.get('highest_close', pos.get('buy_price', 0)), cp)
            holdings.append({**pos, 'current_price': round(cp, 2),
                'unrealized_pnl_pct': round(pnl, 2), 'days_held': days_held,
                'is_zt_today': is_zt, 'highest_close': round(highest, 2),
                'trailing_drop_pct': round((cp - highest) / highest * 100, 1) if highest > 0 else 0,
                'awaiting_reversal': pos.get('awaiting_reversal', False)})

        total_trades = self._state.get('total_trades', 0)
        total_wins = self._state.get('total_wins', 0)
        finished = sorted(self._state['finished'].values(),
                          key=lambda x: x.get('sell_date', ''), reverse=True)
        init_cap = self._state.get('initial_capital', INITIAL_CAPITAL)
        pos_val = sum(h.get('shares', 0) * h.get('current_price', h.get('buy_price', 0)) for h in holdings)
        total_val = self._state.get('cash', init_cap) + pos_val
        cum_ret = (total_val / init_cap - 1) * 100 if init_cap > 0 else 0
        ph = self._state.get('portfolio_history', [])
        peak = init_cap; max_dd = 0.0
        for snap in ph:
            peak = max(peak, snap.get('total', 0))
            max_dd = max(max_dd, (peak - snap.get('total', 0)) / peak * 100 if peak > 0 else 0)
        total_return = round(sum(f.get('net_ret', 0) for f in finished), 2)
        avg_return = round(total_return / max(total_trades, 1), 2)
        market = self._get_market_state(today)

        return {'date': today, 'last_update': self._state.get('last_update', ''),
            'watch_list': watch, 'sim_buy_today': buy_today,
            'sim_sell_today': sell_today,
            'pending_sells': [{**ps, 'code': c} for c, ps in self._state.get('pending_sell', {}).items()],
            'holdings': holdings, 'finished_list': finished[:20],
            'summary': {
                'total_trades': total_trades, 'wins': total_wins,
                'losses': total_trades - total_wins,
                'win_rate': round(total_wins / max(total_trades, 1) * 100, 1),
                'total_return': total_return, 'avg_return': avg_return,
                'watch_count': len(watch), 'buy_count': len(buy_today),
                'sell_count': len(sell_today), 'holding_count': len(holdings),
                'ready_count': len(self._state['ready']),
                'pending_sell_count': len(self._state.get('pending_sell', {})),
                'cash': round(self._state.get('cash', init_cap), 2),
                'positions_value': round(pos_val, 2),
                'portfolio_value': round(total_val, 2),
                'initial_capital': init_cap,
                'cumulative_return': round(cum_ret, 2),
                'max_drawdown': round(max_dd, 2),
                'market_bull': market.get('is_bull', True),
            }}

    def record_buy(self, code: str, actual_price: float = None, buy_date: str = None) -> bool:
        if code not in self._state['ready']: return False
        info = self._state['ready'].pop(code)
        bp = actual_price or info.get('buy_price', 0)
        shares = int(INITIAL_CAPITAL * PER_POSITION_PCT / max(bp, 0.01) / 100) * 100
        if shares < 100: shares = 100
        cost = shares * bp * (1 + BUY_COMMISSION)
        if cost > self._state.get('cash', INITIAL_CAPITAL): return False
        self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) - cost
        info.update(status='holding', buy_date=buy_date or _today_str(),
                    buy_price=round(bp, 2), shares=shares,
                    had_zt=False, highest_close=bp, awaiting_reversal=False)
        self._state['holding'][code] = info; self._save()
        return True

    def record_sell(self, code: str, sell_price: float, sell_date: str = None) -> bool:
        pos = None
        if code in self._state['holding']: pos = self._state['holding'].pop(code)
        elif code in self._state.get('pending_sell', {}):
            pos = self._state['pending_sell'].pop(code).get('hold_info', {})
            if code in self._state['holding']: del self._state['holding'][code]
        else: return False
        bp = pos.get('buy_price', 0); shares = pos.get('shares', 0)
        gr = (sell_price - bp) / bp if bp > 0 else 0; nr = gr - TOTAL_COST
        if shares > 0: self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) + shares * sell_price * (1 - SELL_COST)
        pos.update(status='finished', sell_date=sell_date or _today_str(),
                   sell_price=sell_price, gross_ret=round(gr * 100, 2),
                   net_ret=round(nr * 100, 2), is_win=nr > 0)
        self._state['finished'][code] = pos
        self._state['total_trades'] = self._state.get('total_trades', 0) + 1
        if nr > 0: self._state['total_wins'] = self._state.get('total_wins', 0) + 1
        self._save(); return True

    def update_finished(self, code: str, updates: dict) -> bool:
        """更新已完成交易记录中的字段（含重算统计）。"""
        if code not in self._state['finished']: return False
        fin = self._state['finished'][code]
        old_is_win = fin.get('is_win', False)

        for key in ('buy_price', 'sell_price', 'net_ret', 'gross_ret',
                    'is_win', 'exit_reason', 'days_held', 'sell_date',
                    'buy_date', 'shares', 'name', 'score', 'sig_type'):
            if key in updates:
                if key in ('buy_price', 'sell_price', 'net_ret', 'gross_ret'):
                    fin[key] = float(updates[key]) if updates[key] is not None else 0
                elif key == 'is_win':
                    fin[key] = bool(updates[key])
                elif key in ('days_held', 'shares', 'score'):
                    fin[key] = int(updates[key]) if updates[key] is not None else 0
                else:
                    fin[key] = str(updates[key]) if updates[key] is not None else ''

        if 'buy_price' in updates or 'sell_price' in updates:
            bp = fin.get('buy_price', 0); sp = fin.get('sell_price', 0)
            if bp > 0:
                gr = (sp - bp) / bp; nr = gr - TOTAL_COST
                fin['gross_ret'] = round(gr * 100, 2)
                fin['net_ret'] = round(nr * 100, 2)
                fin['is_win'] = nr > 0

        new_is_win = fin.get('is_win', False)
        if old_is_win != new_is_win:
            if old_is_win: self._state['total_wins'] = max(0, self._state.get('total_wins', 0) - 1)
            if new_is_win: self._state['total_wins'] = self._state.get('total_wins', 0) + 1
        self._save(); return True

    def delete_finished(self, code: str) -> bool:
        """删除已完成交易记录并更新统计。"""
        if code not in self._state['finished']: return False
        info = self._state['finished'].pop(code)
        self._state['total_trades'] = max(0, self._state.get('total_trades', 0) - 1)
        if info.get('is_win'): self._state['total_wins'] = max(0, self._state.get('total_wins', 0) - 1)
        self._save(); return True

    def get_all_finished(self) -> list:
        """获取所有已完成交易（按卖出日期倒序）。"""
        items = list(self._state['finished'].values())
        for item in items:
            item['code'] = item.get('code', '')
            if not item.get('name') or item['name'] == item.get('code', ''):
                item['name'] = self._get_name(item['code'])
        items.sort(key=lambda x: x.get('sell_date', ''), reverse=True)
        return items

    def delete_holding(self, code: str) -> bool:
        if code in self._state['holding']: del self._state['holding'][code]; self._save(); return True
        if code in self._state['ready']: del self._state['ready'][code]; self._save(); return True
        if code in self._state['finished']:
            info = self._state['finished'].pop(code)
            self._state['total_trades'] = max(0, self._state.get('total_trades', 0) - 1)
            if info.get('is_win'): self._state['total_wins'] = max(0, self._state.get('total_wins', 0) - 1)
            self._save(); return True
        return False
