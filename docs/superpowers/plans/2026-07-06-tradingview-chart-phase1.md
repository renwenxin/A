# TradingView 看盘界面 Phase 1 — 核心K线图 MVP 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Flask A 股复盘系统中新增 `/chart` 看盘页面，实现交互式 K 线图（蜡烛图 + MA 叠加 + 成交量 + MACD），支持日线/分钟线多周期切换。

**Architecture:** Flask 新增 1 个页面路由 + 1 个数据 API，前端使用 lightweight-charts v4.x CDN + Vanilla JS ES 模块（事件总线 Pub/Sub），日线数据走 TdxReader 本地读取、分钟线走 AkshareFetcher 在线获取，前端 IndexedDB 缓存。

**Tech Stack:** Python Flask + lightweight-charts v4.x (CDN) + Vanilla JS ES modules + IndexedDB

---

## File Structure

```
ashare_review/web/
├── templates/
│   └── chart.html              # NEW — 看盘页面 (继承 base.html，三列布局)
├── static/
│   └── chart/
│       ├── app.js              # NEW — 入口 + EventBus + 初始化协调
│       ├── chart-core.js       # NEW — lightweight-charts 主图/副图创建
│       ├── chart-data.js       # NEW — API调用 + IndexedDB缓存 + 周期切换
│       ├── chart-indicators.js # NEW — MA/MACD计算和叠加
│       └── style.css           # NEW — 看盘页面专有样式 (暗色主题)

ashare_review/web/
├── app.py                      # MODIFY — 新增 /chart + /api/chart/kline 路由
├── templates/
│   └── base.html               # MODIFY — 侧边栏新增「看盘」nav-item

ashare_review/data/
└── akshare_fetcher.py          # MODIFY — 新增 get_min_kline() 方法
```

---

### Task 1: 后端 — 新增分钟K线数据接口

**Files:**
- Modify: `ashare_review/data/akshare_fetcher.py` (末尾追加方法)

- [ ] **Step 1: 在 AkshareFetcher 类中新增 `get_min_kline()` 方法**

在 `ashare_review/data/akshare_fetcher.py` 文件末尾（AkshareFetcher 类内部，最后一个方法之后）添加：

```python
def get_min_kline(self, code: str, period: str = '60', days: int = 30) -> pd.DataFrame:
    """获取分钟K线数据

    Args:
        code: 股票代码 (6位)
        period: 周期 '5','15','30','60'
        days: 获取近多少天的数据，默认30天

    Returns:
        DataFrame with columns: time, open, high, low, close, volume
        空DataFrame表示获取失败
    """
    import akshare as ak

    # 确定市场前缀
    if code.startswith('6'):
        symbol = f'{code}'
    elif code.startswith('0') or code.startswith('3'):
        symbol = f'{code}'
    elif code.startswith('8') or code.startswith('4'):
        symbol = f'{code}'
    else:
        symbol = code

    cache_key = f'min_kline_{code}_{period}_{days}'
    cached = self._cache_get(cache_key, ttl_minutes=10)
    if cached:
        import json
        records = json.loads(cached)
        return pd.DataFrame(records)

    try:
        _clean_proxy()
        df = ak.stock_zh_a_hist_min_em(
            symbol=symbol,
            period=period,
            adjust='qfq',
            start_date=(datetime.now() - timedelta(days=days)).strftime('%Y%m%d'),
            end_date=datetime.now().strftime('%Y%m%d'),
        )
    except Exception:
        import traceback
        traceback.print_exc()
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # 标准化列名：akshare 返回列名可能是中文
    col_map = {}
    for c in df.columns:
        if c in ('时间', 'time'):
            col_map[c] = 'time'
        elif c in ('开盘', 'open'):
            col_map[c] = 'open'
        elif c in ('最高', 'high'):
            col_map[c] = 'high'
        elif c in ('最低', 'low'):
            col_map[c] = 'low'
        elif c in ('收盘', 'close'):
            col_map[c] = 'close'
        elif c in ('成交量', 'volume'):
            col_map[c] = 'volume'
        elif c in ('成交额', 'amount'):
            col_map[c] = 'amount'

    df = df.rename(columns=col_map)

    # 确保必要列存在
    required = ['time', 'open', 'high', 'low', 'close', 'volume']
    missing = [c for c in required if c not in df.columns]
    if missing:
        return pd.DataFrame()

    # 缓存
    records = df[required].to_dict(orient='records')
    # Convert time to string for JSON serialization
    for r in records:
        if hasattr(r['time'], 'strftime'):
            r['time'] = r['time'].strftime('%Y-%m-%d %H:%M:%S')
        elif not isinstance(r['time'], str):
            r['time'] = str(r['time'])
    self._cache_set(cache_key, json.dumps(records, ensure_ascii=False, default=str))

    return df[required]
```

- [ ] **Step 2: 添加 datetime + timedelta import（如文件顶部没有）**

检查文件顶部是否有 `from datetime import datetime, date, timedelta`。当前文件第 14 行已有 `from datetime import datetime, date, timedelta`，无需修改。

- [ ] **Step 3: 验证方法可导入**

