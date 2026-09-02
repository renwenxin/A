"""明日突破预测 — 启动突破 V3 蓄势池增强

逻辑哥思路第三层: 选出压力位下方蓄力、预测明日放量突破的标的（坐在起爆点）。
基于历史校准(analysis/breakout_predict_calibrate.py, 16833 样本):
  基准次日突破率 15.3%
  near(距压力位≤3%): 34.5%   ← 最强
  near+probe+limit: 38.3%    ← 实用组合
  vol_shrink(地量)单独为负(11.1%) → 不作正向权重(仅 near 基础上小加成)
评分 = 基础 + 特征加权(由校准 lift 折算), 满分~80+ 视为高概率。

台账: data/breakout_predict.db 表 predictions
  (date, code, name, score, pressure, features_json, next_date, next_breakout, hit)
次日验证: close > pressure(当日记录的压力位) 视为突破命中。
"""
import json, os, sqlite3, sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .sim_portfolio import Vol180SimPortfolio

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_DB_FILE = os.path.join(_DATA_DIR, 'breakout_predict.db')

# 评分权重（校准折算: 基准15.3% → near+34.5% 等）
SCORE_BASE = 25.0          # 蓄势日基础分(高于无特征7.9%)
W_NEAR = 30.0              # 距压力位≤3%
W_LIMIT = 8.0              # 年涨停≥15
W_PROBE = 6.0              # 近15日试盘摸高≥97%压力位
W_MA_BULL = 5.0            # 5>10>20 多头
W_VOL_SHRINK = 3.0         # 地量(仅 near 基础上小加成, 单独为负)
W_VOL_UP = 5.0             # 温和放量 1.2~2x(启动的是量)
NEAR_DIST_PCT = 3.0        # 贴压力位阈值
PROBE_WIN = 15             # 试盘回看天数
PROBE_RATIO = 0.97         # 摸高阈值


def _today_str() -> str:
    return date.today().strftime('%Y-%m-%d')


