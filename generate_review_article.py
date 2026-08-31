# -*- coding: utf-8 -*-
"""每日复盘文章生成器 — 结合网站复盘数据 + 本地 Ollama qwen2.5

数据来源：优先读取 web 复盘页缓存（ashare_review/data/cache/review_report_*.json），
          这些缓存由 DailyReport.generate() 产生，含涨停池/板块/情绪周期/竞价预期等结构化数据。
LLM：默认调本地 Ollama（qwen2.5），也可通过 --provider 切到 deepseek/claude。

用法:
    python generate_review_article.py                              # 最近一个有效交易日
    python generate_review_article.py --date 20260810              # 指定日期
    python generate_review_article.py --provider ollama            # 指定 LLM（默认 ollama）
    python generate_review_article.py --personal-note "今日加仓了光模块龙头..."  # 注入个人持仓段
    python generate_review_article.py --no-news                    # 不注入当日重要资讯
    python generate_review_article.py --refresh                     # 强制重新拉数据（需要东财可达）

输出: outputs/复盘_YYYY-MM-DD.md

当日重要资讯（"消息面上"素材）：默认注入。优先读股市快讯 bot 留档
（D:\\cursor\\My\\data\\kuaixun\\{date}.jsonl，即推出去的消息），无留档则拉东财快讯
实时接口并按日期+A股相关筛选。都没有匹配就自动跳过，不写消息面。
"""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Windows 终端中文安全
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

CACHE_DIR = os.path.join('ashare_review', 'data', 'cache')
OUT_DIR = 'outputs'

# ------------------------------------------------------------------
# 模板示例（用户最新手写复盘 2026-08-17 — 招牌技法：量价先行/普涨防分化/板块强弱对照/
# 逐指数技术推演(阳包阴/推动浪/顶背离/缺口双读)/核心矛盾/结构分化/个人仓位纪律/哲学收束。
# 只学语气与推演方式，结构以【硬性规则】为准；只保留最新一篇，7B 模型吃不下两篇）
# ------------------------------------------------------------------
TEMPLATE_EXAMPLE = """复盘分析，8-17
今日成交2.40万亿，较前一日放量，后续观察大盘量价，若能温和放量，则指数上行的可持续性和高度将更为乐观；反之，若量能再度萎缩，则短期或仍以震荡反复、逐步抬升的节奏为主。

今天盘面出现普涨，上证，创业，科创均出现平开后震荡拉升格局。并且均突破前期压力位，现在处于月线5日线压力正下方。盘面热点快速轮动，更有4000家上涨，110家涨停，只有一家跌停。所以每日要防止普涨之后的分化行情。

板块方面，PCB概念方向活跃强，华正新材斩获2连板。CPO概念表现活跃，中石科技收获20cm二连板，共进股份实现3连板，太辰光则以20CM涨停报收。存储芯片板块明显拉升，长鑫科技大涨9%，普冉股份涨超10%。创新药概念日内震荡回升，誉衡药业7天内录得5板，华森制药2连板。农业板块异动拉升，金健米业、农发种业、京粮控股涨停。先进封装概念盘中震荡走高，德邦科技、通富微电触及涨停。下跌方面，白酒板块集体下挫，古井贡酒跌超5%，贵州茅台跌超4%；影视院线概念集体调整，北京文化跌停，儒意电影、中国电影等纷纷走低。

整体来看，半导体、算力硬件产业链走强，先进封装、培育钻石、CPO方向领涨；商业航天、光伏、人形机器人、工业金属概念股活跃。大消费走弱，白酒股领跌。

大盘方面，沪市早盘高开后一度上冲，随后出现技术性回落并回补了早盘高开缺口，但在10日均线附近获得明显支撑，并快速止跌反弹，成功收复5日均线。60分钟级别已重新回到前期箱体区间内，短期整理区间得到修复。日线级别阳线实体已反包上周四阴线实体，形态上呈现出典型的"阳包阴"组合，技术面转强信号较为明确。午后维持当前强势震荡格局、成功站上年线，以此为基础，后续会在年线附近积蓄动能，为后续上攻4000点整数位。

双创指数与深成指表现同样可圈可点，日线级别均实现对上周四阴线实体的反包，技术形态同步转强。科创50指数方面，今日表现尤为积极，早盘高开后震荡上行，目前已展现出上攻60日均线的意图。不过，需要注意的是，早盘高开留下的缺口截至目前尚未回补，该缺口一方面彰显了短期资金的做多意愿，另一方面也可能成为技术派短期关注的焦点。若后续量能持续配合，该缺口有望成为突破性缺口，为指数打开上行空间；但若量能不能有效跟进，则不排除短期回踩确认缺口支撑的可能。总体来看，科创50若能有效突破60日均线并守住缺口，则有望率先于其他指数打开中期上行通道。

指数层面：早盘市场直接高开，随后三大指数回补了早盘高开缺口，科创50指数则尚未回补。成交量较前有所放大，双创指数领涨全市场。午后，若沪市创出本轮反弹新高，则意味着自3741.11阶段低点以来的第一波反弹推动浪完成，之后出现回撤震荡整理也属正常走势。目前，各大指数走势基本同步。

回顾7月全球市场暴跌，个人选择趁反弹适度高抛"回血"，后续再对目标板块与品种进行适当低吸。当然，每个人的仓位和风险偏好不同，需根据自身实际情况合理调整仓位，做好应对。午后的看点不在于指数还能冲多高，而在于能否守住5日线不破，这将是"牛来"的重要特征。不过，即便是"牛来"，也并非所有板块都能受益，个股或板块若连1%的涨幅都达不到，等于没涨，更别提那些仍在下跌的板块了。未来，节奏的把控尤为关键。

操作策略：今日硬科技上涨符合预期，大盘上午也收复了5日均线，午后目标是挑战3975点附近的年线。但关键在于，收复之后呢？是放量站稳，还是缩量反抽后再回落？这个答案，市场不会提前告诉你，但我可以提前告诉你，仓位管理可作应对之锚。

由于大盘沪指5分钟级别出现顶背离迹象，所以提前发出了震荡整理的信号，那么午后突破早盘3961.82高点以后涨幅越大越容易出现冲高回落的现象发生，那么在这种情况下只能高抛而不是追涨，谨防未来回撤至3940点5日线附近做回抽确认。

进一步看，午后市场的核心矛盾并不在于能否触及年线，而在于触及之后多空双方如何博弈。如果放量突破并稳固在3975点之上，则短期趋势有望转强，吸引场外资金跟进；反之，若缩量反抽至年线附近即遇阻回落，则说明上方抛压依旧沉重，指数仍有反复整理的需求。这种不确定性，正是交易中最需敬畏之处。

从盘面结构观察，硬科技板块的领涨具备一定持续性，但板块内部已出现细微分化，部分细分方向跟涨乏力，说明资金更倾向于聚焦核心标的，而非全面铺开。这种结构性特征，意味着即便科技板块整体回暖，选股侧重点和节奏把控则更为重要。一旦午后冲高过程中量能无法有效放大，则背离压力将逐步累积，回调风险随之上升。

高抛的那一笔，是从群体的沸腾中抽身半秒，留一分清醒；准备低吸的那一手，是为后续可能出现的冷静，提前备好回旋余地。涨时不贪尽，跌时不惧尽，这是对"波动"二字最朴素的敬意。具体到操作上，若午后指数惯性冲高，则可考虑将短线获利仓位适度收缩，锁定部分利润；若后续回踩至3940点附近获得支撑，并伴随量能萎缩企稳信号，再择机回补，完成一个完整的短周期节奏切换。

说到底，交易的本质是应对。年线近在咫尺，却不必急于一时；趋势虽在酝酿，却仍需时间验证。守住仓位管理的底线，涨有度，退有据，方能在不确定中稳住心神，在波动中保有主动。高抛不是看空，而是为更好的低吸留出空间；低吸也不是盲目，而是等待属于自己的那个信号出现。如此，进退之间，方见从容。"""