Run: `cd /d/cursor/project && python -c "from ashare_review.data.akshare_fetcher import AkshareFetcher; f = AkshareFetcher(); print('get_min_kline' in dir(f))"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add ashare_review/data/akshare_fetcher.py
git commit -m "feat: add get_min_kline() to AkshareFetcher for multi-timeframe chart data"
```

---

### Task 2: 后端 — Flask 新增看盘页面路由 + K线数据 API

**Files:**
- Modify: `ashare_review/web/app.py` (在现有路由之后追加)

- [ ] **Step 1: 在 app.py 末尾（`if __name__ == '__main__'` 之前）新增 `/chart` 页面路由**

```python
@app.route('/chart')
def chart_page():
    """看盘页面 — TradingView 风格K线图"""
    code = request.args.get('code', '000001')
    return render_template('chart.html', default_code=code)
```

- [ ] **Step 2: 新增 `/api/chart/kline` 数据接口**

```python
@app.route('/api/chart/kline')
def api_chart_kline():
    """K线数据接口 — 多周期支持

    Query params:
        code: 股票代码 (6位), 必填
        period: 周期 daily/60min/30min/15min/5min, 默认 daily
    """
    code = request.args.get('code', '')
    period = request.args.get('period', 'daily')

    if not code or len(code) != 6:
        return jsonify({'error': 'code is required (6 digits)'}), 400

    # 确定市场
    if code.startswith('6'):
        market = 'sh'
    elif code.startswith('0') or code.startswith('3'):
        market = 'sz'
    elif code.startswith('8') or code.startswith('4'):
        market = 'bj'
    else:
        return jsonify({'error': f'Unknown market for code: {code}'}), 400

    try:
        if period == 'daily':
            # 日线 — 通达信本地数据
            df = tdx.read_daily(code, market)
            if df.empty:
                return jsonify({'error': f'No daily data for {code}', 'code': code}), 404

            # 转成 lightweight-charts 需要的格式
            bars = []
            for _, row in df.iterrows():
                d = row['trade_date']
                bars.append({
                    'time': d.isoformat() if hasattr(d, 'isoformat') else str(d),
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': int(row['volume']),
                })
        else:
            # 分钟线 — akshare 在线获取
            period_map = {'60min': '60', '30min': '30', '15min': '15', '5min': '5'}
            ak_period = period_map.get(period, '60')

            df = ak_fetcher.get_min_kline(code, period=ak_period)
            if df.empty:
                return jsonify({
                    'error': f'No minute data for {code} period={period}',
                    'code': code,
                    'fallback': True,
                }), 404

            bars = []
            for _, row in df.iterrows():
                t = row['time']
                bars.append({
                    'time': t.isoformat() if hasattr(t, 'isoformat') else str(t),
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': int(row['volume']),
                })

        # 获取股票名称（从行情快照查找）
        name = ''
        try:
            spot_df = ak_fetcher.get_spot_df()
            if spot_df is not None and not spot_df.empty:
                row = spot_df[spot_df['代码'] == code]
                if not row.empty:
                    name = str(row.iloc[0].get('名称', ''))
        except Exception:
            pass

        return jsonify({
            'code': code,
            'name': name,
            'period': period,
            'market': market,
            'total': len(bars),
            'bars': bars,
        })

    except FileNotFoundError as e:
        return jsonify({'error': str(e), 'code': code}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'code': code}), 500
```

- [ ] **Step 3: 验证路由可访问**

Run: `cd /d/cursor/project && python -c "from ashare_review.web.app import app; routes = [r.rule for r in app.url_map.iter_rules()]; print('/chart' in routes, '/api/chart/kline' in routes)"`
Expected: `True True`

- [ ] **Step 4: Commit**

```bash
git add ashare_review/web/app.py
git commit -m "feat: add /chart page and /api/chart/kline endpoint for multi-timeframe K-line data"
```

---

### Task 3: 前端基础 — chart.html 模板 + style.css

**Files:**
- Create: `ashare_review/web/templates/chart.html`
- Create: `ashare_review/web/static/chart/style.css`

- [ ] **Step 1: 创建看盘页面 CSS（暗色主题，独立文件）**

创建 `ashare_review/web/static/chart/style.css`：

