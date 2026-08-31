# ashare_review/one_two_v2/service.py
"""今日1进2 — 编排层（盘后精选/次日验证/上下文组装 + 后台任务）"""
import os
import threading
import uuid
from datetime import date
from typing import Dict, List, Optional

from ..utils.calendar import TradingCalendar
from .ledger import Ledger
from .weights import DEFAULT_WEIGHTS
from . import picks as picks_mod

LEDGER_DB = os.environ.get(
    'ONE_TWO_LEDGER',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'data', 'one_two_ledger.db'))
JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def default_weights() -> dict:
    return DEFAULT_WEIGHTS


def _today() -> str:
    return date.today().strftime('%Y%m%d')


def _load_concept_map() -> dict:
    """加载 data/concept_map.json，返回 {概念名: {members, partial, source}}（空→{}）。"""
    import json as _json
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'data', 'concept_map.json')
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                d = _json.load(f)
            c = d.get('concepts') if isinstance(d, dict) else None
            if isinstance(c, dict):
                return c
    except Exception:
        pass
    return {}


def _volume_health_data(tdx, code: str, trade_date: str,
                        lookback: int = 60) -> tuple:
    """TDX 日线算量能：今日成交量 + 前 lookback 日最高成交量。返回 (today_vol, prev_high_vol)。"""
    from datetime import datetime
    market = 'bj' if code.startswith(('8', '4', '92')) else ('sh' if code.startswith('6') else 'sz')
    try:
        df = tdx.read_daily(code, market)
        if df is None or df.empty or 'volume' not in df.columns:
            return 0, 0
        df = df.reset_index(drop=True)
        target = datetime.strptime(trade_date, '%Y%m%d').date()
        pos = None
        for i in range(len(df) - 1, -1, -1):
            if df['trade_date'].iloc[i] <= target:
                pos = i
                break
        if pos is None:
            return 0, 0
        today_vol = float(df.iloc[pos]['volume'] or 0)
        start = max(0, pos - lookback)
        prev_high = float(df['volume'].iloc[start:pos].max()) if pos > start else 0
        return today_vol, prev_high
    except Exception:
        return 0, 0


def build_pick_context(pool: List, tdx=None, calendar=None,
                       concept_map: Optional[dict] = None,
                       state_df=None, trade_date: Optional[str] = None) -> Dict:
    """组装 8 维打分所需上下文（板块聚合/高能量梯队/情绪趋势/量能/概念叠加/地位）。

    返回 {"scored": {code: {sector, zt_trend, ladder_at_2, ladder_at_3,
                            today_vol, prev_high_vol, concept_count, upper_same_theme}}}
    """
    calendar = calendar or TradingCalendar()
    concept_map = concept_map or {}
    td = trade_date or _today()
    sector = {}
    for lu in pool or []:
        ind = str(getattr(lu, 'board_type', '') or '未知')
        s = sector.setdefault(ind, {'zt_count': 0, 'max_consecutive': 1, 'is_new_theme': False})
        s['zt_count'] += 1
        s['max_consecutive'] = max(s['max_consecutive'], lu.consecutive or 1)
    ladder = {2: 0, 3: 0, 4: 0}
    for lu in pool or []:
        c = lu.consecutive or 1
        if 2 <= c <= 4:
            try:
                if str(lu.limit_up_time).replace(':', '')[:4] == '0925':
                    ladder[c] += 1
            except Exception:
                pass
    ladder_at_2 = ladder[2] >= max(ladder[3], ladder[4], 1)
    ladder_at_3 = ladder[3] > ladder[2] and ladder[3] >= max(ladder[2], ladder[4], 1)
    zt_trend = 'neutral'
    if state_df is not None and len(state_df) >= 3:
        try:
            zts = list(state_df['limit_up'].dropna().tail(3))
            if len(zts) >= 3 and zts[-1] < zts[-2] < zts[-3]:
                zt_trend = 'double_ice'
            elif len(zts) >= 3 and zts[-1] > zts[-2] > zts[-3]:
                zt_trend = 'double_climax'
        except Exception:
            pass
    upper_concepts = set()
    for lu in pool or []:
        if (lu.consecutive or 1) >= 2:
            for cn, info in (concept_map or {}).items():
                if str(lu.code) in (info.get('members') or {}):
                    upper_concepts.add(cn)
    code_to_concepts = {}
    for lu in pool or []:
        hits = [cn for cn, info in (concept_map or {}).items()
                if str(lu.code) in (info.get('members') or {})]
        code_to_concepts[str(lu.code)] = hits
    scored = {}
    for lu in pool or []:
        code = str(lu.code)
        ind = str(getattr(lu, 'board_type', '') or '未知')
        tv, pv = 0, 0
        if tdx is not None:
            tv, pv = _volume_health_data(tdx, code, td)
        scored[code] = {
            'sector': sector.get(ind, {}),
            'zt_trend': zt_trend,
            'ladder_at_2': ladder_at_2,
            'ladder_at_3': ladder_at_3,
            'today_vol': tv, 'prev_high_vol': pv,
            'concept_count': len(code_to_concepts.get(code, [])),
            'concept_coverage': len(concept_map),
            'upper_same_theme': bool(set(code_to_concepts.get(code, [])) & upper_concepts),
        }
    return {'scored': scored}


