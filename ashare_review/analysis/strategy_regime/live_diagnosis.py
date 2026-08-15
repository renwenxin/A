"""今日行情诊断 + 三战法今日标的（快速版）

行情判断: 复用 market_state/regime（上证缠论趋势 + 国证2000相对强弱 + 情绪温度）
今日标的:
  - 启动突破 V3: 复用 v3_backtest 的快速向量化压力/MAVOL180 检测（秒级，不用慢速 zigzag screener）
  - 1进2 接力:   复用复盘 _select_top_picks（akshare 涨停池）
  - 冰点抄底:    若今日是冰点反转确认日 → 列出超跌候选，否则无机会
"""
import os
from datetime import date, datetime, timedelta
from typing import Dict, List

import pandas as pd

from ...data.tdx_reader import TdxReader
from ...data.akshare_fetcher import AkshareFetcher
from ..v3_backtest import V3Backtest, MAVOL_MULTIPLIER, MAVOL_PERIOD, TOTAL_COST
from . import market_state as ms
from . import causal_universe as cu
from . import ice_backtest as ice

START = date(2025, 8, 8)
END = date(2026, 8, 7)
REGIME_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'strategy_regime')
STATE_CACHE = os.path.join(REGIME_DIR, 'market_state.csv')
UNIVERSE_CACHE = os.path.join(REGIME_DIR, 'causal_universe.json')

REGIME_W = {'强势趋势': '启动突破V3', '题材轮动': '1进2接力', '冰点超跌': '冰点抄底',
            '震荡观望': '轻仓观望', '弱市回调': '轻仓观望', '退潮下跌': '空仓'}
REGIME_W_COLOR = {'强势趋势': '#059669', '题材轮动': '#d97706', '冰点超跌': '#7c3aed',
                  '震荡观望': '#6b7280', '弱市回调': '#b45309', '退潮下跌': '#dc2626'}


def _load_state():
    return ms.load_state(START, END, STATE_CACHE)


def get_regime_diagnosis() -> Dict:
    """今日行情判断（最新交易日）"""
    state = _load_state()
    latest = state.iloc[-1]
    sh = latest.get('sh_close')
    gz = latest.get('gz_close')
    return {
        'date': str(latest.get('date')),
        'sh_close': sh, 'sh_chg': latest.get('sh_chg'),
        'gz_close': gz, 'gz_chg': latest.get('gz_chg'),
        'up_count': latest.get('up_count'), 'down_count': latest.get('down_count'),
        'limit_up': latest.get('limit_up'), 'limit_down': latest.get('limit_down'),
        'sh_ma20': latest.get('sh_ma20'), 'sh_ma60': latest.get('sh_ma60'),
        'sh_trend_now': latest.get('sh_trend_now'), 'sh_beichi': latest.get('sh_beichi'),
        'rel_strength': latest.get('rel_strength'),
        'emotion': latest.get('emotion'),
        'regime': latest.get('regime'),
        'recommend': latest.get('recommend'),
        'recommend_color': REGIME_W_COLOR.get(latest.get('regime'), '#6b7280'),
        'regime_weights': REGIME_W,
    }


def get_v3_picks(latest_date: date, top_n: int = 10) -> List[Dict]:
    """今日 V3 突破候选（快速检测）"""
    tdx = TdxReader()
    uni = cu.CausalUniverse(tdx, START, END, cache_path=UNIVERSE_CACHE)
    bt = V3Backtest()
    picks = []
    for code in sorted(uni.codes):
        if not (str(code).startswith(('60', '00'))):
            continue
        if not uni.eligible(code, latest_date):
            continue
        name = bt._get_name(code)
        if 'ST' in name:
            continue
        # 名字查不到（_get_name 返回代码本身）→ 显示 '—'
        if name == code or (name and name.isdigit()):
            name = '—'
        df = bt._read_stock_full(code)
        if df is None or df.empty:
            continue
        if 'trade_date' not in df.columns:
            continue
        mask = df['trade_date'].apply(lambda x: (x.date() if hasattr(x, 'date') else x) <= latest_date)
        sub = df[mask]
        if len(sub) < MAVOL_PERIOD + 20:
            continue
        idx = len(sub) - 1
        close = float(sub['close'].iloc[idx])
        vol = float(sub['volume'].iloc[idx])
        mavol180 = float(sub['mavol180'].iloc[idx])
        if pd.isna(mavol180) or mavol180 <= 0:
            continue
        pressure = bt._calc_pressure_at(sub, idx)
        if pressure <= 0:
            continue
        dist_pct = (close - pressure) / pressure * 100
        vol_ratio = vol / mavol180
        # 今日刚突破（金叉）：昨日收盘仍压在压力线下，今日收盘突破
        if idx < 1:
            continue
        prev_close = float(sub['close'].iloc[idx - 1])
        prev_pressure = bt._calc_pressure_at(sub, idx - 1)
        fresh_break = (close > pressure and prev_close <= prev_pressure * 1.005)
        if not fresh_break:
            continue
        # 距压力位不太远（刚突破，不应超~12%）
        if not (0 < dist_pct <= 12.0):
            continue
        if not (vol > mavol180 * MAVOL_MULTIPLIER):
            continue
        # V3 过滤: 死亡区间 / 过度放量
        if 3 < abs(dist_pct) <= 5 or vol_ratio >= 5.0:
            continue
        score = min(100, 60 + (10 if abs(dist_pct) >= 3 else 0)
                          + (10 if vol_ratio >= 2.0 else 5 if vol_ratio >= 1.5 else 0))
        picks.append({
            'code': code, 'name': name, 'score': score,
            'close': round(close, 2), 'pressure': round(pressure, 2),
            'dist_pct': round(dist_pct, 1), 'vol_ratio': round(vol_ratio, 1),
            'limit_count': 0,
        })
    picks.sort(key=lambda x: -x['score'])
    return picks[:top_n]


