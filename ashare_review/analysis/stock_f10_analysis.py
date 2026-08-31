# -*- coding: utf-8 -*-
"""个股 F10 确定性分析 — 按 xx.txt 九维框架写成文。

纯函数（无 IO），输入 f10 档案 + 当日上下文，输出 9 维度结论 + 有人味总结。
所有结论只基于真实抓到的数据，不做任何猜测性编造；数据不足的维度降级为 🟡。

风格对齐复盘生成器：第一人称化叙述、条件式推演、"上涨方面/风险点"收束。
"""
from typing import Dict, List, Optional


def _num(v, default=None):
    """数值或 None"""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _yi(v):
    """元 → 'x.xx亿' / 'x.x万' / None"""
    v = _num(v)
    if v is None:
        return None
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f}万"
    return f"{v:.0f}"


def _pct(v, signed=False, digits=1):
    """数值 → 百分比字符串"""
    v = _num(v)
    if v is None:
        return "—"
    s = f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"
    return s + "%"


def _dim(key: str, title: str) -> dict:
    return {"key": key, "title": title, "level": "🟡",
            "verdict": "", "points": []}


def _add_point(dim, label, value, note=""):
    dim["points"].append({"label": label, "value": value, "note": note})


def _worst_level(*levels):
    order = {"🔴": 3, "🟡": 2, "🟢": 1}
    return max(levels, key=lambda x: order.get(x, 2))


def _first_item(items):
    for it in items or []:
        if it.get("item"):
            return it
    return None


def _business_keyword_hit(f10: dict, keyword: str) -> bool:
    """题材关键词是否命中主营构成/主营范围（判断『炒的是不是真业务』）。"""
    if not keyword:
        return False
    kw = keyword.strip()[:4]
    if len(kw) < 2:
        return False
    biz = f10.get("business") or {}
    hay = []
    for grp in ("by_product", "by_industry", "by_region"):
        for it in (biz.get(grp) or []):
            hay.append(it.get("item", ""))
    hay.append(biz.get("scope", ""))
    hay.append((f10.get("company") or {}).get("business_scope", ""))
    blob = "|".join([h for h in hay if h])
    # 关键字出现在主营里（含 2~4 字窗口）
    return any(k in blob for k in (kw, kw[:3], kw[:2]) if k)


def classify_seat(name: str) -> str:
    """席位分类：机构资金 vs 游资席位。"""
    n = name or ""
    if any(k in n for k in ("机构专用", "沪股通", "深股通", "北向", "港股通")):
        return "机构"
    return "游资"


# ======================================================================
# 九维度
# ======================================================================

def _company_value(f10: dict, ctx: dict) -> dict:
    d = _dim("company_value", "① 公司真实价值 · 靠什么赚钱")
    biz = f10.get("business") or {}
    top = _first_item(biz.get("by_product")) or _first_item(biz.get("by_industry"))
    sh = f10.get("shareholders") or {}
    ctrl = sh.get("controller") or ""
    # 金融/综合类常按地区披露主营，无产品拆分 → 用经营范围兜底
    if not top:
        scope = biz.get("scope") or (f10.get("company") or {}).get("business_scope") or ""
        reg_top = _first_item(biz.get("by_region"))
        if scope or reg_top:
            d["level"] = "🟢"
            desc = scope[:40] + ("…" if len(scope) > 40 else "") if scope else (
                f"主要集中「{reg_top['item']}」")
            d["verdict"] = f"主营见经营范围：{desc}"
            _add_point(d, "主营特征", desc, "未按产品拆分（金融/综合类常见）")
            if reg_top:
                _add_point(d, "收入最大分部", f"{reg_top['item']} ({reg_top.get('income_ratio', 0):.1f}%)")
        else:
            d["verdict"] = "主营构成数据不足"
            return d
        if ctrl:
            _add_point(d, "实控人", ctrl,
                       "国资背景" if _is_state_owned(ctrl) else "民营/其他")
        return d
    ratio = _num(top.get("income_ratio"), 0)
    name = top.get("item", "")
    if ratio >= 80:
        d["level"] = "🟡"
        d["verdict"] = f"主营高度集中在「{name}」，占收入 {ratio:.1f}%——业务清晰但单一，抗周期波动弱"
    elif ratio >= 60:
        d["level"] = "🟢"
        d["verdict"] = f"核心靠「{name}」赚钱，占收入 {ratio:.1f}%，相对聚焦"
    else:
        d["level"] = "🟢"
        d["verdict"] = f"业务较多元，第一大产品「{name}」只占 {ratio:.1f}%"
    _add_point(d, "第一大产品", f"{name} ({ratio:.1f}%)")
    gm = _num(top.get("gross_margin"))
    if gm is not None:
        _add_point(d, "毛利率", _pct(gm),
                   "偏低" if gm < 15 else "中等" if gm < 30 else "偏高")
    if ctrl:
        _add_point(d, "实控人", ctrl,
                   "国资背景" if _is_state_owned(ctrl) else "民营/其他")
    scope = biz.get("scope") or ""
    if scope:
        _add_point(d, "主营范围", scope[:40])
    return d