def _grade_next_day(prev_close: float, close: float, code: str) -> tuple:
    """纯分级：次日涨幅 → (next_result, hit)。zt 或涨≥3% 记命中。"""
    chg = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0
    limit = 19.6 if code.startswith(('30', '68')) else (29.4 if code.startswith(('8', '4', '92')) else 9.8)
    if chg >= limit:
        return 'zt', 1
    if chg >= 3:
        return 'up3', 1
    if chg >= 0:
        return 'up', 0
    if chg >= -3:
        return 'flat', 0
    return 'down', 0


def _verify_one(row) -> tuple:
    """真实验证：TDX 次日行情 → (next_result, hit, auction_ratio)。"""
    from datetime import datetime
    from ..data.tdx_reader import TdxReader
    tdx = TdxReader()
    cal = TradingCalendar()
    d = datetime.strptime(row['pick_date'], '%Y%m%d').date()
    nxt = cal.next_trading_day(d, offset=1)
    if nxt is None:
        return None, None, None
    code = row['code']
    market = 'bj' if code.startswith(('8', '4', '92')) else ('sh' if code.startswith('6') else 'sz')
    try:
        df = tdx.read_daily(code, market)
        if df is None or df.empty:
            return None, None, None
        df = df.reset_index(drop=True)
        mask = df['trade_date'] == nxt
        if not mask.any():
            return None, None, None
        pos = int(mask.idxmax())
        if pos == 0:
            return None, None, None
        prev_c = float(df.iloc[pos - 1]['close'])
        close = float(df.iloc[pos]['close'])
        result, hit = _grade_next_day(prev_c, close, code)
        return result, hit, None
    except Exception:
        return None, None, None


def run_picks(pool: List, weights: Optional[dict] = None,
              ctx: Optional[dict] = None, trade_date: Optional[str] = None,
              top_n: int = 8, ledger: Optional[Ledger] = None, tdx=None) -> Dict:
    """盘后精选：过滤候选 → 组装上下文 → 8 维打分 → 落库。返回 {total, picks, date}"""
    weights = weights or default_weights()
    ledger = ledger or Ledger(LEDGER_DB)
    td = trade_date or _today()
    if ctx is None:
        from ..data.tdx_reader import TdxReader
        ctx = build_pick_context(pool, tdx=tdx or TdxReader(),
                                concept_map=_load_concept_map(), trade_date=td)
    cands = picks_mod.filter_candidates(pool or [])
    picks_out = []
    for c in cands[:50]:
        lu = c['lu']
        d = picks_mod.compute_score(lu, (ctx or {}).get('scored', {}).get(c['code'], {}), weights)
        picks_out.append({
            'code': c['code'], 'name': c['name'],
            'score': d['score'], 'dimensions': d['dimensions'], 'tactic': d['tactic'],
            'mcap': float(lu.float_market_cap or 0),
        })
    picks_out.sort(key=lambda x: x['score'], reverse=True)
    picks_out = picks_out[:top_n]
    if ledger:
        ledger.clear_day(td)   # 覆盖当日，保证今日精选=最新一批
        for p in picks_out:
            ledger.record_pick(td, p['code'], p['name'], p['score'],
                               p['dimensions'], p['tactic'], mcap=p.get('mcap'))
    return {'total': len(picks_out), 'picks': picks_out, 'date': td}


def verify_pending(ledger: Optional[Ledger] = None, verify_fake=None) -> int:
    """验证所有未验证记录（次日结果）。verify_fake 供测试注入。"""
    ledger = ledger or Ledger(LEDGER_DB)
    pending = ledger.get_pending()
    verified = 0
    for row in pending:
        try:
            if verify_fake:
                next_result, hit, ratio = verify_fake(row)
            else:
                next_result, hit, ratio = _verify_one(row)
            if next_result and hit is not None:
                ledger.verify_pick(row['pick_date'], row['code'], next_result, hit, ratio)
                verified += 1
        except Exception:
            continue
    return verified


def start_job(kind: str, params: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        JOBS[job_id] = {'status': 'running', 'progress': '排队中', 'error': None}
    def _worker():
        try:
            with _JOBS_LOCK:
                JOBS[job_id]['progress'] = '运行中…'
            if kind == 'picks':
                from ..data.akshare_fetcher import AkshareFetcher
                pool = AkshareFetcher().get_limit_up_pool()
                r = run_picks(pool, trade_date=params.get('trade_date') or None)
                with _JOBS_LOCK:
                    JOBS[job_id]['result'] = r
            with _JOBS_LOCK:
                JOBS[job_id]['status'] = 'done'
        except Exception as e:
            with _JOBS_LOCK:
                JOBS[job_id]['status'] = 'error'
                JOBS[job_id]['error'] = str(e)
    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _JOBS_LOCK:
        return dict(JOBS.get(job_id) or {}) or None