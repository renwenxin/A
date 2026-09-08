# 三策略观察实验室（strategy_lab）设计（2026-09-08）

> 目标：把 `/strategy_bench`（原历史回测对比台）改造成"启动突破V3 · 一进二 · 超跌BOLL V9"
> 三个策略的**每日观察实验室**——每天记录三池标的 + 次日回填，跑一个月后能按
> 市场环境 / 板块强弱 / 竞价·量比·封成比逐层拆出"策略到底赚什么钱"。

---

## 0. 已确认决策（与用户逐条确认）

1. `/strategy_bench` 主页 → 三策略观察实验室；**原历史回测搬到 `/strategy_bench/backtest` 二级页保留**（路由/模板/API 不动，只挪入口）。
2. 一进二池 = 复盘报告 `sentiment.picks`（`_select_top_picks` 口径，评分≥8，**Top8 → Top10**，复盘页显示同步变 10 只）。
3. 超跌池 = BOLL V9 公式**逐行移植成 Python**（仿 `main_formula` 对齐先例），每日全量收录不过滤 TopN；`WINNER(C)` 用筹码分布近似（现有 `chip.py` 成本分布），其余逐行等价。
4. 时间口径：**T 日快照 + T+1 收盘收益**；竞价涨幅记 **T+1 开盘竞价**（次日回填）。开高低收/成交额/量比/封成比/板块/市场环境全部为 T 日。
5. 市场环境记录 **strategy_regime 6 档原文**，分析时归并 强/中/弱（映射做成 stats.py 常量）。
6. 触发 = 实验室页「📝 记录今日三池」按钮一键记录；次日页面加载/记录时自动回填；幂等。
7. 实现路径：**新建独立 `strategy_lab` 模块**（方案 A），不动 prediction_ledger / strategy_bench 既有语义。

---

## 1. 架构与数据流

```
ashare_review/strategy_lab/
├── store.py        # pool_rows 表 + CRUD（新 DB: data/strategy_lab.db）
├── service.py      # record_today / backfill_next / 编排三个 collector
├── collectors/
│   ├── __init__.py # 注册表: {breakout_v3, one2, boll_v9}
│   ├── breakout_v3.py  # 接现有 BreakoutPredictor.predict(top_n=10)
│   ├── one2.py         # 接复盘报告 sentiment.picks（评分≥8 → Top10）
│   └── boll_v9.py      # 新移植 BOLL V9 公式 → 每日全市场扫描
└── stats.py        # 分层胜率/均值/重叠统计
```

数据流（T 日为例）：

1. T 日收盘后 → 用户点「📝 记录今日三池」→ `record_today(T)`。
2. 三个 collector 各跑一遍：突破预测 Top10 / 一进二 Top10 / BOLL V9 全量。
3. 每条写一行快照（T 日字段齐全，`gap_pct` 留空），自动带 T 日 6 档行情分类。
4. T+1 早晨 9:25 后（页面加载或再次点记录时顺带）→ `backfill_next(T)`：回填 **T+1 开盘竞价涨幅**。
5. T+1 收盘后 → 再次调用回填 **次日收益** `(T+1收−T收)/T收×100`。
6. 统计页随时可看：市场环境下钻 / 板块强弱 / 竞价·量比·封成比分档 / 三池重叠。

---

## 2. 数据模型

新 DB：`ashare_review/data/strategy_lab.db`

```sql
CREATE TABLE pool_rows (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date    TEXT NOT NULL,        -- T 日 YYYY-MM-DD
  strategy      TEXT NOT NULL,        -- breakout_v3 | one2 | boll_v9
  rank          INTEGER,              -- 1..10；超跌全量按 YBT 给展示位
  code          TEXT NOT NULL,
  name          TEXT NOT NULL DEFAULT '',
  sector        TEXT DEFAULT '',      -- 所属行业板块（代码→行业映射）
  regime        TEXT DEFAULT '',      -- T 日 6 档行情分类原文
  amount_yi     REAL,                 -- T 日成交额(亿)
  vol_ratio     REAL,                 -- 量比：当日量 / 前5日均量
  seal_ratio    REAL,                 -- 封成比（涨停股才有，非涨停 NULL）
  gap_pct       REAL,                 -- T+1 开盘竞价涨幅 %（次日回填）
  open_px       REAL,  high_px REAL,
  low_px        REAL,  close_px REAL, -- T 日 OHLC
  next_return   REAL,                 -- (T+1收−T收)/T收×100
  extra         TEXT DEFAULT '',      -- JSON：score/reasons/压力位等策略专属
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(trade_date, strategy, code)
);
```

- 幂等：重复点「记录今日」不产生重复行（UNIQUE + INSERT OR IGNORE / 存在则更新）。
- `seal_ratio`：仅涨停类样本有值；超跌池此列天然 NULL，统计按"非涨停"剔除该维，不填 0。
- 量比口径：当日成交量 ÷ 前 5 日均量（日线级；若需通达信竞价量比口径另行标注）。

---

## 3. 三个 Collector

统一接口：`collect(trade_date) -> list[dict]`（dict 字段对齐 pool_rows，策略专属字段进 `extra`）。

### 3.1 breakout_v3（启动突破 · 明日突破预测 Top10）
- 复用 `tools/breakout_predict.py`：`verify_pending()` → `predict(trade_date, top_n=10, persist=True)` → 10 条 `{code,name,score,close,pressure,dist_pct,features,reasons}`。
- 映射：rank=1..10；sector 用代码→行业映射补；close_px 用 predict.close；其余 OHLC/成交额/量比走统一取数；`extra={score,pressure,dist_pct,reasons}`。
- 前置：watch 蓄势池已扫描（沿用现有"运行扫描"）。空池时提示先跑扫描，不硬记。