def _is_state_owned(holder: str) -> bool:
    return any(k in holder for k in ("国有资产", "人民政府", "国资委", "财政",
                                     "汇金", "社保基金", "国家开发", "中粮", "中国烟草"))


def _match_hot_board(ctx: dict, f10: dict) -> Optional[dict]:
    """当日涨停潮里，与该公司行业/题材匹配的板块（substring 匹配）。"""
    industry = (f10.get("valuation") or {}).get("board_name", "")
    boards = [b for b in (ctx.get("board_names") or []) if b]
    hot = ctx.get("hot_boards") or []
    for h in hot:
        hn = (h or {}).get("name", "") if isinstance(h, dict) else str(h)
        if not hn:
            continue
        if any(hn in x for x in boards if x) or (industry and hn in industry):
            return h
    return None


def _industry_cycle(f10: dict, ctx: dict) -> dict:
    d = _dim("industry_cycle", "② 行业周期 · 景气与风口")
    val = f10.get("valuation") or {}
    fin = f10.get("financial") or {}
    lrb = fin.get("lrb") or {}
    industry = val.get("board_name") or (f10.get("company") or {}).get("industry") or ""
    d["verdict"] = f"行业：{industry or '—'}"
    _add_point(d, "东财行业", industry or "—")

    yoy = _num(lrb.get("TOTAL_OPERATE_INCOME_YOY"))
    if yoy is None:
        d["verdict"] += "，营收趋势数据不足"
    else:
        if yoy > 15:
            tag, lv = "景气向上", "🟢"
        elif yoy >= 0:
            tag, lv = "温和扩张", "🟢"
        elif yoy >= -15:
            tag, lv = "承压", "🟡"
        else:
            tag, lv = "明显下滑", "🔴"
        d["level"] = lv
        _add_point(d, "营收同比(最新季)", _pct(yoy, signed=True), tag)

    # 今日该行业是否在涨停潮
    hit = _match_hot_board(ctx, f10)
    if hit:
        zc = _num(hit.get("zt_count"))
        d["level"] = _worst_level(d["level"], "🟢")
        _add_point(d, "今日板块热度", f"涨停{zc:.0f}家" if zc else "有涨停潮",
                   "在今日风口")
    elif ctx.get("hot_boards"):
        d["level"] = _worst_level(d["level"], "🟡")
        _add_point(d, "今日板块热度", "不在涨停潮前排", "题材轮动关注")
    else:
        _add_point(d, "今日板块热度", "无涨停潮数据", "")
    return d