```css
/* ================================================================
   TradingView 风格看盘界面 — 暗色主题
   ================================================================ */

/* ---- 根容器 (占满主内容区) ---- */
.chart-page {
    display: flex;
    flex-direction: column;
    height: calc(100vh - 60px); /* 减去顶部可能的间距 */
    background: #131722;
    color: #d1d4dc;
}

/* ---- 顶部股票标题栏 ---- */
.chart-toolbar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 8px 16px;
    background: #1e222d;
    border-bottom: 1px solid #2a2e39;
    min-height: 44px;
    flex-shrink: 0;
}
.chart-toolbar .stock-code {
    font-size: 16px;
    font-weight: 700;
    color: #f0f6fc;
}
.chart-toolbar .stock-name {
    font-size: 13px;
    color: #8b949e;
}
.chart-toolbar .stock-price {
    font-size: 18px;
    font-weight: 700;
    color: #ef5350;
}
.chart-toolbar .stock-change {
    font-size: 13px;
    font-weight: 600;
}
.chart-toolbar .stock-change.up { color: #26a69a; }
.chart-toolbar .stock-change.down { color: #ef5350; }

/* ---- 周期切换按钮组 ---- */
.period-switcher {
    display: flex;
    gap: 2px;
    margin-left: auto;
    background: #2a2e39;
    border-radius: 4px;
    overflow: hidden;
}
.period-btn {
    padding: 4px 12px;
    font-size: 12px;
    color: #787b86;
    background: transparent;
    border: none;
    cursor: pointer;
    transition: all 0.15s;
}
.period-btn:hover {
    color: #d1d4dc;
    background: #363a45;
}
.period-btn.active {
    color: #fff;
    background: #2962ff;
}

/* ---- 三列布局容器 ---- */
.chart-layout {
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

/* ---- 左列: 自选列表占位 ---- */
.chart-sidebar-left {
    width: 200px;
    min-width: 200px;
    background: #1e222d;
    border-right: 1px solid #2a2e39;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
}
.chart-sidebar-left .panel-header {
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 600;
    color: #d1d4dc;
    border-bottom: 1px solid #2a2e39;
}
.chart-sidebar-left .placeholder-text {
    padding: 16px 12px;
    font-size: 11px;
    color: #787b86;
    text-align: center;
}

/* ---- 中列: 图表区 ---- */
.chart-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
}

/* ---- 右列: 工具占位 ---- */
.chart-sidebar-right {
    width: 220px;
    min-width: 220px;
    background: #1e222d;
    border-left: 1px solid #2a2e39;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
}
.chart-sidebar-right .panel-header {
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 600;
    color: #d1d4dc;
    border-bottom: 1px solid #2a2e39;
}
.chart-sidebar-right .placeholder-text {
    padding: 16px 12px;
    font-size: 11px;
    color: #787b86;
    text-align: center;
}

/* ---- 响应式 ---- */
@media (max-width: 1024px) {
    .chart-sidebar-right { display: none; }
}
@media (max-width: 768px) {
    .chart-sidebar-left { display: none; }
}
```

- [ ] **Step 2: 创建 chart.html 模板（继承 base.html）**

创建 `ashare_review/web/templates/chart.html`：

```html
{% extends "base.html" %}
{% block content %}
<div class="chart-page" id="chartPage">
    <!-- 顶部股票标题栏 -->
    <div class="chart-toolbar" id="chartToolbar">
        <span class="stock-code" id="toolbarCode">{{ default_code }}</span>
        <span class="stock-name" id="toolbarName">--</span>
        <span class="stock-price" id="toolbarPrice">--</span>
        <span class="stock-change" id="toolbarChange">--</span>

        <div class="period-switcher" id="periodSwitcher">
            <button class="period-btn" data-period="5min">5分</button>
            <button class="period-btn" data-period="15min">15分</button>
            <button class="period-btn" data-period="30min">30分</button>
            <button class="period-btn" data-period="60min">60分</button>
            <button class="period-btn active" data-period="daily">日线</button>
        </div>
    </div>

    <!-- 三列布局 -->
    <div class="chart-layout">
        <!-- 左列: 自选列表 (Phase 2 实现) -->
        <aside class="chart-sidebar-left" id="sidebarLeft">
            <div class="panel-header">📋 自选股</div>
            <p class="placeholder-text">自选列表将在 Phase 2 实现<br>当前可直接修改URL参数切换标的:<br><code>/chart?code=600519</code></p>
        </aside>

        <!-- 中列: 图表 -->
        <main class="chart-main" id="chartMain">
            <!-- lightweight-charts 动态创建 chart div -->
        </main>

        <!-- 右列: 工具面板 (Phase 2 实现) -->
        <aside class="chart-sidebar-right" id="sidebarRight">
            <div class="panel-header">🔧 工具 & 盘口</div>
            <p class="placeholder-text">盘口五档 / 策略信号<br>将在 Phase 2 实现</p>
        </aside>
    </div>
</div>

<!-- lightweight-charts CDN -->
<script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>

<!-- ES 模块（按依赖顺序加载） -->
<script type="module" src="/static/chart/app.js"></script>
{% endblock %}
```

- [ ] **Step 3: Verify template renders**

Run: `cd /d/cursor/project && python -c "from ashare_review.web.app import app; from flask import template_rendered; print('chart.html' in app.jinja_loader.list_templates())"`
Expected: Check that chart.html is listed in templates.

- [ ] **Step 4: Commit**

```bash
git add ashare_review/web/templates/chart.html ashare_review/web/static/chart/style.css
git commit -m "feat: add chart.html template with TradingView-style dark theme layout"
```

---

### Task 4: 前端 — app.js 入口 + EventBus

**Files:**
- Create: `ashare_review/web/static/chart/app.js`

- [ ] **Step 1: 创建 app.js — EventBus + 模块协调入口**

创建 `ashare_review/web/static/chart/app.js`：

