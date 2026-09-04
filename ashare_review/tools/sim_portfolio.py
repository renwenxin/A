"""
VOL180 突破战法 — 模拟持仓管理器

选股: 沪深主板 · 年涨停>10 · 非ST · 距60日高点(压力位)≤10%
买入: 收盘突破压力位 + 成交量 > MAVOL180 → 次日开盘买入
卖出: 连板持有 / 断板卖出 / 最多3天

状态机:
  WATCH → BUY_SIGNAL → HOLDING → FINISHED
"""
import json, os, sys, struct, threading
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ashare_review.data.tdx_reader import TdxReader, RECORD_SIZE
from ashare_review.analysis.indicators import calc_ma, calc_zigzag_find_top_line

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
STATE_FILE = os.path.join(DATA_DIR, 'sim_vol180_state.json')

# 进程内互斥锁：串行化状态文件的读-改-写（配合原子替换防止并发损坏）
_STATE_LOCK = threading.Lock()
CACHE_FILE = os.path.join(DATA_DIR, 'sim_vol180_cache.json')
LIMIT_UP_POOL_FILE = os.path.join(DATA_DIR, 'limit_up_pool.json')  # 年涨停≥10候选池

# ─── 常量 ───
FEE = 0.0015
SLIPPAGE = 0.002
TOTAL_COST = FEE + SLIPPAGE
MAX_HOLD_DAYS = 5            # V3: 最大持有 5 天
MIN_LIMIT_UP_COUNT = 10       # 年涨停≥10次
PRESSURE_DIST_PCT = 10.0       # 距压力位≤10%
MAVOL_PERIOD = 180
MAVOL_MULTIPLIER = 1.2         # 量>MAVOL180×1.2

# ── 追高上限（启动突破教学） ──
# 视频: 突破瞬间 8 个点以下才做（"突破压力位大于10%的追高标的不做"）
# 页面模板/V4 池已确立口径: 10cm ≤6% / 20cm ≤8% / 30cm ≤30%
CHASE_LIMIT_PCT = 6.0          # 10cm 主板: 突破后累计涨幅 >6% → 放弃
CHASE_LIMIT_GEM_PCT = 8.0      # 20cm 创业板/科创板
CHASE_LIMIT_BJ_PCT = 30.0      # 30cm 北交所

# ── 资金/仓位模型（与回测一致） ──
INITIAL_CAPITAL = 1_000_000.0  # 初始资金 100万
MAX_POSITIONS = 10             # 最大持仓数
MAX_NEW_PER_DAY = 3            # 每天最多新开仓
PER_POSITION_PCT = 0.10        # 单票仓位 10%
BUY_COMMISSION = 0.0003        # 买入佣金万3
SELL_COST = 0.0008             # 卖出成本（佣金+印花税）

# ── 斐波那契均线纪律（V3 新增） ──
FIB_MA_PERIODS = [13, 34, 55, 89]     # 只做 13 日线上；破 34/55 谨慎；破 89 休息
FIB_MA13 = 13
FIB_MA34 = 34
FIB_MA55 = 55
FIB_MA89 = 89
PULLBACK_MA_PERIODS = [5, 10, 20]     # 回踩低吸：5/10/20 日线附近
PULLBACK_BIAS_PCT = 3.0               # 乖离 < 3%
PULLBACK_VOL_RATIO = 0.6              # 量 < 前5日均量 × 60%

# ── 板块中军判定（V3 新增） ──
ZHONGJUN_AMOUNT = 2_000_000_000       # 中军成交额阈值: 20 亿元（与 daily_pool 一致）
ZHONGJUN_UP_PCT = 5.0                 # 趋势向上近似: 当日涨幅 ≥ 5%（大阳）


def _today_str() -> str:
    return date.today().strftime('%Y-%m-%d')


def _date_from_str(s: str) -> date:
    if isinstance(s, date):
        return s
    return datetime.strptime(s[:10], '%Y-%m-%d').date()


def fib_state(close, ma13, ma34, ma55, ma89):
    """斐波那契均线状态（优先级从高到低，互斥）:
    - rest:    close < MA89 → 休息(硬过滤)
    - caution: close < MA34 或 close < MA55 (且 ≥ MA89) → 谨慎参与
    - ok:      其余 → 可做
    语义: 缺失的均线不参与判定（该条规则视为不触发）；close 为 None 视为 ok。
    ma13 保留为入参仅供调用方对齐签名，本状态分档不使用它
    （13 日线作为买入硬门槛在 Task 4 处理）。
    """
    if close is None:
        return 'ok'
    if ma89 is not None and close < ma89:
        return 'rest'
    if ((ma34 is not None and close < ma34)
            or (ma55 is not None and close < ma55)):
        return 'caution'
    return 'ok'


def pullback_metrics(latest: dict) -> dict:
    """从 _read_latest 输出计算回踩低吸指标（缺字段时返回空 dict = 不命中）。

    Returns:
        {dist_min_pct, vol_ratio_5, has_lower_shadow, has_bottom_fractal}
    """
    close = latest.get('close')
    if close is None:
        return {}
    ma_vals = [latest.get(f'ma{p}') for p in PULLBACK_MA_PERIODS]
    valid = [m for m in ma_vals if m]
    dists = [abs(close / m - 1) * 100 for m in valid]
    if not dists:
        return {}
    # 下影线: 最近一根 下影 ≥ 实体 × 0.5（含十字星/大长腿）
    tail = latest.get('tail') or []
    has_lower_shadow = False
    has_bottom_fractal = False
    if len(tail) >= 1:
        bar = tail[-1]
        body = abs(bar['c'] - bar['o'])
        lower_shadow = min(bar['o'], bar['c']) - bar['l']
        if body > 0:
            has_lower_shadow = lower_shadow >= body * 0.5
        else:
            has_lower_shadow = lower_shadow > 0  # 十字星带下影
    if len(tail) >= 3:
        b1, b2, b3 = tail[-3], tail[-2], tail[-1]
        has_bottom_fractal = (b2['l'] < b1['l'] and b2['l'] < b3['l']
                              and b2['h'] < b1['h'] and b2['h'] < b3['h'])
    vol = latest.get('vol')
    vol_ma5 = latest.get('vol_ma5_prev') or 0
    vol_ratio_5 = (vol / vol_ma5) if (vol is not None and vol_ma5 > 0) else None
    return {
        'dist_min_pct': round(min(dists), 2),
        'vol_ratio_5': round(vol_ratio_5, 2) if vol_ratio_5 is not None else None,
        'has_lower_shadow': has_lower_shadow,
        'has_bottom_fractal': has_bottom_fractal,
    }


def pullback_signal_pass(latest: dict, zhongjun) -> Optional[dict]:
    """回踩低吸三条件 AND:
    1. 板块中军(zhongjun=True 命中; None=数据缺失不拦截; False=拦截)
    2. 回踩: 0 <= dist_min_pct < 3%（含恰好回踩到均线）
    3. 缩量企稳: vol_ratio_5 < 0.6 且 (下影线 or 底分型)
    """
    if zhongjun is False:
        return None
    m = pullback_metrics(latest)
    if not m or m.get('dist_min_pct') is None or m['dist_min_pct'] >= PULLBACK_BIAS_PCT:
        return None
    vr = m.get('vol_ratio_5')
    if vr is None or vr >= PULLBACK_VOL_RATIO:
        return None
    if not (m.get('has_lower_shadow') or m.get('has_bottom_fractal')):
        return None
    reasons = [f"回踩MA(乖离{m['dist_min_pct']}%)"]
    if m.get('has_lower_shadow'):
        reasons.append('下影线企稳')
    if m.get('has_bottom_fractal'):
        reasons.append('底分型')
    reasons.append(f"缩量{vr:.0%}")
    return {'zhongjun': zhongjun, 'reasons': reasons, 'metrics': m}


def sector_has_zhongjun(members: list, spot_rows: dict) -> bool:
    """板块是否有中军: members 中任一票 当日涨幅≥5% 且 成交额≥20亿。"""
    for code in members:
        r = spot_rows.get(code)
        if not r:
            continue
        try:
            chg = float(r.get('chg', 0))
            amount = float(r.get('amount', 0))
        except (TypeError, ValueError):
            continue
        if chg >= ZHONGJUN_UP_PCT and amount >= ZHONGJUN_AMOUNT:
            return True
    return False


def zhongjun_sector_of(code: str, industry_map: dict, concept_map: dict,
                       spot_rows: dict):
    """code 所属板块是否有中军。

    板块归属规则: 行业(industry_map)为主 —— 行业组内有中军即 True；
    行业缺失 或 行业组无中军 → 用 code 命中的所有概念成员(concept_map)并集补位判定。
    Returns: True=有中军 / False=明确无 / None=无 spot 数据(放行,不拦截)。
    """
    if not spot_rows:
        return None
    industry = industry_map.get(code, '')
    if industry:
        members = [c for c, v in industry_map.items() if v == industry and c != code]
        if sector_has_zhongjun(members or [code], spot_rows):
            return True
    # 概念补位：无行业 或 行业组无中军时，用 code 命中的所有概念成员并集再判一次
    concept_members = set()
    for name, meta in concept_map.items():
        m = (meta.get('members') or {}) if isinstance(meta, dict) else {}
        if code in m:
            concept_members.update(k for k in m.keys() if k != code)
    if concept_members and sector_has_zhongjun(list(concept_members), spot_rows):
        return True
    return False if (industry or concept_members) else None


