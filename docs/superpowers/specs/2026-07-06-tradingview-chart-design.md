# TradingView 风格看盘界面 — 设计文档

> 日期：2026-07-06 | 状态：已确认 | 关联：[Vibe-Trading 集成设计](2026-06-19-vibe-trading-integration-design.md)

## 1. 目标

在现有 A 股复盘系统中新增一个 TradingView 风格的看盘界面，提供专业级图表分析能力，并与现有选股/复盘系统深度集成。

## 2. 功能范围（全部 6 项）

1. **K线图 + 技术指标叠加** — 交互式蜡烛图，叠加 MA/MACD/RSI/布林带/成交量，支持多周期切换
2. **多窗口分屏布局** — 2/4 分屏，每屏独立标的和周期
3. **画线工具** — 趋势线/水平线/矩形/斐波那契回撤，本地持久化
4. **自选股监视列表** — 分组、搜索、拖拽排序，左侧面板
5. **实时数据推送** — 分时图实时更新、盘口五档、异动提醒
6. **选股系统集成** — 筛选结果一键加载图表，策略买卖点标注

## 3. 技术架构

### 3.1 前端

- **图表引擎**：lightweight-charts v4.x（TradingView 官方开源，MIT 协议，CDN 引入）
- **前端框架**：无框架，Vanilla JS ES 模块
- **数据缓存**：浏览器 IndexedDB（K线数据）+ localStorage（画线/自选列表）
- **实时推送**：复用现有 Flask SSE 基础设施
- **模板**：复用现有 Jinja2 base.html（侧边栏导航 + AI 弹窗）
- **零新增前端依赖**：不引入 React/Vue/Webpack/Node.js 工具链

### 3.2 后端

- **Web 框架**：Flask（现有）
- **日线数据**：TdxReader（本地 .day 文件）
- **分钟数据**：AkshareFetcher（`stock_zh_a_hist_min_em` 接口）
- **盘口数据**：AkshareFetcher（实时接口）
- **自选存储**：SQLite（现有 cache.db 或新建 watchlist.db）
- **新增路由**：1 个页面路由 + 6 个 API 端点

### 3.3 新增 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chart` | GET | 看盘页面（Jinja2 模板，继承 base.html） |
| `/api/chart/kline?code=&period=` | GET | 多周期 K 线数据（daily/60min/30min/15min/5min） |
| `/api/chart/intra?code=` | GET | 当日分时图数据 |
| `/api/chart/depth?code=` | GET | 盘口五档实时数据 |
| `/api/watchlist` | GET/POST | 自选列表查询/新增 |
| `/api/watchlist/<id>` | PUT/DELETE | 自选股更新/删除 |
| `/api/chart/events?code=` | GET | 策略事件标注（买卖点/涨停日/龙虎榜） |

## 4. 页面布局

经典 TradingView 三列布局（1280px+ 宽屏基准）：

```
┌──────────┬─────────────────────────────────┬──────────┐
│ 左列     │ 中列 (flex:1)                    │ 右列     │
│ 200px    │                                 │ 220px    │
│          │ ┌─────────────────────────────┐ │          │
│ 📋 自选  │ │ 股票标题栏                   │ │ 🔧 工具  │
│ 股列表  │ │ 600519 贵州茅台 1,852.00 -1.2%│ │ 画线按钮 │
│          │ ├─────────────────────────────┤ │          │
│ 🔍 搜索  │ │                             │ │ 📊 盘口  │
│          │ │   K线主图                    │ │ 五档     │
│ 分组标签 │ │   (Candlestick + MA叠加)     │ │          │
│          │ │                             │ │ 🎯 策略  │
│ 自选条目 │ ├─────────────────────────────┤ │ 信号列表 │
│ 带涨跌幅 │ │   成交量副图                  │ │          │
│          │ ├─────────────────────────────┤ │          │
│ +添加    │ │   MACD副图                   │ │          │
│          │ └─────────────────────────────┘ │          │
└──────────┴─────────────────────────────────┴──────────┘
```

### 4.1 三区比例

- **左列**：200px 固定宽度，内容溢出时内部滚动
- **中列**：flex:1 弹性填充，K线主图占 60% 高度，成交量+MACD 各约 70px
- **右列**：220px 固定宽度，工具区+盘口区+策略信号区纵向排列

### 4.2 响应式策略

- 屏幕宽度 < 1024px：右列折叠为底部 Tab 栏
- 屏幕宽度 < 768px：左右列全部折叠，仅保留中列图表 + 汉堡菜单

## 5. 前端模块拆分

8 个 ES 模块，通过事件总线 (Pub/Sub) 通信：

| 模块文件 | 职责 | 关键依赖 |
|----------|------|----------|
| `chart-core.js` | lightweight-charts 初始化，主图/副图创建，布局管理 | lightweight-charts |
| `chart-data.js` | K线数据获取、周期切换、IndexedDB 缓存管理 | `/api/chart/kline` |
| `chart-indicators.js` | MA/MACD/RSI/布林带计算和渲染 | lightweight-charts |
| `chart-drawings.js` | 画线工具（趋势线/水平线/矩形/斐波那契），localStorage 持久化 | lightweight-charts |
| `watchlist.js` | 自选列表管理、分组、搜索、拖拽排序 | `/api/watchlist` |
| `toolbar.js` | 右侧工具面板、盘口五档渲染、指标开关 | `/api/chart/depth` |
| `strategy-overlay.js` | 策略买卖点标注、选股结果图表联动、事件图标 | `/api/chart/events` |
| `app.js` | 主入口，事件总线 (Pub/Sub)，各模块协调 | 所有模块 |