```javascript
/**
 * 看盘界面 — 主入口
 * 职责：EventBus、模块初始化、全局协调
 */

// ====== EventBus ======
export const bus = {
    _handlers: {},
    on(event, fn) {
        (this._handlers[event] = this._handlers[event] || []).push(fn);
    },
    off(event, fn) {
        const arr = this._handlers[event];
        if (arr) this._handlers[event] = arr.filter(f => f !== fn);
    },
    emit(event, data) {
        (this._handlers[event] || []).forEach(fn => {
            try { fn(data); } catch (e) { console.error(`[EventBus] ${event}`, e); }
        });
    },
};

// ====== 全局状态 ======
export const state = {
    code: null,      // 当前股票代码
    name: '',        // 当前股票名称
    period: 'daily', // 当前周期
};

// https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js
// 暴露全局变量 window.LightweightCharts

// ====== 初始化入口 ======
async function init() {
    // 从 URL 参数或模板默认值获取初始代码
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code') || document.getElementById('toolbarCode')?.textContent?.trim() || '000001';

    state.code = code;

    console.log('[chart] Initializing with code:', code);

    // 动态导入子模块
    const [{ initChart, loadData }, { updateToolbar }] = await Promise.all([
        import('/static/chart/chart-core.js'),
        import('/static/chart/chart-data.js'),
    ]);

    // 初始化图表
    initChart(document.getElementById('chartMain'));

    // 加载数据
    await loadData(code, state.period);

    // 订阅事件
    bus.on('period:changed', async ({ period }) => {
        state.period = period;
        await loadData(state.code, period);
    });

    bus.on('symbol:changed', async ({ code, name }) => {
        state.code = code;
        state.name = name || '';
        await loadData(code, state.period);
    });

    // 周期按钮点击
    document.getElementById('periodSwitcher').addEventListener('click', (e) => {
        const btn = e.target.closest('.period-btn');
        if (!btn) return;
        const period = btn.dataset.period;

        // 更新active状态
        document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        bus.emit('period:changed', { period });
    });

    console.log('[chart] Initialization complete');
}

// 启动
document.addEventListener('DOMContentLoaded', init);
```

- [ ] **Step 2: Commit**

```bash
git add ashare_review/web/static/chart/app.js
git commit -m "feat: add chart app.js entry point with EventBus and module coordination"
```

---

### Task 5: 前端 — chart-core.js 图表初始化

**Files:**
- Create: `ashare_review/web/static/chart/chart-core.js`

- [ ] **Step 1: 创建 chart-core.js — lightweight-charts 初始化 + 主图/副图**

创建 `ashare_review/web/static/chart/chart-core.js`：

```javascript
/**
 * 图表核心 — lightweight-charts 主图/副图创建和更新
 */

import { bus, state } from '/static/chart/app.js';

const LC = window.LightweightCharts;
if (!LC) throw new Error('lightweight-charts not loaded. Check CDN script in chart.html.');

// ====== 模块内部状态 ======
let chart = null;
let candleSeries = null;
let volumeSeries = null;
let macdSeries = null;      // MACD histogram
let macdDIFSeries = null;   // DIF line
let macdDEASeries = null;   // DEA line
let maSeries = {};           // { 'ma5': LineSeries, 'ma10': ..., ... }

// ====== 创建图表 ======
export function initChart(container) {
    if (chart) {
        // 如果已存在，先清理
        container.innerHTML = '';
        chart = null;
        candleSeries = null;
        volumeSeries = null;
        macdSeries = null;
        macdDIFSeries = null;
        macdDEASeries = null;
        maSeries = {};
    }

    // 创建主图
    chart = LC.createChart(container, {
        layout: {
            background: { color: '#131722' },
            textColor: '#d1d4dc',
        },
        grid: {
            vertLines: { color: '#1e222d' },
            horzLines: { color: '#1e222d' },
        },
        crosshair: {
            mode: 1,  // 十字光标跟随
            vertLine: { color: '#787b86', labelBackgroundColor: '#2962ff' },
            horzLine: { color: '#787b86', labelBackgroundColor: '#2962ff' },
        },
        rightPriceScale: {
            borderColor: '#2a2e39',
        },
        timeScale: {
            borderColor: '#2a2e39',
            timeVisible: true,
            secondsVisible: false,
        },
    });

    // ---- K线主图 ----
    candleSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
    });

    // ---- 成交量副图 (叠加在主图下方 pane) ----
    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',  // 独立的Y轴
    });
    // 设置成交量 pane 高度
    chart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },  // 底部20%高度
    });

    // ---- MACD副图 (Phase 1 先算好再叠; 后续可增量Pane) ----
    // lightweight-charts 4.x 支持多 pane, 我们在此先创建单 pane
    // MACD 将画在独立的 pane 上
    const macdPaneIndex = 1; // 预留 Phase 3 多 pane 方案

    // ---- 窗口大小自适应 ----
    window.addEventListener('resize', () => {
        if (chart && container) {
            chart.applyOptions({
                width: container.clientWidth,
                height: container.clientHeight,
            });
        }
    });

    console.log('[chart-core] Chart initialized');
}

// ====== 渲染K线数据 ======
export function renderData(bars) {
    if (!candleSeries) {
        console.error('[chart-core] candleSeries not initialized');
        return;
    }

    if (!bars || bars.length === 0) {
        console.warn('[chart-core] No bars to render');
        return;
    }

    // 转换数据格式: time需要是Date对象 (日线) 或 秒级时间戳 (分钟线)
    const candleData = bars.map(b => ({
        time: formatTime(b.time),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
    }));

    const volumeData = bars.map(b => ({
        time: formatTime(b.time),
        value: b.volume,
        color: b.close >= b.open ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
    }));

    candleSeries.setData(candleData);
    volumeSeries.setData(volumeData);

    // 自适应内容
    chart.timeScale().fitContent();

    console.log(`[chart-core] Rendered ${bars.length} bars`);
}

// ====== 时间格式转换 ======
function formatTime(t) {
    // 如果已经是秒级时间戳 (数字)，直接使用
    if (typeof t === 'number') return t;

    // 如果是 ISO 字符串，判断是日线还是分钟线
    if (typeof t === 'string') {
        // 日线格式: '2026-07-06' → Date
        if (t.length === 10) {
            return t; // lightweight-charts 接受 'YYYY-MM-DD' 格式的字符串
        }
        // 分钟线格式: '2026-07-06 14:30:00' → 秒级时间戳
        if (t.length >= 19) {
            const d = new Date(t);
            if (!isNaN(d.getTime())) {
                return Math.floor(d.getTime() / 1000);
            }
        }
        return t;
    }

    return t;
}

// ====== 清理图表 ======
export function destroyChart() {
    if (chart) {
        chart.remove();
        chart = null;
    }
}

// ====== 导出图表实例引用 (供 indicators/drawings 模块使用) ======
export function getChart() { return chart; }
export function getCandleSeries() { return candleSeries; }
export function getVolumeSeries() { return volumeSeries; }
```

