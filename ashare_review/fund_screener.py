"""基金挑选板块 → 网页版：按板块/赛道筛出符合养基体系五条件的主动基金，每板块 Top5。

依据（养基体系 + 《2026公募基金一季报》散户指南）：
  1. 择时 > 择基（页面顶部展示择时清单，选基只是第二步）
  2. 选混合型基金（灵活调仓）→ 混合型加分
  3. 选小规模基金（两三千万最佳，但需避开 5000 万清盘线）→ 规模得分，剔除 <0.5亿 / >60亿
  4. 单压赛道的基金才有超额 → 按简称关键词把基金归到对应板块（单赛道语义由板块归属保证）
  5. 看策略不看排名 → 板块内多周期业绩分位 + 稳定性扣分，而非简单追涨幅榜

数据：天天基金排行（业绩）+ 雪球单只规模（只对每板块短名单按只拉取）。
"""
import math
import re
from datetime import date, timedelta

import pandas as pd

from .data.fund_fetcher import (
    get_all_rank,
    get_fund_scale_xq,
    fmt_scale,
    scale_flag,
    size_score,
    _nan_to_none,
)

# 基金公司简称（基金名称开头的公司令牌）。
# 分类前先剥离公司名，避免"农银行业轮动"(农银+行业)、"中银证券内需"(公司名=中银证券)
# 这类公司名含赛道字的基金被误分到板块。按长度降序取最长匹配。
COMPANY_PREFIXES = [
    # 含 银/证/券/险 等易碰撞字样的公司（最先处理，长度优先）
    "农银汇理", "中银证券", "国投瑞银", "民生加银", "浦银安盛", "工银瑞信",
    "上投摩根", "交银施罗德", "汇丰晋信", "国海富兰克林", "光大保德信",
    "申万菱信", "方正富邦", "创金合信", "中信保诚", "华泰柏瑞", "景顺长城",
    "泰达宏利", "国寿安保", "国新国证", "华润元大", "招商证券", "汇添富",
    "金元顺安", "前海开源", "东方红", "农银", "中银", "工银", "上银", "兴银",
    "银华", "银河",
    # 常见基金公司
    "南方", "华夏", "嘉实", "博时", "易方达", "广发", "富国", "华安", "建信",
    "招商", "鹏华", "大成", "长盛", "长信", "万家", "中欧", "融通", "诺安",
    "中邮", "浙商", "金鹰", "新华", "圆信永丰", "长城", "永赢", "平安",
    "太平", "德邦", "中融", "宝盈", "东吴", "东财", "天弘", "国泰", "华商",
    "国联安", "安信", "中加", "华富", "东方", "景顺", "信诚", "摩根",
    "贝莱德", "施罗德", "宏利", "惠升", "淳厚", "信达澳亚", "海富通",
    "国金", "国联", "中航", "兴业",
]
_COMPANY_SORTED = sorted(set(COMPANY_PREFIXES), key=len, reverse=True)


def _strip_company(name: str) -> str:
    """去掉基金简称开头的公司令牌，返回剩下的主题部分。"""
    for p in _COMPANY_SORTED:
        if name.startswith(p):
            return name[len(p):]
    return name

# 板块定义（优先级有序：一只基金命中多个板块时归到最先命中的那个）
SECTORS = [
    {"key": "chip", "name": "半导体/芯片", "kw": ["半导体", "芯片", "集成电路"]},
    {"key": "robot", "name": "机器人/智能制造", "kw": ["机器人", "智能制造"]},
    {"key": "ai", "name": "AI算力/人工智能", "kw": ["人工智能", "算力", "数字经济", "云计算", "大数据", "AI"]},
    {"key": "pharma", "name": "医药生物", "kw": ["医药", "医疗", "生物", "创新药", "中药", "健康"]},
    {"key": "newenergy", "name": "新能源/电力", "kw": ["新能源", "光伏", "锂", "储能", "电池", "风电", "碳中和", "环保", "电力"]},
    {"key": "defense", "name": "军工/高端制造", "kw": ["军工", "国防", "航天", "航空", "高端装备"]},
    {"key": "gold", "name": "黄金/资源", "kw": ["黄金", "贵金属", "有色金属", "资源"]},
    {"key": "finance", "name": "金融/券商", "kw": ["证券", "金融", "银行", "保险"]},
    {"key": "dividend", "name": "红利/高股息", "kw": ["红利", "股息", "央企", "国企"]},
    {"key": "consume", "name": "大消费", "kw": ["消费", "食品饮料", "白酒", "家电", "品牌", "内需"]},
    {"key": "auto", "name": "汽车", "kw": ["汽车", "智能驾驶"]},
    {"key": "tech", "name": "科技成长", "kw": ["科技", "信息技术", "软件", "数字", "通信", "5G"]},
]