### 5.1 事件总线设计

```javascript
// app.js 中的 Pub/Sub 模式
const bus = {
  _handlers: {},
  on(event, fn) { (this._handlers[event] ??= []).push(fn); },
  emit(event, data) { (this._handlers[event] || []).forEach(fn => fn(data)); },
};
// 关键事件：
// 'symbol:changed' → {code, name}  — 切换标的时触发，图表+盘口+策略面板同步更新
// 'period:changed' → {period}       — 切换周期时触发，K线数据重新加载
// 'drawing:selected' → {tool}       — 选择画线工具时触发
// 'theme:changed' → {theme}         — 切换明暗主题时触发
```

## 6. 数据流

### 6.1 K线数据加载流程

```
用户点击标的/切换周期
  → app.js emit('symbol:changed')
  → chart-data.js 检查 IndexedDB 缓存
    → 命中缓存 → 直接渲染
    → 未命中 → GET /api/chart/kline?code=600519&period=daily
      → Flask: period='daily' → TdxReader.read_daily()
      → Flask: period='60min' → AkshareFetcher.get_min_kline()
      → 返回 JSON [{time, open, high, low, close, volume}]
    → 存入 IndexedDB (TTL: 日线 1天 / 分钟线 30分钟)
    → 传给 chart-core.js 渲染
```

### 6.2 实时数据流程（Phase 4）

```
chart-data.js 建立 SSE 连接
  → GET /api/chart/intra/stream?code=600519
  → 服务端每 3 秒推送一次最新 tick
  → 前端更新分时图/最新价/盘口
  → 断开时自动重连（指数退避）
```

## 7. 实现阶段

### Phase 1 — 核心K线图 MVP（2-3天）
- 新增 `/chart` 页面路由 + Jinja2 模板
- 引入 lightweight-charts CDN
- K线主图（蜡烛图）+ 成交量副图
- MA5/MA10/MA20/MA60/MA89 叠加
- MACD 副图（DIF/DEA/柱状图）
- `/api/chart/kline` 接口：日线(TDX) + 分钟线(akshare)
- 时间周期切换（日线/60分/30分/15分/5分）
- IndexedDB 前端缓存
- 十字光标 + 缩放拖拽

### Phase 2 — 自选列表+盘口+面板（1-2天）
- 自选列表面板（搜索/分组/点击切换）
- `/api/watchlist` 自选 CRUD（SQLite）
- 盘口五档面板（akshare 实时数据）
- 工具栏：RSI/布林带切换开关
- 股票标题栏：代码/名称/最新价/涨跌幅

### Phase 3 — 画线+分时+策略集成（2-3天）
- 趋势线/水平线/矩形/斐波那契回撤
- 画线数据 localStorage 持久化
- 当日分时图模式
- `/api/chart/events` 策略事件标注
- 选股结果一键跳转图表
- 涨停/炸板/龙虎榜图标标记

### Phase 4 — 分屏+实时+打磨（2-3天）
- 2/4 分屏布局
- SSE 分时图实时推送
- 涨速/量比异动提醒
- 暗色/亮色主题切换
- 键盘快捷键
- 移动端响应式适配

**总计预估：7-11 天**

## 8. 文件变更清单

### 新增文件
```
ashare_review/web/
├── templates/
│   └── chart.html              # 看盘页面模板
├── static/
│   └── chart/
│       ├── app.js              # 主入口 + 事件总线
│       ├── chart-core.js       # lightweight-charts 初始化
│       ├── chart-data.js       # K线数据 + IndexedDB
│       ├── chart-indicators.js # 技术指标计算
│       ├── chart-drawings.js   # 画线工具
│       ├── watchlist.js        # 自选列表面板
│       ├── toolbar.js          # 右侧工具栏 + 盘口
│       ├── strategy-overlay.js # 策略标注集成
│       └── style.css           # 看盘界面专用样式
```

### 修改文件
```
ashare_review/web/
├── app.py                      # 新增 /chart 路由 + 6个 API 端点
├── templates/
│   └── base.html               # 侧边栏新增"看盘"导航项
│   └── screening.html          # 筛选结果增加"图表查看"链接
└── static/
    └── style.css               # 可能的全局样式微调

ashare_review/data/
└── akshare_fetcher.py          # 新增 get_min_kline() 方法（如不存在）
```

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| akshare 分钟线接口不稳定 | 分钟K线无法加载 | 降级到仅日线模式，提示用户；IndexedDB 缓存减少重复请求 |
| lightweight-charts 无内置分时图 | Phase 3 分时图需要额外开发 | 用 LineSeries 模拟分时图，或引入轻量分时图实现 |
| 多分屏性能（4×K线图同时渲染） | 低配机器卡顿 | 仅渲染可见分屏，折叠的分屏用占位符；限制同时活跃图表数为 2 |
| 盘口数据延迟 | 盘口与实际有偏差 | 界面标注数据时间戳，不用于实际交易决策 |

## 10. 未决问题（待 Phase 1 前确认）

1. 通达信目录下是否有 `.5`/`.1` 分钟线文件？如有可替代 akshare 在线请求
2. 自选股数据是否需要跨设备同步？当前设计为浏览器 localStorage + 后端 SQLite
3. 画线数据是否需要跨设备同步？当前设计仅 localStorage

---

*关联记忆：[R's Trading System Overview](r-trading-system-overview.md) | [1进2战法详解](1-into-2-strategy.md)*