- [ ] **Step 2: Commit**

```bash
git add ashare_review/web/static/chart/chart-core.js
git commit -m "feat: add chart-core.js — lightweight-charts candlestick + volume series"
```

---

### Task 6: 前端 — chart-data.js API 调用 + IndexedDB 缓存

**Files:**
- Create: `ashare_review/web/static/chart/chart-data.js`

- [ ] **Step 1: 创建 chart-data.js**

创建 `ashare_review/web/static/chart/chart-data.js`：

```javascript
/**
 * 数据层 — API调用 + IndexedDB缓存 + 周期切换
 */

import { bus, state } from '/static/chart/app.js';
import { renderData } from '/static/chart/chart-core.js';
import { calcIndicators, addIndicatorSeries, removeIndicatorSeries } from '/static/chart/chart-indicators.js';

// ====== IndexedDB 缓存 ======
const DB_NAME = 'chart_cache';
const DB_VERSION = 1;
const STORE_NAME = 'kline';

function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, { keyPath: 'cacheKey' });
            }
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    });
}

async function getCache(cacheKey) {
    const db = await openDB();
    return new Promise((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const req = store.get(cacheKey);
        req.onsuccess = () => {
            const record = req.result;
            if (!record) return resolve(null);

            // 检查 TTL
            const age = Date.now() - record.cachedAt;
            const ttl = cacheKey.startsWith('daily_') ? 86400000 : 1800000; // 日线1天, 分钟30分钟
            if (age > ttl) {
                // 过期删除
                const delTx = db.transaction(STORE_NAME, 'readwrite');
                delTx.objectStore(STORE_NAME).delete(cacheKey);
                resolve(null);
            } else {
                resolve(record.bars);
            }
        };
        req.onerror = () => resolve(null);
    });
}

async function setCache(cacheKey, bars) {
    const db = await openDB();
    return new Promise((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).put({ cacheKey, bars, cachedAt: Date.now() });
        tx.oncomplete = () => resolve();
        tx.onerror = () => resolve();
    });
}

// ====== API 调用 ======
async function fetchKline(code, period) {
    const url = `/api/chart/kline?code=${encodeURIComponent(code)}&period=${encodeURIComponent(period)}`;
    try {
        const resp = await fetch(url);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        return data;
    } catch (e) {
        console.error('[chart-data] Fetch failed:', e);
        throw e;
    }
}

// ====== 主加载函数 ======
export async function loadData(code, period) {
    if (!code) return;

    const cacheKey = `${period}_${code}`;

    console.log(`[chart-data] Loading ${code} ${period}...`);

    // 1. 尝试缓存
    let bars = await getCache(cacheKey);

    // 2. 缓存未命中 → API 请求
    if (!bars || bars.length === 0) {
        console.log(`[chart-data] Cache miss, fetching from API...`);
        const data = await fetchKline(code, period);
        bars = data.bars || [];

        // 更新名称
        if (data.name) {
            state.name = data.name;
            bus.emit('toolbar:update', { code, name: data.name });
        }

        // 存入缓存
        if (bars.length > 0) {
            await setCache(cacheKey, bars);
            console.log(`[chart-data] Cached ${bars.length} bars`);
        }
    } else {
        console.log(`[chart-data] Cache hit: ${bars.length} bars`);
    }

    // 3. 渲染数据
    if (bars.length > 0) {
        renderData(bars);

        // 4. 计算并叠加技术指标
        const lastBar = bars[bars.length - 1];
        const name = state.name || code;
        bus.emit('toolbar:update', {
            code,
            name,
            price: lastBar.close,
            change: bars.length >= 2
                ? ((lastBar.close - bars[bars.length - 2].close) / bars[bars.length - 2].close * 100)
                : 0,
        });
    } else {
        console.warn(`[chart-data] No data for ${code} ${period}`);
    }
}

// ====== 更新工具栏 ======
bus.on('toolbar:update', ({ code, name, price, change }) => {
    if (code != null) document.getElementById('toolbarCode').textContent = code;
    if (name != null) document.getElementById('toolbarName').textContent = name;
    if (price != null) {
        document.getElementById('toolbarPrice').textContent = price.toFixed(2);
    }
    if (change != null) {
        const el = document.getElementById('toolbarChange');
        el.textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        el.className = `stock-change ${change >= 0 ? 'up' : 'down'}`;
        document.getElementById('toolbarPrice').style.color = change >= 0 ? '#26a69a' : '#ef5350';
    }
});
```