def get_one2_picks() -> List[Dict]:
    """今日 1进2 精选（复用复盘 _select_top_picks）"""
    try:
        from ...report.daily import DailyReport
        tdx = TdxReader()
        ak = AkshareFetcher(cache_db=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'cache.db'))
        dr = DailyReport(tdx, ak)
        lus = ak.get_limit_up_pool()
        picks = dr._select_top_picks(lus)
        return [{'code': p['code'],
                 'name': p['name'] if (p['name'] != p['code'] and not str(p['name']).isdigit()) else '—',
                 'score': p['score'],
                 'close': p.get('price', 0), 'seal_ratio': p.get('seal_ratio', 0),
                 'reasons': p.get('reasons', [])} for p in picks]
    except Exception as e:
        return [{'error': str(e)}]


def get_ice_picks(latest_date: date) -> Dict:
    """今日冰点抄底机会（若今日是反转确认日 → 超跌候选）"""
    state = _load_state()
    rev_idx = ice.find_reversal_days(state)
    rev_dates = set()
    for i in rev_idx:
        d = state.iloc[i]['date']
        if isinstance(d, str):
            d = date.fromisoformat(d)
        rev_dates.add(d)

    if latest_date not in rev_dates:
        # 是不是冰点日
        latest = state.iloc[-1]
        is_ice = latest.get('emotion') in ('冰点', '极冰点')
        return {'is_reversal': False, 'is_ice': is_ice, 'picks': [],
                'note': ('冰点反转确认日（今日非反转日，冰点抄底不触发）'
                         if not is_ice else '当前为冰点期，等待反转确认（上证大阳或涨停回升）')}

    # 反转日 → 列出超跌候选
    try:
        tdx = TdxReader()
        bt = ice.IceBottomBacktest(tdx)
        universe = sorted(c for c in bt.get_universe()
                          if str(c).startswith(('60', '00')) and 'ST' not in bt._get_name(c))
        bt._load_cache(universe)
        cands = bt._oversold_candidates(latest_date)
        return {'is_reversal': True, 'is_ice': False, 'picks': [
            {'code': c['code'],
             'name': c['name'] if (c['name'] != c['code'] and not str(c['name']).isdigit()) else '—',
             'close': round(c['close'], 2),
             'drop': round(c['drop'] * 100, 1)} for c in cands],
            'note': f'冰点反转确认日！缠论二买点，可关注超跌强势股 {len(cands)} 只'}
    except Exception as e:
        return {'is_reversal': True, 'is_ice': False, 'picks': [], 'note': f'冰点候选计算失败: {e}'}


def get_latest_trade_date() -> date:
    state = _load_state()
    d = state.iloc[-1]['date']
    return date.fromisoformat(d) if isinstance(d, str) else d


def get_full_diagnosis() -> Dict:
    """汇总：行情判断 + 三战法今日标的"""
    latest = get_latest_trade_date()
    return {
        'regime': get_regime_diagnosis(),
        'v3_picks': get_v3_picks(latest),
        'one2_picks': get_one2_picks(),
        'ice': get_ice_picks(latest),
        'latest_date': str(latest),
    }