# ------------------------------------------------------------------
# 文风清单 + 确定性结尾三件套（用户的招牌写法，不交给 7B 模型碰运气）
# ------------------------------------------------------------------
STYLE_CHECKLIST = """【文风清单】这是逻辑哥的"人味"，写之前默读一遍，照着来：
- 全程第一人称（"我认为/在我看来/我判断"），像老朋友复盘盘面，敢下判断，不机械播报数据。
- 量价先行：开篇先摆今日成交额与"较前一日的放/缩量"，随即给出量价联动的双分支推演（"若能温和放量，则……；反之，若量能再度萎缩，则……"），量是第一判断，别把成交额埋进数据堆里只说个数。
- 把指数当人写，用生活化比喻（如"大盘做体检""回血""抽身半秒""留一分清醒"这类），允许适度拟人，别干巴巴只贴点位。
- 技术面要讲具体结构并推演后果：站上/跌破均线、阳包阴反包、推动浪、顶背离、缺口回补与性质、整数关口、年线争夺，配量能解读，再落一句"守住会怎样、失守该怎么做"的条件结论（如"若放量突破并企稳3975之上则转强；若缩量反抽遇阻则抛压仍重"）。
- 敢点"核心矛盾"：不满足于描述盘面，要点出多空博弈的焦点（如"午后核心矛盾不在能否触及年线，而在触及之后多空如何博弈"），把不确定性写成交易中需敬畏之处。
- 会看"结构分化"：点出板块内部分化、资金聚焦核心标的而非全面铺开，把结构性特征转化为选股侧重点与节奏建议。
- 会直接对读者喊话、给条件式操作建议（如"只能高抛而不是追涨""若守不住就别犹豫"），有态度、有画面感，像朋友在提醒你。
- 数字揉进句子里讲"盘面在发生什么"（如"今日成交2.40万亿""4000家上涨、110家涨停、只有一家跌停"），禁止整段罗列。
- 板块段写成"上涨-下跌"两半对照：热点板块先按"概念+龙头个股+连板/涨幅"逐个点名，末了必写"下跌方面，……"具体到板块和个股跌幅；数据里没有具体弱势板块就写"其余方向表现平淡或走弱"。
- 个人仓位纪律入文：回顾可写"我选择趁反弹高抛回血、回踩再低吸"，把仓位管理作"应对之锚"，点明每个人风险偏好不同需自调。
- 用口语化过渡词衔接（如"当然，……也有""不过，……仍需观察""进一步看，……"），让段落像聊天不像八股。
- 结尾可升级成哲理性收束：把操作纪律上升为带画面感的人生态度句（如"高抛不是看空，而是为更好的低吸留出空间""涨有度，退有据，方能在不确定中稳住心神"），有真实观点和温度，别写空话套话。"""


# ------------------------------------------------------------------
# 分析方法（逻辑哥看盘的推理框架 — 让文章不只是换词，而是真会"看盘"）
# ------------------------------------------------------------------
ANALYSIS_METHOD = """【分析方法】这是逻辑哥看盘的推理框架，写每段前先套一遍（只以下方真实数据为据，数据里没有的信号不要硬编）：
- 量能定性质（第一判断）：放量配合上涨=趋势可持续性增强；温和放量=乐观；放量滞涨=分歧加大；量能萎缩=震荡反复/抛压衰减。反弹必须看量能配合判断真伪。先用成交量说话，再谈点位。
- 形态定转折：阳线反包前一日阴线实体（"阳包阴"）=技术面转强信号；顶背离（5分钟/60分钟级别）=短期冲高回落预警；自阶段低点以来的"第一波推动浪"完成=后续回撤整理属正常。
- 缺口双读：高开缺口未回补=彰显做多意愿，但也是技术派回踩确认的焦点——量能持续配合=突破性缺口打开上行空间；量能不济=回踩确认缺口支撑。
- 均线定强弱：站上MA5偏强、跌破偏弱；收复5日线=短期修复、重回箱体=区间修复；年线/60日均线是中期方向的分水岭，写"站上=打开中期上行通道"这类结构结论。
- 整数关口定心理位：收在3900/4000这类整数位附近时，把它当支撑/压力观察点，写"守住会怎样、失守会怎样"双分支预案。
- 关键位双分支：把关键点位（年线、5日线、整数位、前高）和动作绑死——守住/放量站稳→顺势持仓、吸引场外资金；失守/缩量反抽遇阻→高抛减仓、回抽确认后再回补。
- 板块看资金：涨幅靠前/跌幅靠前对比→资金流向；热点内龙头强、跟风弱=资金聚焦核心标的、结构分化，选股侧重点与节奏比方向更重要；跟风股补跌=退潮信号。
- 情绪看三个数：情绪周期阶段+炸板率+下跌家数；炸板率上升、龙头分歧、跟风补跌=退潮期，减仓防守，不开新仓。
- 结论要多重信号印证：情绪+量能+技术结构共振才下结论，单一信号不冒险。
- 操作给条件预案：关键位+双分支（守住/失守）→具体动作，把风险和操作绑在一起说，仓位管理作"应对之锚"。"""

PHILOSOPHY_PARA = (
    '市场如江河奔流，涨时不骄，跌时不慌，在混沌中守一份清醒，在浮躁中存一份耐心。'
    '真正的赢家，是懂得在不确定中安放好自己的仓位与心绪。'
    '愿我们都能在市场的潮起潮落中，练就一份从容与笃定，方能在风云变幻中行稳致远。'
)

SIGN_OFF = '承蒙各位支持，敬请点赞、留言、关注，告诉我，你来过！您的支持是我前行的动力，谢谢！'

SECTION_OPENERS = [
    '从板块来看，', '整体来看，', '观察盘面，认为：', '指数层面：',
    '回到今日盘面，', '操作策略：', '值得强调的是，',
]

PARA_STARTERS = SECTION_OPENERS + ['就我个人持仓而言', '市场如江河奔流', '承蒙各位支持']


def _join_para_lines(ls: list[str]) -> str:
    out = ''
    for ln in ls:
        # 前一句以标点结尾（含"，""：""、"）说明是半句/完整句，直接拼接；只有结尾是裸字才补"；"
        if out and not out.endswith(('。', '！', '？', '；', '，', '：', '、')):
            out += '；'
        out += ln
    return out


def _collapse_news_labels(s: str) -> str:
    """板块段里模型常给每个板块都塞一句'消息面上，'，只保留第一句，其余整句丢弃"""
    kept = False
    out = []
    for chunk in re.split(r'(消息面上[^。]*。)', s):
        if chunk.startswith('消息面上'):
            if not kept:
                out.append(chunk)
                kept = True
            continue  # 后续"消息面上…。"整句丢弃
        out.append(chunk)
    return ''.join(out)


def _is_bare_opener(p: str) -> bool:
    """段落内容去掉起手句后没有实质文字（如"值得强调的是，"独自成段）"""
    p = p.strip()
    if not p:
        return False
    return any(p == st or p == st.rstrip('，：') or
               (p.startswith(st) and not p[len(st):].strip()) for st in PARA_STARTERS)