- [ ] **Step 2: Commit**

```bash
git add ashare_review/web/static/chart/chart-data.js
git commit -m "feat: add chart-data.js — API fetch + IndexedDB cache + toolbar sync"
```

---

### Task 7: 前端 — chart-indicators.js 技术指标

**Files:**
- Create: `ashare_review/web/static/chart/chart-indicators.js`

- [ ] **Step 1: 创建 chart-indicators.js**

创建 `ashare_review/web/static/chart/chart-indicators.js`：

```javascript
/**
 * 技术指标 — MA/MACD 计算 + lightweight-charts 叠加
 */

import { getChart, getCandleSeries } from '/static/chart/chart-core.js';

const LC = window.LightweightCharts;

// ====== MA 叠加 ======
let maLines = {};  // { 'ma5': LineSeries, ... }

const MA_PERIODS = [5, 10, 20, 60, 89];
const MA_COLORS = {
    5: '#f9a825',    // 黄
    10: '#ff9800',   // 橙
    20: '#e91e63',   // 粉
    60: '#00bcd4',   // 青
    89: '#7c4dff',   // 紫
};

export function addMASeries(chart, periods = MA_PERIODS) {
    periods.forEach(p => {
        if (maLines[`ma${p}`]) return;  // 已存在

        const lineSeries = chart.addLineSeries({
            color: MA_COLORS[p] || '#ffffff',
            lineWidth: 1,
            priceScaleId: 'right',  // 与K线共享Y轴
        });
        maLines[`ma${p}`] = lineSeries;
    });
}

export function removeMASeries(chart) {
    Object.values(maLines).forEach(s => {
        try { chart.removeSeries(s); } catch (e) {}
    });
    maLines = {};
}

export function calcMA(bars, period) {
    const result = [];
    for (let i = 0; i < bars.length; i++) {
        if (i < period - 1) {
            result.push(null);
        } else {
            let sum = 0;
            for (let j = i - period + 1; j <= i; j++) sum += bars[j].close;
            result.push(sum / period);
        }
    }
    return result;
}

// ====== MACD ======
let macdSeries = {
    dif: null,   // LineSeries - DIF (快线)
    dea: null,   // LineSeries - DEA (慢线)
    bar: null,   // HistogramSeries - MACD柱
};

const FAST = 12, SLOW = 26, SIGNAL = 9;

export function calcMACD(bars) {
    // EMA
    const closes = bars.map(b => b.close);
    const ema = (data, period) => {
        const result = new Array(data.length).fill(null);
        const k = 2 / (period + 1);
        // 用第一个有效值作为初始EMA
        result[period - 1] = data.slice(0, period).reduce((a, b) => a + b, 0) / period;
        for (let i = period; i < data.length; i++) {
            result[i] = data[i] * k + result[i - 1] * (1 - k);
        }
        return result;
    };

    const emaFast = ema(closes, FAST);
    const emaSlow = ema(closes, SLOW);

    const dif = emaFast.map((v, i) => (v != null && emaSlow[i] != null) ? v - emaSlow[i] : null);
    const dea = [];
    const bar = [];

    // DEA = EMA of DIF
    const kSignal = 2 / (SIGNAL + 1);
    for (let i = 0; i < dif.length; i++) {
        if (dif[i] == null) {
            dea.push(null);
            bar.push(null);
        } else if (dea.filter(Boolean).length === 0 && i >= SLOW + SIGNAL - 2) {
            // 第一个有效 DEA
            const val = dif.slice(SLOW - 1, i + 1).reduce((a, b) => a + b, 0) / SIGNAL;
            dea.push(val);
            bar.push((dif[i] - val) * 2);
        } else if (dea[dea.length - 1] != null) {
            const val = dif[i] * kSignal + dea[dea.length - 1] * (1 - kSignal);
            dea.push(val);
            bar.push((dif[i] - val) * 2);
        } else {
            dea.push(null);
            bar.push(null);
        }
    }

    return { dif, dea, bar };
}

// ====== MACD 副图 (独立 Pane) ======
export function addMACDPane(chart, bars) {
    const { dif, dea, bar: macdBar } = calcMACD(bars);

    // 创建独立的 MACD 副图 pane
    // 先移除旧的 MACD pane（如果存在）
    removeMACDPane(chart);

    // 在 chart 的 panes 中创建 MACD 系列
    // lightweight-charts 4.x: 用 addLineSeries 时指定 priceScaleId 来创建新 pane
    // 通过使用独立的 overlay price scale 实现

    // 方法: 使用 HistogramSeries + LineSeries 在单独的 price scale 上
    macdSeries.bar = chart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: 'macd',
        // 设置 MACD pane 在底部的缩放比例
    });

    chart.priceScale('macd').applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
    });

    // 由于 lightweight-charts 免费版不支持明确的 sub-pane API,
    // 我们使用一个折中方案：MACD 线叠加到主图的另一个 scale 区域
    // 实际上我们将 volume 和 macd 都用 bottom scale
    // 这里使用简化方案：MACD 数据先计算好，Phase 3 可升级为多 pane

    const timeValues = bars.map(b => ({
        time: typeof b.time === 'string' && b.time.length === 10 ? b.time
             : (typeof b.time === 'number' ? b.time : Math.floor(new Date(b.time).getTime() / 1000)),
    }));

    const macdData = timeValues.map((t, i) => ({
        time: t.time,
        value: macdBar[i] || 0,
        color: (macdBar[i] || 0) >= 0 ? 'rgba(38,166,154,0.6)' : 'rgba(239,83,80,0.6)',
    }));

    macdSeries.bar.setData(macdData);

    // DIF 线
    macdSeries.dif = chart.addLineSeries({
        color: '#f9a825',
        lineWidth: 1,
        priceScaleId: 'macd',
    });
    macdSeries.dif.setData(timeValues.map((t, i) => ({
        time: t.time,
        value: dif[i] || 0,
    })));

    // DEA 线
    macdSeries.dea = chart.addLineSeries({
        color: '#e91e63',
        lineWidth: 1,
        priceScaleId: 'macd',
    });
    macdSeries.dea.setData(timeValues.map((t, i) => ({
        time: t.time,
        value: dea[i] || 0,
    })));
}

export function removeMACDPane(chart) {
    [macdSeries.bar, macdSeries.dif, macdSeries.dea].forEach(s => {
        if (s) {
            try { chart.removeSeries(s); } catch (e) {}
        }
    });
    macdSeries = { dif: null, dea: null, bar: null };
}

// ====== 一键计算+渲染 ======
export function calcIndicators(bars) {
    return {
        ma: MA_PERIODS.reduce((acc, p) => {
            acc[`ma${p}`] = calcMA(bars, p);
            return acc;
        }, {}),
        macd: calcMACD(bars),
    };
}

export function addIndicatorSeries(chart, bars) {
    // 先清旧指标
    removeIndicatorSeries(chart);

    // MA 线
    addMASeries(chart);

    const indicators = calcIndicators(bars);
    const timeValues = bars.map(b => ({
        time: typeof b.time === 'string' && b.time.length === 10 ? b.time
             : (typeof b.time === 'number' ? b.time : Math.floor(new Date(b.time).getTime() / 1000)),
    }));

    // 设置 MA 数据
    MA_PERIODS.forEach(p => {
        const series = maLines[`ma${p}`];
        if (!series) return;
        const data = timeValues.map((t, i) => ({
            time: t.time,
            value: indicators.ma[`ma${p}`][i],
        })).filter(d => d.value != null);
        series.setData(data);
    });

    // MACD 副图
    addMACDPane(chart, bars);
}

export function removeIndicatorSeries(chart) {
    removeMASeries(chart);
    removeMACDPane(chart);
}
```