class Vol180SimPortfolio:
    """VOL180 突破战法模拟持仓"""

    def __init__(self):
        self.tdx = TdxReader()
        from ..risk.store import RiskStore
        self._risk = RiskStore()
        os.makedirs(DATA_DIR, exist_ok=True)
        self._state = self._load()
        self._name_cache: Dict[str, str] = {}

    # ── 持久化 ──

    def _load(self) -> dict:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                # ── 迁移: 确保新版字段存在 ──
                state.setdefault('pending_sell', {})
                state.setdefault('cash', INITIAL_CAPITAL)
                state.setdefault('initial_capital', INITIAL_CAPITAL)
                state.setdefault('portfolio_history', [])
                state.setdefault('max_positions', MAX_POSITIONS)
                state.setdefault('per_position_pct', PER_POSITION_PCT)
                state.setdefault('pullback', [])
                return state
            except Exception:
                pass
        return {
            'watch': {},
            'ready': {},
            'pending_sell': {},
            'holding': {},
            'finished': {},
            'pullback': [],
            'last_update': '',
            'cache_date': '',
            'total_trades': 0,
            'total_wins': 0,
            'cash': INITIAL_CAPITAL,
            'initial_capital': INITIAL_CAPITAL,
            'portfolio_history': [],
            'max_positions': MAX_POSITIONS,
            'per_position_pct': PER_POSITION_PCT,
        }

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with _STATE_LOCK:
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)  # 原子替换，避免半写文件

    # ── 工具 ──

    NAME_CACHE_FILE = os.path.join(DATA_DIR, 'stock_name_map.json')

    def _load_name_map(self) -> dict:
        """加载名称映射（仅从缓存读，不调akshare避免卡死）。"""
        if os.path.exists(self.NAME_CACHE_FILE):
            try:
                with open(self.NAME_CACHE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and len(data) > 1000:
                    return data
            except Exception:
                pass
        return {}

    def _get_name(self, code: str) -> str:
        if code in self._name_cache:
            return self._name_cache[code]
        name_map = self._load_name_map()
        name = name_map.get(code, code)
        self._name_cache[code] = name
        return name

    @staticmethod
    def _limit_threshold(code: str) -> float:
        code = str(code).zfill(6)
        if code.startswith(('300', '301', '688')): return 0.199
        if code.startswith(('8', '4')): return 0.299
        return 0.095

    @staticmethod
    def _chase_limit_pct(code: str) -> float:
        """追高上限(%)：已突破压力位的累计涨幅超过该比例 → 放弃（追高不做）。

        规则来源: 启动突破教学视频（"八个点以下才做"、"突破压力位>10%的追高不做"）
        + 页面模板/V4 池已确立口径: 10cm ≤6% / 20cm ≤8% / 30cm ≤30%。
        """
        c = str(code).zfill(6)
        if c.startswith(('300', '301', '688')):
            return CHASE_LIMIT_GEM_PCT
        if c.startswith(('8', '4')):
            return CHASE_LIMIT_BJ_PCT
        return CHASE_LIMIT_PCT

    @staticmethod
    def _is_main_board(code: str) -> bool:
        """沪深主板: 60xxxx, 00xxxx, 001xxx, 002xxx"""
        code = str(code).zfill(6)
        return code.startswith(('60', '00', '001', '002'))

    @staticmethod
    def _fast_count_limit_ups(code: str, tdx, lookback: int = 250) -> int:
        """快速数涨停（只读收盘价，不建DataFrame不算MA）。"""
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8', '4')):
            market = 'bj'
        try:
            fpath = os.path.join(tdx._market_dir(market), f'{market}{code}.day')
            if not os.path.exists(fpath):
                return 0
            fsize = os.path.getsize(fpath)
            read_bytes = min(RECORD_SIZE * (lookback + 10), fsize)
            with open(fpath, 'rb') as f:
                f.seek(fsize - read_bytes)
                raw = f.read(read_bytes)
            n = len(raw) // RECORD_SIZE
            if n < 20:
                return 0
            count = 0
            prev_close = 0
            for j in range(n):
                offset = j * RECORD_SIZE
                cl_int = struct.unpack_from('I', raw, offset + 16)[0]
                close = cl_int / 100.0
                if prev_close > 0:
                    chg = (close - prev_close) / prev_close
                    if chg >= 0.093:
                        count += 1
                prev_close = close
            return count
        except Exception:
            return 0

    def _read_daily(self, code: str, up_to_date: str = None, min_len: int = None) -> Optional[pd.DataFrame]:
        """统一日线读取入口：market 判定 + up_to_date 双格式解析 + trade_date 过滤 + try/except。

        只返回原始日线（不含均线），供调用方按需计算 MA。
        min_len: 可选最少行数（过滤后），None 表示不限制（卖出场景用）。
        """
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8', '4')):
            market = 'bj'
        try:
            df = self.tdx.read_daily(code, market)
            if df is None or df.empty:
                return None
            if up_to_date:
                try:
                    target = datetime.strptime(up_to_date, '%Y%m%d').date()
                except ValueError:
                    try:
                        target = datetime.strptime(up_to_date, '%Y-%m-%d').date()
                    except ValueError:
                        return df
                df = df[df['trade_date'].apply(
                    lambda x: (x.date() if hasattr(x, 'date') else x) <= target
                )]
            if min_len is not None and len(df) < min_len:
                return None
            return df if not df.empty else None
        except Exception:
            return None

    def _read_stock(self, code: str, up_to_date: str = None) -> Optional[pd.DataFrame]:
        """买入检测用日线：至少 60 根，并计算 MA5/MA10/MAVOL180。"""
        df = self._read_daily(code, up_to_date=up_to_date, min_len=60)
        if df is None:
            return None
        df = calc_ma(df, [5, 10])
        df['mavol180'] = df['volume'].rolling(MAVOL_PERIOD).mean()
        # SWS 生命线按公式还原：A = MAX(1, 100*SUM(VOL,5)/(3*CAPITAL))
        from ..analysis.indicators import calc_swl_sws
        df = calc_swl_sws(df, capital_hands=self._capital_hands(code))
        return df

    def _capital_hands(self, code: str) -> Optional[float]:
        """该股流通股本(手)，用于 SWS；无数据返回 None（用默认值）。"""
        try:
            from ..data.float_share import load_capital_hands_map
            m = load_capital_hands_map()
            v = m.get(str(code).zfill(6))
            return float(v) if v and v > 0 else None
        except Exception:
            return None

    def _read_sell_df(self, code: str, up_to_date: str = None) -> Optional[pd.DataFrame]:
        """卖出检查用日线：读原始日线并按 up_to_date 过滤，不强制 MAVOL/最少 60 根。

        卖出规则只用 close/open/volume，不依赖均线；放宽长度约束后，
        既能省去历史均线计算，也支持测试注入的少量可控日线。
        """
        return self._read_daily(code, up_to_date=up_to_date)

    def _read_latest(self, code: str) -> Optional[dict]:
        """只读最近一根K线 + MAVOL180 + 斐波那契均线 + 回踩指标（日常扫描用）。"""
        market = 'sh' if str(code).startswith('6') else 'sz'
        if str(code).startswith(('8', '4')):
            market = 'bj'
        try:
            fpath = os.path.join(self.tdx._market_dir(market), f'{market}{code}.day')
            if not os.path.exists(fpath):
                return None
            fsize = os.path.getsize(fpath)
            if fsize < RECORD_SIZE * MAVOL_PERIOD:
                return None
            read_bytes = min(RECORD_SIZE * 200, fsize)
            with open(fpath, 'rb') as f:
                f.seek(fsize - read_bytes)
                raw = f.read(read_bytes)
            n = len(raw) // RECORD_SIZE
            if n < 2:
                return None
            closes, volumes = [], []
            tail_bars = []  # 近 3 根 {o,h,l,c}
            for j in range(n):
                offset = j * RECORD_SIZE
                dt, op, hi, lo, cl_int, amt, vol, _ = struct.unpack(
                    'IIIIIfII', raw[offset:offset + RECORD_SIZE])
                closes.append(cl_int / 100.0)
                volumes.append(float(vol))
                if j >= n - 3:
                    tail_bars.append({
                        'o': op / 100.0, 'h': hi / 100.0,
                        'l': lo / 100.0, 'c': cl_int / 100.0,
                    })
            closes_np = np.array(closes)
            volumes_np = np.array(volumes)
            close = float(closes_np[-1])
            vol = float(volumes_np[-1])
            prev_close = float(closes_np[-2]) if len(closes_np) >= 2 else close
            mavol180 = float(np.mean(volumes_np[-MAVOL_PERIOD:])) \
                if len(volumes_np) >= MAVOL_PERIOD else 0

            def _ma(p):
                return float(np.mean(closes_np[-p:])) if len(closes_np) >= p else None

            vol_ma5_prev = float(np.mean(volumes_np[-6:-1])) \
                if len(volumes_np) >= 6 else 0
            latest = {
                'close': round(close, 2),
                'prev_close': round(prev_close, 2),
                'vol': int(vol),
                'mavol180': round(mavol180, 0),
                'vol_ma5_prev': round(vol_ma5_prev, 0),
                'tail': tail_bars,
            }
            for p in [5, 10, 20, 13, 34, 55, 89]:
                latest[f'ma{p}'] = round(_ma(p), 3) if _ma(p) is not None else None
            return latest
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # 板块中军判定（V3）: 行业主 + 概念补位 + 按日磁盘缓存
    # ═══════════════════════════════════════════════════════════════════════════

    def _zhongjun_cache(self, trade_date: str, codes: list) -> dict:
        """当日板块中军缓存(只算请求的 codes): {code: True/False}；无 spot 数据返回 {}。

        Args:
            trade_date: YYYY-MM-DD（磁盘缓存键）
            codes: 本批候选代码（避免全市场概念扫描）
        """
        codes = [c for c in (codes or []) if c]
        if not codes:
            return {}
        key = (trade_date or '').replace('-', '')
        mem = getattr(self, '_zj_cache', None)
        if mem is None:
            mem = {}
            self._zj_cache_date = key
            self._zj_cache = mem
        if getattr(self, '_zj_cache_date', None) != key:
            mem.clear()
            self._zj_cache_date = key
        disk = os.path.join(DATA_DIR, 'cache', f'zhongjun_sector_{key}.json')
        disk_cache = {}
        if os.path.exists(disk):
            try:
                with open(disk, 'r', encoding='utf-8') as f:
                    disk_cache = json.load(f)
            except Exception:
                disk_cache = {}
        missing = [c for c in codes if c not in mem and c not in disk_cache]
        if missing:
            try:
                from ..data.akshare_fetcher import AkshareFetcher
                df = AkshareFetcher().get_spot_df()
            except Exception:
                df = None
            if df is not None and not df.empty:
                imap, cmap = self._load_industry_map(), self._load_concept_map()
                rows = {}
                for _, row in df.iterrows():
                    try:
                        c = str(row.get('代码', '')).zfill(6)
                        rows[c] = {'chg': float(row.get('涨跌幅', 0) or 0),
                                   'amount': float(row.get('成交额', 0) or 0)}
                    except Exception:
                        continue
                # 降级快照(竞价/TDX回退)成交额恒为0 → 视同数据缺失，不判 False、不写盘
                if rows and any(r['amount'] > 0 for r in rows.values()):
                    for c in missing:
                        disk_cache[c] = zhongjun_sector_of(c, imap, cmap, rows)
                    os.makedirs(os.path.dirname(disk), exist_ok=True)
                    try:
                        with open(disk, 'w', encoding='utf-8') as f:
                            json.dump(disk_cache, f, ensure_ascii=False)
                    except Exception:
                        pass
        out = {c: disk_cache.get(c) for c in codes if c in disk_cache}
        mem.update(out)
        return out

    def _load_industry_map(self) -> dict:
        if getattr(self, '_imap', None) is not None:
            return self._imap
        out = {}
        p = os.path.join(DATA_DIR, 'industry_map.json')
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    out = json.load(f)
            except Exception:
                pass
        self._imap = out
        return out

    def _load_concept_map(self) -> dict:
        if getattr(self, '_cmap', None) is not None:
            return self._cmap
        out = {}
        p = os.path.join(DATA_DIR, 'concept_map.json')
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                out = data.get('concepts', data) if isinstance(data, dict) else {}
            except Exception:
                pass
        self._cmap = out
        return out

    # ═══════════════════════════════════════════════════════════════════════════
    # 候选池: 年涨停>=10 + 主板 + 非ST（预计算，每周刷新一次）
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_eligible_pool(self) -> list:
        """获取年涨停>=10的候选池（优先缓存，过期重建）。"""
        if os.path.exists(LIMIT_UP_POOL_FILE):
            try:
                with open(LIMIT_UP_POOL_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                pool = data.get('pool', [])
                updated = data.get('updated', '')
                if pool and updated:
                    cache_date = _date_from_str(updated)
                    if (date.today() - cache_date).days < 7:
                        print(f'[SimPortfolio] 使用候选池缓存: {len(pool)} 只 (更新于 {updated})')
                        return pool
            except Exception:
                pass
        return self._build_limit_up_pool()

    def _build_limit_up_pool(self) -> list:
        """全市场扫描，构建年涨停>=10+主板+非ST的候选池（一次性，缓存7天）。"""
        print('[SimPortfolio] 构建候选池 (年涨停>=10 + 主板 + 非ST)...')
        name_map = self._load_name_map()
        stocks = self.tdx.list_stocks()
        print(f'[SimPortfolio] 全市场共 {len(stocks)} 只，开始逐只检查...')
        pool = []
        import time as _time
        t0 = _time.time()

        for si, (code, market) in enumerate(stocks):
            if code.startswith(('8', '4')) or not self._is_main_board(code):
                continue
            stock_name = name_map.get(code, '')
            # 不在名称缓存中 → 可能是ST（akshare构建时已剔除），保守跳过
            if not stock_name:
                continue
            if 'ST' in stock_name or '*ST' in stock_name:
                continue

            if (si + 1) % 500 == 0:
                e = _time.time() - t0
                print(f'  池构建 {si+1}/{len(stocks)} ({e:.0f}s, 已找到{len(pool)}只)...')

            try:
                # 第1步: 快速数涨停（只读二进制，不建DataFrame）
                limit_count = self._fast_count_limit_ups(code, self.tdx, 250)
                if limit_count < MIN_LIMIT_UP_COUNT:
                    continue

                # 第2步: 通过涨停检查后读文件+算找顶线（截断250行加速）
                df = self._read_stock(code)
                if df is None or df.empty:
                    continue
                if len(df) > 250:
                    df = df.iloc[-250:].copy()
                try:
                    df = calc_zigzag_find_top_line(df)
                    # 取最近一个波峰(_nn)的高点作为压力位，不用DRAWLINE外推值
                    nn_mask = df['_nn'] > 0
                    if nn_mask.any():
                        last_nn_idx = df.index[nn_mask][-1]
                        top_line = float(df.loc[last_nn_idx, 'high'])
                    else:
                        top_line = float(df['high'].rolling(60).max().shift(1).iloc[-1])
                    if pd.isna(top_line) or top_line <= 0:
                        top_line = float(df['high'].rolling(60).max().shift(1).iloc[-1])
                except Exception:
                    top_line = float(df['high'].rolling(60).max().shift(1).iloc[-1])
                pool.append({
                    'code': code, 'market': market,
                    'name': stock_name if stock_name != code else self._get_name(code),
                    'limit_count': limit_count,
                    'pressure': round(top_line, 2),
                })
            except Exception:
                continue

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LIMIT_UP_POOL_FILE, 'w', encoding='utf-8') as f:
            json.dump({'pool': pool, 'updated': _today_str(),
                        'count': len(pool)}, f, ensure_ascii=False)
        print(f'[SimPortfolio] 候选池构建完成: {len(pool)} 只 ({_time.time() - t0:.0f}s)')
        return pool

    # ═══════════════════════════════════════════════════════════════════════════
    # 市场环境
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_market_state(self, trade_date: str = None) -> dict:
        """获取上证指数 MA60 状态（熊市过滤用）。"""
        td = trade_date or _today_str()
        try:
            sh_df = self.tdx.read_daily('999999', 'sh')
            if sh_df is not None and len(sh_df) >= 60:
                sh_df['ma60'] = sh_df['close'].rolling(60).mean()
                target = datetime.strptime(td.replace('-', ''), '%Y%m%d').date()
                mask = sh_df['trade_date'].apply(
                    lambda x: (x.date() if hasattr(x, 'date') else x) <= target
                )
                sub = sh_df[mask]
                if len(sub) >= 60:
                    close = float(sub['close'].iloc[-1])
                    ma60 = float(sub['ma60'].iloc[-1])
                    return {
                        'is_bull': close > ma60 and not pd.isna(ma60),
                        'sh_close': round(close, 2),
                        'sh_ma60': round(ma60, 2),
                    }
        except Exception:
            pass
        return {'is_bull': True, 'sh_close': 0, 'sh_ma60': 0}  # 默认允许

    # ═══════════════════════════════════════════════════════════════════════════
    # 核心: VOL180 选股 + 信号检测
    # ═══════════════════════════════════════════════════════════════════════════

    def screen_candidates(self, trade_date: str) -> List[dict]:
        """从候选池中筛选距找顶线<=10%的股票（压力位已缓存，无需重复算zigzag）。"""
        ds = trade_date.replace('-', '')
        candidates = []

        eligible = self._get_eligible_pool()
        total = len(eligible)
        print(f'[SimPortfolio] 从 {total} 只候选池中筛选...')

        for si, stock in enumerate(eligible):
            if (si + 1) % 100 == 0:
                print(f'  筛选 {si+1}/{total}...')
            code = stock['code']
            limit_count = stock.get('limit_count', 0)
            pressure = stock.get('pressure', 0)

            if pressure <= 0:
                continue

            try:
                # 只读最后200根K线（不需要全文件）
                latest = self._read_latest(code)
                if latest is None:
                    continue

                close = latest['close']
                vol = latest['vol']
                mavol180 = latest['mavol180']
                prev_close = latest.get('prev_close', 0)

                if mavol180 <= 0:
                    continue

                dist_pct = round((close - pressure) / pressure * 100, 1)

                # ── 斐波那契均线纪律: 破 89 休息 → 硬剔除 ──
                fs = fib_state(
                    close,
                    latest.get('ma13'), latest.get('ma34'),
                    latest.get('ma55'), latest.get('ma89'),
                )
                if fs == 'rest':
                    continue

                fib_extra = {
                    'fib_state': fs,
                    # 回踩指标所需字段一次带全（watch/breakout 行共用，供 pullback_metrics 消费）
                    'ma5': latest.get('ma5'), 'ma10': latest.get('ma10'),
                    'ma20': latest.get('ma20'),
                    'ma13': latest.get('ma13'), 'ma34': latest.get('ma34'),
                    'ma55': latest.get('ma55'), 'ma89': latest.get('ma89'),
                    'vol_ma5_prev': latest.get('vol_ma5_prev'),
                    'tail': latest.get('tail') or [],
                }

                if dist_pct > 0:
                    # 追高上限: 已突破压力位超过板块阈值 → 放弃
                    # (教学: "八个点以下才做" · 突破压力位>10%的追高不做)
                    if dist_pct > self._chase_limit_pct(code):
                        continue
                    candidates.append({
                        'code': code, 'close': round(close, 2),
                        'prev_close': round(prev_close, 2),
                        'top_line': pressure, 'dist_pct': dist_pct,
                        'vol': int(vol), 'mavol180': round(mavol180, 0),
                        'vol_ratio': round(vol / mavol180, 1) if mavol180 > 0 else 0,
                        'limit_count': limit_count, 'status': 'breakout',
                        **fib_extra,
                    })
                    continue

                if dist_pct < -PRESSURE_DIST_PCT:
                    continue

                candidates.append({
                    'code': code, 'close': round(close, 2),
                    'prev_close': round(prev_close, 2),
                    'top_line': pressure, 'dist_pct': dist_pct,
                    'vol': int(vol), 'mavol180': round(mavol180, 0),
                    'vol_ratio': round(vol / mavol180, 1) if mavol180 > 0 else 0,
                    'limit_count': limit_count, 'status': 'watching',
                    **fib_extra,
                })

            except Exception:
                continue

        return candidates

    def _scan_buy_signals(self, candidates: List[dict], mode: str = 'v1') -> List[dict]:
        """从候选池中检测今日触发买入信号的股票。

        买入条件:
          收盘价 > 压力位 + 成交量 > MAVOL180 x 1.2
        V2/V3额外过滤:
          距压力位 3-5% 死亡区间 → 跳过
          量比 >= 5x 过度放量 → 跳过
        V3: 熊市（上证<MA60）时评分门槛 ≥ 75
        """
        market = self._get_market_state()
        is_bull = market.get('is_bull', True)

        buy_signals = []
        for c in candidates:
            # ── 斐波那契纪律: 只做 13 日线上 · 休息票防御性拦截 ──
            if c.get('fib_state') == 'rest':
                continue
            if (c.get('ma13') is not None
                    and c.get('close') is not None
                    and c['close'] <= c['ma13']):
                continue
            if c['close'] > c['top_line'] and c['vol'] > c['mavol180'] * MAVOL_MULTIPLIER:
                vol_ratio = c.get('vol_ratio', 0)
                dist_pct = abs(c.get('dist_pct', 0))

                # ── 当日突破（CROSS 语义）: 昨日收盘未站上压力位 + 今日收盘突破 ──
                # 公式 DRAWICON(CROSS(C,找顶线),...) — 只抓"今天当天刚突破"的标的，
                # 排除突破后回踩/横盘（几天前突破、今日仍在压力位附近的票）。
                prev_close = c.get('prev_close', 0)
                if prev_close > 0 and prev_close > c['top_line'] * 1.001:
                    continue   # 昨日已站上压力位 → 非当日突破（突破后回踩）

                # ── 追高上限（双保险）: 已突破压力位超过板块阈值 → 放弃 ──
                # (教学: "八个点以下才做" · 突破压力位>10%的追高不做)
                if c.get('dist_pct', 0) > self._chase_limit_pct(c['code']):
                    continue

                # ── V2/V3 过滤规则 ──
                if mode in ('v2', 'v3'):
                    if 3 < dist_pct <= 5:       # 死亡区间
                        continue
                    if vol_ratio >= 5.0:          # 过度放量
                        continue

                score = 60
                if dist_pct >= 3: score += 10
                if vol_ratio >= 2.0: score += 10
                elif vol_ratio >= 1.5: score += 5
                if c.get('limit_count', 0) >= 20: score += 10
                elif c.get('limit_count', 0) >= 15: score += 5
                score += 10
                score = min(score, 100)

                # ── V3: 熊市过滤 ──
                if mode == 'v3' and not is_bull and score < 75:
                    continue

                c['score'] = score
                c['break_pct'] = round((c['close'] - c['top_line']) / c['top_line'] * 100, 1)
                buy_signals.append(c)

        buy_signals.sort(key=lambda x: x.get('score', 0), reverse=True)
        return buy_signals

    # ═══════════════════════════════════════════════════════════════════════════
    # 卖出检查 (VOL180规则)
    # ═══════════════════════════════════════════════════════════════════════════

    def _check_sell_vol180(self, code: str, position: dict, today: str) -> Optional[dict]:
        """卖出规则（与回测 _simulate_sell 完全一致）:
        0. 收盘价相对买入价跌幅 ≥ 6% → 止损卖出 [V2]
        1. 今日涨停 → 继续持有（标记有过涨停）
        2. 之前有过涨停 + 今日断板 → 卖出
        3. 始终没涨停 + 已持有>=3天 → 卖出
        4. 始终没涨停 + 持有<3天 → 继续等待
        """
        td_fmt = today.replace('-', '')
        buy_date = position.get('buy_date', '')
        buy_price = position.get('buy_price', 0)
        had_zt = position.get('had_zt', False)
        mode = position.get('mode', 'v1')

        df = self._read_sell_df(code, up_to_date=td_fmt)
        if df is None or df.empty:
            return None

        idx = len(df) - 1
        close = float(df['close'].iloc[idx])

        # 计算交易日持有天数
        try:
            from ashare_review.utils.calendar import TradingCalendar
            cal = TradingCalendar()
            bd = _date_from_str(buy_date)
            td = _date_from_str(today)
            trading_days = cal.trading_days_between(bd, td)
        except Exception:
            trading_days = 3

        # ── V2: 硬止损（读风控配置，默认 -6%） ──
        from ..risk.evaluate import stop_loss_pct
        stop = stop_loss_pct(self._risk.get('vol180')) / 100.0
        if buy_price > 0:
            loss_pct = (close - buy_price) / buy_price
            if loss_pct <= stop:
                return {
                    'sell_price': round(close, 2),
                    'sell_reason': f'止损{abs(stop*100):.0f}%',
                    'is_zt': False,
                    'days_held': trading_days,
                }

        # 检查今日是否涨停
        if idx >= 1:
            prev_close = float(df['close'].iloc[idx - 1])
            limit_pct = self._limit_threshold(code)
            today_chg = (close - prev_close) / prev_close if prev_close > 0 else 0
            is_zt_today = today_chg >= limit_pct
        else:
            is_zt_today = False

        # 今日涨停 → 继续持有，标记有过涨停
        if is_zt_today:
            position['had_zt'] = True
            return None

        # 不是涨停...
        if had_zt:
            return {
                'sell_price': round(close, 2),
                'sell_reason': '断板卖出',
                'is_zt': False,
                'days_held': trading_days,
            }
        elif trading_days >= MAX_HOLD_DAYS:
            return {
                'sell_price': round(close, 2),
                'sell_reason': '持有满3天',
                'is_zt': False,
                'days_held': trading_days,
            }
        else:
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # 卖出检查 V3: N字反包 + 移动止盈 + -6%止损
    # ═══════════════════════════════════════════════════════════════════════════

    def _check_sell_v3(self, code: str, position: dict, today: str) -> Optional[dict]:
        """V3 卖出规则:
        0. -6% 硬止损（保留 V2）
        1. 移动止盈: 从买入以来最高收盘价回落 > 5% → 卖出
        2. N字反包: 涨停后断板 → 等一天看反包，放量收阳→继续持有
        3. 持仓 ≥ 5 天兜底 → 到期卖出
        """
        td_fmt = today.replace('-', '')
        buy_date = position.get('buy_date', '')
        buy_price = position.get('buy_price', 0)
        had_zt = position.get('had_zt', False)

        # V3 只用 close/open/volume 列，不依赖均线 → 走 _read_sell_df（放宽长度约束）
        df = self._read_sell_df(code, up_to_date=td_fmt)
        if df is None or df.empty:
            return None

        idx = len(df) - 1
        close = float(df['close'].iloc[idx])

        # 更新最高收盘价（移动止盈基准）
        highest = position.get('highest_close', buy_price)
        highest = max(highest, close)
        position['highest_close'] = round(highest, 2)

        try:
            from ashare_review.utils.calendar import TradingCalendar
            cal = TradingCalendar()
            bd = _date_from_str(buy_date)
            td = _date_from_str(today)
            trading_days = cal.trading_days_between(bd, td)
        except Exception:
            trading_days = 3

        HOLD_MAX = 5  # V3: 最大持有 5 天（比 V2 宽松，给趋势空间）

        # ── V3 硬止损（读风控配置，默认 -6%，与 V2 相同） ──
        from ..risk.evaluate import stop_loss_pct
        stop = stop_loss_pct(self._risk.get('vol180')) / 100.0
        if buy_price > 0:
            loss_pct = (close - buy_price) / buy_price
            if loss_pct <= stop:
                return {
                    'sell_price': round(close, 2),
                    'sell_reason': f'止损{abs(stop*100):.0f}%',
                    'is_zt': False,
                    'days_held': trading_days,
                }

        # ── V3 移动止盈: 从最高回落 > 5% ──
        if highest > buy_price * 1.03:  # 至少盈利 3% 后才启用移动止盈
            pullback = (close - highest) / highest
            if pullback <= -0.05:
                return {
                    'sell_price': round(close, 2),
                    'sell_reason': f'移动止盈-5%(最高{highest:.2f}→现{close:.2f})',
                    'is_zt': False,
                    'days_held': trading_days,
                }

        # 检查今日是否涨停
        if idx >= 1:
            prev_close = float(df['close'].iloc[idx - 1])
            limit_pct = self._limit_threshold(code)
            today_chg = (close - prev_close) / prev_close if prev_close > 0 else 0
            is_zt_today = today_chg >= limit_pct
        else:
            is_zt_today = False

        # ── 今日涨停 → 继续持有 ──
        if is_zt_today:
            position['had_zt'] = True
            position['awaiting_reversal'] = False  # 涨停了，清除反包等待
            return None

        # ── N字反包: 有过涨停，今日断板 ──
        if had_zt:
            awaiting = position.get('awaiting_reversal', False)
            if not awaiting:
                # 第一天断板 → 不立即卖，等反包
                position['awaiting_reversal'] = True
                position['reversal_day_close'] = round(close, 2)
                if idx >= 1:
                    position['reversal_day_vol'] = int(float(df['volume'].iloc[idx]))
                return None  # 再持有一天
            else:
                # 第二天: 检查是否 N 字反包
                rev_close = position.get('reversal_day_close', close)
                rev_vol = position.get('reversal_day_vol', 0)
                today_vol = int(float(df['volume'].iloc[idx])) if idx >= 0 else 0
                # 反包条件: 今日收阳 + 放量（量 > 昨天）
                is_up = close > (float(df['open'].iloc[idx]) if idx >= 0 else close)
                vol_expand = today_vol > rev_vol

                if is_up and vol_expand:
                    # N字反包成功！清除标志，继续持有
                    position['awaiting_reversal'] = False
                    position['had_zt'] = True  # 视作新的启动
                    return None
                else:
                    # 反包失败 → 卖出
                    return {
                        'sell_price': round(close, 2),
                        'sell_reason': '涨停后断板离场',
                        'is_zt': False,
                        'days_held': trading_days,
                    }

        # ── 始终没涨停 + 持有 ≥ 5 天兜底 ──
        if trading_days >= HOLD_MAX:
            return {
                'sell_price': round(close, 2),
                'sell_reason': f'持有{trading_days}天到期',
                'is_zt': False,
                'days_held': trading_days,
            }

        # ── 移动止盈也适用于没涨停的情况 ──
        if highest > buy_price * 1.05:  # 浮盈 5%+ 才启用
            pullback = (close - highest) / highest
            if pullback <= -0.05:
                return {
                    'sell_price': round(close, 2),
                    'sell_reason': f'移动止盈-5%(最高{highest:.2f})',
                    'is_zt': False,
                    'days_held': trading_days,
                }

        return None

    def _check_auction_v3(self, code: str, buy_date: str) -> Optional[str]:
        """V3 竞价确认: 开盘前复查，返回 None=通过, str=拒绝原因。

        检查:
          1. 低开 > 3% → "竞价低开>3%"
          2. 竞价量缩 > 50%（相比前一交易日）→ "竞价缩量>50%"
          3. 非一字跌停 → None（通过）
        """
        td_fmt = buy_date.replace('-', '')
        df = self._read_stock(code, up_to_date=td_fmt)
        if df is None or df.empty or len(df) < 2:
            return None  # 数据不足，不拦截

        idx = len(df) - 1
        open_p = float(df['open'].iloc[idx])
        prev_close = float(df['close'].iloc[idx - 1])
        vol_today = float(df['volume'].iloc[idx]) if idx >= 0 else 0
        vol_prev = float(df['volume'].iloc[idx - 1]) if idx >= 1 else 0

        # 低开 > 3%
        open_chg = (open_p - prev_close) / prev_close * 100 if prev_close > 0 else 0
        if open_chg < -3:
            return f'竞价低开{open_chg:.1f}%'

        # 竞价缩量 > 50%（用全天量近似，因为 sim 环境无实时竞价数据）
        if vol_prev > 0 and vol_today < vol_prev * 0.5:
            return '竞价缩量>50%'

        return None  # 通过

    # ═══════════════════════════════════════════════════════════════════════════
    # 对外 API
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_pullback_list(self, cand_rows: list, trade_date: str) -> list:
        """候选行 → 回踩低吸信号（三条件 AND）。中军 True/None=放行(标注)，False=拦截。"""
        codes = [c['code'] for c in cand_rows]
        zj = self._zhongjun_cache(trade_date, codes) if codes else {}
        out = []
        for c in cand_rows:
            code = c['code']
            if c.get('fib_state') == 'rest':
                continue
            zhongjun = zj.get(code) if zj else None
            if zhongjun is False:
                continue
            sig = pullback_signal_pass(c, zhongjun)
            if sig is None:
                continue
            out.append({
                'code': code,
                'name': c.get('name') or self._get_name(code),
                'close': c.get('close'),
                'ma5': c.get('ma5'), 'ma10': c.get('ma10'), 'ma20': c.get('ma20'),
                'fib_state': c.get('fib_state', 'ok'),
                'dist_min_pct': sig['metrics'].get('dist_min_pct'),
                'vol_ratio_5': sig['metrics'].get('vol_ratio_5'),
                'zhongjun': zhongjun,           # True / None(数据缺失)
                'has_lower_shadow': sig['metrics'].get('has_lower_shadow'),
                'has_bottom_fractal': sig['metrics'].get('has_bottom_fractal'),
                'reasons': sig['reasons'],
            })
        out.sort(key=lambda x: (x.get('zhongjun') is not True,
                                 99 if x.get('dist_min_pct') is None
                                 else x.get('dist_min_pct')))
        return out

    def run_daily(self, trade_date: str = None, mode: str = 'v1',
                  force_rebuild_pool: bool = False) -> dict:
        """每日运行: 执行挂单卖出 → 刷新观察池 → 检测买入信号 → 检查卖出。

        V3 资金模型: 初始 100 万，最大 10 仓，单票 10%，每天最多新开 3 只。
        """
        td = trade_date or _today_str()
        td_dt = _date_from_str(td)

        if td_dt.weekday() >= 5:
            self._state['last_update'] = td
            self._save()
            return {'date': td, 'note': '非交易日', 'watch': 0, 'buys': 0, 'sells': 0}

        # ── Step 0: 市场环境 ──
        market = self._get_market_state(td)
        is_bull = market.get('is_bull', True)

        # ── Step 0b: 强制重建候选池 ──
        if force_rebuild_pool:
            print('[SimPortfolio] 强制重建候选池...')
            self._build_limit_up_pool()

        # ── Step 0c: 执行挂单卖出（用今日开盘价） ──
        sells_executed = 0
        for code in list(self._state.get('pending_sell', {}).keys()):
            ps = self._state['pending_sell'][code]
            if ps.get('sell_date', '') <= td:
                # 用今日开盘价卖出
                pos = ps['hold_info']
                try:
                    df_today = self._read_stock(code, up_to_date=td.replace('-', ''))
                    if df_today is not None and not df_today.empty:
                        sell_price = float(df_today['open'].iloc[-1])
                    else:
                        sell_price = ps.get('close_price', pos.get('buy_price', 0))
                except Exception:
                    sell_price = ps.get('close_price', pos.get('buy_price', 0))

                buy_price = pos.get('buy_price', 0)
                shares = pos.get('shares', 0)
                gross_ret = (sell_price - buy_price) / buy_price if buy_price > 0 else 0
                net_ret = gross_ret - TOTAL_COST

                # 回笼资金
                if shares > 0:
                    self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) + \
                        shares * sell_price * (1 - SELL_COST)

                self._state['finished'][code] = {
                    **pos,
                    'status': 'finished',
                    'sell_date': td,
                    'sell_price': round(sell_price, 2),
                    'gross_ret': round(gross_ret * 100, 2),
                    'net_ret': round(net_ret * 100, 2),
                    'is_win': net_ret > 0,
                    'exit_reason': ps.get('sell_reason', ''),
                    'days_held': ps.get('days_held', 0),
                }
                self._state['total_trades'] = self._state.get('total_trades', 0) + 1
                if net_ret > 0:
                    self._state['total_wins'] = self._state.get('total_wins', 0) + 1
                del self._state['pending_sell'][code]
                if code in self._state['holding']:
                    del self._state['holding'][code]
                sells_executed += 1

        # ── Step 1: 刷新候选池（观察池） ──
        candidates = self.screen_candidates(td)
        watch_list = [c for c in candidates if c.get('status') == 'watching']
        already_broken = [c for c in candidates if c.get('status') == 'breakout']
        print(f'[SimPortfolio] 观察池: {len(watch_list)} 只, 已突破: {len(already_broken)} 只')

        # 更新观察池状态 — 每次扫描清空旧信号，强制重新评估
        self._state['watch'] = {}
        self._state['ready'] = {}  # ← 清空过期买入信号，防止旧数据残留
        for c in watch_list:
            code = c['code']
            entry = dict(c)  # 透传 close/top_line/dist_pct/vol_ratio/limit_count/fib_* /ma*/tail
            entry['name'] = self._get_name(code)
            entry['update_date'] = td
            self._state['watch'][code] = entry

        # ── 回踩低吸信号（独立于高位突破，只扫本候选池） ──
        self._state['pullback'] = self._build_pullback_list(
            watch_list + already_broken, td)

        # ── Step 2: 检测买入信号（按评分 + 仓位限制 + 风控） ──
        buy_signals = self._scan_buy_signals(already_broken + watch_list, mode=mode)
        new_buys = 0
        cfg = self._risk.get('vol180')
        # ── 风控判定 ──
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
        # 注：evaluate 的 max_new_per_day 分支在此接入点不生效（new_buys 初始为 0），
        # 每日新开上限由下方 max_new = min(cfg['max_new_per_day'], ...) 切片兜底。
        risk = evaluate(cfg, {
            'positions': len(self._state['holding']) + len(self._state['ready']),
            'opened_today': new_buys,
            'total_value': self._state.get('cash', INITIAL_CAPITAL) + holdings_val,
            'history_peak': hist_peak,
            'breaker_tripped': (self._state.get('last_risk') or {}).get('breaker_tripped', False),
        }, regime)
        if risk['blocked_reasons']:
            print(f"[SimPortfolio] 风控拦截开仓: {'；'.join(risk['blocked_reasons'])}")
        self._state['last_risk'] = risk   # 供 status API 读取
        available_slots = max(0, cfg['max_positions'] - len(self._state['holding']) - len(self._state['ready']))
        max_new = min(cfg['max_new_per_day'], available_slots)

        # 风控只影响执行买入(Step 3)，不影响信号生成——用户始终能看到今日标的
        for sig in buy_signals[:max_new]:
            code = sig['code']
            if code in self._state['holding'] or code in self._state['ready']:
                continue
            name = self._get_name(code)
            # 买入日 = 下一交易日
            try:
                from ashare_review.utils.calendar import TradingCalendar
                cal = TradingCalendar()
                next_day = cal.next_trading_day(td_dt, offset=1)
                buy_date = next_day.strftime('%Y-%m-%d') if next_day else (
                    td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
            except Exception:
                buy_date = (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')

            # 计算买入份额（读配置 + regime 缩放）
            position_capital = INITIAL_CAPITAL * (risk['suggested_size_pct'] / 100.0)
            buy_price_est = sig['close']
            shares = int(position_capital / buy_price_est / 100) * 100
            if shares < 100:
                shares = 100

            self._state['ready'][code] = {
                'code': code, 'name': name,
                'signal_date': td, 'buy_date': buy_date,
                'buy_price': sig['close'],
                'shares': shares,
                'close': sig['close'], 'top_line': sig['top_line'],
                'break_pct': sig.get('break_pct', 0),
                'vol_ratio': sig['vol_ratio'],
                'score': sig.get('score', 60),
                'limit_count': sig.get('limit_count', 0),
                'suggested_size_pct': risk['suggested_size_pct'],  # 信号日判定的缩放仓位（随 ready 跨日持久化，以信号日为准）
                'mode': mode,
                'market_bull': is_bull,
            }
            new_buys += 1

        self._state['today_buys'] = new_buys   # 今日新开信号数（供风控 status 展示）

        # ── Step 3: 自动执行买入 ──
        auto_buys = 0
        for code in list(self._state['ready'].keys()):
            rd = self._state['ready'][code]
            if rd['buy_date'] <= td:
                skip_reason = None
                risk_now = self._state.get('last_risk') or {}
                if not risk_now.get('can_open', True):
                    skip_reason = '风控拦截: ' + '；'.join(risk_now.get('blocked_reasons', []))
                try:
                    df_check = self._read_stock(code, up_to_date=td.replace('-', ''))
                    if df_check is not None and not df_check.empty:
                        idx = len(df_check) - 1
                        open_p = float(df_check['open'].iloc[idx])
                        if idx >= 1:
                            prev_c = float(df_check['close'].iloc[idx - 1])
                            limit_pct = self._limit_threshold(code)
                            if open_p <= prev_c * (1 - limit_pct):
                                skip_reason = '开盘跌停'
                except Exception:
                    pass

                # ── V3 竞价确认 ──
                if skip_reason is None and mode == 'v3':
                    auction_reject = self._check_auction_v3(code, td)
                    if auction_reject:
                        skip_reason = auction_reject

                if skip_reason:
                    self._state['finished'][code] = {
                        **rd, 'status': 'skipped',
                        'skip_reason': skip_reason, 'skip_date': td,
                    }
                    del self._state['ready'][code]
                    continue

                # 实际买入价 = 今日开盘价
                buy_price_actual = rd.get('buy_price', rd.get('close', 0))
                try:
                    df_buy = self._read_stock(code, up_to_date=td.replace('-', ''))
                    if df_buy is not None and not df_buy.empty:
                        buy_price_actual = float(df_buy['open'].iloc[-1])
                except Exception:
                    pass

                # 重新计算买入份额（用实际开盘价 + 风控缩放仓位）
                # 信号日快照：执行日不重评估熔断/regime（如需执行日复查，后续增强）
                size_pct = rd.get('suggested_size_pct', PER_POSITION_PCT)
                position_capital = INITIAL_CAPITAL * (size_pct / 100.0)
                actual_shares = int(position_capital / max(buy_price_actual, 0.01) / 100) * 100
                if actual_shares < 100:
                    actual_shares = 100
                buy_cost = actual_shares * buy_price_actual * (1 + BUY_COMMISSION)
                if buy_cost > self._state.get('cash', INITIAL_CAPITAL):
                    self._state['finished'][code] = {
                        **rd, 'status': 'skipped',
                        'skip_reason': '资金不足', 'skip_date': td,
                    }
                    del self._state['ready'][code]
                    continue

                self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) - buy_cost

                self._state['holding'][code] = {
                    **rd,
                    'status': 'holding',
                    'buy_date': td,
                    'buy_price': round(buy_price_actual, 2),
                    'shares': actual_shares,
                    'had_zt': False,
                    'highest_close': buy_price_actual,
                    'awaiting_reversal': False,
                }
                del self._state['ready'][code]
                auto_buys += 1

        # ── Step 4: 检查卖出（挂单，次日开盘执行） ──
        sells_today = 0
        for code in list(self._state['holding'].keys()):
            if code in self._state.get('pending_sell', {}):
                continue  # 已有挂单
            pos = self._state['holding'][code]
            pos_mode = pos.get('mode', 'v1')
            if pos_mode == 'v3':
                sell_signal = self._check_sell_v3(code, pos, td)
            else:
                sell_signal = self._check_sell_vol180(code, pos, td)
            if sell_signal:
                # 挂单：下一个交易日以开盘价卖出
                try:
                    from ashare_review.utils.calendar import TradingCalendar
                    cal = TradingCalendar()
                    next_sell_day = cal.next_trading_day(td_dt, offset=1)
                    sell_date = next_sell_day.strftime('%Y-%m-%d') if next_sell_day else (
                        td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                except Exception:
                    sell_date = (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')

                self._state['pending_sell'][code] = {
                    'hold_info': {**pos},
                    'sell_date': sell_date,
                    'sell_reason': sell_signal['sell_reason'],
                    'close_price': sell_signal['sell_price'],
                    'days_held': sell_signal.get('days_held', 0),
                }
                sells_today += 1

        # ── Step 5: 记录组合快照 ──
        pos_val = 0.0
        for code, pos in self._state['holding'].items():
            latest = self._read_latest(code)
            if latest:
                pos_val += pos.get('shares', 0) * latest['close']
        total_val = self._state.get('cash', INITIAL_CAPITAL) + pos_val
        self._state.setdefault('portfolio_history', []).append({
            'date': td,
            'cash': round(self._state.get('cash', INITIAL_CAPITAL), 2),
            'positions_value': round(pos_val, 2),
            'total': round(total_val, 2),
            'market_bull': is_bull,
        })
        # 只保留最近 500 条记录
        if len(self._state['portfolio_history']) > 500:
            self._state['portfolio_history'] = self._state['portfolio_history'][-500:]

        self._state['last_update'] = td
        self._state['cache_date'] = td
        self._save()
        print(f'[SimPortfolio] 扫描完成: 市场{"牛" if is_bull else "熊"} | '
              f'观察{len(self._state["watch"])} 买入{new_buys}(+{auto_buys}执行) '
              f'卖出挂单{sells_today}(执行{sells_executed}) 持仓{len(self._state["holding"])}')

        return {
            'date': td,
            'watch_count': len(self._state['watch']),
            'buy_count': new_buys,
            'auto_buy_count': auto_buys,
            'sell_count': sells_today,
            'sell_executed': sells_executed,
            'holding_count': len(self._state['holding']),
            'ready_count': len(self._state['ready']),
            'pending_sell_count': len(self._state.get('pending_sell', {})),
            'market_bull': is_bull,
            'portfolio_value': round(total_val, 2),
        }

    def refresh_daily_status(self, trade_date: str = None, mode: str = 'v1') -> dict:
        """轻量刷新：只更新价格+执行到期操作，不重新选股。

        与 run_daily() 的区别：
        - run_daily(): 重建候选池 → 全量选股 → 生成买入信号 → 完整流程
        - refresh_daily_status(): 仅更新已有持仓/观察池价格，执行到期买入/卖出

        这样可以保证"明日开盘买入"列表在一次扫描后保持稳定，
        不会因为刷新而改变筛选结果。
        """
        td = trade_date or _today_str()
        td_dt = _date_from_str(td)

        if td_dt.weekday() >= 5:
            return {'date': td, 'note': '非交易日', 'watch': 0, 'buys': 0, 'sells': 0}

        market = self._get_market_state(td)
        is_bull = market.get('is_bull', True)

        # ── Step 0: 执行挂单卖出 ──
        sells_executed = 0
        for code in list(self._state.get('pending_sell', {}).keys()):
            ps = self._state['pending_sell'][code]
            if ps.get('sell_date', '') <= td:
                pos = ps['hold_info']
                try:
                    df_today = self._read_stock(code, up_to_date=td.replace('-', ''))
                    sell_price = float(df_today['open'].iloc[-1]) if df_today is not None and not df_today.empty else ps.get('close_price', pos.get('buy_price', 0))
                except Exception:
                    sell_price = ps.get('close_price', pos.get('buy_price', 0))

                buy_price = pos.get('buy_price', 0)
                shares = pos.get('shares', 0)
                gross_ret = (sell_price - buy_price) / buy_price if buy_price > 0 else 0
                net_ret = gross_ret - TOTAL_COST

                if shares > 0:
                    self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) + shares * sell_price * (1 - SELL_COST)

                self._state['finished'][code] = {
                    **pos, 'status': 'finished',
                    'sell_date': td, 'sell_price': round(sell_price, 2),
                    'gross_ret': round(gross_ret * 100, 2),
                    'net_ret': round(net_ret * 100, 2),
                    'is_win': net_ret > 0,
                    'exit_reason': ps.get('sell_reason', ''),
                    'days_held': ps.get('days_held', 0),
                }
                self._state['total_trades'] = self._state.get('total_trades', 0) + 1
                if net_ret > 0:
                    self._state['total_wins'] = self._state.get('total_wins', 0) + 1
                del self._state['pending_sell'][code]
                if code in self._state['holding']:
                    del self._state['holding'][code]
                sells_executed += 1

        # ── Step 1: 刷新观察池价格 + fib 字段 ──
        updated_watch = {}
        for code, w in self._state['watch'].items():
            latest = self._read_latest(code)
            if latest is None:
                updated_watch[code] = w
                continue

            close = latest['close']; vol = latest['vol']; mavol180 = latest['mavol180']
            top_line = w.get('top_line', 0)
            if top_line <= 0 or mavol180 <= 0:
                updated_watch[code] = w
                continue

            dist_pct = round((close - top_line) / top_line * 100, 1)
            vol_ratio = round(vol / mavol180, 1) if mavol180 > 0 else 0
            fs = fib_state(close,
                           latest.get('ma13'), latest.get('ma34'),
                           latest.get('ma55'), latest.get('ma89'))
            updated_watch[code] = {
                **w, 'close': round(close, 2), 'dist_pct': dist_pct,
                'vol_ratio': vol_ratio, 'vol': int(vol),
                'mavol180': round(mavol180, 0), 'update_date': td,
                # fib / 回踩指标字段并入（供 fib_state/回踩复用，避免二次读盘）
                'fib_state': fs,
                'ma5': latest.get('ma5'), 'ma10': latest.get('ma10'),
                'ma20': latest.get('ma20'),
                'ma13': latest.get('ma13'), 'ma34': latest.get('ma34'),
                'ma55': latest.get('ma55'), 'ma89': latest.get('ma89'),
                'vol_ma5_prev': latest.get('vol_ma5_prev'),
                'tail': latest.get('tail') or [],
            }

        # 破89(休息) → 剔除出观察池（refresh 不重选股，只做纪律剔除）
        self._state['watch'] = {
            code: w for code, w in updated_watch.items()
            if w.get('dist_pct', -999) >= -PRESSURE_DIST_PCT
            and w.get('fib_state') != 'rest'
        }
        # 保持 ready 池不变，不重新选股（与 run_daily 的核心区别）
        new_buys = 0

        # ── 重算回踩低吸（基于刷新后的观察池行；观察池被清空则清掉旧信号） ──
        if self._state['watch']:
            self._state['pullback'] = self._build_pullback_list(
                list(self._state['watch'].values()), td)
        else:
            self._state['pullback'] = []

        # ── Step 3: 自动执行到期买入 ──
        auto_buys = 0
        for code in list(self._state['ready'].keys()):
            rd = self._state['ready'][code]
            if rd['buy_date'] <= td:
                skip_reason = None
                try:
                    df_check = self._read_stock(code, up_to_date=td.replace('-', ''))
                    if df_check is not None and not df_check.empty:
                        idx = len(df_check) - 1
                        open_p = float(df_check['open'].iloc[idx])
                        if idx >= 1:
                            prev_c = float(df_check['close'].iloc[idx - 1])
                            limit_pct = self._limit_threshold(code)
                            if open_p <= prev_c * (1 - limit_pct):
                                skip_reason = '开盘跌停'
                except Exception:
                    pass

                if skip_reason is None and mode == 'v3':
                    auction_reject = self._check_auction_v3(code, td)
                    if auction_reject:
                        skip_reason = auction_reject

                if skip_reason:
                    self._state['finished'][code] = {
                        **rd, 'status': 'skipped',
                        'skip_reason': skip_reason, 'skip_date': td,
                    }
                    del self._state['ready'][code]
                    continue

                buy_price_actual = rd.get('buy_price', rd.get('close', 0))
                try:
                    df_buy = self._read_stock(code, up_to_date=td.replace('-', ''))
                    if df_buy is not None and not df_buy.empty:
                        buy_price_actual = float(df_buy['open'].iloc[-1])
                except Exception:
                    pass

                size_pct = rd.get('suggested_size_pct', PER_POSITION_PCT)
                position_capital = INITIAL_CAPITAL * (size_pct / 100.0)
                actual_shares = int(position_capital / max(buy_price_actual, 0.01) / 100) * 100
                if actual_shares < 100: actual_shares = 100
                buy_cost = actual_shares * buy_price_actual * (1 + BUY_COMMISSION)
                if buy_cost > self._state.get('cash', INITIAL_CAPITAL):
                    self._state['finished'][code] = {
                        **rd, 'status': 'skipped',
                        'skip_reason': '资金不足', 'skip_date': td,
                    }
                    del self._state['ready'][code]
                    continue

                self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) - buy_cost
                self._state['holding'][code] = {
                    **rd, 'status': 'holding',
                    'buy_date': td,
                    'buy_price': round(buy_price_actual, 2),
                    'shares': actual_shares,
                    'had_zt': False,
                    'highest_close': buy_price_actual,
                    'awaiting_reversal': False,
                }
                del self._state['ready'][code]
                auto_buys += 1

        # ── Step 4: 检查卖出（挂单次日执行） ──
        sells_today = 0
        for code in list(self._state['holding'].keys()):
            if code in self._state.get('pending_sell', {}):
                continue
            pos = self._state['holding'][code]
            pos_mode = pos.get('mode', 'v1')
            if pos_mode == 'v3':
                sell_signal = self._check_sell_v3(code, pos, td)
            else:
                sell_signal = self._check_sell_vol180(code, pos, td)
            if sell_signal:
                try:
                    from ashare_review.utils.calendar import TradingCalendar
                    cal = TradingCalendar()
                    next_sell_day = cal.next_trading_day(td_dt, offset=1)
                    sell_date = next_sell_day.strftime('%Y-%m-%d') if next_sell_day else (
                        td_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                except Exception:
                    sell_date = (td_dt + timedelta(days=1)).strftime('%Y-%m-%d')

                self._state['pending_sell'][code] = {
                    'hold_info': {**pos},
                    'sell_date': sell_date,
                    'sell_reason': sell_signal['sell_reason'],
                    'close_price': sell_signal['sell_price'],
                    'days_held': sell_signal.get('days_held', 0),
                }
                sells_today += 1

        # ── Step 5: 组合快照 ──
        pos_val = 0.0
        for code, pos in self._state['holding'].items():
            latest = self._read_latest(code)
            if latest:
                pos_val += pos.get('shares', 0) * latest['close']
        total_val = self._state.get('cash', INITIAL_CAPITAL) + pos_val
        self._state.setdefault('portfolio_history', []).append({
            'date': td, 'cash': round(self._state.get('cash', INITIAL_CAPITAL), 2),
            'positions_value': round(pos_val, 2), 'total': round(total_val, 2),
            'market_bull': is_bull,
        })
        if len(self._state['portfolio_history']) > 500:
            self._state['portfolio_history'] = self._state['portfolio_history'][-500:]

        self._state['last_update'] = td
        self._save()
        print(f'[SimPortfolio] 轻量刷新完成: 观察{len(self._state.get("watch", {}))} '
              f'就绪{len(self._state.get("ready", {}))} 买入{auto_buys} '
              f'卖出挂单{sells_today}(执行{sells_executed}) '
              f'持仓{len(self._state.get("holding", {}))} （未重新选股）')

        return {
            'date': td,
            'watch_count': len(self._state['watch']),
            'buy_count': new_buys,
            'auto_buy_count': auto_buys,
            'sell_count': sells_today,
            'sell_executed': sells_executed,
            'holding_count': len(self._state['holding']),
            'ready_count': len(self._state['ready']),
            'pending_sell_count': len(self._state.get('pending_sell', {})),
            'market_bull': is_bull,
            'portfolio_value': round(total_val, 2),
        }

    def get_summary(self) -> dict:
        """获取当前完整状态（供 Web 返回）。"""
        today = _today_str()

        # ── 确保所有条目有名称 ──
        def _ensure_name(item: dict, code: str):
            if not item.get('name') or item['name'] == code:
                item['name'] = self._get_name(code)

        # ── 观察池 ──
        watch = []
        for code, w in self._state['watch'].items():
            _ensure_name(w, code)
            watch.append({**w})

        # ── 今日买入（ready池中待执行的） ──
        buy_today = []
        for code, rd in self._state['ready'].items():
            _ensure_name(rd, code)
            buy_today.append({**rd})

        # ── 今日卖出（持仓中到期/不涨停的） ──
        sell_today = []
        for code, pos in self._state['holding'].items():
            _ensure_name(pos, code)
            # 检查今天是否该卖（V3 用 _check_sell_v3）
            if pos.get('mode') == 'v3':
                sell_sig = self._check_sell_v3(code, pos, today)
            else:
                sell_sig = self._check_sell_vol180(code, pos, today)
            if sell_sig:
                buy_price = pos.get('buy_price', 0)
                sell_price = sell_sig['sell_price']
                gross_ret = (sell_price - buy_price) / buy_price if buy_price > 0 else 0
                net_ret = gross_ret - TOTAL_COST
                sell_today.append({
                    **pos,
                    'sell_price_today': sell_price,
                    'estimated_net_ret': round(net_ret * 100, 2),
                    'exit_reason': sell_sig['sell_reason'],
                    'days_held': sell_sig.get('days_held', 0),
                })

        # ── 持仓（实时浮盈 + V3 移动止盈/反包状态） ──
        holdings = []
        for code, pos in self._state['holding'].items():
            _ensure_name(pos, code)
            df = self._read_stock(code)
            current_price = pos.get('buy_price', 0)
            is_zt = False
            if df is not None and not df.empty:
                idx = len(df) - 1
                current_price = float(df['close'].iloc[idx])
                if idx >= 1:
                    prev_c = float(df['close'].iloc[idx - 1])
                    limit_pct = self._limit_threshold(code)
                    chg = (current_price - prev_c) / prev_c if prev_c > 0 else 0
                    is_zt = chg >= limit_pct

            pnl = (current_price - pos.get('buy_price', 0)) / pos.get('buy_price', 1) * 100
            try:
                from ashare_review.utils.calendar import TradingCalendar
                cal = TradingCalendar()
                bd = _date_from_str(pos.get('buy_date', today))
                td = _date_from_str(today)
                days_held = cal.trading_days_between(bd, td)
            except Exception:
                days_held = 0

            # ── V3 专属字段 ──
            highest = pos.get('highest_close', pos.get('buy_price', 0))
            highest = max(highest, current_price)
            trailing_drop = round((current_price - highest) / highest * 100, 1) if highest > 0 else 0
            awaiting = pos.get('awaiting_reversal', False)

            holdings.append({
                **pos,
                'current_price': round(current_price, 2),
                'unrealized_pnl_pct': round(pnl, 2),
                'days_held': days_held,
                'is_zt_today': is_zt,
                'highest_close': round(highest, 2),
                'trailing_drop_pct': trailing_drop,
                'awaiting_reversal': awaiting,
            })

        # ── 统计 ──
        total_trades = self._state.get('total_trades', 0)
        total_wins = self._state.get('total_wins', 0)
        market = self._get_market_state(today)

        # 最近完成的交易
        finished = list(self._state['finished'].values())
        finished.sort(key=lambda x: x.get('sell_date', ''), reverse=True)

        # 组合层级统计
        init_cap = self._state.get('initial_capital', INITIAL_CAPITAL)
        pos_val = sum(
            h.get('shares', 0) * h.get('current_price', h.get('buy_price', 0))
            for h in holdings
        )
        total_val = self._state.get('cash', init_cap) + pos_val
        cum_ret = (total_val / init_cap - 1) * 100 if init_cap > 0 else 0

        # 最大回撤
        ph = self._state.get('portfolio_history', [])
        peak = init_cap
        max_dd = 0.0
        for snap in ph:
            peak = max(peak, snap.get('total', 0))
            dd = (peak - snap.get('total', 0)) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # 累计净收益（所有已完成交易）
        total_return = round(sum(f.get('net_ret', 0) for f in finished), 2)
        avg_return = round(total_return / max(total_trades, 1), 2)

        return {
            'date': today,
            'last_update': self._state.get('last_update', ''),
            'watch_list': watch,
            'pullback_list': list(self._state.get('pullback', [])),
            'sim_buy_today': buy_today,
            'sim_sell_today': sell_today,
            'pending_sells': [
                {**ps, 'code': code}
                for code, ps in self._state.get('pending_sell', {}).items()
            ],
            'holdings': holdings,
            'finished_list': finished[:20],
            'summary': {
                'total_trades': total_trades,
                'wins': total_wins,
                'losses': total_trades - total_wins,
                'win_rate': round(total_wins / max(total_trades, 1) * 100, 1),
                'total_return': total_return,
                'avg_return': avg_return,
                'watch_count': len(watch),
                'buy_count': len(buy_today),
                'sell_count': len(sell_today),
                'holding_count': len(holdings),
                'ready_count': len(self._state['ready']),
                'pending_sell_count': len(self._state.get('pending_sell', {})),
                # ── 组合层级 ──
                'cash': round(self._state.get('cash', init_cap), 2),
                'positions_value': round(pos_val, 2),
                'portfolio_value': round(total_val, 2),
                'initial_capital': init_cap,
                'cumulative_return': round(cum_ret, 2),
                'max_drawdown': round(max_dd, 2),
                'max_positions': MAX_POSITIONS,
                # ── 市场 ──
                'market_bull': market.get('is_bull', True),
                'sh_close': market.get('sh_close', 0),
                'sh_ma60': market.get('sh_ma60', 0),
            },
        }

    def record_buy(self, code: str, actual_price: float = None,
                   buy_date: str = None) -> bool:
        """记录实际买入（从ready移入holding），使用真实资金模型。"""
        if code not in self._state['ready']:
            return False
        info = self._state['ready'].pop(code)
        buy_price = actual_price or info.get('buy_price', 0)
        buy_dt = buy_date or _today_str()

        # 用实际价格重新计算份额
        position_capital = INITIAL_CAPITAL * PER_POSITION_PCT
        shares = int(position_capital / max(buy_price, 0.01) / 100) * 100
        if shares < 100:
            shares = 100
        buy_cost = shares * buy_price * (1 + BUY_COMMISSION)
        if buy_cost > self._state.get('cash', INITIAL_CAPITAL):
            return False  # 资金不足

        self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) - buy_cost

        info['status'] = 'holding'
        info['had_zt'] = False
        info['buy_price'] = round(buy_price, 2)
        info['buy_date'] = buy_dt
        info['shares'] = shares
        info['highest_close'] = buy_price
        info['awaiting_reversal'] = False
        self._state['holding'][code] = info
        self._save()
        return True

    def record_sell(self, code: str, sell_price: float,
                    sell_date: str = None) -> bool:
        """记录实际卖出（含资金回笼）。"""
        pos = None
        if code in self._state['holding']:
            pos = self._state['holding'].pop(code)
        elif code in self._state.get('pending_sell', {}):
            pos = self._state['pending_sell'].pop(code).get('hold_info', {})
            if code in self._state['holding']:
                del self._state['holding'][code]
        else:
            return False

        buy_price = pos.get('buy_price', 0)
        shares = pos.get('shares', 0)
        gross_ret = (sell_price - buy_price) / buy_price if buy_price > 0 else 0
        net_ret = gross_ret - TOTAL_COST

        # 回笼资金
        if shares > 0:
            self._state['cash'] = self._state.get('cash', INITIAL_CAPITAL) + \
                shares * sell_price * (1 - SELL_COST)

        pos.update({
            'status': 'finished',
            'sell_date': sell_date or _today_str(),
            'sell_price': sell_price,
            'gross_ret': round(gross_ret * 100, 2),
            'net_ret': round(net_ret * 100, 2),
            'is_win': net_ret > 0,
        })
        self._state['finished'][code] = pos
        self._state['total_trades'] = self._state.get('total_trades', 0) + 1
        if net_ret > 0:
            self._state['total_wins'] = self._state.get('total_wins', 0) + 1
        self._save()
        return True

    def update_finished(self, code: str, updates: dict) -> bool:
        """更新已完成交易记录中的字段（含重算统计）。"""
        if code not in self._state['finished']:
            return False
        fin = self._state['finished'][code]
        old_is_win = fin.get('is_win', False)
        old_net_ret = fin.get('net_ret', 0)

        # 更新字段
        for key in ('buy_price', 'sell_price', 'net_ret', 'gross_ret',
                    'is_win', 'exit_reason', 'days_held', 'sell_date',
                    'buy_date', 'shares', 'name', 'score', 'mode'):
            if key in updates:
                if key in ('buy_price', 'sell_price', 'net_ret', 'gross_ret'):
                    fin[key] = float(updates[key]) if updates[key] is not None else 0
                elif key == 'is_win':
                    fin[key] = bool(updates[key])
                elif key in ('days_held', 'shares', 'score'):
                    fin[key] = int(updates[key]) if updates[key] is not None else 0
                else:
                    fin[key] = str(updates[key]) if updates[key] is not None else ''

        # 如果改了 buy_price 或 sell_price，重算收益
        if 'buy_price' in updates or 'sell_price' in updates:
            bp = fin.get('buy_price', 0)
            sp = fin.get('sell_price', 0)
            if bp > 0:
                gross_ret = (sp - bp) / bp
                net_ret = gross_ret - TOTAL_COST
                fin['gross_ret'] = round(gross_ret * 100, 2)
                fin['net_ret'] = round(net_ret * 100, 2)
                fin['is_win'] = net_ret > 0

        # 重算统计
        new_is_win = fin.get('is_win', False)
        new_net_ret = fin.get('net_ret', 0)
        if old_is_win != new_is_win:
            if old_is_win:
                self._state['total_wins'] = max(0, self._state.get('total_wins', 0) - 1)
            if new_is_win:
                self._state['total_wins'] = self._state.get('total_wins', 0) + 1

        self._save()
        return True

    def delete_finished(self, code: str) -> bool:
        """删除已完成交易记录并更新统计。"""
        if code not in self._state['finished']:
            return False
        info = self._state['finished'].pop(code)
        self._state['total_trades'] = max(0, self._state.get('total_trades', 0) - 1)
        if info.get('is_win'):
            self._state['total_wins'] = max(0, self._state.get('total_wins', 0) - 1)
        self._save()
        return True

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
        """删除持仓或就绪记录（回退误操作）。"""
        if code in self._state['holding']:
            del self._state['holding'][code]
            self._save()
            return True
        if code in self._state['ready']:
            del self._state['ready'][code]
            self._save()
            return True
        if code in self._state['finished']:
            info = self._state['finished'].pop(code)
            # 退回统计
            self._state['total_trades'] = max(0, self._state.get('total_trades', 0) - 1)
            if info.get('is_win'):
                self._state['total_wins'] = max(0, self._state.get('total_wins', 0) - 1)
            self._save()
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='VOL180 突破模拟持仓')
    p.add_argument('--scan', action='store_true', help='运行每日扫描')
    p.add_argument('--summary', action='store_true', help='打印当前状态')
    p.add_argument('--date', type=str, default=None, help='日期 YYYY-MM-DD')
    args = p.parse_args()

    sp = Vol180SimPortfolio()

    if args.scan:
        result = sp.run_daily(trade_date=args.date)
        print(f"扫描完成: {result}")

    if args.summary or not args.scan:
        s = sp.get_summary()
        summary = s['summary']
        print(f"\n{'='*60}")
        print(f"  VOL180 模拟持仓状态 ({s['date']})")
        print(f"{'='*60}")
        print(f"  已完成: {summary['total_trades']}笔 | "
              f"胜{summary['wins']}负{summary['losses']} | "
              f"胜率{summary['win_rate']:.1f}%")
        print(f"  观察池: {summary['watch_count']}只 | "
              f"今日买入: {summary['buy_count']}只 | "
              f"今日卖出: {summary['sell_count']}只 | "
              f"持仓: {summary['holding_count']}只")

        for label, key in [('👀 观察池', 'watch_list'),
                            ('🟢 今日买入', 'sim_buy_today'),
                            ('🔴 今日卖出', 'sim_sell_today'),
                            ('💼 持仓', 'holdings')]:
            items = s[key]
            if items:
                print(f"\n  {label} ({len(items)}只):")
                for item in items[:10]:
                    if key == 'watch_list':
                        print(f"    {item['code']} {item.get('name',''):<8s} "
                              f"距压力位 {item.get('dist_pct',0):+.1f}% "
                              f"量比 {item.get('vol_ratio',0):.1f}x "
                              f"涨停{item.get('limit_count',0)}次")
                    elif key == 'holdings':
                        print(f"    {item['code']} {item.get('name',''):<8s} "
                              f"买{item.get('buy_price',0)} 现{item.get('current_price',0)} "
                              f"浮盈{item.get('unrealized_pnl_pct',0):+.1f}%")
                    elif key == 'sim_sell_today':
                        print(f"    {item['code']} {item.get('name',''):<8s} "
                              f"预计收益{item.get('estimated_net_ret',0):+.1f}%")
                    else:
                        print(f"    {item['code']} {item.get('name',''):<8s} "
                              f"突破{item.get('break_pct',0):+.1f}% "
                              f"量比{item.get('vol_ratio',0):.1f}x "
                              f"评分{item.get('score',0)}")
        print(f"{'='*60}")