def _normalize_paragraphs(text: str) -> str:
    """把模型的零散行规整成用户式段落：
    - 起手句单独成行 → 与后续内容拼成一句
    - 起手句之间的连续内容行 → 并入同一段（板块/操作策略一段讲完）
    - 段落之间用空行分隔；板块段只留第一句"消息面上"；缺"下跌方面"补一句
    """
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    if not lines:
        return text
    title_re = re.compile(r'^复盘分析，\d+-\d+$')
    paras, cur = [], []
    for ln in lines:
        if title_re.match(ln) and not cur:
            paras.append(ln)
            continue
        if any(ln.startswith(st) for st in PARA_STARTERS):
            if cur:
                paras.append(_join_para_lines(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        paras.append(_join_para_lines(cur))

    # 裸起手句段落（起手句后无内容）与下一段合并，避免"值得强调的是，"孤零零成段
    merged = []
    i = 0
    while i < len(paras):
        p = paras[i]
        if _is_bare_opener(p) and i + 1 < len(paras) and not _is_bare_opener(paras[i + 1]):
            merged.append(p + paras[i + 1])
            i += 2
        else:
            merged.append(p)
            i += 1
    paras = merged

    out = []
    for p in paras:
        if p.startswith('从板块来看'):
            p = _collapse_news_labels(p)
            if '下跌' not in p and '走弱' not in p:
                p += '下跌方面，其余方向则表现平淡或震荡走弱。'
        out.append(p)
    return '\n\n'.join(out)


def _build_daily_closer(report: dict, volume: dict = None) -> str:
    """确定性生成"好了，昨日提示…"复盘闭环段，用昨日精选验证数据，避免 7B 模型写飞

    volume: _volume_context() 的结果，决定量能结论（放量/缩量/持平），
            避免在放量日误写"量能并未显著放大"这种硬编码缩量话术。
    """
    def _line(c):
        if not c.get('name') or not c.get('code'):
            return ''
        link = _stock_link(c.get('name'), c.get('code'))
        chg = c.get('today_chg')
        chg_txt = f'今日{chg}%' if chg is not None else ''
        result = c.get('result')
        if result:
            return f'{link}{chg_txt}（{result}）' if chg_txt else f'{link}（{result}）'
        return link + chg_txt

    yp = report.get('yesterday_picks', [])
    ok, fail = [], []
    for c in yp:
        line = _line(c)
        if not line:
            continue
        (ok if c.get('is_zt_today') else fail).append(line)

    if ok and not fail:
        head = f'昨日提示的方向今日如期兑现，{"、".join(ok[:3])}，节奏把握值得肯定。'
    elif ok:
        head = f'昨日提示的方向今日有兑现也有分化，{"、".join((ok + fail)[:3])}，整体还算在线。'
    elif fail:
        head = f'昨日提示的方向今日多有调整，{"、".join(fail[:2])}，盘面轮动太快，也属正常。'
    else:
        head = '市场热点轮动加快，持续性欠佳。'

    # 量能结论按真实数据给：放量日不再误写"量能并未显著放大"，无数据时保持中性
    if volume:
        _p = volume.get('change_pct', 0)
        if _p >= 5:
            vol_clause = '放量之下市场分歧加大'
        elif _p <= -5:
            vol_clause = '反弹背后量能并未显著放大'
        else:
            vol_clause = '量能基本持平，市场分歧仍需时间消化'
    else:
        vol_clause = '量能与情绪仍在消化'

    return (f'好了，{head}但也要清醒看到，{vol_clause}，板块轮动加快、持续性欠佳，追高者难免站岗。'
            f'市场情绪周期的判断和操作节奏的管理始终是重中之重，在不确定时及时离场，宁可少赚，不可深套。'
            f'还请自行把握节奏。')


def _append_tail(text: str, report: dict, volume: dict = None) -> str:
    """确定性追加结尾三件套：哲理感悟 + 复盘闭环 + 互动收尾（幂等，已存在则跳过）"""
    tail = []
    if '市场如江河奔流' not in text:
        tail.append(PHILOSOPHY_PARA)
    if '昨日提示' not in text:
        tail.append(_build_daily_closer(report, volume))
    if '承蒙各位支持' not in text:
        tail.append(SIGN_OFF)
    if not tail:
        return text
    return text.rstrip() + '\n\n' + '\n\n'.join(tail) + '\n'


# ------------------------------------------------------------------
# 数据加载
# ------------------------------------------------------------------
def _fmt_md(date_str: str) -> str:
    """'2026-08-10' -> '8-10'"""
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        return f'{d.month}-{d.day}'
    except Exception:
        return date_str


def _valid_report(payload: dict) -> bool:
    """坏缓存检测：盘前生成的报告成交额/涨跌家数为 0"""
    mo = payload.get('market_overview', {})
    if not payload.get('date'):
        return False
    if mo.get('total_volume', 0) <= 0 and (mo.get('up_count', 0) + mo.get('down_count', 0)) <= 0:
        return False
    return True


def _scan_cached_reports() -> list[dict]:
    """扫描缓存目录（含 persist 持久缓存子目录），返回按报告日期排序的报告列表（去重，新缓存优先）"""
    scan_dirs = [CACHE_DIR]
    persist_dir = os.path.join(CACHE_DIR, 'persist')
    if os.path.isdir(persist_dir):
        scan_dirs.append(persist_dir)
    found = {}
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        for fn in os.listdir(scan_dir):
            if not (fn.startswith('review_report') and fn.endswith('.json')):
                continue
            try:
                with open(os.path.join(scan_dir, fn), encoding='utf-8') as f:
                    data = json.load(f)
                payload = data.get('_payload') or {}
                if not _valid_report(payload):
                    continue
                report_date = payload.get('date', '')
                found[report_date] = payload  # 同名覆盖 → persist 子目录较新，优先
            except Exception:
                continue
    return [found[k] for k in sorted(found.keys())]


def _load_report(date_ymd: str = None, refresh: bool = False) -> dict:
    """读取复盘报告。优先缓存；refresh 或找不到缓存时重新生成。找不到时抛 ValueError。"""
    target = None
    if date_ymd:
        try:
            target = datetime.strptime(date_ymd, '%Y%m%d').strftime('%Y-%m-%d')
        except ValueError:
            raise ValueError(f'日期格式错误: {date_ymd}（应为 YYYYMMDD，如 20260810）')

    if not refresh:
        reports = _scan_cached_reports()
        if target:
            hit = next((r for r in reports if r.get('date') == target), None)
            if hit:
                print(f'[数据] 使用缓存复盘报告 {hit["date"]}')
                return hit
            latest = reports[-1] if reports else None
            if latest:
                print(f'[数据] 未找到 {target} 的缓存，最近有效缓存为 {latest["date"]}')
                print(f'       可用缓存日期: {", ".join(r["date"] for r in reports)}')
                print('       （--refresh 可重新拉取数据，但需东财接口可达）')
                raise ValueError(
                    f'未找到 {target} 的复盘数据缓存；可用日期: '
                    f'{", ".join(r["date"] for r in reports)}'
                )
        else:
            # 无显式日期 → 用截止时间解析"最新已收盘交易日"（15:30 前=昨日），
            # 而非盲取最新缓存，保证盘前不拿到今天的残缺数据
            from ashare_review.report.daily import DailyReport
            resolved = DailyReport()._resolve_trade_date()   # YYYYMMDD
            target = datetime.strptime(resolved, '%Y%m%d').strftime('%Y-%m-%d')
            hit = next((r for r in reports if r.get('date') == target), None)
            if hit:
                print(f'[数据] 使用缓存复盘报告 {hit["date"]}')
                return hit
            # 目标日无缓存 → 落到下方重新生成（generate 内部同样按截止时间解析）
            print(f'[数据] 无 {target} 的缓存，尝试重新生成...')

    # 重新生成
    print('[数据] 尝试重新生成复盘报告（需要东财/通达信数据）...')
    from ashare_review.report.daily import DailyReport
    try:
        report = DailyReport().generate(date_ymd)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise ValueError(f'复盘数据生成失败: {e}')
    if not _valid_report(report):
        raise ValueError(
            f'生成的数据不完整（成交额/涨跌家数为0），可能东财接口不可达。'
            f'请改用已有缓存日期，或确认数据源可用后加 --refresh。'
        )
    return report


def _stock_link(name: str, code: str) -> str:
    """生成东财行情链接 [名称](https://quote.eastmoney.com/unify/r/{mkt}.{code})"""
    code = str(code).zfill(6)
    mkt = '1' if code.startswith(('6', '9', '5')) else '0'
    return f'[{name}](https://quote.eastmoney.com/unify/r/{mkt}.{code})'


def _build_link_table(report: dict) -> dict[str, str]:
    """收集报告中出现的所有个股，构造 名称 -> 链接 映射"""
    table = {}

    def add(name, code):
        if name and code:
            table.setdefault(str(name), _stock_link(str(name), str(code)))

    for m in report.get('multi_board_list', []):
        add(m.get('name'), m.get('code'))
    for p in report.get('sentiment', {}).get('picks', []):
        add(p.get('name'), p.get('code'))
    for s in report.get('sector_analysis', {}).get('all_sectors', []):
        add(s.get('leader_name'), s.get('leader_code'))
    for s in report.get('concept_analysis', {}).get('concept_sectors', []):
        add(s.get('leader_name'), s.get('leader_code'))
    for c in report.get('weak_to_strong', []):
        add(c.get('name'), c.get('code'))
    for c in report.get('yesterday_picks', []):
        add(c.get('name'), c.get('code'))
    for c in report.get('auction_forecast', {}).get('strong_multi', []):
        add(c.get('name'), c.get('code'))
    for c in report.get('zt_replica_picks', [])[:8]:
        add(c.get('name'), c.get('code'))
    for l in report.get('top_lhb', [])[:5]:
        add(l.get('name'), l.get('code'))
    return table


def _index_tech() -> dict:
    """从通达信本地读取三大指数技术面（点位/均线 + 可计算的日线结构信号）。

    除收盘/涨跌幅/MA5/10/20/20日区间外，还从日线数据推导：均线排列、
    MA5/MA10 金叉死叉、缠论顶/底分型、20日区间位置、跳空缺口及回补。
    这些是范文里"做体检/双死叉/缺口回补"等推演的数据底座，让模型有据可依、不硬编。
    """
    try:
        from ashare_review.data.tdx_reader import TdxReader
        tdx = TdxReader()
    except Exception as e:
        return {'error': str(e)}

    def _signals(df) -> dict:
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        c = float(closes[-1])
        prev = float(closes[-2]) if len(closes) > 1 else c
        chg = (c - prev) / prev * 100 if prev else 0.0
        ma5 = float(closes[-5:].mean())
        ma10 = float(closes[-10:].mean())
        ma20 = float(closes[-20:].mean())
        ma5p = float(closes[-6:-1].mean())
        ma10p = float(closes[-11:-1].mean())
        hi20 = float(closes[-20:].max())
        lo20 = float(closes[-20:].min())

        # 均线排列：多头/空头/纠缠
        if ma5 > ma10 > ma20:
            align = '均线多头排列'
        elif ma5 < ma10 < ma20:
            align = '均线空头排列'
        else:
            align = '均线纠缠'
        # MA5/MA10 金叉/死叉（今日 vs 昨日）
        cross = ''
        if ma5p <= ma10p and ma5 > ma10:
            cross = 'MA5上穿MA10(金叉)'
        elif ma5p >= ma10p and ma5 < ma10:
            cross = 'MA5下穿MA10(死叉)'
        # 缠论顶/底分型（看倒数第二根，今日为右侧确认）
        fractal = ''
        if len(closes) >= 3:
            i = len(closes) - 2
            mid_h, mid_l = highs[i], lows[i]
            if (mid_h > highs[i-1] and mid_h > highs[i+1]
                    and mid_l > lows[i-1] and mid_l > lows[i+1]):
                fractal = '日线呈顶分型'
            elif (mid_h < highs[i-1] and mid_h < highs[i+1]
                    and mid_l < lows[i-1] and mid_l < lows[i+1]):
                fractal = '日线呈底分型'
            else:
                fractal = '日线顶分型未成立'
        # 20日区间位置
        pos = (c - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
        pos_label = ('20日区间高位' if pos >= 0.7
                     else '20日区间低位' if pos <= 0.3 else '20日区间中位')
        # 跳空缺口及回补（开盘 vs 昨收）
        gap, gap_note = '平开', ''
        o = float(df['open'].iloc[-1])
        gap_pct = (o - prev) / prev * 100 if prev else 0.0
        if gap_pct >= 0.3:
            gap = '跳空高开'
            gap_note = '冲高回落回补缺口' if c <= o else '高开缺口未回补'
        elif gap_pct <= -0.3:
            gap = '跳空低开'
            gap_note = '低开高走收复缺口' if c >= o else '低开缺口未收复'
        return {
            'close': round(c, 2),
            'change_pct': round(chg, 2),
            'ma5': round(ma5, 2),
            'ma10': round(ma10, 2),
            'ma20': round(ma20, 2),
            'hi20': round(hi20, 2),
            'lo20': round(lo20, 2),
            'ma_align': align,
            'ma_cross': cross,
            'fractal': fractal,
            'pos_label': pos_label,
            'gap': gap,
            'gap_note': gap_note,
        }

    out = {}
    for code, mkt, label in [('000001', 'sh', '上证指数'),
                             ('399001', 'sz', '深证成指'),
                             ('399006', 'sz', '创业板指')]:
        try:
            df = tdx.read_daily(code, mkt)
            if df is None or df.empty or len(df) < 25:
                continue
            out[label] = _signals(df)
        except Exception as e:
            out[label] = {'error': str(e)}
    return out


def _volume_context(report_date: str = None) -> dict:
    """从通达信本地读取沪深两市成交额及量能变化（用于判断放量/缩量）。

    口径与报告 market_overview.total_volume 一致：上证指数 + 深证成指成交额（亿）。
    report_date 在通达信里找不到（如盘前生成、未来日期）时回退到最后两根日线。
    返回 {'total_volume', 'prev_volume', 'change_pct'}；数据不可用返回 {}。
    """
    try:
        from ashare_review.data.tdx_reader import TdxReader
        tdx = TdxReader()
    except Exception:
        return {}
    cur_total, prev_total = 0.0, 0.0
    for code, mkt in (('000001', 'sh'), ('399001', 'sz')):
        try:
            df = tdx.read_daily(code, mkt)
            if df is None or df.empty or len(df) < 2:
                continue
            dates = df['trade_date'].astype(str)
            i = len(df) - 1
            if report_date and (dates == report_date).any():
                i = int((dates == report_date).argmax())
            cur_total += float(df['amount'].iloc[i]) / 1e8
            prev_total += float(df['amount'].iloc[i - 1]) / 1e8
        except Exception:
            continue
    if cur_total <= 0 or prev_total <= 0:
        return {}
    return {
        'total_volume': round(cur_total, 0),
        'prev_volume': round(prev_total, 0),
        'change_pct': round((cur_total - prev_total) / prev_total * 100, 1),
    }


def _volume_desc(ctx: dict) -> str:
    """量能变化口径描述：放量/缩量/持平（相对上一交易日，±5% 为界）"""
    if not ctx:
        return ''
    p = ctx.get('change_pct', 0)
    if p >= 5:
        return f'放量{abs(p):.0f}%'
    if p <= -5:
        return f'缩量{abs(p):.0f}%'
    return f'量能基本持平（{p:+.1f}%）'


# ------------------------------------------------------------------
# 当日重要资讯（"消息面上"素材）— 股市快讯 bot 留档 或 东财快讯实时接口
# ------------------------------------------------------------------
# bot 留档目录：D:\cursor\My（股市快讯推送项目），真正推出去的消息按天落档 data/kuaixun/YYYY-MM-DD.jsonl
KAIXUN_DIR = os.environ.get('KAIXUN_DIR', r'D:\cursor\My\data\kuaixun')

# A股相关筛选关键词（个股异动 + 宏观/政策/市场）
_MARKET_KEYWORDS = (
    'A股', '沪指', '深成', '创业板', '北证', '沪深',
    '涨停', '跌停', '炸板', '封板', '连板', '板块', '概念', '主力资金',
    '证监会', '央行', '国务院', '国常会', '发改委', '工信部', '财政部', '国家队',
    '印花税', '降准', '降息', '北向', '南向',
    '回购', '增持', '减持', '重组', '并购', '定增', '中标', '要约收购',
    '业绩预增', '业绩预告', '业绩下滑', '立案调查', '处罚', '退市',
    'IPO', '上市', '指数', 'ETF', '收评', '午评', '开盘', '收盘',
)


def _is_market_relevant(text: str, stocks: str = '') -> bool:
    """判断一条快讯是否与 A股市场相关（个股/宏观/政策）"""
    if stocks and stocks.strip() not in ('', '[]', 'null'):
        return True
    return any(k in text for k in _MARKET_KEYWORDS)


# 异动里最值得进消息面的关键词（板块/涨停/连板类，直接对应当日盘面）
_HOT_KX_KEYS = ('涨停', '跌停', '连板', '板块', '概念', '龙头', '炸板', '封板', '一字')


def _kx_dedup_key(title: str) -> str:
    """资讯去重键：提取核心标题（去类别、去尾部时间来源、去空白）后取前 16 字。

    同一新闻常被不同源重复推送，格式不同：
      - 新浪: 【异动】【算力租赁概念继续活跃 城地香江3连板】早盘…
      - 东财: 【异动】算力租赁概念继续活跃 城地香江3连板 09:26 · 东财7x24
    先去掉第一个类别标签，若紧跟嵌套【…】则以其内容作为核心标题，
    归一化后合并重复来源，把宝贵的消息面名额留给更多不同的异动。
    """
    t = re.sub(r'^【[^】]*】', '', title or '').strip()
    m = re.match(r'^【([^】]*)】', t)
    if m:
        t = m.group(1)
    t = re.sub(r'\d{2}:\d{2}.*$', '', t)
    t = re.sub(r'\s+', '', t)
    return t[:16]


def _kx_priority(title: str) -> int:
    """给一条资讯打分，数值越小越靠前。

    异动(尤其板块/涨停/连板类)最有复盘价值 → 公告 → 宏观最弱。
    """
    m = re.match(r'【([^】]+)】', title or '')
    cat = m.group(1) if m else ''
    if cat == '异动':
        return 0 if any(k in title for k in _HOT_KX_KEYS) else 1
    if cat == '公告':
        return 2
    if cat == '宏观':
        return 3
    return 4


def _fetch_kuaixun(date_ymd: str) -> list[dict]:
    """取指定交易日(YYYY-MM-DD)的 A股相关重要资讯，供"消息面上"参考。

    优先读 bot 留档（用户认可的"每日重要资讯"）；无留档则拉东财快讯实时接口，
    按日期过滤。都没有该日期的资讯则返回空列表（文章层自动跳过，不写消息面）。
    选稿按"异动优先"排序：板块/涨停/连板类异动排最前，宏观殿后。
    """
    items: list[dict] = []

    # 1) 本地留档（bot 推出去的消息）
    arch = os.path.join(KAIXUN_DIR, f'{date_ymd}.jsonl')
    if os.path.exists(arch):
        try:
            with open(arch, encoding='utf-8') as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        d = json.loads(ln)
                    except Exception:
                        continue
                    title = str(d.get('content', '') or '')
                    ts = str(d.get('ts', '') or '')
                    if ts[:10] != date_ymd:
                        continue
                    if _is_market_relevant(title, ''):
                        items.append({'time': ts[11:16] if len(ts) >= 16 else '', 'title': title})
        except Exception:
            items = []

    # 2) 无留档 → 东财快讯实时接口
    if not items:
        try:
            import requests
            url = 'https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html'
            r = requests.get(url, timeout=8, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.eastmoney.com/'})
            data = r.json()
            raw = data.get('data', {}).get('list', []) if isinstance(data, dict) else []
            for it in raw:
                t = str(it.get('time', ''))
                if t[:10] != date_ymd:
                    continue
                title = str(it.get('title', '') or '')
                if _is_market_relevant(title, str(it.get('stocks', '') or '')):
                    items.append({'time': t[11:16], 'title': title})
        except Exception:
            pass

    # 3) 异动优先排序 + 去重 + 截断（同优先级保持原始时间顺序）
    seen, out = set(), []
    for it in sorted(items, key=lambda x: _kx_priority(x.get('title', ''))):
        key = _kx_dedup_key(it['title'])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= 8:
            break
    if out:
        print(f"[资讯] 当日 A股相关重要资讯 {len(out)} 条（来源: {'bot留档' if os.path.exists(arch) else '东财快讯'}）")
    else:
        print(f"[资讯] {date_ymd} 无匹配的重要资讯，文章不写消息面")
    return out


# ------------------------------------------------------------------
# Prompt 构建
# ------------------------------------------------------------------
def _fmt_time_dist(td: dict) -> str:
    if not td:
        return ''
    return ('，'.join(f'{k}:{v}只' for k, v in td.items()))


def _fmt_sector(s: dict) -> str:
    extra = []
    if s.get('is_new_theme'):
        extra.append('新题材')
    if s.get('first_count') == s.get('zt_count'):
        extra.append('全部首板')
    leader = s.get('leader_name') or '-'
    lc = s.get('leader_cons', 1)
    return (f"{s.get('name')}: {s.get('zt_count')}家涨停"
            f"({s.get('strength')}{','.join(extra) if extra else ''})"
            f"，龙头{leader}({lc}板)")


def _build_prompt(report: dict, index_tech: dict, personal_note: str,
                  kuaixun: list = None, volume: dict = None) -> str:
    r = report
    mo = r.get('market_overview', {})
    cyc = r.get('cycle', {})
    sens = r.get('sentiment', {})
    auc = r.get('auction_forecast', {})

    def L(name, code, extra=''):
        return _stock_link(name, code) + extra

    # --- 精选每段用到的少数事实 ---
    hot = r.get('sector_analysis', {}).get('all_sectors', [])
    top_sectors = hot[:4]
    sectors_block = '\n'.join(
        f"- {s.get('name')}: {s.get('zt_count')}家涨停，龙头{L(s.get('leader_name'), s.get('leader_code'), f'（{s.get("leader_cons")}板）')}"
        for s in top_sectors) if top_sectors else '- 无显著热点板块'

    # 细分概念题材（概念映射表×涨停池，如"MLCC概念""算力租赁"）
    concepts = r.get('concept_analysis', {}).get('concept_sectors', [])
    concept_block = '\n'.join(
        f"- {s.get('name')}: {s.get('zt_count')}家涨停，龙头{L(s.get('leader_name'), s.get('leader_code'), f'（{s.get("leader_cons")}板）')}"
        for s in concepts[:4]) if concepts else '- 无细分概念匹配（今日涨停股未落入已维护的概念表）'

    ladder = r.get('multi_board_list', [])[:6]
    ladder_block = '\n'.join(
        f"- {L(m.get('name'), m.get('code'))}：{m.get('consecutive')}连板，{m.get('board_type')}"
        for m in ladder) if ladder else '- 无'

    yp = r.get('yesterday_picks', [])
    yp_ok = [c for c in yp if c.get('is_zt_today')][:3]
    yp_fail = [c for c in yp if not c.get('is_zt_today')][:2]
    yp_lines = [f"{L(c.get('name'), c.get('code'))}今日{c.get('today_chg')}%（{c.get('result')}）"
                for c in yp_ok + yp_fail]
    yp_block = '；'.join(yp_lines) if yp_lines else '无昨日精选数据'

    wts = r.get('weak_to_strong', [])[:3]
    wts_block = '；'.join(
        f"{L(c.get('name'), c.get('code'))}（{c.get('weak_signal')}）" for c in wts) if wts else '无'

    themes = r.get('ongoing_themes', [])[:4]
    theme_block = '；'.join(f"{t.get('name')}：{t.get('description')}" for t in themes) if themes else '无'

    # 当日重要资讯（"消息面上"素材）
    kx = kuaixun or []
    news_block = '\n'.join(f"- {it['time']} {it['title']}" for it in kx) if kx else ''
    if kx:
        news_inst = ('。消息面上优先从下方【当日重要资讯】里挑与今日热点板块/连板股相关的异动'
                     '（如"算力租赁概念异动、城地香江3连板"这类盘面异动），补一句"消息面上，……"；'
                     '只有全部都不相关才不写。只写下方真实出现的，禁止编造新闻或政策')
    else:
        news_inst = '。没有相关资讯就只写盘面，不要编造消息面'

    # --- 网页补充分析（筹码/龙虎榜/涨停复制/时间分布/精选统计） ---
    chips = r.get('chip_signals', [])[:4]
    chip_block = '；'.join(
        f"{L(c.get('name'), c.get('code'))}({c.get('consecutive')}板,{c.get('pattern')})：{c.get('description')} {c.get('action')}"
        for c in chips) if chips else '无'
    lhb_seen, lhb = set(), []
    for x in (r.get('top_lhb', []) or []):
        if not x.get('code') or x.get('code') in lhb_seen:
            continue
        lhb_seen.add(x['code'])
        lhb.append(x)
        if len(lhb) >= 3:
            break
    lhb_block = '；'.join(
        f"{L(x.get('name'), x.get('code'))}净买{float(x.get('net_amount') or 0)/1e4:.2f}亿"
        for x in lhb) if lhb else '无'
    replica = r.get('zt_replica_picks', [])[:3]
    replica_block = '；'.join(
        f"{L(c.get('name'), c.get('code'))}({c.get('sig_type')},量比{c.get('vol_ratio')})"
        for c in replica) if replica else '无'
    time_block = _fmt_time_dist(r.get('time_distribution')) or '无'
    ps = r.get('pick_stats') or {}
    pick_stats_block = (f"近{ps.get('total_days', 0)}日共精选{ps.get('total_picks', 0)}只，均分{ps.get('avg_score', 0)}"
                        if ps.get('total_picks') else '无')

    # --- 指数技术面 ---
    if index_tech.get('error'):
        idx_text = f"（指数数据不可用: {index_tech['error']}）"
    else:
        idx_lines = []
        for label in ('上证指数', '深证成指', '创业板指'):
            v = index_tech.get(label)
            if not v:
                continue
            above5 = '站上' if v['close'] >= v['ma5'] else '跌破'
            sig = [x for x in (v.get('ma_align'), v.get('ma_cross'), v.get('fractal'),
                               v.get('pos_label')) if x]
            if v.get('gap') and v.get('gap') != '平开':
                sig.append(v['gap'] + ('、' + v['gap_note'] if v.get('gap_note') else ''))
            sig_txt = f"，{'、'.join(sig)}" if sig else ''
            idx_lines.append(
                f"{label}收于{v['close']}点，涨跌{v['change_pct']:+.2f}%，{above5}5日均线{v['ma5']}，"
                f"20日区间[{v['lo20']}，{v['hi20']}]{sig_txt}"
            )
        idx_text = '；'.join(idx_lines)

    # 指数红绿基调（供第7段判断用，避免套用范文"虽然指数收红"的固定句式）
    if index_tech.get('error'):
        idx_close_tone = '指数涨跌以该段真实涨跌家数为准'
    else:
        sh_v = index_tech.get('上证指数')
        _c = sh_v.get('change_pct', 0) if sh_v else 0
        idx_close_tone = '指数收红' if _c > 0 else ('指数收绿' if _c < 0 else '指数涨跌互现')

    if personal_note:
        personal_inst = (
            f'（必写段，插在"回到今日盘面"段与"操作策略"段之间）单起一段，开头"就我个人持仓而言"，'
            f'以这句话为底、润色成范文口吻："{personal_note}"。'
            f'可补一笔仓位管理与心态回顾（如"趁反弹适度高抛回血、回踩再低吸"，点明每个人的仓位/风险偏好不同需自调），'
            f'把仓位管理作为应对之锚，别只罗列持仓名字。'
        )
        personal_note_rule = f'（个人持仓段必写）在"回到今日盘面"段与"操作策略"段之间必须单起一段"就我个人持仓而言"。'
        personal_check_item = '第8段"就我个人持仓而言，"'
    else:
        personal_inst = '不写个人持仓段。'
        personal_note_rule = '不写个人持仓段。'
        personal_check_item = ''

    md_date = _fmt_md(r.get('date', ''))

    # 量能对比（较上一交易日放量/缩量），喂给第2段大盘综述，避免模型凭空写"缩量/放量"
    vol_chg = f'（较上一交易日{_volume_desc(volume)}）' if volume else ''

    prompt = f"""你是一名A股短线复盘作者，网名"逻辑哥"。下面是你的范文，请模仿它的口语化文风、句式与起手写法，写今天的盘后复盘。

【范文】
{TEMPLATE_EXAMPLE}

{STYLE_CHECKLIST}

{ANALYSIS_METHOD}

【硬性规则】
- 每段必须用范文的招牌起手句，一个都不能少：
  第3段开头"从板块来看，"
  第4段开头"整体来看，"
  第5段开头"观察盘面，认为："
  第6段开头"指数层面："
  第7段开头"回到今日盘面，"
  第8段开头"操作策略："
- 写成长段通顺的中文，像范文那样把数字揉进句子里；禁止逐字复述"数据"原文、禁止列点、禁止任何 Markdown（#、-、*、1.、加粗）。
- 所有数字、个股、板块、连板数必须与"该段数据"一致；禁止编造额外的个股、板块、点位或新闻。范文里的具体点位/成交额/家数/涨跌幅都只是示例，一律不要照抄。
- 范文可包含"午后推演"式前瞻，但只学它的语气、比喻和推演方式，严禁照抄范文里的点位/成交额/家数/涨跌幅等任何具体数字（那些都是8-17的旧数据）。盘后文章可用"午后/次日/后市"视角做条件和推演，但"半日成交额/半日家数"这类半日数据禁止（报告数据全天收盘）。
- 个股带链接：数据里凡是已经带 [名称](链接) 的，正文出现该名称时必须原样用上这个链接，不要只写名称。
- 首行单独一行写：复盘分析，{md_date}
- 全文 900-1300 字。
- {personal_note_rule}
- 以下起手句必须各自独立成段，且顺序不变，一个都不能少：
  第3段"从板块来看，"
  第4段"整体来看，"
  第5段"观察盘面，认为："
  第6段"指数层面："
  第7段"回到今日盘面，"
  第8段"操作策略："
  {personal_check_item}
  结尾"值得强调的是，"
  写完自查一遍，缺哪个就补上哪个。
- 直接输出正文，不要任何解释。

【第2段 大盘综述】开头先摆两市成交额与"较前一日放/缩量"，随即给量价联动双分支推演（如"若能温和放量，则指数上行可持续性和高度更为乐观；反之若量能再度萎缩，则短期以震荡反复为主"），再带出三大指数涨跌与普涨/分化格局（普涨要补一句"注意防止普涨后的分化"）、上涨/下跌家数、涨停与最高板（参考范文第2段"今日成交2.40万亿，较前一日放量……"的写法）。
该段数据：上证收{index_tech.get('上证指数', {}).get('close', '?')}({index_tech.get('上证指数', {}).get('change_pct', 0):+.2f}%)、深成收{index_tech.get('深证成指', {}).get('close', '?')}({index_tech.get('深证成指', {}).get('change_pct', 0):+.2f}%)、创业板收{index_tech.get('创业板指', {}).get('close', '?')}({index_tech.get('创业板指', {}).get('change_pct', 0):+.2f}%)；两市成交额约{mo.get('total_volume', 0):.0f}亿{vol_chg}；上涨{mo.get('up_count', 0)}家、下跌{mo.get('down_count', 0)}家；涨停{r.get('total_limit_ups')}只（封板率{r.get('seal_rate')}），最高{r.get('max_consecutive')}板。

【第3段 热点板块】开头写"从板块来看，"，选 2-3 个行业热点 + 1-2 个细分概念题材，每个写"XX走强/活跃，龙头A四连板，B、C等跟涨"（范文式）。板块段要写出"气势"：用"持续走强/震荡反弹/双双涨停/领涨"这类动感词（参考范文"剑桥科技继昨日大幅上涨后，今日又迎大涨""艾华集团、华正新材双双涨停"），别把板块名和涨停数逐条罗列成数据清单。封板率、炸板数、成交额这类全市场指标不要写进板块段（它们属于第7段），涨停数只写本板块自己的。细分概念要写成精确概念名（如"MLCC概念""算力租赁""创新药"），只写下方"细分概念数据"里出现的，禁止编造概念名或个股归属。若有题材背景就补一句"消息面上，……"{news_inst}。
行业热点数据：
{sectors_block}
细分概念数据：
{concept_block}
题材背景（可选）：{theme_block}
当日重要资讯（可选，消息面上素材）：
{news_block if news_block else '（今日无匹配资讯）'}

【第4段 整体小结】开头写"整体来看，"，一句话概括：下方"活跃方向"里列出的行业/概念方向活跃，其余方向平淡或走弱（直接用下方真实名称写，禁止写占位字母或未替换的示例符号）。活跃方向：{'、'.join(s.get('name') for s in top_sectors) or '无'}；概念题材：{'、'.join(s.get('name') for s in concepts[:3]) or '无'}。

【第5段 观察盘面】开头写"观察盘面，认为："，用情绪周期信息写 2-3 句量能与轮动的点评（参考范文"市场情绪也因此受到压制""情绪端尚未出现系统性恶化信号"这种带判断的口吻，敢下结论，不要整段照抄数据原文）。涨停时间分布（下方补充素材）可作为情绪佐证：早盘封板多=抢筹意愿强，午后封板多=分歧加大；有跌停家数时用它佐证恐慌是否可控。
该段数据：情绪周期{cyc.get('stage')}；{cyc.get('stage_desc')}；建议{cyc.get('action')}。

【第6段 指数层面】开头写"指数层面："，用下方"点位数据"里真实的技术结构写 2-3 句，把技术形态和双分支推演绑死：均线站上/跌破、阳包阴反包=转强、推动浪（自阶段低点以来第一波=回撤属正常）、顶背离=冲高回落预警、缺口性质（高开未回补=做多意愿+回踩确认焦点）、年线/60日均线=中期方向分水岭、整数关口。配量能解读（缩量/放量），并落"守住关键位会怎样、失守该怎么应对"的条件结论，可点当日"核心矛盾"（如"午后核心矛盾不在能否触及年线，而在触及之后多空如何博弈"）。参考范文"双创指数与深成指……缺口有望成为突破性缺口"那种推演口吻，不要只贴点位就完事。数据里没有的信号（如某指数今日平开无缺口）不要硬编。
点位数据：{idx_text}

【第7段 回到今日盘面】开头写"回到今日盘面，"，用该段真实涨跌家数写 2-3 句：{idx_close_tone}，但要写出涨停封板率与连板高度背后的真实含义（下跌家数、炸板率、追高风险），把数字揉进句子里（如"仍有{mo.get('down_count', 0)}家个股下跌"）。下跌家数必须用该段数据，禁止照抄范文里"超过3000家"这类固定数字，也不要重复第4段或第5段的情绪周期描述。注意：指数红绿必须与第2段/第6段给出的真实涨跌一致，禁止套用范文里写死指数涨跌方向的固定句式。若有筹码警示（获利盘>90%）的高位连板股（下方补充素材），可点一句高位出货风险。
该段数据：上涨{mo.get('up_count', 0)}家、下跌{mo.get('down_count', 0)}家；封板率{r.get('seal_rate')}，涨停{r.get('total_limit_ups')}只、炸板{r.get('broken', 0)}只。

{personal_inst}

【第8段 操作策略】开头写"操作策略："，写 4-5 句：①先给次日整体判断（竞价偏强/偏弱）②再给关注方向（哪些板块龙头或个股），若数据支持先点一句"核心矛盾或结构分化"（如热度集中在核心标的、跟风弱、指数与个股分化）再落到选股侧重③再给确认条件（如竞价高开幅度、封单、弱转强确认）④用下方【昨日精选验证】的真实结果佐证（句式示例"昨日精选的X今日2连板成功"，X 必须是该数据里出现过的名称，禁止编造名单外的个股或连板数）⑤次日关注方向可参考下方【补充素材】的龙虎榜净买与涨停复制候选（只写素材里真实出现的）⑥把仓位管理作"应对之锚"，收一句"宁可少赚，不可深套，不确定时及时离场"式纪律。
竞价预期：{auc.get('forecast')}（{auc.get('forecast_desc')}）；早盘秒板{auc.get('early_sealed')}只、上午{auc.get('morning_sealed')}只、下午{auc.get('afternoon_sealed')}只。
昨日精选验证：{yp_block}
（注：以上为昨日精选名单，验证"昨日选出→今日表现"；禁止混入弱转强候选、涨停复制或其他今日候选名单里的个股，也不要改变其今日涨幅数据。）
弱转强候选：{wts_block}

【补充素材（网页分析，相关性高才引用，禁止编造个股/数字/涨跌幅）】
涨停时间分布：{time_block}。
筹码警示：{chip_block}。
龙虎榜：{lhb_block}。
涨停复制候选：{replica_block}。
昨日精选统计：{pick_stats_block}。

【第9段 结尾】以"值得强调的是，"开头，写 2-3 句心态劝勉 + 一句次日观察点。要有真实观点和温度，可用口语化转场（参考范文"当然，……也有""好了，……"的松弛感），也可把操作纪律升级成哲理性收束（如"高抛不是看空，而是为更好的低吸留出空间""涨有度，退有据，方能在不确定中稳住心神"），别写成干巴巴的总结。注意：结尾的哲理感悟段与"承蒙各位支持…"互动收尾会自动追加在文末，你只需写到这里为止，不要自己写这两段。"""

    return prompt


# ------------------------------------------------------------------
# 生成
# ------------------------------------------------------------------
def _generate(prompt: str, provider: str, model: str, stream: bool) -> str:
    from ashare_review.agents.providers import create_provider
    p = create_provider(provider)
    if model:
        p.model = model
    print(f'[LLM] {p.model} 生成中...')

    messages = [
        {'role': 'system', 'content': '你是A股复盘作者"逻辑哥"。输出必须是纯文本段落，严禁使用任何Markdown符号（#、-、*、数字列表、加粗）。'},
        {'role': 'user', 'content': prompt},
    ]

    if stream:
        async def _stream():
            chunks = []
            async for piece in p.chat_stream(messages, temperature=0.55, max_tokens=4096):
                print(piece, end='', flush=True)
                chunks.append(piece)
            print()
            return ''.join(chunks)
        return asyncio.run(_stream())

    return p.chat_sync(messages, temperature=0.55, max_tokens=4096)


# ------------------------------------------------------------------
# 后处理：补链接 + 段落自检/修复
# ------------------------------------------------------------------
REQUIRED_SECTIONS = [
    ('从板块来看', '热点板块'),
    ('整体来看', '整体小结'),
    ('观察盘面，认为：', '观察盘面'),
    ('指数层面：', '指数层面'),
    ('回到今日盘面', '个股分化'),
    ('操作策略：', '操作策略'),
    ('值得强调的是', '结尾劝勉'),
]


def _apply_links(text: str, report: dict) -> str:
    """给正文补上行情链接：对链接表里的个股名做确定性替换，跳过已带链接的"""
    table = _build_link_table(report)
    if not table:
        return text
    out = text
    for name in sorted(table, key=len, reverse=True):
        link = table[name]
        # 排除已链接（前有 '[' 后有 ']('）的情况
        pattern = re.compile(r'(?<!\[)' + re.escape(name) + r'(?!\]\()')
        out = pattern.sub(link, out)
    return out


def _verify_missing(text: str, personal_note: bool) -> list[str]:
    """检查必写起手句是否齐全，返回缺失的段落标签"""
    missing = [label for opener, label in REQUIRED_SECTIONS if opener not in text]
    # 第2段大盘综述没有固定起手句，按内容检测：全文若无"成交/量能"表述说明模型直接跳过了第一段
    # （7B 常跳段，若不检测则量价先行段永远丢失）。有则视为已写过。
    if '成交' not in text:
        missing.append('大盘综述')
    if personal_note and '就我个人持仓而言' not in text:
        missing.append('个人持仓')
    return missing


def _smooth_markdown(text: str) -> str:
    """把残留的 markdown 列表/加粗抹平成通顺文字（确定性清洗）"""
    # 连续 bullet/编号行合并为一句（分号连接）
    lines = text.split('\n')
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        is_bullet = re.match(r'^\s*[-*]\s+', line) or re.match(r'^\s*\d+[\.、]\s+', line)
        if is_bullet:
            parts = []
            while i < len(lines) and (re.match(r'^\s*[-*]\s+', lines[i]) or re.match(r'^\s*\d+[\.、]\s+', lines[i])):
                parts.append(re.sub(r'^\s*[-*]\s+|^\s*\d+[\.、]\s+', '', lines[i]).strip())
                i += 1
            out.append('；'.join(parts))
        else:
            out.append(line)
            i += 1
    text = '\n'.join(out)
    text = text.replace('**', '')
    # 清理模型回显的段落结构标签（【第1段 大盘综述】【第8段 操作策略】【补充素材】等）
    text = re.sub(r'【[^】]*】', '', text)
    # 折叠重复起手句（缺段修复插入与原文残留叠加导致，如"从板块来看，从板块来看，…"）
    for op in ('从板块来看，', '整体来看，', '观察盘面，认为：', '指数层面：',
               '回到今日盘面，', '操作策略：', '值得强调的是，'):
        text = text.replace(op + op, op)
    text = text.replace('认为：认为：', '认为：')   # 模型把起手句回显两次（"观察盘面，认为：认为："）
    # 修复模型把补写的大盘综述段回显两次、中间夹"大盘综述："标签 → 从标签处截断到行尾，保留前半段
    text = re.sub(r'。\s*大盘综述[:：].*$', '。', text, flags=re.M)
    # 修复模型把"补充素材"（涨停时间分布/筹码警示/龙虎榜/涨停复制候选/精选统计）整段贴进正文 → 从标签截断到行尾
    text = re.sub(r'(涨停时间分布|筹码警示|龙虎榜|涨停复制候选|昨日精选统计)[:：].*$', '', text, flags=re.M)
    # 范文用"从板块表现来看"（用户最新写法），统一成规范起手句，保证段落自检命中
    text = text.replace('从板块表现来看，', '从板块来看，')
    text = re.sub(r'；{2,}', '；', text)          # 折叠重复分号
    text = re.sub(r'^[，；、\s]+', '', text, flags=re.M)  # 去行首标点
    return text


def _build_compact_data(report: dict, index_tech: dict) -> str:
    """为修复调用构建精简数据块"""
    r = report
    mo = r.get('market_overview', {})
    cyc = r.get('cycle', {})
    auc = r.get('auction_forecast', {})
    hot = r.get('sector_analysis', {}).get('all_sectors', [])[:4]

    parts = [
        f"日期{r.get('date')}；涨停{r.get('total_limit_ups')}只(封板率{r.get('seal_rate')})，"
        f"最高{r.get('max_consecutive')}板；成交额约{mo.get('total_volume', 0):.0f}亿；"
        f"上涨{mo.get('up_count', 0)}家、下跌{mo.get('down_count', 0)}家。"
    ]
    sec_texts = []
    for s in hot:
        sec_texts.append('{}（{}家，龙头{}{}板）'.format(
            s.get('name'), s.get('zt_count'),
            s.get('leader_name'), s.get('leader_cons')))
    if sec_texts:
        parts.append('热点板块：' + '；'.join(sec_texts) + '。')
    concept_texts = []
    for s in r.get('concept_analysis', {}).get('concept_sectors', [])[:4]:
        concept_texts.append('{}（{}家，龙头{}{}板）'.format(
            s.get('name'), s.get('zt_count'),
            s.get('leader_name'), s.get('leader_cons')))
    if concept_texts:
        parts.append('细分概念：' + '；'.join(concept_texts) + '。')
    parts.append('情绪周期：{}，{}，建议{}。'.format(
        cyc.get('stage'), cyc.get('stage_desc'), cyc.get('action')))
    parts.append('竞价预期：{}，{}。'.format(auc.get('forecast'), auc.get('forecast_desc')))

    if not index_tech.get('error'):
        idx = []
        for label in ('上证指数', '深证成指', '创业板指'):
            v = index_tech.get(label)
            if v:
                idx.append('{}{}({:+.2f}%) MA5={}'.format(label, v['close'], v['change_pct'], v['ma5']))
        parts.append('指数：' + '；'.join(idx) + '。')
    return '\n'.join(parts)


def _section_hints(labels: list[str], report: dict, index_tech: dict) -> str:
    """给每个缺失段落配它该用的数据，避免修复时写套话"""
    r = report
    mo = r.get('market_overview', {})
    cyc = r.get('cycle', {})
    auc = r.get('auction_forecast', {})
    hot = r.get('sector_analysis', {}).get('all_sectors', [])[:4]
    yp = r.get('yesterday_picks', [])
    wts = r.get('weak_to_strong', [])[:3]

    def _idx(label):
        v = index_tech.get(label)
        return '{}{}({:+.2f}%) MA5={}'.format(label, v['close'], v['change_pct'], v['ma5']) if v else ''

    hints = []
    for label in labels:
        if label == '大盘综述':
            h = ('成交额约{:.0f}亿；上涨{}家、下跌{}家；涨停{}只（封板率{}）、最高{}板。'
                 '写第一段（无固定起手句，插在全文最前）：先量价先行——开头写"今日成交X万亿、较前一日放量/缩量"，'
                 '随即给量价联动双分支推演（温和放量则上行可持续、量能萎缩则震荡反复），再带三大指数涨跌与家数/涨停。'
                 .format(mo.get('total_volume', 0), mo.get('up_count', 0), mo.get('down_count', 0),
                         r.get('total_limit_ups'), r.get('seal_rate'), r.get('max_consecutive')))
        elif label == '热点板块':
            h = '板块：' + '；'.join(
                '{}（{}家，龙头{}{}板）'.format(s.get('name'), s.get('zt_count'),
                                            s.get('leader_name'), s.get('leader_cons')) for s in hot)
            csec = r.get('concept_analysis', {}).get('concept_sectors', [])[:3]
            if csec:
                h += '；细分概念：' + '；'.join(
                    '{}（{}家，龙头{}{}板）'.format(s.get('name'), s.get('zt_count'),
                                                s.get('leader_name'), s.get('leader_cons')) for s in csec)
        elif label == '整体小结':
            h = '活跃方向：' + ('、'.join(s.get('name') for s in hot) or '无')
        elif label == '观察盘面':
            h = '情绪周期：{}；{}；建议{}。'.format(cyc.get('stage'), cyc.get('stage_desc'), cyc.get('action'))
        elif label == '指数层面':
            h = '指数：' + ('；'.join(_idx(x) for x in ('上证指数', '深证成指', '创业板指') if _idx(x)) or '')
        elif label == '个股分化':
            h = '上涨{}家、下跌{}家。'.format(mo.get('up_count', 0), mo.get('down_count', 0))
        elif label == '操作策略':
            yp_t = '；'.join('{}今{}%'.format(c.get('name'), c.get('today_chg')) for c in yp[:3])
            wts_t = '；'.join(c.get('name') for c in wts)
            h = '竞价预期：{}（{}）；昨日精选：{}；弱转强候选：{}。'.format(
                auc.get('forecast'), auc.get('forecast_desc'), yp_t, wts_t)
        elif label == '结尾劝勉':
            h = '今日情绪周期：{}。'.format(cyc.get('stage'))
        elif label == '个人持仓':
            h = '（个人持仓段，按你提供的持仓话来写）'
        else:
            h = ''
        hints.append(f'{label} 对应数据：{h}')
    return '\n'.join(hints)


def _normalize_repair_opener(block: str) -> str:
    """修复模型常把提示里的段标签回显成开头（如"整体小结：""热点板块："），统一成规范起手句"""
    m = {
        '热点板块': '从板块来看，',
        '整体小结': '整体来看，',
        '观察盘面': '观察盘面，认为：',
        '指数层面': '指数层面：',
        '个股分化': '回到今日盘面，',
        '操作策略': '操作策略：',
        '结尾劝勉': '值得强调的是，',
    }
    for label, opener in m.items():
        if block.startswith(label):
            return opener + block[len(label):].lstrip('：:，, ')
    if block.startswith('大盘综述'):
        return block[len('大盘综述'):].lstrip('：:，, ')
    return block


def _place_repaired_blocks(text: str, blocks: list[str]) -> str:
    """把补写块按规范顺序插回正文。

    模型返回的块顺序不可靠（常打乱，还爱回显段标签），这里按每块的实际起手句
    判断归属、逆序插入：_insert_before_next 以"下一个已存在起手句"为锚点，
    高位段先插、低位段后插，让后插的低位段落到已插高位段之前，保证规范先后。
    """
    order = [op for op, _ in REQUIRED_SECTIONS]
    placed = []
    for block in blocks:
        block = re.sub(r'【[^】]*】', '', block)      # 去结构标签
        block = re.sub(r'^[；:：、\s]+', '', block)    # 去行首标点
        block = _normalize_repair_opener(block)
        if not block:
            continue
        rank = next((i for i, op in enumerate(order) if op in block[:20]), len(order) + 1)
        placed.append((rank, block))
    out = text
    for _, block in sorted(placed, key=lambda x: -x[0]):   # 逆序：高位段先插
        out = _insert_before_next(out, block)
    return out


def _repair_missing(text: str, missing: list[str], report: dict,
                    index_tech: dict, provider: str, model: str) -> str:
    """缺段落时，用一次短 LLM 调用补写并拼回正确位置"""
    data = _build_compact_data(report, index_tech)
    hints = _section_hints(missing, report, index_tech)
    prompt = (
        '下面是一篇A股盘后复盘，缺失了以下段落：' + '、'.join(missing) + '。\n'
        '请用与全文一致的口语化文风，只补写缺失的这些段落。每段独立成段，开头必须用正确的起手句'
        '（如"从板块来看，……""观察盘面，认为：……""指数层面：……""操作策略：……"）。'
        '其中"大盘综述"段没有固定起手句：先写成交额与量能（"今日成交X万亿、较前一日放量/缩量"），'
        '给量价联动双分支推演，插到文章最前面。\n'
        '务必把下方"对应数据"里的数字用进段落里，写成通顺句子，不要写套话。\n'
        '文风要求：板块段要有气势（"持续走强/双双涨停"这类动感词，别把涨停数逐条罗列成清单）；'
        '指数/技术面讲具体结构并给条件结论（守住会怎样、失守怎么办）；敢下判断，像老朋友聊天，别机械播报数据。\n'
        '多个段落之间用一行"===NEXT==="分隔。只输出补写内容，不要解释、不要列点。\n\n'
        '【现有文章】\n' + text + '\n\n'
        '【整体数据】\n' + data + '\n\n'
        '【各缺失段对应数据】\n' + hints
    )
    print(f'[修复] 缺失段落: {", ".join(missing)}，补写中...')
    from ashare_review.agents.providers import create_provider
    p = create_provider(provider)
    if model:
        p.model = model
    repaired = p.chat_sync(
        [{'role': 'user', 'content': prompt}], temperature=0.5, max_tokens=1600)

    # 按分隔行切块，与缺失段对应（模型返回顺序不可靠，按实际起手句排序插回）
    blocks = [b.strip() for b in re.split(r'===NEXT===', repaired) if b.strip()]
    return _place_repaired_blocks(text, blocks)


def _insert_before_next(text: str, block: str) -> str:
    """把补写块插到规范顺序中"下一个已存在起手句"之前"""
    order = [op for op, _ in REQUIRED_SECTIONS]
    # 确定 block 属于哪个起手句
    own_opener = next((op for op in order if block.startswith(op)), None)
    if not own_opener:
        own_opener = next((op for op in order if op in block[:15]), None)
    if own_opener and own_opener in order:
        idx = order.index(own_opener)
        for op in order[idx + 1:]:
            pos = text.find(op)
            if pos >= 0:
                return text[:pos].rstrip() + '\n\n' + block + '\n\n' + text[pos:].lstrip()
    # 兜底：插到全文第一个已存在段落前
    pos = min((text.find(op) for op, _ in REQUIRED_SECTIONS if text.find(op) >= 0), default=-1)
    if pos >= 0:
        return text[:pos].rstrip() + '\n\n' + block + '\n\n' + text[pos:].lstrip()
    return text + '\n\n' + block


def _postprocess(text: str, report: dict, index_tech: dict,
                 provider: str, model: str, personal_note: str,
                 volume: dict = None) -> str:
    """去 markdown → 合并拆行的起手句/板块段 → 补链接 → 段落自检 → 缺段修复（最多一轮）→ 追加确定性结尾"""
    md_date = _fmt_md(report.get('date', ''))
    # 首行"复盘分析，xx-xx"确定性生成（7B 常忽略），种在顶部以便缺段修复把大盘综述插到头行之后
    header = f'复盘分析，{md_date}'
    if not text.lstrip().startswith('复盘分析'):
        text = header + '\n\n' + text
    text = _smooth_markdown(text)
    text = _normalize_paragraphs(text)
    text = _apply_links(text, report)
    missing = _verify_missing(text, bool(personal_note))
    if missing:
        text = _repair_missing(text, missing, report, index_tech, provider, model)
        text = _smooth_markdown(text)
        text = _normalize_paragraphs(text)
        text = _apply_links(text, report)
        still = _verify_missing(text, bool(personal_note))
        if still:
            print(f'[提醒] 修复后仍缺: {", ".join(still)}（可 --no-stream 重跑一次）')
    text = _append_tail(text, report, volume)
    return text


def _write_output(text: str, report_date: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f'复盘_{report_date}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def generate_article(date_ymd: str = None, personal_note: str = '', provider: str = 'ollama',
                     model: str = None, refresh: bool = False, stream: bool = False,
                     use_kuaixun: bool = True) -> dict:
    """核心入口（CLI 与 web 共用）：加载报告 → 构建 prompt → 生成 → 后处理。

    use_kuaixun: 是否注入当日重要资讯（"消息面上"素材）。true 时优先读 bot 留档
                 （D:\\cursor\\My\\data\\kuaixun\\{date}.jsonl），无则拉东财快讯接口。

    Returns:
        {'date': 'YYYY-MM-DD', 'text': 文章正文}
    """
    report = _load_report(date_ymd, refresh)
    report_date = report.get('date', '')
    print(f'[信息] 报告日期 {report_date} | 涨停{report.get("total_limit_ups")} | '
          f'封板率{report.get("seal_rate")} | 最高{report.get("max_consecutive")}板 | '
          f'周期{report.get("cycle", {}).get("stage")}')

    index_tech = _index_tech()
    if index_tech.get('error'):
        print(f'[警告] 指数技术面不可用: {index_tech["error"]}')

    volume = _volume_context(report_date)
    if volume:
        print(f'[量能] 成交额约{volume["total_volume"]:.0f}亿，较上一交易日{_volume_desc(volume)}')

    kuaixun = _fetch_kuaixun(report_date) if use_kuaixun else []

    prompt = _build_prompt(report, index_tech, personal_note, kuaixun, volume)
    print(f'[提示] Prompt 约 {len(prompt)} 字符')

    text = _generate(prompt, provider, model, stream)
    text = _postprocess(text, report, index_tech, provider, model, personal_note, volume)
    return {'date': report_date, 'text': text}


def main():
    ap = argparse.ArgumentParser(description='每日复盘文章生成器（本地 qwen2.5 / 任意 OpenAI 兼容 LLM）')
    ap.add_argument('--date', default=None, help='交易日 YYYYMMDD（默认最近有效交易日）')
    ap.add_argument('--provider', default='ollama', help='LLM provider: ollama/deepseek/claude（默认 ollama）')
    ap.add_argument('--model', default=None, help='覆盖模型名（默认用 provider 配置）')
    ap.add_argument('--personal-note', default='', help='注入个人持仓点评段（可选）')
    ap.add_argument('--refresh', action='store_true', help='忽略缓存，重新拉取数据（需东财可达）')
    ap.add_argument('--no-stream', action='store_true', help='不流式输出，一次性返回')
    ap.add_argument('--no-news', action='store_true', help='不注入当日重要资讯（默认注入）')
    args = ap.parse_args()

    try:
        result = generate_article(args.date, args.personal_note, args.provider,
                                  args.model, args.refresh, not args.no_stream,
                                  use_kuaixun=not args.no_news)
    except ValueError as e:
        print(f'[错误] {e}')
        sys.exit(1)

    path = _write_output(result['text'], result['date'])
    print(f'\n[完成] 已写入 {path}')


if __name__ == '__main__':
    main()
