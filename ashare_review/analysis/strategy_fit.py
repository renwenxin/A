# -*- coding: utf-8 -*-
"""战法适配：把「三战法 × 行情分类」回测结果整理成"什么行情能做什么"的判定表。

**单一口径（2026-09-22 改）**：本模块只保留**一个行情轴** —— **广度口径（斩龙 v1）**：
涨停/跌停家数 + 炸板率 + 最高连板 + 指数 vs MA20 → 6 个状态（BSTATES）。

原「本工程分类器」口径（上证缠论趋势 + 国证2000 相对强弱 + 情绪温度，见 load()）**降级为
跨口径校验轴**：它没有自己的矩阵表、没有自己的灯、没有自己的"今日结论"；唯一作用是
对同一格（战法 × 广度状态）比较**两个互相独立的期望**：
  · 广度口径笔均 = 这一格自己的笔均；
  · regime 口径期望 = 按这一格的 regime 构成，去加权**本工程 regime 口径自己的各档笔均**
    avg_regime[r]（在**全样本**上按 regime 分组算出，与这一格无关）。
符号相反 → 该格判「不稳」；同向或任一侧无数据 → 不改变结论。

⚠️ 注意这里是**两个口径的独立统计**相比，而不是"把同一格成交再分桶"：
后者是同一批成交的两种分桶，n 加权平均恒等于原笔均，符号永远相同（数学上恒等）。
现在的写法可以真的翻号 —— 例如某个广度状态自身笔均为正，但它的成交大多落在
regime 口径里评分为负的档位上。

判定顺序（`_verdict_unified`，第一条命中即返回）：
  1 样本不足 → 2 跨版本不一致 → 3 跨时点口径相反 → 4 跨阈值翻转 → 5 跨口径相反
  → 6 停用 → 7 首选 → 8 可用 → 9 谨慎；
即**四个稳健性轴（跨版本 / 跨阈值 / 跨时点 / 跨口径）任一翻转 → 「不稳」**。

适用范围：**只覆盖当前专精的两套战法** —— 启动突破 V3 / 一进二接力。
（冰点抄底虽在同批回测里，但不在专精范围内，本模块不纳入判定。）

证据来源（本机数据，只读）：
  ashare_review/data/strategy_regime/三战法_行情分类回测_*.xlsx
  → 逐笔表（含 regime 列）重算「交易数 / 胜率 / 笔均收益 / 盈亏比」
  → 逐笔表另含 signal_date：按 signal_date → 广度状态 分组（信号在收盘、买入在次日开盘）
  → 同批 6 个工作簿跨版本比对，得到"跨版本一致性"，用于区分稳健结论与口径敏感结论

实时状态：
  ashare_review/analysis/breadth_state.py
  → 广度口径（TDX 本地日线自算，落盘 breadth_state_250d.json，是**唯一**判定轴）
  ashare_review/analysis/strategy_regime/{market_state,regime}.py
  → 本工程 regime 口径，仅用于跨口径校验（独立缓存 market_state_live.csv）

⚠️ 本模块只做"历史分组统计 + 条件判定"，不预测涨跌、不构成投资建议。
"""
import os
import glob
import time
from datetime import date, timedelta

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(os.path.dirname(_DIR), 'data', 'strategy_regime')
LIVE_CACHE = os.path.join(_DATA, 'market_state_live.csv')

STATES = ['强势趋势', '题材轮动', '冰点超跌', '震荡观望', '弱市回调', '退潮下跌']
STRATEGIES = {'逐笔-启动突破V3': '启动突破V3', '逐笔-1进2接力': '一进二接力'}
MIN_N_STATS = 30      # 统计口径最低样本
MIN_N_ANY = 15        # 低于此值直接标"样本不足"

LEVELS = {
    '首选': dict(color='#059669', icon='🟢', desc='历史正期望且胜率过半，可正常做'),
    '可用': dict(color='#0891b2', icon='🔵', desc='历史正期望，可做'),
    '谨慎': dict(color='#d97706', icon='🟡', desc='正期望但胜率偏低，靠盈亏比吃饭：降仓、快进快出'),
    '停用': dict(color='#dc2626', icon='🔴', desc='历史负期望，不做'),
    '不稳': dict(color='#7c3aed', icon='🟣',
                 desc='稳健性轴翻转（跨版本/跨阈值/跨时点/跨口径任一）：不要据此下结论'),
    '样本不足': dict(color='#6b7280', icon='⚪', desc='样本太少，不足以判断'),
}