# 份额类别结尾（A/C/E/I 等），用于同基金 A/C 份额去重
_SHARE_SUFFIX = re.compile(r"[A-Z]$")

# 搜索同义词扩展：用户输入常用说法，自动补上基金简称里的标准叫法
# （基金名里多用「人工智能/半导体/食品饮料」，用户常打「AI/芯片/白酒」）
SEARCH_SYNONYMS = {
    "AI": ["人工智能", "算力"],
    "人工智能": ["AI", "算力"],
    "算力": ["人工智能", "AI", "数字经济"],
    "芯片": ["半导体", "集成电路"],
    "半导体": ["芯片", "集成电路"],
    "医药": ["医疗", "生物", "创新药"],
    "医疗": ["医药", "生物"],
    "军工": ["国防"],
    "国防": ["军工"],
    "机器人": ["智能制造"],
    "新能源": ["光伏", "锂", "储能", "电池"],
    "光伏": ["新能源", "锂"],
    "白酒": ["食品饮料"],
    "消费": ["内需", "食品饮料"],
    "红利": ["股息"],
    "券商": ["证券", "金融"],
    "黄金": ["贵金属", "有色金属"],
    "汽车": ["智能驾驶"],
}


class FundScreener:
    """按板块筛选符合养基体系五条件的主动基金，每板块 Top5。"""

    def __init__(self, top_per_sector: int = 5, shortlist: int = 24):
        self.top_per_sector = top_per_sector
        self.shortlist = shortlist

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def _load_pool(self) -> pd.DataFrame:
        pool = get_all_rank()
        return pool

    def _clean_pool(self, df: pd.DataFrame) -> pd.DataFrame:
        """排除指数/ETF/增强等非主动基金，保留有一定历史的基金。"""
        from .data.fund_fetcher import INDEX_NAME_PAT

        name = df["基金简称"].astype(str)
        df = df[~name.str.contains(INDEX_NAME_PAT, regex=True)].copy()
        # 至少近6月或近1年有业绩（排除太新的基金）
        df["近6月"] = pd.to_numeric(df["近6月"], errors="coerce")
        df["近1年"] = pd.to_numeric(df["近1年"], errors="coerce")
        df = df[df["近6月"].notna() | df["近1年"].notna()]
        return df

    # ------------------------------------------------------------------
    # 板块分类
    # ------------------------------------------------------------------
    def _classify(self, df: pd.DataFrame) -> pd.DataFrame:
        name = df["基金简称"].astype(str).map(_strip_company)
        sector = pd.Series([""] * len(df), index=df.index, dtype=object)
        for s in SECTORS:
            pat = re.compile("|".join(re.escape(k) for k in s["kw"]))
            hit = name.str.contains(pat)
            sector = sector.where(~(hit & (sector == "")), s["key"])
        df["sector"] = sector
        return df[df["sector"] != ""].copy()

    # ------------------------------------------------------------------
    # 打分
    # ------------------------------------------------------------------
    def _score_pool(self, df: pd.DataFrame) -> pd.DataFrame:
        """分组内多周期业绩分位（看策略） + 稳定性扣分 + 规模占位。

        sector 列缺失时视为单一分组（搜索路径），分位相对全主动基金池。
        """
        df = df.copy()
        if "sector" not in df.columns:
            df["sector"] = "__all__"
        for col in ("近1年", "近3月", "今年来", "成立来"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 1) 业绩分位：近1年/近3月/今年来 在板块内的百分位加权（缺失→中位50）
        perf = pd.Series(0.0, index=df.index)
        for col, w in (("近1年", 0.5), ("近3月", 0.3), ("今年来", 0.2)):
            pct = df.groupby("sector")[col].rank(pct=True) * 100
            perf += w * pct.fillna(50.0)
        df["perf"] = perf

        # 2) 稳定性：近3月深跌 / 近1年亏损 → 扣分（不追已深度回撤的基金）
        pen = pd.Series(0.0, index=df.index)
        r3m, r1y = df["近3月"], df["近1年"]
        pen += r3m.where(r3m < -10, 0.0) / -10 * 20
        pen += r3m.where((r3m >= -10) & (r3m < -5), 0.0) / -5 * 10
        pen += r1y.where(r1y < -8, 0.0) / -8 * 25
        pen += r1y.where((r1y >= -8) & (r1y < 0), 0.0) / -8 * 10
        df["stab"] = (100 - pen).clip(lower=0.0)

        # 3) 规模（短名单阶段补拉，尚未拉到的一律 neutral）
        df["scale_yi"] = None
        df["size_score"] = 45.0
        return df

    # ------------------------------------------------------------------
    # 规模回填 + 综合分 + 去重 + 每分组 TopN（板块浏览 / 关键词搜索共用）
    # ------------------------------------------------------------------
    def _finalize_picks(self, pool: pd.DataFrame, group_col: str = "sector"):
        """给已算好 perf/stab 的候选池回填规模、算综合分、剔除不达标、份额去重、每分组取 TopN。

        Args:
            pool: 含 perf/stab 的候选池（含 group_col 分组列）
            group_col: 分组列名，如 'sector'
        Returns:
            (picked_df, eligible_df, scale_store)
        """
        short = (
            pool.sort_values("perf", ascending=False)
            .groupby(group_col)
            .head(self.shortlist)
        )
        scale_store = get_fund_scale_xq(short["基金代码"].tolist())

        pool = pool.copy()
        pool["scale_yi"] = pool["基金代码"].map(
            lambda c: _nan_to_none(scale_store.get(c, {}).get("scale_yi"))
        )
        pool["size_score"] = pool["scale_yi"].map(size_score)
        pool["manager"] = pool["基金代码"].map(
            lambda c: scale_store.get(c, {}).get("manager", "")
        )
        pool["ftype"] = pool["基金代码"].map(
            lambda c: scale_store.get(c, {}).get("ftype", "")
        )
        pool["established"] = pool["基金代码"].map(
            lambda c: scale_store.get(c, {}).get("established", "")
        )

        # 综合得分：业绩45% + 规模25% + 稳定性30%；混合型加分（灵活调仓）
        score = 0.45 * pool["perf"] + 0.25 * pool["size_score"] + 0.30 * pool["stab"]
        score = score + pool["kind"].eq("混合型").astype(float) * 8
        pool["score"] = score.round(1)

        # 剔除规模不达标（清盘风险 / 巨无霸）；且只从验证过规模的短名单里选
        eligible = pool[pool["size_score"] > 0]
        eligible = eligible[eligible["基金代码"].isin(short["基金代码"])]

        # 份额类别去重：A/C 同基金只留综合分更高者
        base = eligible["基金简称"].astype(str).str.replace(_SHARE_SUFFIX, "", regex=True)
        eligible = eligible.assign(_base=base)
        eligible = eligible.sort_values("score", ascending=False).drop_duplicates("_base")

        picked = eligible.groupby(group_col).head(self.top_per_sector)
        return picked, eligible, scale_store

    # ------------------------------------------------------------------
    # 板块浏览
    # ------------------------------------------------------------------
    def screen(self) -> dict:
        """按板块分类筛选，每板块 Top5。"""
        pool = self._clean_pool(self._load_pool())
        pool = self._classify(pool)
        pool = self._score_pool(pool)

        picked, eligible, scale_store = self._finalize_picks(pool, "sector")

        sectors_out = []
        for s in SECTORS:
            sub = picked[picked["sector"] == s["key"]]
            funds = [
                self._fund_payload(f"单压[{s['name']}]赛道", r, scale_store)
                for _, r in sub.iterrows()
            ]
            sectors_out.append({
                "key": s["key"],
                "name": s["name"],
                "matched": int((pool["sector"] == s["key"]).sum()),
                "candidates": int((eligible["sector"] == s["key"]).sum()),
                "picked": len(funds),
                "funds": funds,
            })

        return {
            "generated_at": date.today().strftime("%Y-%m-%d"),
            "total_pool": int(len(pool)),
            "eligible_pool": int(len(eligible)),
            "sectors": sectors_out,
            "note": "选基依据：养基体系五条件（混合型/小规模/单压赛道/看策略/风格匹配）。"
                    "规模>60亿巨无霸与<0.5亿清盘风险已剔除；规模缺失按中下分处理。",
        }

    # ------------------------------------------------------------------
    # 关键词搜索（任意输入 → 匹配基金名 → 五条件打分 → Top5）
    # ------------------------------------------------------------------
    def search(self, keyword: str) -> dict:
        """用户输入任意关键词，匹配主动基金简称，按养基体系五条件打分，返回 Top5。

        与板块浏览共用 _finalize_picks 管线；业绩分位相对全主动基金池计算。
        支持同义词扩展（AI→人工智能、芯片→半导体…），并做公司名前缀剥离。
        """
        kw = (keyword or "").strip()
        if not kw:
            return {"keyword": kw, "matched": 0, "funds": [], "note": "请输入搜索关键词"}

        # 同义词扩展：terms = [原词] + 同义词
        terms = [kw] + SEARCH_SYNONYMS.get(kw, [])
        pat = re.compile("|".join(re.escape(t) for t in terms))

        pool = self._score_pool(self._clean_pool(self._load_pool()))
        stripped = pool["基金简称"].astype(str).map(_strip_company)
        matched = pool[stripped.str.contains(pat)].copy()
        if matched.empty:
            return {
                "keyword": kw, "matched": 0, "funds": [],
                "note": f"没有简称含「{kw}」的主动基金（指数/ETF/联接/增强已排除；换个说法试试，如「芯片」「医药」）",
            }

        picked, eligible, scale_store = self._finalize_picks(matched, "sector")
        funds = [
            self._fund_payload(f"匹配「{kw}」", r, scale_store)
            for _, r in picked.iterrows()
        ]
        if not funds:
            note = (f"匹配 {len(matched)} 只含「{kw}」的主动基金，但均未通过养基体系门槛"
                    "（<0.5亿清盘风险 / >60亿巨无霸 / 规模缺失 / 业绩过新被剔除）")
        else:
            note = f"匹配 {len(matched)} 只名称含「{kw}」的主动基金，按养基体系五条件打分，选出 Top{len(funds)}。"
        return {
            "keyword": kw,
            "matched": int(len(matched)),
            "candidates": int(len(eligible)),
            "funds": funds,
            "note": note,
        }

    # ------------------------------------------------------------------
    # 单只基金输出
    # ------------------------------------------------------------------
    def _fund_payload(self, track_label: str, r, scale_store) -> dict:
        """track_label: 赛道显示名，如「单压[半导体/芯片]赛道」或「匹配[资源]」。"""
        code = r["基金代码"]
        name = r["基金简称"]
        scale = _nan_to_none(r["scale_yi"])

        reasons = []
        kind_txt = "混合型" if r["kind"] == "混合型" else "股票型"
        reasons.append(f"{kind_txt}·灵活调仓" if kind_txt == "混合型" else f"{kind_txt}·主动")
        reasons.append(track_label)
        if scale is None:
            reasons.append("规模缺失·未能验证小规模")
        else:
            reasons.append(f"规模{fmt_scale(scale)}·{scale_flag(scale)}")
        if r.get("perf") is not None and not pd.isna(r.get("perf")):
            reasons.append(f"业绩分位{int(r['perf']):d}%")

        # 计算可读业绩
        def _txt(v):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return "—"
            return f"{v:+.1f}%"

        reasons.append(f"近1年{_txt(r['近1年'])}")

        return {
            "code": code,
            "name": name,
            "kind": kind_txt,
            "scale": fmt_scale(scale),
            "scale_flag": scale_flag(scale),
            "scale_yi": round(scale, 2) if scale is not None else None,
            "manager": str(r.get("manager", "")),
            "ftype": str(r.get("ftype", "")),
            "established": str(r.get("established", "")),
            "r1y": _txt(r["近1年"]),
            "r3m": _txt(r["近3月"]),
            "ytd": _txt(r["今年来"]),
            "since": _txt(r["成立来"]),
            "score": float(r["score"]),
            "reasons": reasons,
        }