### 3.2 one2（复盘一进二精选 Top10）
- 与复盘页同源 `_select_top_picks`（report/daily.py L1188）：首板、非一字、评分≥8 → 排序取 Top10。
- 依赖当日复盘报告已生成；未生成则提示"先到复盘分析页生成当日报告"，不自动触发全量复盘。
- `extra={score,reasons,seal_ratio,seal_strength,price,market_cap,limit_up_time,theme_stage,theme_label,sector_name,sector_zt_count,board_type}`。
- 注：复盘页显示随 Top8→Top10 同步变为 10 只。

### 3.3 boll_v9（超跌 BOLL V9 全量）
- 新建移植层 `analysis/boll_v9.py`：通达信原语实现 + 公式逐行等价（参照根目录 `BOLL_V9_超跌动态买点选股公式.txt`）。
  - 复用：`calc_swl_sws`（SWL/SWS）、float_share（CAPITAL 手数）、TdxReader。
  - 需新增：BOLL/UB/LB、RSI(SMA 法)、MFI(14)、XYS 动量、吸筹 `/618` 系、量能复合（剑/箭/炮/出刀）、UPNDAY、CROSS、COUNT、`WINNER(C)`（唯一近似项，用 `analysis/chip.py` 成本分布近似，单测标注）。
  - 输出 `XG`（N 偏移默认 N=0）。
- 扫描：T 日收盘后全市场（复用 tail_adapter 全市场循环 + ST 剔除范式），出信号按 `YBT` 排序全量收录。
- `extra={score=YBT, dist_to_lb, rsi, mfi, reasons}`。

---

## 4. 次日回填

`backfill_next(T)`（T+1 当天任意时候调用；幂等，只补空行）：
1. **竞价涨幅 gap_pct**：akshare 竞价接口取 T+1 日 `open_change_pct`（9:25 后可得；取不到留空不硬填）。
2. **次日收益 next_return**：等 T+1 日线落库后，用 TDX 定位 T+1 收盘 → `(T+1收−T收)/T收×100`。交易日历用现有 `TradingCalendar`。

---

## 5. 市场环境（regime）

- 记录时从 `strategy_regime/market_state.csv` 取 T 日 `regime`（6 档原文存库）。
- 分析归并（stats.py 常量）：
  - 强 = 强势趋势
  - 中 = 题材轮动 / 震荡观望 / 冰点超跌
  - 弱 = 弱市回调 / 退潮下跌

---

## 6. 统计分层（stats.py）

全部输出：样本数 + 胜率(次日收益>0) + 平均次日收益%；样本数 <5 标注 "(n<5 样本不足)"。

1. **三池总览**：每策略 {样本数, 胜率, 平均收益, 盈亏比近似}。
2. **市场环境维度**（主下钻）：6 档原文 → 归并 强/中/弱；每策略 × 每档 胜率/均值。
3. **板块强弱二次拆**：板块当日涨停家数≥3 或在当日热点前列 = 强势板块，否则普通；每策略 × 强弱板块胜率。
4. **入场条件分档**（策略内自由组合下钻）：
   - 竞价涨幅：高开≥3% / 高开0~3% / 低开
   - 量比：≥2 / 1~2 / <1
   - 封成比：≥0.5 / <0.5（仅涨停类样本；超跌池此维为空自动跳过）
   - 组合示例：强势市场 + 高竞价 + 放量
5. **三池重叠**：按 `(trade_date, code)` 找 ≥2 池同现标的；对重叠行打标签看"只被A选 / 只被B选 / AB都选"三组胜率差异（三池同日同股快照字段相同，重叠分析回答的是策略池优先级的整体问题）。
6. **时间趋势**：按周/月累计样本与胜率。

---

## 7. 页面与 API

- `/strategy_bench`（主页 → 实验室）：
  - 顶栏：标题 + 最近记录日期 + 「📝 记录今日三池」按钮 + 状态行（今日待记录 / 待回填数）。
  - 今日三池：三列并排（Top10 / Top10 / 超跌 N 只），行含 rank/代码/名称/板块/收盘/评分/当日涨跌/量比；竞价涨幅与次日收益列标"待回填"。
  - 历史明细：日期区间/策略/环境筛选 + CSV 导出。
  - 统计 Tab：第 6 节 1-6 分层视图（市场环境主表 + 点击下钻）。
  - 重叠 Tab：重叠明细 + 优先级对比。
- `/strategy_bench/backtest`：原回测台原样保留（模板 + API 不动）。
- 新 API：
  - `POST /api/strategy_lab/record`（记录今日三池）
  - `GET  /api/strategy_lab/rows`（明细，支持 date/strategy/regime 筛选）
  - `GET  /api/strategy_lab/stats`（分层统计）
  - `GET  /api/strategy_lab/overlap`（重叠分析）
  - `GET  /api/strategy_lab/status`（待记录/待回填状态）

---

## 8. 测试

- `tests/test_strategy_lab_store.py`：schema / 幂等 upsert / 回填只补空行
- `tests/test_strategy_lab_collectors.py`：三 collector 字段齐全、排序正确、空池不炸
- `tests/test_strategy_lab_stats.py`：分层/归并/重叠/样本不足标注（构造小样本）
- `tests/test_boll_v9.py`：BOLL V9 逐行对账（已知行情手算 XG），`WINNER` 近似单测
- web 页/API 冒烟（沿用现有 test 风格）

---

## 9. 范围外（本期不做）

- BOLL V9 通达信端公式本身的改动（以存档 txt 为准逐行移植）。
- 把 prediction_ledger / one_two_v2 并入本系统（保持各自独立）。
- 自动定时任务（cron/常驻调度）；本期靠页面按钮 + 页面加载回填。
