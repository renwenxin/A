"""热门事件日历 — 基于研报梳理的2026年事件驱动投资日历

数据来源：研报汇总（逻辑哥复盘笔记）+ 公开事件日历
覆盖范围：近14天已发生事件 + 未来30天即将发生事件
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional


class Event:
    """市场事件"""
    def __init__(self, name: str, start: str, end: str = "",
                 sectors: List[str] = None, importance: int = 3,
                 description: str = "", category: str = "事件驱动",
                 status_override: str = ""):
        """
        Args:
            name: 事件名称
            start: 开始日期 "YYYY-MM-DD"
            end: 结束日期（可选）
            sectors: 受影响板块
            importance: 重要程度 1-5（5最高）
            description: 事件描述/投资逻辑
            category: 类别（政策/科技/消费/周期/地缘）
            status_override: 手动覆盖状态（'ongoing'用于持续性主题）
        """
        self.name = name
        self.start = start
        self.end = end or start
        self.sectors = sectors or []
        self.importance = importance
        self.description = description
        self.category = category
        self.status_override = status_override


# ======================================================================
# 2026年6月-7月核心事件日历（基于研报梳理）
# ======================================================================

EVENTS_2026 = [
    # ---- 已发生 / 持续中 ----
    Event(
        name="英伟达GTC 2026大会",
        start="2026-06-01", end="2026-06-04",
        sectors=["AI算力", "半导体", "光模块", "液冷散热", "HBM"],
        importance=5,
        description="Rubin平台发布，1.6T光模块升级周期启动。关注铜缆/AEC/光互联、CPO、液冷等上游材料需求爆发。",
        category="科技",
    ),
    Event(
        name="宇树科技IPO",
        start="2026-06-01", end="2026-06-03",
        sectors=["人形机器人", "伺服电机", "减速器", "传感器"],
        importance=4,
        description="国内人形机器人龙头上市，市场对人形机器人产业链关注度飙升。关注减速器/伺服/传感器等核心零部件标的。",
        category="科技",
    ),
    Event(
        name="苹果WWDC 2026",
        start="2026-06-08", end="2026-06-12",
        sectors=["苹果产业链", "折叠屏", "消费电子", "AI终端", "MR/XR"],
        importance=5,
        description="iOS 20 + AI全面整合。折叠屏软件生态铺垫，为秋季折叠屏硬件发布做准备。关注立讯/歌尔/蓝思等果链核心标的。",
        category="科技",
    ),
    Event(
        name="SpaceX星舰上市",
        start="2026-06-12", end="2026-06-14",
        sectors=["商业航天", "卫星互联网", "火箭发动机", "航天材料"],
        importance=5,
        description="史上最大商业航天IPO，全球航天板块估值锚。关注A股卫星/火箭产业链映射标的。",
        category="科技",
    ),
    Event(
        name="华为HDC 2026开发者大会",
        start="2026-06-12", end="2026-06-14",
        sectors=["国产算力", "鸿蒙生态", "昇腾AI", "信创"],
        importance=5,
        description="鸿蒙NEXT + 昇腾超节点 + 国产GPU生态。DeepSeek V4国产算力适配是核心看点。关注鲲鹏/昇腾产业链。",
        category="科技",
    ),
    Event(
        name="特斯拉FSD V14入华测试",
        start="2026-06-15", end="2026-06-30",
        sectors=["智能驾驶", "特斯拉产业链", "激光雷达", "车载摄像头"],
        importance=4,
        description="FSD V14端到端模型入华路测，'马年炒马'。关注特斯拉供应链+智驾方案商。",
        category="科技",
    ),
    Event(
        name="中报业绩预告密集披露期",
        start="2026-06-15", end="2026-07-15",
        sectors=["全市场", "业绩超预期"],
        importance=4,
        description="中报预告窗口开启，业绩超预期标的将获资金追捧，不达预期面临调整。重点关注AI算力链业绩兑现情况。",
        category="周期",
        status_override="ongoing",
    ),

    # ---- 即将发生 ----
    Event(
        name="链博会（中国国际供应链促进博览会）",
        start="2026-06-22", end="2026-06-26",
        sectors=["新质生产力", "供应链安全", "先进制造", "绿色低碳"],
        importance=4,
        description="国家级供应链主题展会，'十五五'新质生产力方向集中展示。关注'六张网'新基建+供应链安全主线。",
        category="政策",
    ),
    Event(
        name="夏季达沃斯论坛",
        start="2026-06-25", end="2026-06-27",
        sectors=["科技创新", "能源转型", "数字经济"],
        importance=3,
        description="全球经济与科技趋势展望。夏季达沃斯期间科技创新板块通常有情绪提振。",
        category="政策",
    ),
    Event(
        name="二十届四中全会（预期）",
        start="2026-07-01", end="2026-07-05",
        sectors=["政策主线", "国企改革", "新质生产力"],
        importance=5,
        description="重要政策窗口，关注'十五五'规划方向定调、科技体制改革、国企改革深化等政策信号。",
        category="政策",
    ),
    Event(
        name="英伟达Rubin平台供应链备货启动",
        start="2026-07-01", end="2026-07-31",
        sectors=["PCB/CCL", "铜缆/AEC", "光模块", "液冷"],
        importance=4,
        description="Rubin平台Q3量产出货，上游材料（Q布/石英布）供需缺口驱动量价齐升。覆铜板/PCB/铜连接进入旺季备货。",
        category="科技",
        status_override="ongoing",
    ),
    Event(
        name="长鑫存储IPO（预期）",
        start="2026-07-05", end="2026-07-15",
        sectors=["存储芯片", "半导体设备", "半导体材料"],
        importance=5,
        description="国产DRAM龙头IPO，带动存储产业链估值重估。关注设备/材料/封测等国产替代标的。",
        category="科技",
    ),

    # ---- 持续性主题 ----
    Event(
        name="半导体涨价周期",
        start="2026-05-15", end="2026-08-31",
        sectors=["模拟芯片", "功率半导体", "存储芯片", "晶圆代工"],
        importance=5,
        description="德州仪器/英飞凌涨价传导，AI驱动供需错配。模拟芯片/功率半导体/MCU全线上涨，板块β行情。",
        category="周期",
        status_override="ongoing",
    ),
    Event(
        name="厄尔尼诺夏季高温主题",
        start="2026-06-01", end="2026-08-31",
        sectors=["空调/白电", "电力/火电", "制冷剂", "啤酒/饮料"],
        importance=4,
        description="预计今夏用电负荷同比+9000万千瓦，高温驱动空调/制冷需求爆发。火电价值重估+家电旺季双重逻辑。",
        category="消费",
        status_override="ongoing",
    ),
    Event(
        name="电力超级周期",
        start="2026-05-01", end="2026-09-30",
        sectors=["火电", "新能源运营", "电力设备", "特高压"],
        importance=4,
        description="AI算力中心耗电激增+夏季用电高峰+电力市场化改革。火电从'弃儿'变'宠儿'，新能源运营商价值重估。",
        category="周期",
        status_override="ongoing",
    ),
    Event(
        name="中美关系缓和窗口",
        start="2026-05-20", end="2026-07-31",
        sectors=["出口链", "科技", "新能源", "CXO"],
        importance=4,
        description="关税减免预期+科技合作重启。利好出口链、CXO、新能源等受关税影响大的方向。",
        category="地缘",
        status_override="ongoing",
    ),
    Event(
        name="AI智能体产业链爆发",
        start="2026-05-25", end="2026-08-31",
        sectors=["AI Agent", "企业软件", "RPA", "AI终端"],
        importance=4,
        description="DeepSeek V4 + 各大厂Agent平台竞相发布。AI应用从'聊天'走向'执行'，企业服务/AI终端迎来商业化拐点。",
        category="科技",
        status_override="ongoing",
    ),
]


# ======================================================================
# 事件关联标的详情（区分短线情绪标的 vs 机构标的）
# 基于研报汇总提取，仅供参考，不构成投资建议
# ======================================================================

EVENT_STOCK_DETAIL = {
    "英伟达GTC 2026大会": {
        "sentiment": [
            {"code": "300502", "name": "新易盛", "reason": "1.6T光模块核心供应商，GTC催化最直接"},
            {"code": "300308", "name": "中际旭创", "reason": "800G光模块龙头，Rubin平台受益"},
            {"code": "002916", "name": "深南电路", "reason": "AI服务器PCB，弹性标的"},
        ],
        "institutional": [
            {"code": "002475", "name": "立讯精密", "reason": "AI算力连接方案，机构重仓"},
            {"code": "603228", "name": "景旺电子", "reason": "PCB龙头，业绩确定性高"},
            {"code": "688012", "name": "中微公司", "reason": "半导体设备，AI资本开支受益"},
        ],
    },
    "宇树科技IPO": {
        "sentiment": [
            {"code": "603728", "name": "鸣志电器", "reason": "空心杯电机龙头，机器人灵巧手核心"},
            {"code": "002747", "name": "埃斯顿", "reason": "工业机器人本体+伺服，弹性大"},
            {"code": "300124", "name": "汇川技术", "reason": "伺服系统龙头，机器人关节电机"},
        ],
        "institutional": [
            {"code": "688017", "name": "绿的谐波", "reason": "谐波减速器龙头，机器人核心零部件"},
            {"code": "300660", "name": "江苏雷利", "reason": "微型传动系统，扫地机→机器人升级"},
        ],
    },
    "苹果WWDC 2026": {
        "sentiment": [
            {"code": "002475", "name": "立讯精密", "reason": "果链总成龙头，AI终端升级受益"},
            {"code": "002241", "name": "歌尔股份", "reason": "声学+VR代工，WWDC概念弹性标的"},
            {"code": "300433", "name": "蓝思科技", "reason": "玻璃盖板+折叠屏UTG，新品周期"},
        ],
        "institutional": [
            {"code": "002456", "name": "欧菲光", "reason": "光学模组，AI手机摄像头升级"},
            {"code": "002138", "name": "顺络电子", "reason": "电感龙头，消费电子被动元件"},
        ],
    },
    "SpaceX星舰上市": {
        "sentiment": [
            {"code": "600118", "name": "中国卫星", "reason": "卫星制造龙头，航天情绪龙头"},
            {"code": "300342", "name": "天银机电", "reason": "星敏感器，卫星核心部件"},
        ],
        "institutional": [
            {"code": "688568", "name": "中科星图", "reason": "数字地球，卫星遥感数据平台"},
            {"code": "300045", "name": "华力创通", "reason": "卫星导航仿真测试，军工电子"},
        ],
    },
    "华为HDC 2026开发者大会": {
        "sentiment": [
            {"code": "688041", "name": "海光信息", "reason": "国产GPU龙头，昇腾生态映射"},
            {"code": "300474", "name": "景嘉微", "reason": "国产GPU，信创情绪标的"},
        ],
        "institutional": [
            {"code": "002261", "name": "拓维信息", "reason": "华为鲲鹏/昇腾合作伙伴，信创整机"},
            {"code": "000034", "name": "神州数码", "reason": "华为企业业务核心代理商"},
        ],
    },
    "特斯拉FSD V14入华测试": {
        "sentiment": [
            {"code": "002920", "name": "德赛西威", "reason": "智能座舱+域控制器，智驾情绪龙头"},
            {"code": "688326", "name": "经纬恒润", "reason": "汽车电子，智能驾驶方案商"},
        ],
        "institutional": [
            {"code": "300750", "name": "宁德时代", "reason": "动力电池龙头，特斯拉核心供应商"},
            {"code": "300496", "name": "中科创达", "reason": "智能汽车操作系统，机构配置标的"},
        ],
    },
    "链博会": {
        "sentiment": [
            {"code": "600760", "name": "中航沈飞", "reason": "军工龙头，供应链安全主题"},
            {"code": "002415", "name": "海康威视", "reason": "AI视觉，新质生产力代表"},
        ],
        "institutional": [
            {"code": "601766", "name": "中国中车", "reason": "轨交龙头，先进制造代表"},
            {"code": "000768", "name": "中航西飞", "reason": "大飞机产业链，十五五核心"},
        ],
    },
    "长鑫存储IPO": {
        "sentiment": [
            {"code": "002371", "name": "北方华创", "reason": "半导体设备龙头，存储扩产最受益"},
            {"code": "300604", "name": "长川科技", "reason": "测试设备，存储芯片封测弹性标的"},
        ],
        "institutional": [
            {"code": "688012", "name": "中微公司", "reason": "刻蚀设备，存储厂核心供应商"},
            {"code": "688981", "name": "中芯国际", "reason": "晶圆代工龙头，国产存储产业链枢纽"},
        ],
    },
    "半导体涨价周期": {
        "sentiment": [
            {"code": "300623", "name": "捷捷微电", "reason": "功率半导体，涨价弹性最大"},
            {"code": "600460", "name": "士兰微", "reason": "IDM功率龙头，产能利用率提升"},
            {"code": "603501", "name": "韦尔股份", "reason": "CIS芯片龙头，涨价+量增双击"},
        ],
        "institutional": [
            {"code": "688396", "name": "华润微", "reason": "功率半导体IDM，国企背景稳定性好"},
            {"code": "002049", "name": "紫光国微", "reason": "特种芯片，FPGA国产替代龙头"},
        ],
    },
    "厄尔尼诺夏季高温主题": {
        "sentiment": [
            {"code": "000651", "name": "格力电器", "reason": "空调龙头，高温催化出货量增长"},
            {"code": "600690", "name": "海尔智家", "reason": "白电龙头，海外+国内双驱动"},
        ],
        "institutional": [
            {"code": "000333", "name": "美的集团", "reason": "家电综合龙头，机构底仓标的"},
            {"code": "600900", "name": "长江电力", "reason": "水电龙头，夏季用电高峰受益"},
        ],
    },
    "电力超级周期": {
        "sentiment": [
            {"code": "600011", "name": "华能国际", "reason": "火电龙头，AI算力用电催化"},
            {"code": "600023", "name": "浙能电力", "reason": "区域火电龙头，弹性大"},
        ],
        "institutional": [
            {"code": "601985", "name": "中国核电", "reason": "核电双寡头之一，稳定成长"},
            {"code": "600900", "name": "长江电力", "reason": "水电龙头，股息率高适合机构配置"},
        ],
    },
    "中美关系缓和窗口": {
        "sentiment": [
            {"code": "300760", "name": "迈瑞医疗", "reason": "医疗器械龙头，出口链代表"},
            {"code": "603259", "name": "药明康德", "reason": "CXO龙头，中美缓和最受益"},
        ],
        "institutional": [
            {"code": "002475", "name": "立讯精密", "reason": "果链龙头，关税减免受益"},
            {"code": "300274", "name": "阳光电源", "reason": "逆变器龙头，新能源出海代表"},
        ],
    },
    "AI智能体产业链爆发": {
        "sentiment": [
            {"code": "688111", "name": "金山办公", "reason": "WPS AI，Agent功能落地直接受益"},
            {"code": "300033", "name": "同花顺", "reason": "金融AI Agent，弹性标的"},
        ],
        "institutional": [
            {"code": "002230", "name": "科大讯飞", "reason": "AI平台龙头，星火大模型Agent"},
            {"code": "688088", "name": "虹软科技", "reason": "视觉AI，AI终端核心标的"},
        ],
    },
    "二十届四中全会": {
        "sentiment": [
            {"code": "600760", "name": "中航沈飞", "reason": "军工龙头，政策窗口情绪标的"},
            {"code": "002415", "name": "海康威视", "reason": "科技国企改革代表"},
        ],
        "institutional": [
            {"code": "601668", "name": "中国建筑", "reason": "基建龙头，政策逆周期调节受益"},
            {"code": "600036", "name": "招商银行", "reason": "银行龙头，政策宽松受益"},
        ],
    },
    "英伟达Rubin平台备货": {
        "sentiment": [
            {"code": "002916", "name": "深南电路", "reason": "AI服务器PCB，直接受益Rubin备货"},
            {"code": "002384", "name": "东山精密", "reason": "PCB+软板，多层板需求爆发"},
        ],
        "institutional": [
            {"code": "603228", "name": "景旺电子", "reason": "PCB/CCL龙头，机构配置标的"},
            {"code": "002475", "name": "立讯精密", "reason": "铜缆/AEC连接方案，Rubin平台新需求"},
        ],
    },
    "中报业绩预告密集披露期": {
        "sentiment": [
            {"code": "300502", "name": "新易盛", "reason": "光模块高增长确定，业绩催化"},
            {"code": "300308", "name": "中际旭创", "reason": "800G放量验证，中报超预期概率大"},
        ],
        "institutional": [
            {"code": "600519", "name": "贵州茅台", "reason": "业绩稳定性标杆，机构防御配置"},
            {"code": "300750", "name": "宁德时代", "reason": "动力电池龙头，中报业绩锚"},
        ],
    },
    "夏季达沃斯论坛": {
        "sentiment": [],
        "institutional": [
            {"code": "300274", "name": "阳光电源", "reason": "新能源出海代表，论坛主题相关"},
            {"code": "002230", "name": "科大讯飞", "reason": "AI平台龙头，数字经济主题"},
        ],
    },
}


def match_event_stocks(event_name: str) -> dict:
    """根据事件名称匹配关联标的（模糊匹配）"""
    for key, stocks in EVENT_STOCK_DETAIL.items():
        # 关键词匹配
        if key[:4] in event_name or event_name[:4] in key:
            return stocks
        # 更短的关键词匹配
        short_key = key[:3]
        if short_key in event_name or (len(event_name) >= 3 and event_name[:3] in key):
            return stocks
    return {"sentiment": [], "institutional": []}


def search_events(keyword: str) -> list:
    """搜索事件（按关键词）"""
    results = []
    kw = keyword.lower()
    for evt in EVENTS_2026:
        # 搜索事件名称、板块、描述、类别
        search_text = f"{evt.name} {' '.join(evt.sectors)} {evt.description} {evt.category}"
        if kw in search_text.lower():
            stocks = match_event_stocks(evt.name)
            results.append({
                'name': evt.name,
                'start': evt.start,
                'end': evt.end,
                'sectors': evt.sectors,
                'importance': evt.importance,
                'description': evt.description,
                'category': evt.category,
                'sentiment_stocks': stocks.get('sentiment', []),
                'institutional_stocks': stocks.get('institutional', []),
            })
    # 按重要程度排序
    results.sort(key=lambda x: x['importance'], reverse=True)
    return results


# ======================================================================
# 事件汇总生成
# ======================================================================

def _parse_date(date_str: str) -> date:
    """解析日期字符串 YYYY-MM-DD"""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return date.today()


def get_events_for_period(ref_date: date = None) -> Dict:
    """生成事件汇总数据

    Args:
        ref_date: 参考日期（默认今天）

    Returns:
        {
            'recent_events': [...],   # 近14天已发生/持续中的事件
            'upcoming_events': [...], # 未来30天即将发生的事件
            'ongoing_themes': [...],  # 持续性的主题投资方向
            'generated_date': str,
        }
    """
    if ref_date is None:
        ref_date = date.today()

    recent_cutoff = ref_date - timedelta(days=14)
    upcoming_cutoff = ref_date + timedelta(days=30)

    recent_events = []
    upcoming_events = []
    ongoing_themes = []

    for evt in EVENTS_2026:
        evt_start = _parse_date(evt.start)
        evt_end = _parse_date(evt.end)

        # 持续性主题
        if evt.status_override == "ongoing":
            # 当前日期在主题区间内
            if evt_start <= ref_date <= evt_end:
                ongoing_themes.append(_event_to_dict(evt, ref_date))
            elif ref_date > evt_end:
                # 已过期的持续性主题不显示
                pass
            else:
                # 尚未开始的持续性主题归入 upcoming
                if evt_start <= upcoming_cutoff:
                    upcoming_events.append(_event_to_dict(evt, ref_date))
            continue

        # 已发生/进行中的事件（结束日在近14天内 或 正在进行中）
        if evt_start <= ref_date <= evt_end:
            # 正在进行中
            recent_events.append(_event_to_dict(evt, ref_date))
        elif recent_cutoff <= evt_end <= ref_date:
            # 最近结束的
            recent_events.append(_event_to_dict(evt, ref_date))
        elif evt_start > ref_date and evt_start <= upcoming_cutoff:
            # 即将发生
            upcoming_events.append(_event_to_dict(evt, ref_date))

    # 排序：最近的在前 / 即将的按日期近到远
    recent_events.sort(key=lambda x: x['end_date'], reverse=True)
    upcoming_events.sort(key=lambda x: x['start_date'])
    ongoing_themes.sort(key=lambda x: x['importance'], reverse=True)

    return {
        'recent_events': recent_events,
        'upcoming_events': upcoming_events,
        'ongoing_themes': ongoing_themes,
        'generated_date': ref_date.strftime('%Y-%m-%d'),
        'has_data': bool(recent_events or upcoming_events or ongoing_themes),
    }


def _event_to_dict(evt: Event, ref_date: date) -> Dict:
    """将Event对象转为模板可用的字典"""
    evt_start = _parse_date(evt.start)
    evt_end = _parse_date(evt.end)

    # 判断状态
    if evt.status_override == "ongoing":
        status = "进行中"
        status_class = "ongoing"
    elif ref_date > evt_end:
        days_ago = (ref_date - evt_end).days
        status = f"{days_ago}天前结束" if days_ago > 0 else "今天结束"
        status_class = "past"
    elif evt_start <= ref_date <= evt_end:
        days_left = (evt_end - ref_date).days
        status = f"进行中（剩余{days_left}天）" if days_left > 0 else "今天结束"
        status_class = "active"
    else:
        days_until = (evt_start - ref_date).days
        status = f"{days_until}天后开始" if days_until > 0 else "今天开始"
        status_class = "upcoming"

    # 重要性星级
    stars = '⭐' * evt.importance

    # 匹配关联标的
    stock_data = match_event_stocks(evt.name)

    return {
        'name': evt.name,
        'start_date': evt.start,
        'end_date': evt.end,
        'sectors': evt.sectors,
        'importance': evt.importance,
        'importance_stars': stars,
        'description': evt.description,
        'category': evt.category,
        'status': status,
        'status_class': status_class,
        'sentiment_stocks': stock_data.get('sentiment', []),
        'institutional_stocks': stock_data.get('institutional', []),
    }


def get_event_summary_text(ref_date: date = None) -> str:
    """生成事件驱动的投资策略摘要文本（可用于LLM prompt）"""
    data = get_events_for_period(ref_date)

    lines = ["## 事件驱动投资日历\n"]

    if data['ongoing_themes']:
        lines.append("### 🔥 当前持续性主题")
        for t in data['ongoing_themes']:
            lines.append(f"- **{t['name']}** {t['importance_stars']}")
            lines.append(f"  板块：{'、'.join(t['sectors'][:4])}")
            lines.append(f"  {t['description']}")
        lines.append("")

    if data['recent_events']:
        lines.append("### 📅 近期已发生/进行中事件")
        for e in data['recent_events']:
            lines.append(f"- **{e['name']}** [{e['status']}] {e['importance_stars']}")
            lines.append(f"  板块：{'、'.join(e['sectors'][:4])}")
        lines.append("")

    if data['upcoming_events']:
        lines.append("### 🔮 后续关注事件")
        for e in data['upcoming_events']:
            lines.append(f"- **{e['name']}** [{e['status']}] {e['importance_stars']}")
            lines.append(f"  板块：{'、'.join(e['sectors'][:4])}")
            if e['description']:
                lines.append(f"  {e['description']}")
        lines.append("")

    return '\n'.join(lines)