def _get_db() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, code TEXT, name TEXT, score REAL,
        pressure REAL, features TEXT,
        next_date TEXT, next_breakout INTEGER,
        hit INTEGER, created_at TEXT)''')
    return conn


def compute_features(df: pd.DataFrame, pressure: float,
                     limit_count: int) -> Dict:
    """从日线 df(含 OHLCV) 计算突破前夜特征。

    Args:
        df: 至少 20 根日线(close/high/low/volume)
        pressure: 压力位(找顶线/NN高点)
        limit_count: 年涨停次数
    Returns:
        {near, vol_shrink, vol_ratio, probe, ma_bull, dist_pct}
    """
    out = {'near': False, 'vol_shrink': False, 'probe': False,
           'ma_bull': False, 'dist_pct': None, 'vol_ratio': None}
    n = len(df)
    if n < 20 or pressure <= 0:
        return out
    close = float(df['close'].iloc[-1])
    if close <= 0:
        return out
    dist_pct = (pressure - close) / pressure * 100
    out['dist_pct'] = round(dist_pct, 1)
    out['near'] = 0 < dist_pct <= NEAR_DIST_PCT

    # 量能: 今日量 / 前5日均量(排除今日)
    vol = df['volume'].astype(float)
    vol_ma5_prev = float(vol.iloc[-6:-1].mean()) if n >= 6 else 0
    vol_now = float(vol.iloc[-1])
    if vol_ma5_prev > 0:
        vr = vol_now / vol_ma5_prev
        out['vol_ratio'] = round(vr, 2)
        out['vol_shrink'] = vr < 0.7
        out['vol_up'] = 1.2 <= vr <= 2.5
    else:
        out['vol_up'] = False

    # 试盘: 近15日(不含今日)最高价 摸到压力位97%以上
    if n >= 2:
        win_high = float(df['high'].iloc[-PROBE_WIN - 1:-1].max())
        out['probe'] = win_high >= pressure * PROBE_RATIO

    # 均线多头
    cs = df['close']
    ma5 = float(cs.rolling(5).mean().iloc[-1])
    ma10 = float(cs.rolling(10).mean().iloc[-1])
    ma20 = float(cs.rolling(20).mean().iloc[-1])
    out['ma_bull'] = ma5 > ma10 > ma20
    out['limit_count'] = int(limit_count)
    return out


def score_features(f: Dict) -> float:
    """特征 → 0-100 评分（校准权重）"""
    s = SCORE_BASE
    if f.get('near'):
        s += W_NEAR
    if f.get('limit_count', 0) >= 15:
        s += W_LIMIT
    if f.get('probe'):
        s += W_PROBE
    if f.get('ma_bull'):
        s += W_MA_BULL
    if f.get('vol_shrink') and f.get('near'):
        s += W_VOL_SHRINK      # 地量仅 near 基础上小加成
    if f.get('vol_up'):
        s += W_VOL_UP          # 温和放量(启动的是量)
    return round(min(s, 100), 1)


def features_reasons(f: Dict) -> List[str]:
    r = []
    if f.get('near'):
        r.append(f"距压力位{f.get('dist_pct'):.1f}%·贴线蓄势")
    if f.get('probe'):
        r.append('近15日试盘摸高')
    if f.get('ma_bull'):
        r.append('5>10>20多头')
    if f.get('limit_count', 0) >= 15:
        r.append(f"年涨停{f.get('limit_count')}次·股性好")
    if f.get('vol_shrink') and f.get('near'):
        r.append('地量·抛压耗尽')
    if f.get('vol_up'):
        r.append(f"放量{f.get('vol_ratio')}倍")
    if not r:
        r.append('蓄势观察')
    return r


class BreakoutPredictor:
    """基于 V3 watch 蓄势池的明日突破预测 + 台账验证。"""

    def __init__(self, sp: Vol180SimPortfolio = None):
        self.sp = sp or Vol180SimPortfolio()
        self.tdx = self.sp.tdx

    # ── 预测 ──
    def predict(self, trade_date: str = None, top_n: int = 10,
                persist: bool = True) -> List[Dict]:
        td = trade_date or _today_str()
        watch = self.sp._state.get('watch', {})
        results = []
        for code, w in watch.items():
            pressure = w.get('top_line', 0)
            limit_count = w.get('limit_count', 0)
            if pressure <= 0:
                continue
            try:
                market = 'sh' if code.startswith('6') else 'sz'
                df = self.tdx.read_daily(code, market)
                if df is None or len(df) < 25:
                    continue
                df = df.tail(250).reset_index(drop=True)
                f = compute_features(df, pressure, limit_count)
                score = score_features(f)
                results.append({
                    'code': code, 'name': w.get('name', code),
                    'score': score,
                    'close': float(df['close'].iloc[-1]),
                    'pressure': round(pressure, 2),
                    'dist_pct': f.get('dist_pct'),
                    'features': f,
                    'reasons': features_reasons(f),
                })
            except Exception:
                continue
        results.sort(key=lambda x: -x['score'])
        top = results[:top_n]
        if persist and top:
            self._persist(td, top)
        return top

    def _persist(self, td: str, top: List[Dict]):
        conn = _get_db()
        # 同日重跑先清旧记录(按 date+code)
        existing = {(r[0], r[1]) for r in conn.execute(
            'SELECT date, code FROM predictions WHERE date=?', (td,)).fetchall()}
        for item in top:
            if (td, item['code']) in existing:
                continue
            conn.execute(
                'INSERT INTO predictions(date,code,name,score,pressure,features,next_date,hit) '
                'VALUES(?,?,?,?,?,?,?,NULL)',
                (td, item['code'], item['name'], item['score'],
                 item['pressure'], json.dumps(item['features'], ensure_ascii=False),
                 self._next_trading_day(td)))
        conn.commit()
        conn.close()

    def _next_trading_day(self, d_str: str) -> str:
        try:
            from ..utils.calendar import TradingCalendar
            cal = TradingCalendar()
            d = datetime.strptime(d_str, '%Y-%m-%d').date()
            nd = cal.next_trading_day(d, offset=1)
            return nd.strftime('%Y-%m-%d') if nd else ''
        except Exception:
            return ''

    # ── 次日验证 ──
    def verify_pending(self) -> int:
        """对已到 next_date 且未验证的预测，查 TDX 收盘是否突破当日 pressure。"""
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, code, next_date, pressure FROM predictions "
            "WHERE next_breakout IS NULL AND next_date != '' "
            "AND next_date <= ?", (_today_str(),)).fetchall()
        verified = 0
        for pid, code, nd, pressure in rows:
            try:
                market = 'sh' if code.startswith('6') else 'sz'
                df = self.tdx.read_daily(code, market)
                if df is None or df.empty:
                    continue
                df['ds'] = df['trade_date'].astype(str).str[:10]
                hit_row = df[df['ds'] == nd]
                if hit_row.empty:
                    continue
                close_nd = float(hit_row['close'].iloc[0])
                broke = 1 if (pressure > 0 and close_nd > pressure) else 0
                conn.execute(
                    'UPDATE predictions SET next_breakout=?, hit=? WHERE id=?',
                    (broke, broke, pid))
                verified += 1
            except Exception:
                continue
        conn.commit()
        conn.close()
        return verified

    # ── 台账统计 ──
    def stats(self) -> Dict:
        conn = _get_db()
        total = conn.execute('SELECT COUNT(*) FROM predictions WHERE hit IS NOT NULL').fetchone()[0]
        hits = conn.execute('SELECT COUNT(*) FROM predictions WHERE hit=1').fetchone()[0]
        # 按评分段命中率
        bands = {}
        for score, hit in conn.execute(
                'SELECT score, hit FROM predictions WHERE hit IS NOT NULL').fetchall():
            b = '>=70' if score >= 70 else ('60-69' if score >= 60 else '<60')
            bands.setdefault(b, [0, 0])
            bands[b][0] += 1
            if hit == 1:
                bands[b][1] += 1
        band_stats = {b: {'n': v[0], 'hit': round(v[1] / v[0] * 100, 1) if v[0] else 0}
                      for b, v in sorted(bands.items())}
        pending = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE next_breakout IS NULL").fetchone()[0]
        conn.close()
        return {
            'total_verified': total,
            'hits': hits,
            'hit_rate': round(hits / total * 100, 1) if total else 0,
            'pending': pending,
            'bands': band_stats,
        }