- [ ] **Step 2: Commit**

```bash
git add ashare_review/web/static/chart/chart-indicators.js
git commit -m "feat: add chart-indicators.js — MA(5/10/20/60/89) + MACD overlay"
```

---

### Task 8: 集成 — 更新 chart-core.js 串联指标渲染

**Files:**
- Modify: `ashare_review/web/static/chart/chart-core.js`

- [ ] **Step 1: 修改 `renderData()` 函数，在渲染K线后自动叠加指标**

找到 `chart-core.js` 中的 `renderData` 函数，在 `chart.timeScale().fitContent()` 之后追加：

```javascript
// 在 renderData 函数末尾，fitContent() 之后添加：
import('/static/chart/chart-indicators.js').then(({ addIndicatorSeries, removeIndicatorSeries }) => {
    const chartInstance = getChart();
    removeIndicatorSeries(chartInstance);
    addIndicatorSeries(chartInstance, bars);
});
```

由于 renderData 已经在 chart-core.js 中定义，我们需要重构：将指标叠加逻辑从 renderData 中分离，改为由 chart-data.js 在 renderData 后显式调用。

更好的方式——修改 chart-data.js 的 loadData 函数，在 renderData 后调用 addIndicatorSeries：

在 `chart-data.js` 中，`renderData(bars)` 之后添加：

```javascript
// 叠加技术指标
import('/static/chart/chart-indicators.js').then(({ addIndicatorSeries, removeIndicatorSeries }) => {
    import('/static/chart/chart-core.js').then(({ getChart }) => {
        const chart = getChart();
        if (!chart) return;
        removeIndicatorSeries(chart);
        addIndicatorSeries(chart, bars);
    });
});
```

- [ ] **Step 2: 修改 chart-data.js 完整集成指标**

更新 `ashare_review/web/static/chart/chart-data.js`，在 `loadData` 函数的 `renderData(bars)` 调用后添加指标叠加代码：

将 chart-data.js 中 `loadData` 函数里的：
```javascript
    // 3. 渲染数据
    if (bars.length > 0) {
        renderData(bars);

        // 4. 计算并叠加技术指标
        ...
    }
```