_memo = {}


def _workbooks():
    """按修改时间升序返回三战法工作簿"""
    fs = glob.glob(os.path.join(_DATA, '三战法_行情分类回测_*.xlsx'))
    return sorted(fs, key=os.path.getmtime)


def _stats_from(book):
    """从逐笔表重算矩阵：{战法: {行情: {n, win, avg, pf}}}"""
    out = {}
    for sheet, label in STRATEGIES.items():
        if sheet not in book.sheet_names:
            continue
        d = book.parse(sheet)
        if 'regime' not in d.columns or 'net_ret' not in d.columns:
            continue
        d = d[d['regime'].notna()]
        for c in ('buy_price', 'sell_price'):
            if c in d.columns:
                d = d[(d[c].fillna(0) > 0)]
        rows = {}
        for st in STATES:
            g = d[d['regime'] == st]
            n = len(g)
            if n == 0:
                rows[st] = dict(n=0, win=None, avg=None, pf=None)
                continue
            r = g['net_ret'].astype(float)
            wins, loss = r[r > 0], r[r <= 0]
            rows[st] = dict(
                n=n, win=round((r > 0).mean() * 100, 1), avg=round(r.mean(), 2),
                pf=round(wins.mean() / abs(loss.mean()), 2) if len(wins) and len(loss) else None)
        out[label] = rows
    return out


def _days_per_state(book):
    """行情分类日报 → 每种行情的天数"""
    if '行情分类日报' not in book.sheet_names:
        return {}
    d = book.parse('行情分类日报')
    if 'regime' not in d.columns:
        return {}
    vc = d['regime'].value_counts().to_dict()
    return {st: int(vc.get(st, 0)) for st in STATES}


