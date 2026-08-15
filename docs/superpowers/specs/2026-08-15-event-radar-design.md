# 消息雷达（事件驱动分析）— 设计文档

> 日期：2026-08-15 | 状态：设计已确认 | 关联：ashare_review 复盘选股系统

## 一、目标

构建"事件 → 产业 → 公司 → 资金 → 股价"完整链条的**盘后事件驱动分析工具**（消息雷达）。

**使用场景（已确认）**：盘后做功课——每天收盘后，用户手动勾选当日发酵的事件主题（或新增），系统自动完成"产业链展开 → 公司匹配 → 资金验证"，生成**明日题材候选**清单。

**核心原则**：
1. 事件判断靠人（用户勾选/输入），系统负责繁重的产业链/公司/资金验证
2. LLM（本地 qwen2.5）只做**假设生成**（明日要点文案、节点建议），所有结论用真实数据验证
3. 数据不可用时优雅降级（TDX 本地 + 手工股票池兜底）

## 二、现状与缺口

| 链条环节 | 现状 | 说明 |
|---|---|---|
| ① 发现事件 | ❌ 缺失 | 无新闻聚合（本方案用手动勾选替代） |
| ② 找产业链 | 🟡 部分 | concept_map.json（手工概念库）+ industry_map.json（908 股→行业），无产业链层级 |
| ③ 找公司 | 🟡 部分 | 概念成分股可回答"谁在概念里"，无参与度排序 |
| ④ 查证 | ❌ 缺失 | 无公告/招投标接入（本方案先不做，靠用户判断+概念成分） |
| ⑤ 看资金 | ✅ 已有 | 竞价分析/量能/龙虎榜/板块日度强度/板块分歧筛选 |
| ⑥ 看股票 | ✅ 已有 | 涨停池/突破/竞价全体系 |

**结论**：新增模块补齐 ①②③，⑤⑥ 复用现有数据层。

## 三、设计决策（已与用户确认）

1. **方案 A：独立轻量模块** — 新建 `event_radar/` 包 + 独立页面 + 独立 API，不触碰现有 17 个筛选器
2. **事件输入：手动勾选/输入** — 主题库预置 + 每日勾选事件 + 可新增自定义主题
3. **输出：独立雷达页面** `/event_radar`，支持导出 Markdown
4. **财务数据：默认不做逐个财务查询**（保持速度）；后续可加"深耕模式"（拉主营构成 `stock_zygc_em` 验证营收占比）

## 四、架构

```
Web /event_radar (Flask + Jinja2)
    ↓
event_radar/
  themes.py       # 主题库 CRUD（themes.json）
  events.py       # 事件记录（events.jsonl）
  chain.py        # 产业链展开：节点 → 成分股 + 板块行情
  analyze.py      # 核心分析：资金验证 + 龙头/潜力分层
  report.py       # 结果生成（JSON + Markdown 导出）
  presets.py      # 预置主题库（12 个）
    ↓
数据层（复用）
  AkshareFetcher.get_concept_boards / stock_board_concept_cons_em / get_lhb
  TdxReader.read_daily（量比/涨幅/突破）
  utils.cache（结果缓存）
```

## 五、数据模型

### 主题 Theme（data/event_radar/themes.json）
```json
{
  "themes": [
    {
      "id": "ai_compute",
      "name": "AI算力",
      "chain_nodes": [
        {
          "node": "液冷服务器",
          "concept_name": "液冷服务器",      // 东财概念板块名（实施时校准）
          "manual_codes": ["000977", "603019"]  // 手工股票池（兜底/补充）
        }
      ],
      "last_event": "",
      "updated": "2026-08-15"
    }
  ]
}
```

### 事件 RadarEvent（data/event_radar/events.jsonl）
```json
{"date": "2026-08-15", "theme_id": "ai_compute", "description": "AI数据中心建设加速，多家云厂商上调资本开支", "created_at": "..."}
```

### 分析结果（data/event_radar/results/YYYY-MM-DD.json）
```json
{
  "date": "2026-08-15",
  "events": [
    {
      "theme": "AI算力", "description": "...",
      "chains": [
        {
          "node": "液冷服务器",
          "sector_pct": 4.2, "sector_vol_ratio": 1.8, "zt_count": 3,
          "leaders": [{"code":"...","name":"...","pct":9.9,"vol_ratio":3.2,"is_zt":true}],
          "potentials": [{"code":"...","name":"...","pct":1.2,"vol_ratio":2.1}]
        }
      ],
      "lhb": [{"code":"...","name":"...","net_buy":1234.5,"type":"游资"}],
      "next_day_notes": "..."   // LLM 生成
    }
  ]
}
```

## 六、分析流程（POST /api/radar/analyze）

```
1. 接收 {date, events: [{theme_id, description}]}
2. 对每个事件：
   a. 产业链展开：遍历 theme.chain_nodes
      - concept_name 有值 → 尝试 akshare 拉成分股 + 当日板块行情
      - 失败 → 用 manual_codes 兜底（板块行情标为 N/A）
   b. 资金验证：
      板块层：当日涨幅 / 成交额 vs 5日均量（放大倍数）/ 板块内涨停家数
      个股层（TDX）：当日涨幅 / 量比(vol/MAVOL5) / 是否涨停 / 连板数
      龙虎榜（akshare，可选）：当日板块成分股是否上榜
   c. 分层输出：
      龙头股：板块内涨幅 ≥ 7% 或涨停，按量比/连板排序取前 3
      潜力股：涨幅 0~3% 且 量比 ≥ 1.5（资金刚启动、尚未大涨），取前 5
      明日要点：LLM 基于以上数据生成（qwen2.5，失败则用模板文案）
3. 保存结果 + 返回页面数据
```