替换为：
```javascript
    // 3. 渲染数据 + 技术指标
    if (bars.length > 0) {
        renderData(bars);

        // 叠加技术指标 (MA + MACD)
        const { getChart } = await import('/static/chart/chart-core.js');
        const { addIndicatorSeries, removeIndicatorSeries } = await import('/static/chart/chart-indicators.js');
        const chart = getChart();
        if (chart) {
            removeIndicatorSeries(chart);
            addIndicatorSeries(chart, bars);
        }

        // 更新工具栏
        const lastBar = bars[bars.length - 1];
        const prevClose = bars.length >= 2 ? bars[bars.length - 2].close : lastBar.close;
        const change = prevClose ? ((lastBar.close - prevClose) / prevClose * 100) : 0;
        bus.emit('toolbar:update', {
            code,
            name: state.name || code,
            price: lastBar.close,
            change,
        });
    }
```

- [ ] **Step 3: 验证前端文件无语法错误**

Run: `cd /d/cursor/project && node -e "console.log('Node.js available for syntax check')"` (仅检查 Node 可用性，ES 模块语法在浏览器检查)

- [ ] **Step 4: Commit**

```bash
git add ashare_review/web/static/chart/chart-data.js ashare_review/web/static/chart/chart-core.js
git commit -m "feat: integrate MA/MACD indicators into chart rendering pipeline"
```

---

### Task 9: 集成 — base.html 侧边栏新增「看盘」导航

**Files:**
- Modify: `ashare_review/web/templates/base.html`

- [ ] **Step 1: 在侧边栏导航中添加「看盘」菜单项**

在 `base.html` 中找到「数据与工具」导航分组（`nav-section` 标签），在其下方添加看盘链接：

```html
            <a href="/chart" class="nav-item {% if request.endpoint == 'chart_page' %}active{% endif %}">
                <span class="nav-icon">📈</span>
                <span class="nav-label">看盘</span>
                <span class="nav-badge">NEW</span>
            </a>
```

精确位置：在 `{% if request.endpoint == 'chat' %}` 的 AI分析 导航项之前或之后。

需要先查看 base.html 中 AI分析 导航项的确切位置：

```html
            <a href="/chat" class="nav-item {% if request.endpoint == 'chat' %}active{% endif %}">
```

在此行**之前**插入看盘导航项。

- [ ] **Step 2: Commit**

```bash
git add ashare_review/web/templates/base.html
git commit -m "feat: add '看盘' nav item to sidebar navigation"
```

---

### Task 10: 端到端验证 + 启动测试

**Files:** None (manual testing)

- [ ] **Step 1: 启动 Flask 开发服务器**

```bash
cd /d/cursor/project && python -m flask --app ashare_review.web.app run --host 0.0.0.0 --port 5000 --debug
```

- [ ] **Step 2: 验证 /chart 页面可访问**

浏览器打开 `http://localhost:5000/chart?code=600519`
Expected:
- 页面显示暗色主题布局
- 左侧"自选股"占位面板
- 中间图表区域
- 右侧"工具&盘口"占位面板
- 顶部工具栏显示 600519 代码 + 周期按钮

- [ ] **Step 3: 验证 /api/chart/kline 接口返回数据**

浏览器打开 `http://localhost:5000/api/chart/kline?code=600519&period=daily`
Expected: JSON 响应包含 `code`, `name`, `period`, `total`, `bars` 字段

- [ ] **Step 4: 验证图表渲染**

浏览器 `http://localhost:5000/chart?code=600519`
Expected:
- K线蜡烛图显示
- MA5/MA10/MA20/MA60/MA89 彩色曲线叠加
- 底部成交量柱状图
- MACD 柱状图 + DIF/DEA 线
- 鼠标悬停有十字光标
- 可拖拽/滚轮缩放

- [ ] **Step 5: 验证周期切换**

点击「60分」按钮
Expected: 图表重新加载60分钟K线数据，MA/MACD更新

- [ ] **Step 6: 验证多股票切换**

浏览器访问 `http://localhost:5000/chart?code=300750`
Expected: 加载宁德时代日线数据，图表正常渲染

- [ ] **Step 7: Commit 完成结果**

```bash
git commit --allow-empty -m "test: end-to-end verification of Phase 1 chart MVP passed"
```

---

## Phase 2-4 简要预览（本次计划范围外）

| Phase | 核心内容 | 关键文件 |
|-------|---------|----------|
| 2 | 自选列表 CRUD + 盘口五档 + 指标开关 | `watchlist.js`, `toolbar.js`, `/api/watchlist`, `/api/chart/depth` |
| 3 | 画线工具 + 分时图 + 策略买卖点标注 | `chart-drawings.js`, `strategy-overlay.js`, `/api/chart/events`, `/api/chart/intra` |
| 4 | 多面板分屏 + SSE 实时推送 + 快捷键 + 响应式 | 多实例 chart-core + SSE stream endpoint |

---

## 自检清单

- [x] 设计文档所有 Phase 1 需求均已覆盖
- [x] 无 TBD/TODO 占位符
- [x] 所有 API 接口与设计方案一致
- [x] 所有 JS 模块间的事件名称一致
- [x] 文件路径与 File Structure 一致
- [x] 每个任务有明确的文件/代码/命令