def _financial_health(f10: dict, ctx: dict) -> dict:
    d = _dim("financial_health", "③ 财务健康 · 三表体检")
    fin = f10.get("financial") or {}
    lrb, zcf, xj = fin.get("lrb") or {}, fin.get("zcfzb") or {}, fin.get("xjllb") or {}
    if not lrb:
        d["verdict"] = "财务数据不足"
        return d
    flags = []

    income = _num(lrb.get("OPERATE_INCOME")) or _num(lrb.get("TOTAL_OPERATE_INCOME"))
    cost = _num(lrb.get("OPERATE_COST"))
    pnp = _num(lrb.get("PARENT_NETPROFIT"))
    dnp = _num(lrb.get("DEDUCT_PARENT_NETPROFIT"))
    if income and cost:
        gm = (income - cost) / income * 100
        _add_point(d, "毛利率", _pct(gm),
                   "盈利薄" if gm < 15 else "中等" if gm < 30 else "高毛利")
        if gm < 10:
            flags.append("🔴")
    if income and pnp is not None:
        nm = pnp / income * 100
        tag = "亏损" if nm < 0 else "薄利" if nm < 5 else "盈利健康"
        _add_point(d, "净利率(归母)", _pct(nm), tag)
        if nm < 0:
            flags.append("🔴")
        elif nm < 3:
            flags.append("🟡")
    if pnp is not None and dnp is not None and pnp > 0 and dnp < 0:
        _add_point(d, "利润质量", "扣非亏损但归母为正", "靠非经常性损益")
        flags.append("🟡")

    nco = _num(xj.get("NETCASH_OPERATE"))
    if nco is not None:
        _add_point(d, "经营现金流(最新季)", _yi(nco),
                   "现金流为负" if nco < 0 else "现金创造为正")
        if nco < 0:
            flags.append("🔴")
    ta, tl = _num(zcf.get("TOTAL_ASSETS")), _num(zcf.get("TOTAL_LIABILITIES"))
    if ta and tl:
        dr = tl / ta * 100
        _add_point(d, "资产负债率", _pct(dr),
                   "偏高" if dr > 70 else "中性" if dr > 50 else "稳健")
        if dr > 70:
            flags.append("🔴")
        elif dr > 60:
            flags.append("🟡")
    inv = _num(zcf.get("INVENTORY"))
    if ta and inv:
        ir = inv / ta * 100
        if ir > 30:
            _add_point(d, "存货/总资产", _pct(ir), "存货占比高")
            flags.append("🟡")
    ar = _num(zcf.get("ACCOUNTS_RECE"))
    if income and ar:
        ar_r = ar / income * 100
        if ar_r > 50:
            _add_point(d, "应收账款/收入", _pct(ar_r), "回款压力")
            flags.append("🟡")
    gw = _num(zcf.get("GOODWILL"))
    peq = _num(zcf.get("TOTAL_PARENT_EQUITY"))
    if gw and peq and gw > 0:
        gwr = gw / peq * 100
        _add_point(d, "商誉/净资产", _pct(gwr), "减值风险" if gwr > 20 else "")
        if gwr > 20:
            flags.append("🔴")

    if not flags:
        d["level"] = "🟢"
        d["verdict"] = "三表整体健康：盈利、现金流、资产负债结构都未见明显风险"
    elif "🔴" in flags:
        d["level"] = "🔴"
        d["verdict"] = f"发现 {flags.count('🔴')} 项明显风险信号，财务质量偏弱"
    else:
        d["level"] = "🟡"
        d["verdict"] = "存在若干需留意的财务信号，整体中性偏谨慎"
    return d


def _valuation(f10: dict, ctx: dict) -> dict:
    d = _dim("valuation", "④ 估值 · 价格对得上价值吗")
    val = f10.get("valuation") or {}
    if not val:
        d["verdict"] = "估值数据不足"
        return d
    pe = _num(val.get("pe_ttm"))
    pb = _num(val.get("pb"))
    ps = _num(val.get("ps_ttm"))
    mv = _num(val.get("total_mv"))
    flags = []

    if mv:
        size = "小盘" if mv < 5e9 else "中盘" if mv < 2e10 else "大盘"
        _add_point(d, "总市值", _yi(mv), size)
    if pe is not None:
        if pe < 0:
            _add_point(d, "PE(TTM)", f"{pe:.1f}", "亏损，讲故事估值")
            flags.append("🟡")
        elif pe <= 30:
            _add_point(d, "PE(TTM)", f"{pe:.1f}", "估值合理")
        elif pe <= 60:
            _add_point(d, "PE(TTM)", f"{pe:.1f}", "偏高")
            flags.append("🟡")
        else:
            _add_point(d, "PE(TTM)", f"{pe:.1f}", "很贵")
            flags.append("🔴")
    if pb is not None:
        if pb < 1:
            _add_point(d, "PB", f"{pb:.2f}", "破净")
        elif pb <= 3:
            _add_point(d, "PB", f"{pb:.2f}", "正常")
        elif pb <= 5:
            _add_point(d, "PB", f"{pb:.2f}", "偏高")
            flags.append("🟡")
        else:
            _add_point(d, "PB", f"{pb:.2f}", "贵")
            flags.append("🔴")
    if ps is not None and ps > 5:
        _add_point(d, "PS(TTM)", f"{ps:.2f}", "营收支撑弱")

    if not flags:
        d["level"] = "🟢"
        d["verdict"] = "估值处于合理区间，价格基本对得上基本面"
    elif "🔴" in flags:
        d["level"] = "🔴"
        d["verdict"] = "估值明显偏高，价格已经price-in 了较多预期"
    else:
        d["level"] = "🟡"
        d["verdict"] = "估值中性偏高，上涨空间需要盈利跟上"
    return d