def load(force=False):
    """主入口：读最新工作簿 + 跨版本一致性 + 判定"""
    books = _workbooks()
    if not books:
        return {'error': '未找到 三战法_行情分类回测_*.xlsx'}
    newest = books[-1]
    sig = (newest, os.path.getmtime(newest))
    if not force and _memo.get('sig') == sig:
        return _memo['data']

    book = pd.ExcelFile(newest)
    matrix = _stats_from(book)
    days = _days_per_state(book)

    # 跨版本稳定性
    variants = []
    for f in books:
        try:
            variants.append((os.path.basename(f), _stats_from(pd.ExcelFile(f))))
        except Exception:
            continue
    stability = {}
    for label in matrix:
        stability[label] = {}
        for st in STATES:
            sign, avgs, ns = [], [], []
            for _, m in variants:
                v = m.get(label, {}).get(st)
                if v and v.get('n'):
                    sign.append(1 if v['avg'] > 0 else -1)
                    avgs.append(v['avg'])
                    ns.append(v['n'])
            pos = sum(1 for s in sign if s > 0)
            stability[label][st] = dict(
                n_ver=len(sign), n_pos=pos, n_neg=len(sign) - pos,
                avg_min=round(min(avgs), 2) if avgs else None,
                avg_max=round(max(avgs), 2) if avgs else None,
                n_min=min(ns) if ns else 0, n_max=max(ns) if ns else 0,
                consistent=(len(set(sign)) == 1) if sign else None)

    # 判定
    verdicts = {}
    for label in matrix:
        verdicts[label] = {}
        for st in STATES:
            verdicts[label][st] = _verdict(matrix[label].get(st, {}), stability[label][st])

    # 回测区间（从逐笔表日期推）
    win_txt = ''
    try:
        d = book.parse('逐笔-启动突破V3')
        a = pd.to_datetime(d['buy_date'].astype(str), errors='coerce').min()
        b = pd.to_datetime(d['buy_date'].astype(str), errors='coerce').max()
        win_txt = '%s ~ %s' % (a.date(), b.date())
    except Exception:
        pass

    data = dict(
        source=os.path.basename(newest), source_mtime=time.strftime(
            '%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(newest))),
        n_variants=len(variants), window=win_txt,
        states=STATES, days=days, matrix=matrix, stability=stability,
        verdicts=verdicts, levels=LEVELS,
        variant_names=[os.path.basename(f) for f, _ in variants],
    )
    _memo['sig'] = sig
    _memo['data'] = data
    return data


def _verdict(v, s):
    """把统计量转成红黄绿灯（规则写在页面上）

    两个稳健性轴都要看：
      · 跨版本（同一口径、6 个不同候选池/调仓实现）——方向不一致 → 「不稳」
      · 阈值（仅广度口径有阈值）——在页面上单列一列，不混进灯
    """
    if not v or not v.get('n'):
        return dict(level='样本不足', reason='该行情下无成交样本')
    n, win, avg = v['n'], v['win'], v['avg']
    n_neg = s.get('n_neg', 0)
    n_pos = s.get('n_pos', 0)
    n_ver = s.get('n_ver', 0)
    if n < MIN_N_ANY:
        return dict(level='样本不足', reason='仅 %d 笔（<%d），不足以判断' % (n, MIN_N_ANY))
    if n_ver > 1 and n_pos > 0 and n_neg > 0:
        return dict(level='不稳', reason='%d 个回测版本方向不一致（笔均 %s~%s%%）——结论不可靠' % (
            n_ver, s.get('avg_min'), s.get('avg_max')))
    if avg <= 0 and n_neg == n_ver and n_ver:
        return dict(level='停用', reason='%d 个回测版本全部为负（%s~%s%%）' % (
            n_ver, s.get('avg_min'), s.get('avg_max')))
    if avg <= 0:
        lv = '停用' if n >= MIN_N_STATS else '样本不足'
        return dict(level=lv, reason='笔均 %+.2f%%，%d/%d 个版本为负' % (avg, n_neg, n_ver))
    if win is not None and win >= 50 and n_pos == n_ver and n >= MIN_N_STATS:
        return dict(level='首选', reason='胜率 %.1f%%，%d/%d 个版本为正' % (win, n_pos, n_ver))
    if win is not None and win >= 45:
        return dict(level='可用', reason='胜率 %.1f%%，笔均 %+.2f%%%s' % (
            win, avg, '（样本 %d 笔，偏少）' % n if n < MIN_N_STATS else ''))
    return dict(level='谨慎', reason='胜率仅 %.1f%%，靠盈亏比 %.2f 取胜：必须降仓 + 快进快出' % (
        win, v.get('pf') or 0))


def _flipped(x):
    """该稳健性轴是否给出**明确否定**。

    只有 False（含 numpy.bool_(False)）才算翻转；None / 缺失 / 空 = 样本不足，不算翻转。
    注意不能用 `x is False`：pandas/numpy 比较出来的是 numpy.bool_，`is False` 恒不成立。
    """
    return x is not None and not x


def _verdict_unified(v, stab, sens, conv, cross):
    """统一口径的唯一判定函数（广度口径单轴 + 四个稳健性轴）。

    参数（全部来自**同一格**：某战法 × 某广度状态）：
      v     统计量 {n, win, avg, pf}
      stab  跨版本一致性 {n_ver, n_pos, n_neg, avg_min, avg_max}
      sens  跨阈值敏感性 {sign_stable, avg_min, avg_max}
      conv  跨时点口径 {stable, t1, same}（T-1 广度 vs 同日广度；stable=False 即符号相反）
      cross 跨口径校验 {ok, breadth_avg, regime_avg, n_regimes}
            （该格广度口径笔均 vs 按该格 regime 构成加权的「本工程口径各档笔均」）

    顺序（第一条命中即返回，per-轴翻转为最高优先级）：
      n<MIN_N_ANY → 跨版本 → 跨时点 → 跨阈值 → 跨口径 → avg<=0 → 首选 → 可用 → 谨慎
    任一稳健性轴给出**明确否定**（False，而非"样本不足"的 None）→ 「不稳」。
    """
    stab, sens, conv, cross = stab or {}, sens or {}, conv or {}, cross or {}
    n = (v or {}).get('n') or 0
    win, avg = (v or {}).get('win'), (v or {}).get('avg')
    if n < MIN_N_ANY:
        if not n:
            return dict(level='样本不足', reason='该广度状态在本区间内无成交样本')
        return dict(level='样本不足', reason='仅 %d 笔（<%d），不足以判断' % (n, MIN_N_ANY))
    # 轴 1：跨版本（同一口径下 6 个不同候选池/调仓实现）
    if stab.get('n_ver', 0) > 1 and stab.get('n_pos', 0) > 0 and stab.get('n_neg', 0) > 0:
        return dict(level='不稳', reason='跨版本方向不一致：%d 个回测版本 %d 正 / %d 负（笔均 %s~%s%%）' % (
            stab.get('n_ver', 0), stab.get('n_pos', 0), stab.get('n_neg', 0),
            stab.get('avg_min'), stab.get('avg_max')))
    # 轴 2：跨时点口径（T-1 广度 vs 同日广度，两种都在买入前可得）
    if _flipped(conv.get('stable')):
        return dict(level='不稳', reason='跨时点口径符号相反：T-1 广度 %+.2f%% / 同日广度 %+.2f%%' % (
            conv.get('t1') or 0, conv.get('same') or 0))
    # 轴 3：跨阈值（炸板率 35/40/45）
    if _flipped(sens.get('sign_stable')):
        return dict(level='不稳', reason='跨阈值符号翻转：炸板率 35/40/45%% 下笔均 %+.2f%%~%+.2f%%' % (
            sens.get('avg_min') or 0, sens.get('avg_max') or 0))
    # 轴 4：跨口径（该格广度口径笔均 vs 按该格 regime 构成加权的本工程口径各档笔均）——只做校验，不再单独成表
    if _flipped(cross.get('ok')):
        return dict(level='不稳', reason='跨口径符号相反：广度口径 %+.2f%% / 本工程口径各档加权 %+.2f%%（%d 个 regime 档）' % (
            cross.get('breadth_avg') or 0, cross.get('regime_avg') or 0, cross.get('n_regimes') or 0))
    if avg is not None and avg <= 0:
        if n >= MIN_N_STATS:
            return dict(level='停用', reason='笔均 %+.2f%%，%d 笔——历史负期望' % (avg, n))
        return dict(level='样本不足', reason='笔均 %+.2f%%，但仅 %d 笔（<%d）不足以定论' % (
            avg, n, MIN_N_STATS))
    if win is not None and win >= 50 and n >= MIN_N_STATS:
        return dict(level='首选', reason='胜率 %.1f%%，笔均 %+.2f%%，%d 笔' % (win, avg, n))
    if win is not None and win >= 45:
        return dict(level='可用', reason='胜率 %.1f%%，笔均 %+.2f%%%s' % (
            win, avg, '（样本 %d 笔，偏少）' % n if n < MIN_N_STATS else ''))
    return dict(level='谨慎', reason='胜率仅 %.1f%%，靠盈亏比 %.2f 取胜：必须降仓 + 快进快出' % (
        win if win is not None else 0, (v or {}).get('pf') or 0))


# ───────────────────────── 实时行情状态 ─────────────────────────
def current(force=False):
    """最新交易日的行情状态（与「行情诊断」同口径，独立缓存，5 秒左右）"""
    key = ('current', time.strftime('%Y-%m-%d'))
    if not force and key in _memo:
        return _memo[key]
    try:
        from ..data.tdx_reader import TdxReader
        from .strategy_regime import market_state as ms
        from .strategy_regime import regime as rg

        tdx = TdxReader()
        idx = tdx.read_daily('000001', 'sh')
        idx['trade_date'] = pd.to_datetime(idx['trade_date']).dt.date
        last_td = idx['trade_date'].max()

        df = None
        if not force and os.path.exists(LIVE_CACHE):
            try:
                c = pd.read_csv(LIVE_CACHE)
                c['date'] = pd.to_datetime(c['date']).dt.date
                if c['date'].max() >= last_td:
                    df = c
            except Exception:
                df = None
        if df is None:
            end = last_td
            start = end - timedelta(days=200)
            raw = ms.build_market_state(tdx, start, end, cache_path=LIVE_CACHE)
            raw['date'] = pd.to_datetime(raw['date']).dt.date
            df = raw
        d = rg.compute_regime(df)
        row = d.iloc[-1].to_dict()
        row['date'] = str(row['date'])
        row['stale'] = (df['date'].max() < last_td) if len(df) else True
        _memo[key] = row
        return row
    except Exception as e:
        return {'error': str(e)}


def page_data(force=False):
    """页面数据：**统一到广度口径单轴**（d['fit']），本工程 regime 只留作跨口径校验（d['regime']）。

    保留的顶层键：window / source / n_variants / variant_names / levels / current /
    crosstab_rows / n_common。旧的顶层 regime 矩阵、灯、今日结论不再单独暴露
    （全部收进 d['regime']，仅作证据），避免页面上出现两套并列判定。
    """
    d = load(force=force)
    d['current'] = current(force=force)
    cur = d['current']
    # ── 本工程 regime 口径：降级为「跨口径校验轴」，不再有自己的灯/今日结论 ──
    regime = dict(states=d.get('states'), days=d.get('days'), matrix=d.get('matrix'),
                  verdicts=d.get('verdicts'), stability=d.get('stability'))
    if cur.get('regime'):
        regime['today'] = {}
        for label in STRATEGIES.values():
            rg_st = cur['regime']
            regime['today'][label] = dict(
                stats=d.get('matrix', {}).get(label, {}).get(rg_st, {}),
                verdict=d.get('verdicts', {}).get(label, {}).get(rg_st, {}),
                stability=d.get('stability', {}).get(label, {}).get(rg_st, {}))
    d['regime'] = regime
    for k in ('states', 'days', 'matrix', 'verdicts', 'stability', 'today'):
        d.pop(k, None)
    # ── 唯一判定轴：广度口径（斩龙 v1） ──
    try:
        d.update(breadth_data(force=force))
    except Exception as e:
        d['fit_error'] = str(e)
    return d


# ═══════════════════════ 第二套：广度口径（斩龙 v1） ═══════════════════════
def _matrix_for_states(states_by_date, book_path):
    """把逐笔按「signal_date → 广度状态」分组重算"""
    bk = pd.ExcelFile(book_path)
    out = {}
    for sheet, label in STRATEGIES.items():
        if sheet not in bk.sheet_names:
            continue
        t = bk.parse(sheet)
        if 'regime' not in t.columns or 'net_ret' not in t.columns:
            continue
        t = t[t['regime'].notna()]
        t = t[t['buy_price'].fillna(0) > 0]
        key = t['signal_date'].astype(str) if 'signal_date' in t.columns else t['buy_date'].astype(str)
        t = t.assign(_st=[states_by_date.get(x) for x in key])
        rows = {}
        for st in BSTATES:
            g = t[t._st == st]
            n = len(g)
            if n == 0:
                rows[st] = dict(n=0, win=None, avg=None, pf=None)
                continue
            r = g['net_ret'].astype(float)
            w, l = r[r > 0], r[r <= 0]
            rows[st] = dict(n=n, win=round((r > 0).mean() * 100, 1), avg=round(r.mean(), 2),
                            pf=round(w.mean() / abs(l.mean()), 2) if len(w) and len(l) else None)
        out[label] = rows
    return out


def _load_trades(book_path):
    """把工作簿里两套战法的逐笔一次性读进来（含 signal_date / net_ret / regime），供多次分组复用

    过滤条件与 `_stats_from` **保持一致**（regime 非空 + buy_price/sell_price 均 >0），
    这样「广度口径矩阵」与「本工程 regime 口径矩阵」永远建立在同一批成交上，
    跨口径比较才是有意义的（否则两边样本不同，比较的是两批成交而不是两种口径）。
    """
    try:
        bk = pd.ExcelFile(book_path)
    except Exception:
        return None
    out = {}
    for sheet, label in STRATEGIES.items():
        if sheet not in bk.sheet_names:
            continue
        t = bk.parse(sheet)
        if 'regime' not in t.columns or 'net_ret' not in t.columns:
            continue
        t = t[t['regime'].notna()]
        for c in ('buy_price', 'sell_price'):
            if c in t.columns:
                t = t[t[c].fillna(0) > 0]
        key = t['signal_date'].astype(str) if 'signal_date' in t.columns else t['buy_date'].astype(str)
        out[label] = pd.DataFrame({'key': key.values, 'ret': t['net_ret'].astype(float).values,
                                   'regime': t['regime'].astype(str).values})
    return out


def _regime_matrix_from_trades(trades):
    """本工程 regime 口径矩阵：{label: {regime: {n, win, avg, pf}}}（按**逐笔自带的 regime** 分组）。

    等价于 `_stats_from(pd.ExcelFile(newest))`，只是复用已载入的逐笔，不再解析一次工作簿。
    这个矩阵是跨口径校验里 `avg_regime[r]` 的唯一来源 —— 它是**全样本**上每档 regime 的
    笔均，与某一格（战法 × 广度状态）无关，因此可以给出独立的期望符号。
    """
    out = {}
    for label, t in (trades or {}).items():
        rows = {}
        for st in STATES:
            r = t['ret'][t['regime'] == st]
            n = len(r)
            if n == 0:
                rows[st] = dict(n=0, win=None, avg=None, pf=None)
                continue
            w, l = r[r > 0], r[r <= 0]
            rows[st] = dict(n=n, win=round((r > 0).mean() * 100, 1), avg=round(r.mean(), 2),
                            pf=round(w.mean() / abs(l.mean()), 2) if len(w) and len(l) else None)
        out[label] = rows
    return out


def _cross_caliber(trades, states_by_date, m_main, m_regime):
    """跨口径校验（唯一的用途：让本工程 regime 口径能判「不稳」，而不是自成一套表）。

    对每一格（战法 × 广度状态）比较两个**独立**的期望：

      breadth_avg = 该格的广度口径笔均（`m_main` 里的 avg，即这一格自己的笔均）；
      regime_avg  = 按该格的 regime 构成加权**本工程 regime 口径自己的各档笔均**：
                    Σ_r n_{st,r} · avg_regime[r] / Σ_r n_{st,r}
                    其中 n_{st,r} = 该格成交里 regime == r 的笔数，
                    avg_regime[r] = `m_regime`（全样本按 regime 分组统计）里第 r 档的笔均。
                    只统计 **avg_regime[r] 非空且 n_{st,r} > 0** 的档位（也算进分母）。

    这是关键区别：avg_regime[r] 来自 regime 口径在**全样本**上的独立统计，不是把这一格自己的
    成交再分桶（那种做法的 n 加权平均恒等于 breadth_avg，符号永远一致、永远测不出问题）。
    所以这里的符号可以真的相反 —— 例如某广度状态自身笔均为正，但它的成交大多落在
    regime 口径评分为负的档位上。

    参数：
      trades          {label: DataFrame[key, ret, regime]}（与广度矩阵同一批成交）
      states_by_date  {key: 广度状态}
      m_main          广度口径矩阵 {label: {state: {n, win, avg, pf}}}
      m_regime        本工程 regime 口径矩阵 {label: {regime: {n, win, avg, pf}}}

    返回 {label: {st: {ok, breadth_avg, regime_avg, n_regimes}}}
      ok = (breadth_avg>0) == (regime_avg>0)；两边任一无数据 → None（样本不足，不算翻转）
      n_regimes = 实际参与加权的 regime 档位数
    """
    out = {}
    for label, t in (trades or {}).items():
        x = t.assign(_st=[states_by_date.get(k) for k in t['key']])
        reg = (m_regime or {}).get(label, {}) or {}
        out[label] = {}
        for s in BSTATES:
            breadth_avg = (m_main.get(label, {}).get(s) or {}).get('avg')
            g = x[x._st == s]
            if not len(g):
                out[label][s] = dict(ok=None, breadth_avg=breadth_avg, regime_avg=None, n_regimes=0)
                continue
            num = den = 0.0
            n_reg = 0
            for r, sg in g.groupby('regime'):
                a = (reg.get(r) or {}).get('avg')
                if a is None or pd.isna(a):
                    continue
                k = len(sg)
                num += float(a) * k
                den += k
                n_reg += 1
            regime_avg = round(num / den, 2) if den else None
            ok = None
            if breadth_avg is not None and regime_avg is not None:
                ok = bool((breadth_avg > 0) == (regime_avg > 0))
            out[label][s] = dict(ok=ok, breadth_avg=breadth_avg, regime_avg=regime_avg,
                                 n_regimes=n_reg)
    return out


def _matrix_from_trades(trades, states_by_date):
    """按 key → 广度状态 分组统计（复用已载入的逐笔）"""
    out = {}
    for label, t in (trades or {}).items():
        st = [states_by_date.get(k) for k in t['key']]
        rows = {}
        for s in BSTATES:
            r = t['ret'][[x == s for x in st]]
            n = len(r)
            if n == 0:
                rows[s] = dict(n=0, win=None, avg=None, pf=None)
                continue
            w, l = r[r > 0], r[r <= 0]
            rows[s] = dict(n=n, win=round((r > 0).mean() * 100, 1), avg=round(r.mean(), 2),
                           pf=round(w.mean() / abs(l.mean()), 2) if len(w) and len(l) else None)
        out[label] = rows
    return out


def breadth_data(force=False):
    """第二套矩阵 + 校验 + 阈值敏感性 + 两套口径对照"""
    from . import breadth_state as bs
    df, gen = bs.load()
    need = df is None or not len(df)
    if not need and force:
        # 只有缓存确实落后于最新交易日时才重扫（重扫约 30~40 秒）
        try:
            from ..data.tdx_reader import TdxReader
            idx = TdxReader().read_daily('000001', 'sh')
            last_td = str(pd.to_datetime(idx['trade_date']).dt.date.max())
            cached_last = str(pd.Series(df['date']).astype(str).max())
            need = cached_last < last_td
        except Exception:
            need = False
    if need:
        df = bs.refresh()
        gen = None
    if df is None or not len(df):
        return {'fit_error': '广度状态未生成'}
    df = df.copy()
    df['date'] = df['date'].astype(str)
    asof = dict(zip(df['date'], df['state_asof']))
    same = dict(zip(df['date'], df['state_same_day']))

    books = _workbooks()
    newest = books[-1] if books else None
    if newest is None:
        return {'fit_error': '未找到回测工作簿'}
    trades_main = _load_trades(newest)
    m_main = _matrix_from_trades(trades_main, asof)
    m_same = _matrix_from_trades(trades_main, same)
    # 本工程 regime 口径矩阵（跨口径校验要用的 avg_regime[r]）：从同一批逐笔重算一次，
    # 等价于 _stats_from(pd.ExcelFile(newest))，但不再解析一次工作簿
    m_regime = _regime_matrix_from_trades(trades_main)

    # 跨版本一致性（同一套广度状态；每个工作簿只解析一次）
    stab = {}
    variants = []
    for f in books:
        t = _load_trades(f)
        if t:
            variants.append(_matrix_from_trades(t, asof))
    for label in m_main:
        stab[label] = {}
        for st in BSTATES:
            sign, avgs, ns = [], [], []
            for m in variants:
                v = m.get(label, {}).get(st)
                if v and v.get('n'):
                    sign.append(1 if v['avg'] > 0 else -1)
                    avgs.append(v['avg'])
                    ns.append(v['n'])
            stab[label][st] = dict(
                n_ver=len(sign), n_pos=sum(1 for s in sign if s > 0),
                n_neg=sum(1 for s in sign if s < 0),
                avg_min=round(min(avgs), 2) if avgs else None,
                avg_max=round(max(avgs), 2) if avgs else None,
                n_min=min(ns) if ns else 0, n_max=max(ns) if ns else 0,
                consistent=(len(set(sign)) == 1) if sign else None)

    # 阈值敏感性（炸板率 35/40/45）
    sens = {}
    for th in (35, 40, 45):
        s = _states_with_threshold(df, th)
        mm = _matrix_from_trades(trades_main, dict(zip(df['date'], s)))
        for label, rows in mm.items():
            for st, v in rows.items():
                if not v.get('n'):
                    continue
                cur = sens.setdefault((label, st), dict(avgs=[], ns=[]))
                cur['avgs'].append(v['avg'])
                cur['ns'].append(v['n'])
    sens_out = {label: {} for label in m_main}
    for (label, st), v in sens.items():
        lo, hi = min(v['avgs']), max(v['avgs'])
        sens_out[label][st] = dict(avg_min=round(lo, 2), avg_max=round(hi, 2),
                                   sign_stable=bool((lo > 0 and hi > 0) or (lo < 0 and hi < 0)))

    # 每日数（回测区间内）
    win = df[(df.date >= '2025-08-08') & (df.date <= '2026-08-07')]
    days = {st: int((win['state_asof'] == st).sum()) for st in BSTATES}

    # 四个稳健性轴的素材 —— 跨时点口径（T-1 广度 vs 同日广度）
    conv = {}
    for label in m_main:
        conv[label] = {}
        for st in BSTATES:
            a, s2 = m_main[label].get(st, {}), m_same.get(label, {}).get(st, {})
            if not a.get('n') or not s2.get('n'):
                conv[label][st] = dict(stable=None, t1=a.get('avg'), same=s2.get('avg'))
                continue
            conv[label][st] = dict(stable=bool((a['avg'] > 0) == (s2['avg'] > 0)),
                                   t1=a['avg'], same=s2['avg'])
    # 跨口径校验（本工程 regime 只在这里出现，只用于判「不稳」）
    # 期望侧用的是 regime 口径**全样本**各档笔均 m_regime，不是把这一格自己的成交再分桶
    cross = _cross_caliber(trades_main, asof, m_main, m_regime)

    # 统一判定：广度口径单轴 + 四个稳健性轴（任一轴翻转 → 不稳）
    verd = {label: {st: _verdict_unified(m_main[label].get(st, {}), stab[label][st],
                                         sens_out.get(label, {}).get(st, {}),
                                         conv[label].get(st, {}), cross[label].get(st, {}))
                    for st in BSTATES}
            for label in m_main}

    # 今日/下一交易日（最后一行 = 最新交易日的收盘广度）
    last = df.iloc[-1]
    cur_next = last['state_same_day']          # 由最新交易日收盘广度判定 → 下一交易日可操作
    cur_for_last = last['state_asof']          # 最新交易日当天可用的状态
    today = {}
    if cur_next:
        for label in m_main:
            today[label] = dict(
                stats=m_main[label].get(cur_next, {}),
                verdict=verd[label].get(cur_next, {}),
                stability=stab[label].get(cur_next, {}),
                sens=sens_out.get(label, {}).get(cur_next, {}),
                conv=conv[label].get(cur_next, {}),
                cross=cross[label].get(cur_next, {}))

    # 与斩龙自报结果的校验窗口
    w = df[(df.date >= '2026-08-21') & (df.date <= '2026-09-09')]
    retreat_dates = list(w[w['state_asof'] == '情绪退潮']['date'])

    # 同日交叉表：本工程 regime × 广度 state_asof
    crosstab_rows, n_common = [], 0
    try:
        import datetime as _dt
        from .strategy_regime import market_state as ms
        from .strategy_regime import regime as rg
        _our = os.path.join(_DATA, 'market_state.csv')
        st = rg.compute_regime(ms.load_state(_dt.date(2025, 8, 8), _dt.date(2026, 8, 7), _our))
        st['date'] = st['date'].astype(str)
        j = st[['date', 'regime']].merge(df[['date', 'state_asof']], on='date', how='inner')
        ct = pd.crosstab(j['regime'], j['state_asof'])
        n_common = int(len(j))
        for idx in ct.index:
            crosstab_rows.append(dict(
                regime=idx,
                vals=[int(ct.loc[idx, s]) if s in ct.columns else 0 for s in BSTATES],
                total=int(ct.loc[idx].sum())))
    except Exception:
        pass

    return dict(
        fit=dict(
            states=BSTATES, days=days, matrix=m_main, matrix_same_day=m_same,
            matrix_regime=m_regime,
            verdicts=verd, stability=stab, sens=sens_out, conv=conv, cross=cross,
            generated=gen, last_date=str(last['date']),
            last_row=dict(zt=int(last['zt']), dt=int(last['dt']), zb_rate=float(last['zb_rate']),
                          max_cons=int(last['max_cons']), idx_pct=(float(last['idx_pct']) if pd.notna(last['idx_pct']) else None),
                          state_same_day=cur_next, state_asof=cur_for_last),
            cur_next=cur_next, cur_for_last=cur_for_last, today=today,
            validation=dict(window='2026-08-21 ~ 2026-09-09', n_days=int(len(w)),
                            retreat_n=len(retreat_dates), retreat_dates=retreat_dates),
            sample_note='冰点（涨停≤30 且 最高连板≤3）在本区间从未触发：自算涨停家数最小 31 家',
        ),
        crosstab_rows=crosstab_rows, n_common=n_common)


def _states_with_threshold(df, zb_th):
    """按给定炸板率阈值重算（其余同斩龙 v1），返回 T-1 口径序列"""
    from .breadth_state import classify
    same = []
    for r in df.itertuples():
        if pd.isna(r.zt) or pd.isna(r.idx_pct):
            same.append(None)
            continue
        if r.dt >= 30 or r.idx_pct <= -2.5:
            same.append('极端行情')
        elif (r.zb_rate is not None and r.zb_rate >= zb_th) or \
             (r.yzt_mean is not None and r.yzt_mean <= -0.5 and r.dt >= 8):
            same.append('情绪退潮')
        elif r.zt <= 30 and (r.max_cons or 0) <= 3:
            same.append('冰点')
        elif r.zt >= 80 and (r.zb_rate or 0) <= 25 and (r.max_cons or 0) >= 5 and r.idx_above_ma20:
            same.append('强势主升')
        elif r.zt >= 50 and r.idx_above_ma20:
            same.append('强势震荡')
        else:
            same.append('弱势震荡')
    return pd.Series(same, index=df.index).shift(1).tolist()


BSTATES = ['强势主升', '强势震荡', '弱势震荡', '情绪退潮', '冰点', '极端行情']