**潜力股阈值（默认，可在代码顶部常量调整）**：`涨幅 ≤ 3% 且 量比 ≥ 1.5`（已确认）

## 七、页面 /event_radar

- **左栏 · 主题库**：主题卡片列表（名称 + 产业链节点 chips + 最近事件），勾选今日事件、输入事件描述、新增/编辑主题
- **右栏 · 分析结果**：按事件分组
  - 产业链分支表：节点 | 板块涨幅 | 量能放大 | 涨停家数
  - 龙头股表 / 潜力股表：代码 名称 涨幅 量比 涨停 连板（点击跳 /stock/<code>）
  - 龙虎榜：上榜个股 + 净买 + 席位类型
  - 明日要点：LLM 文案
- **操作**：生成分析 / 导出 Markdown（存 outputs/事件雷达_YYYY-MM-DD.md）

## 八、API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /event_radar | 雷达页面 |
| GET | /api/radar/themes | 主题列表 |
| POST | /api/radar/themes | 新增主题 |
| PUT | /api/radar/themes/<id> | 更新主题 |
| DELETE | /api/radar/themes/<id> | 删除主题 |
| POST | /api/radar/analyze | 生成分析 {date, events[]} |
| GET | /api/radar/results?date= | 读取某日结果 |
| GET | /api/radar/export?date= | 导出 Markdown |

## 九、预置主题库（首批 12 个）

| 主题 | 产业链节点（东财概念名，实施时校准） |
|---|---|
| AI算力 | 液冷服务器 / 光模块 / PCB / 铜缆高速连接 / 电源设备 / MLCC |
| 低空经济 | 低空经济 / eVTOL概念 / 无人机 / 碳纤维 |
| 人形机器人 | 减速器 / 伺服电机 / 丝杠 / 传感器 / 机器人执行器 |
| 固态电池 | 固态电池 / 电解质 / 锂电设备 |
| 创新药 | 创新药 / CXO / 减肥药 / ADC |
| 卫星互联网 | 卫星互联网 / 卫星导航 / 相控阵 |
| 商业航天 | 商业航天 / 火箭发动机 / 卫星应用 |
| 半导体 | 半导体设备 / 半导体材料 / 先进封装 |
| 数据要素 | 数据要素 / 数据确权 |
| 军工 | 军工 / 航空发动机 / 导弹 |
| 电力设备 | 特高压 / 电网设备 / 充电桩 |
| AI眼镜 | AI眼镜 / 消费电子 / 光学光电子 |

> **注意**：节点名必须匹配东财真实概念板块名（`stock_board_concept_name_em()`）。实施时先校准（当前网络不可达），无法匹配的节点自动落到 manual_codes。每个预置主题附带一份初始 manual_codes（由东财成分股快照+人工挑选生成，保证离线可用）。

## 十、复用与新增

**复用**：TdxReader（日线/量比/涨幅）、AkshareFetcher（get_concept_boards / get_lhb / stock_board_concept_cons_em）、utils.cache、web/app.py 路由注册模式、base.html 导航（新增"消息雷达"入口）、design system 样式

**新增**：
```
ashare_review/event_radar/
  __init__.py, themes.py, events.py, chain.py, analyze.py, report.py, presets.py
ashare_review/web/templates/event_radar.html
ashare_review/tests/test_event_radar.py
data/event_radar/{themes.json, events.jsonl, results/}
```

## 十一、数据源与降级策略

| 数据 | 主源 | 降级 |
|---|---|---|
| 主题/事件 | 本地 JSON | — |
| 概念成分股 | akshare stock_board_concept_cons_em | manual_codes 兜底 |
| 板块行情 | akshare stock_board_concept_name_em | 板块字段标 N/A，个股分析照常（TDX） |
| 个股量价 | TDX 本地 .day | —（离线可用） |
| 龙虎榜 | akshare get_lhb | 跳过，标注"无龙虎榜数据" |

## 十二、LLM 角色（本地 qwen2.5，假设不结论）

1. **明日要点**：基于分析结果生成 2-3 句文案（竞价预期/连板梯队/风险），失败回退模板
2. **节点建议**：主题未配置节点时，根据事件描述建议候选节点（供用户确认，不自动写入）
3. 不生成任何交易结论

## 十三、风险与注意事项

1. **概念板块名校准依赖网络**：实施时若东财接口不可达，先以 manual_codes 上线，网络恢复后校准 concept_name
2. **"蹭概念"问题**：潜力股仅按"概念成分+量能"筛选，不保证基本面真实参与；页面标注"以公司公告为准"
3. **性能**：单次分析 1-3 个主题，每主题 4-6 节点，TDX 个股读取毫秒级；akshare 板块行情一次拉全量（缓存 30 分钟）
4. **不构成投资建议**：页面底部免责提示

## 十四、实施阶段（详细计划见 writing-plans）

- Phase 1：数据层（themes/events/chain/presets + 预置库校准）
- Phase 2：分析核心（analyze/report + 测试）
- Phase 3：Web 页面与 API + 导航接入
- Phase 4：集成验证 + 导出 + 文档