def _capital_profile(f10: dict, ctx: dict) -> dict:
    d = _dim("capital_profile", "⑤ 主力资金画像 · 谁在买")
    lhb = f10.get("lhb") or {}
    sh = f10.get("shareholders") or {}
    recs = lhb.get("records") or []
    flags = []

    if recs:
        latest = recs[0]
        net = _num(latest.get("net_amt"))
        _add_point(d, "最新上榜", f"{latest.get('date','')} 净{'买' if net and net>0 else '卖'}{_yi(abs(net) if net else 0)}",
                   latest.get("reason", "")[:24])
        if net is not None:
            flags.append("🟢" if net > 0 else "🔴")
        buy_seats = lhb.get("seats_buy") or []
        sell_seats = lhb.get("seats_sell") or []
        inst_buy = [s for s in buy_seats if classify_seat(s.get("name","")) == "机构"]
        yz_buy = [s for s in buy_seats if classify_seat(s.get("name","")) == "游资"]
        inst_sell = [s for s in sell_seats if classify_seat(s.get("name","")) == "机构"]
        if buy_seats:
            note = ("机构+北向参与" if inst_buy else "纯游资/营业部席位")
            _add_point(d, "买方席位", f"{len(buy_seats)}家",
                       "、".join(s["name"][:12] for s in buy_seats[:3]) + (f"等{len(buy_seats)}家" if len(buy_seats)>3 else ""))
            _add_point(d, "资金属性", "机构+游资混战" if inst_buy and yz_buy
                       else "机构资金为主" if inst_buy else "游资主导，题材资金", note)
            if inst_buy and not inst_sell:
                flags.append("🟢")
            elif not inst_buy and not yz_buy:
                pass
        d5 = _num(latest.get("d5"))
        if d5 is not None:
            _add_point(d, "上榜后5日", _pct(d5, signed=True),
                       "资金持续" if d5 > 0 else "资金退潮")
            flags.append("🟢" if d5 > 0 else "🔴")
    else:
        _add_point(d, "龙虎榜", "近期未上榜", "无游资/机构席位数据")

    ir = _num(sh.get("inst_ratio"))
    if ir is not None:
        _add_point(d, "机构持仓(占流通)", f"{ir:.1f}%",
                   "有机构底仓" if ir >= 20 else "机构关注一般" if ir >= 5 else "机构几乎不碰")
        if ir >= 20:
            flags.append("🟢")
        elif ir < 5:
            flags.append("🟡")

    if not flags:
        d["level"] = "🟡"
        d["verdict"] = "资金面数据有限，暂无明确主力信号"
    elif "🔴" in flags:
        d["level"] = "🔴"
        d["verdict"] = "资金以流出/退潮为主，主力意愿偏弱"
    else:
        d["level"] = "🟢"
        d["verdict"] = "有资金净流入且存在机构/北向参与，主力意愿偏强"
    return d


def _chip_structure(f10: dict, ctx: dict) -> dict:
    d = _dim("chip_structure", "⑥ 筹码结构 · 货在谁手里")
    sh = f10.get("shareholders") or {}
    flags = []
    chg = _num(sh.get("holders_change_pct"))
    if chg is not None:
        if chg > 5:
            _add_point(d, "股东户数环比", _pct(chg, signed=True), "筹码明显分散")
            flags.append("🔴")
        elif chg > 2:
            _add_point(d, "股东户数环比", _pct(chg, signed=True), "筹码略分散")
            flags.append("🟡")
        elif chg < -5:
            _add_point(d, "股东户数环比", _pct(chg, signed=True), "筹码集中，有收集")
            flags.append("🟢")
        else:
            _add_point(d, "股东户数环比", _pct(chg, signed=True), "平稳")
    focus = sh.get("hold_focus") or ""
    if focus:
        _add_point(d, "筹码集中度", focus)
        if "分散" in focus:
            flags.append("🟡")
        elif "集中" in focus:
            flags.append("🟢")
    t10 = _num(sh.get("top10_ratio"))
    if t10 is not None:
        _add_point(d, "前十大股东占比", _pct(t10),
                   "集中" if t10 >= 50 else "较分散" if t10 < 30 else "一般")
        if t10 < 30:
            flags.append("🟡")
    ctrl = sh.get("controller") or ""
    if ctrl:
        _add_point(d, "实控人", ctrl,
                   "国资背景" if _is_state_owned(ctrl) else "民营/其他")

    if not flags:
        d["level"] = "🟢"
        d["verdict"] = "筹码结构健康，未见明显派发/分散信号"
    elif "🔴" in flags:
        d["level"] = "🔴"
        d["verdict"] = "筹码明显分散，短线抛压隐患"
    else:
        d["level"] = "🟡"
        d["verdict"] = "筹码中性，集中度一般"
    return d


def _up_reason(f10: dict, ctx: dict) -> dict:
    d = _dim("up_reason", "⑦ 为什么涨 · 市场在买什么")
    li = ctx.get("limit_info") or {}
    chg = _num(ctx.get("change_pct"))
    board = ctx.get("board_names") or []
    hot = ctx.get("hot_boards") or []

    if li.get("consecutive"):
        n = li["consecutive"]
        d["level"] = "🟡" if n >= 4 else "🟢"
        _add_point(d, "今日状态", f"涨停 {n}连板",
                   f"封成比 {_pct(li.get('seal_pressure'))}" if li.get("seal_pressure") is not None else "")
    elif chg is not None and chg >= 5:
        d["level"] = "🟡"
        _add_point(d, "今日状态", f"大涨 {chg:+.1f}%", "明显异动")
    elif chg is not None and chg >= 3:
        _add_point(d, "今日状态", f"上涨 {chg:+.1f}%", "温和走强")
    else:
        _add_point(d, "今日状态", f"{chg:+.1f}%" if chg is not None else "—", "无显著异动")
        d["level"] = "🟡"

    # 题材归属
    b_names = [b for b in (board or [])]
    if b_names:
        _add_point(d, "所属题材", "、".join(b_names[:3]),
                   f"{len(b_names)}个热点归属" if len(b_names) > 3 else "热点归属")
        d["level"] = _worst_level(d["level"], "🟢")

    # 炒的是不是真业务
    hit = False
    for b in b_names:
        if _business_keyword_hit(f10, b):
            hit = True
            break
    if b_names:
        if hit:
            d["level"] = _worst_level(d["level"], "🟢")
            _add_point(d, "主营匹配", "题材与主营吻合", "炒的是真业务")
        else:
            d["level"] = _worst_level(d["level"], "🟡")
            _add_point(d, "主营匹配", "题材与主营关联弱", "警惕『业务切换/蹭概念』")

    if li.get("consecutive"):
        d["verdict"] = (f"今日涨停 {li['consecutive']}连板，炒作方向为「"
                        + ("、".join(b_names[:2]) if b_names else "—") + "」"
                        + ("，题材与主营吻合" if hit and b_names else "，题材与主营关联弱" if b_names else ""))
    else:
        d["verdict"] = (f"市场买的是「{'、'.join(b_names[:2]) if b_names else '—'}」方向"
                        + ("，与主营吻合" if hit and b_names else "，与主营关联弱，偏情绪" if b_names else ""))
    return d


def _sustainability(f10: dict, ctx: dict) -> dict:
    d = _dim("sustainability", "⑧ 能不能持续 · 资金与题材续航")
    lhb = f10.get("lhb") or {}
    recs = lhb.get("records") or []
    flags = []
    if recs:
        latest = recs[0]
        net = _num(latest.get("net_amt"))
        if net is not None:
            flags.append("🟢" if net > 0 else "🔴")
        # 连续上榜 = 资金持续关注
        dates = [r.get("date", "") for r in recs if r.get("date")]
        _add_point(d, "近期上榜", f"{len(dates)}次" if dates else "—",
                   "连续上榜" if len(dates) >= 2 else "单次上榜")
        if len(dates) >= 2:
            flags.append("🟢")
        d10 = _num(latest.get("d10"))
        if d10 is not None:
            _add_point(d, "上榜后10日", _pct(d10, signed=True),
                       "资金持续" if d10 > 0 else "资金退潮")
            flags.append("🟢" if d10 > 0 else "🔴")
    else:
        _add_point(d, "资金面", "近期未上榜", "持续性需看量能")

    li = ctx.get("limit_info") or {}
    n = li.get("consecutive")
    if n:
        if n >= 5:
            _add_point(d, "连板高度", f"{n}板", "高位，接力风险大")
            flags.append("🔴")
        elif n >= 3:
            _add_point(d, "连板高度", f"{n}板", "进入分歧区")
            flags.append("🟡")
        else:
            _add_point(d, "连板高度", f"{n}板", "相对低位")
            flags.append("🟢")

    hit = _match_hot_board(ctx, f10)
    if hit:
        zc = _num(hit.get("zt_count"))
        if zc and zc >= 3:
            _add_point(d, "板块效应", f"相关板块涨停{zc:.0f}家", "有板块合力")
            flags.append("🟢")
        elif zc:
            _add_point(d, "板块效应", f"相关板块涨停{zc:.0f}家", "板块合力一般")
    else:
        _add_point(d, "板块效应", "板块涨停家数少", "孤立个股行情")

    if not flags:
        d["level"] = "🟡"
        d["verdict"] = "续航信号不明确，需要观察次日竞价与量能"
    elif "🔴" in flags:
        d["level"] = "🔴"
        d["verdict"] = "高位+资金退潮，持续性存疑，注意兑现"
    else:
        d["level"] = "🟢"
        d["verdict"] = "资金持续关注+板块有合力，续航尚可"
    return d


def _risks(f10: dict, ctx: dict) -> dict:
    d = _dim("risks", "⑨ 风险点 · 先想亏多少")
    fin = f10.get("financial") or {}
    lrb, zcf, xj = fin.get("lrb") or {}, fin.get("zcfzb") or {}, fin.get("xjllb") or {}
    val = f10.get("valuation") or {}
    sh = f10.get("shareholders") or {}
    levels = []

    nco = _num(xj.get("NETCASH_OPERATE"))
    if nco is not None and nco < 0:
        _add_point(d, "现金流", f"经营现金流 {_yi(nco)}", "造血能力不足，利润含金量打折")
        levels.append("🔴")
    pnp, dnp = _num(lrb.get("PARENT_NETPROFIT")), _num(lrb.get("DEDUCT_PARENT_NETPROFIT"))
    if pnp is not None and dnp is not None and pnp > 0 and dnp < 0:
        _add_point(d, "利润质量", "扣非亏损，靠非经常损益撑利润", "盈利不扎实")
        levels.append("🟡")
    gw = _num(zcf.get("GOODWILL"))
    peq = _num(zcf.get("TOTAL_PARENT_EQUITY"))
    if gw and peq and gw > 0 and gw / peq > 0.2:
        _add_point(d, "商誉", _yi(gw), "减值暴雷隐患")
        levels.append("🔴")
    pe = _num(val.get("pe_ttm"))
    pb = _num(val.get("pb"))
    if pe is not None and pe < 0:
        _add_point(d, "估值", "PE(TTM)为负", "亏损股靠情绪定价，回撤起来没有底")
        levels.append("🟡")
    elif pe is not None and pe > 60:
        _add_point(d, "估值", f"PE(TTM) {pe:.0f}", "估值高企，透支预期")
        levels.append("🟡")
    if pb is not None and pb > 5:
        _add_point(d, "估值", f"PB {pb:.2f}", "市净率高，安全垫薄")
        levels.append("🟡")
    chg = _num(sh.get("holders_change_pct"))
    if chg is not None and chg > 5:
        _add_point(d, "筹码", f"股东户数 +{chg:.1f}%", "筹码快速分散")
        levels.append("🟡")
    n = (ctx.get("limit_info") or {}).get("consecutive")
    if n and n >= 4:
        _add_point(d, "位置", f"{n}连板高位", "断板即补跌，核按钮风险")
        levels.append("🔴")

    if not levels:
        d["level"] = "🟢"
        d["verdict"] = "未发现显著风险点"
    elif "🔴" in levels:
        d["level"] = "🔴"
        d["verdict"] = f"重点风险 {levels.count('🔴')} 项，先想好亏损再谈收益"
    else:
        d["level"] = "🟡"
        d["verdict"] = "存在若干需跟踪的风险信号"
    return d


# ======================================================================
# 入口
# ======================================================================

def analyze_stock(f10: dict, ctx: dict) -> dict:
    """九维确定性分析。f10 为 F10Fetcher.get_f10() 返回，ctx 为当日上下文。

    ctx 约定（app.py _load_stock_context 构造）：
      change_pct  当日涨跌幅
      limit_info  {consecutive, seal_pressure} 今日涨停池信息（无则 None）
      board_names 该股所属热点题材名列表（涨停池板块/概念）
      hot_boards  [{name, zt_count}] 当日涨停潮板块
    """
    dims = [
        _company_value(f10, ctx),
        _industry_cycle(f10, ctx),
        _financial_health(f10, ctx),
        _valuation(f10, ctx),
        _capital_profile(f10, ctx),
        _chip_structure(f10, ctx),
        _up_reason(f10, ctx),
        _sustainability(f10, ctx),
        _risks(f10, ctx),
    ]
    return {"dims": dims, "summary": _summary(dims, f10, ctx)}


# ======================================================================
# 总结
# ======================================================================

def _summary(dims: List[dict], f10: dict, ctx: dict) -> str:
    d = {x["key"]: x for x in dims}
    comp = f10.get("company") or {}
    val = f10.get("valuation") or {}
    biz = f10.get("business") or {}
    sh = f10.get("shareholders") or {}
    li = ctx.get("limit_info") or {}

    name = comp.get("name") or "这家公司"
    top = _first_item(biz.get("by_product")) or _first_item(biz.get("by_industry"))
    parts = []

    # 开头定性
    if top:
        parts.append(f"{name}靠「{top.get('item','')}」赚钱"
                     f"（占收入{_num(top.get('income_ratio'),0):.0f}%），"
                     f"{'国资背景' if _is_state_owned(sh.get('controller') or '') else '民企' if sh.get('controller') else '股权结构'}。")
    else:
        cv = d.get("company_value", {}).get("verdict", "")
        if cv:
            parts.append(f"{name}：{cv}。")

    # 财务一句话
    fh = d.get("financial_health", {}).get("verdict", "")
    parts.append(f"财务上：{fh}。")

    # 估值一句话
    mv = _num(val.get("total_mv"))
    pe = _num(val.get("pe_ttm"))
    vv = d.get("valuation", {}).get("verdict", "")
    if mv:
        parts.append(f"当前市值{_yi(mv)}，{vv}。")

    # 资金/题材/筹码
    ur = d.get("up_reason", {}).get("verdict", "")
    if ur:
        parts.append(f"{ur}。")
    parts.append(f"{d.get('capital_profile',{}).get('verdict','')}。")
    parts.append(f"筹码上：{d.get('chip_structure',{}).get('verdict','')}。")

    # 风险 + 条件式收尾
    risks = d.get("risks", {}).get("points", [])
    if risks:
        rn = "、".join(p["label"] for p in risks[:3])
        parts.append(f"主要风险在{rn}。")
    sus = d.get("sustainability", {}).get("verdict", "")
    parts.append(f"持续性上：{sus}。")

    text = "".join(parts)
    return text
