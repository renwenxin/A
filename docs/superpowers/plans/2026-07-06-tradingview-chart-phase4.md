# Phase 4 — 多面板分屏 + SSE 实时推送 + 快捷键 + 主题 实现计划

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** 多面板分屏(2/4格) + 键盘快捷键 + 明暗主题切换 + SSE 实时推送 + 涨速/量比异动提醒 + 响应式适配

**Architecture:** 多面板用 CSS Grid + 多 lightweight-charts 实例；SSE 复用现有 Flask SSE 基础设施；主题用 CSS 变量切换；快捷键用全局 keydown 事件

**Tech Stack:** CSS Grid + Flask SSE + CSS custom properties

---

## File Structure

```
# 修改
ashare_review/web/
├── app.py                    # + SSE intraday stream route
├── templates/chart.html      # 布局切换按钮 + 主题按钮
└── static/chart/
    ├── chart-core.js         # Multi-pane support
    ├── app.js                # Keyboard shortcuts + Theme init
    └── style.css             # Theme variables + grid layout + responsive
```

---

### Task 1: 后端 — SSE 分时实时推送

**Files:** Modify `ashare_review/web/app.py`

在现有路由之后添加（复用已有的 `_sse_stream`, `queue`, `threading` 基础设施）:

```python
@app.route('/api/chart/intra/stream')
def api_chart_intra_stream():
    """分时图实时SSE推送 — 每5秒推送最新价格"""
    code = request.args.get('code', '')
    if not code or len(code) != 6:
        return jsonify({'error': 'code is required'}), 400

    from ..data.akshare_fetcher import _clean_proxy
    import akshare as ak

    def generate():
        last_price = None
        count = 0
        while count < 360:  # max 30 minutes (360 * 5s)
            try:
                _clean_proxy()
                df = ak.stock_zh_a_hist_min_em(
                    symbol=code, period='5', adjust='qfq',
                    start_date=datetime.now().strftime('%Y%m%d'),
                    end_date=datetime.now().strftime('%Y%m%d'))
                if df is not None and not df.empty:
                    row = df.iloc[-1]
                    close_col = next((c for c in df.columns if c in ('收盘','close')), df.columns[-2])
                    vol_col = next((c for c in df.columns if c in ('成交量','volume')), df.columns[-1])
                    price = float(row[close_col])
                    volume = int(row[vol_col]) if vol_col else 0
                    changed = last_price is None or abs(price - last_price) > 0.001
                    last_price = price
                    d = {'time': datetime.now().strftime('%H:%M:%S'), 'price': round(price, 2),
                         'volume': volume, 'changed': changed,
                         'cum_volume': int(df[vol_col].sum()) if vol_col else 0}
                    yield f"data: {json.dumps(d, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'no_data'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            count += 1
            time.sleep(5)

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
```

需要确保 `Response`, `stream_with_context`, `time` 已在文件顶部导入。app.py 已有 `from flask import Response, stream_with_context` (line 23)，但需要添加 `import time`。

Commit: `feat: add SSE intraday stream endpoint for real-time price updates`

---

### Task 2: 前端 — 多面板分屏 + 键盘快捷键 + 主题 + 响应式

**Files:**
- Modify: `chart-core.js` — multi-pane grid support
- Modify: `app.js` — keyboard shortcuts + theme toggle
- Modify: `chart.html` — layout buttons + theme button
- Modify: `style.css` — CSS variables + grid + responsive

### chart-core.js — 末尾追加

```javascript
// ====== 多面板分屏支持 ======
let panes = {};          // { 'pane0': {chart, candle, volume}, ... }
let activePaneCount = 1;

export function setPaneLayout(count) {
    const main = document.getElementById('chartMain');
    if (!main) return;

    // 保存当前实例
    if (Object.keys(panes).length > 0) {
        destroyAllPanes();
    }

    activePaneCount = count;
    const cols = count <= 2 ? count : 2;
    const rows = count <= 2 ? 1 : Math.ceil(count / 2);

    main.style.display = 'grid';
    main.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    main.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
    main.style.gap = '0';

    // 清理旧内容
    main.innerHTML = '';

    for (let i = 0; i < count; i++) {
        const cell = document.createElement('div');
        cell.id = `chartPane${i}`;
        cell.style.minWidth = '0';
        cell.style.minHeight = '0';
        cell.style.position = 'relative';
        main.appendChild(cell);

        const chart = LightweightCharts.createChart(cell, {
            layout: { background: { color: '#131722' }, textColor: '#d1d4dc' },
            grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
            crosshair: { mode: 1 },
            rightPriceScale: { borderColor: '#2a2e39' },
            timeScale: { borderColor: '#2a2e39', timeVisible: true },
            width: cell.clientWidth,
            height: cell.clientHeight,
        });

        const candle = chart.addCandlestickSeries({
            upColor: '#26a69a', downColor: '#ef5350',
            borderUpColor: '#26a69a', borderDownColor: '#ef5350',
            wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        });

        const volume = chart.addHistogramSeries({
            priceFormat: { type: 'volume' }, priceScaleId: `vol_${i}`,
        });
        chart.priceScale(`vol_${i}`).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

        panes[`pane${i}`] = { chart, candle, volume, element: cell };
    }

    // 重新指定主图表为 pane0
    chart = panes['pane0']?.chart || null;
    candleSeries = panes['pane0']?.candle || null;
    volumeSeries = panes['pane0']?.volume || null;

    // 重新绑定 resize
    window.addEventListener('resize', () => {
        Object.values(panes).forEach(p => {
            p.chart.applyOptions({ width: p.element.clientWidth, height: p.element.clientHeight });
        });
    });
}

export function renderDataToPane(paneIndex, bars) {
    const pane = panes[`pane${paneIndex}`];
    if (!pane) return;
    const candleData = bars.map(b => ({
        time: typeof b.time === 'string' && b.time.length === 10 ? b.time
             : (typeof b.time === 'number' ? b.time : Math.floor(new Date(b.time).getTime() / 1000)),
        open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    const volData = bars.map(b => ({
        time: typeof b.time === 'string' && b.time.length === 10 ? b.time
             : (typeof b.time === 'number' ? b.time : Math.floor(new Date(b.time).getTime() / 1000)),
        value: b.volume, color: b.close >= b.open ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
    }));
    pane.candle.setData(candleData);
    pane.volume.setData(volData);
    pane.chart.timeScale().fitContent();
}

export function destroyAllPanes() {
    Object.values(panes).forEach(p => { try { p.chart.remove(); } catch (e) {} });
    panes = {};
}

export function getActivePaneCount() { return activePaneCount; }

// Override initChart to support multi-pane
const _origInitChart = initChart;
initChart = function(container) {
    // Use setPaneLayout instead for multi-pane
    setPaneLayout(1);
};
```

### app.js — 追加键盘快捷键 + 主题切换

在 `init()` 函数末尾添加:

```javascript
    // ====== 键盘快捷键 ======
    document.addEventListener('keydown', (e) => {
        // 不拦截输入框内的按键
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        switch (e.key) {
            case '1': case '2': case '4':
                // 数字键切换布局
                if (!e.ctrlKey && !e.metaKey) {
                    const count = parseInt(e.key);
                    import('/static/chart/chart-core.js').then(m => {
                        m.setPaneLayout(count);
                        // 重新加载当前标的到所有面板
                        m.renderDataToPane(0, []); // 需要bars数据
                        bus.emit('period:changed', { period: state.period });
                    });
                }
                break;
            case ' ':
                // 空格切换分时/日线
                e.preventDefault();
                if (state.period === 'intra') {
                    bus.emit('period:changed', { period: 'daily' });
                } else {
                    import('/static/chart/chart-data.js').then(m => m.loadIntraData(state.code));
                }
                break;
            case 'Tab':
                // Tab在自选列表中循环切换
                e.preventDefault();
                cycleWatchlistSymbol();
                break;
            case 't':
                // t 切换主题
                if (!e.ctrlKey && !e.metaKey) {
                    toggleTheme();
                }
                break;
        }
    });

    // ====== 主题切换 ======
    const themeBtn = document.getElementById('themeToggle');
    if (themeBtn) {
        themeBtn.addEventListener('click', toggleTheme);
    }
    // 加载保存的主题
    if (localStorage.getItem('chart_theme') === 'light') {
        document.body.classList.add('theme-light');
    }
```

在 `init()` 函数之前添加全局函数:

```javascript
function toggleTheme() {
    const isLight = document.body.classList.toggle('theme-light');
    localStorage.setItem('chart_theme', isLight ? 'light' : 'dark');
}

function cycleWatchlistSymbol() {
    // 通过 watchlist 模块获取下一个标的
    import('/static/chart/watchlist.js').then(({ refreshWatchlist }) => {
        // 简单实现: 发射事件让 watchlist 处理
        bus.emit('watchlist:next');
    });
}
```

### chart.html — 新增布局按钮 + 主题按钮

在 chart-toolbar 中，drawing-tools 之后、period-switcher 之前添加:

```html
        <div class="layout-btns" id="layoutBtns">
            <button class="layout-btn active" data-layout="1" title="单屏">⊞</button>
            <button class="layout-btn" data-layout="2" title="2分屏">⿰</button>
            <button class="layout-btn" data-layout="4" title="4分屏">⿻</button>
        </div>
        <button class="theme-btn" id="themeToggle" title="切换主题">🌓</button>
```

在 `app.js` 的 init() 中为 layout-btns 添加事件:

```javascript
    // 布局切换按钮
    document.getElementById('layoutBtns')?.addEventListener('click', (e) => {
        const btn = e.target.closest('.layout-btn');
        if (!btn) return;
        const count = parseInt(btn.dataset.layout);
        document.querySelectorAll('.layout-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        import('/static/chart/chart-core.js').then(m => {
            m.setPaneLayout(count);
            bus.emit('period:changed', { period: state.period });
        });
    });
```

### style.css — 追加主题变量 + 响应式 + 布局按钮样式

在 style.css 开头（任何规则之前）追加 CSS 变量:

```css
/* ---- 主题变量 ---- */
.chart-page {
    --bg-primary: #131722;
    --bg-secondary: #1e222d;
    --bg-tertiary: #2a2e39;
    --text-primary: #d1d4dc;
    --text-secondary: #787b86;
    --border-color: #2a2e39;
    --accent: #2962ff;
}
body.theme-light .chart-page {
    --bg-primary: #ffffff;
    --bg-secondary: #f5f5f5;
    --bg-tertiary: #e8e8e8;
    --text-primary: #1a1a2e;
    --text-secondary: #666666;
    --border-color: #e0e0e0;
    --accent: #1976d2;
}
```

然后将现有颜色值替换为 CSS 变量。关键替换:
- `#131722` → `var(--bg-primary)`
- `#1e222d` → `var(--bg-secondary)`
- `#2a2e39` → `var(--bg-tertiary)`
- `#d1d4dc` → `var(--text-primary)`
- `#787b86` → `var(--text-secondary)`

在末尾追加:

```css
/* ---- 布局按钮 ---- */
.layout-btns { display: flex; gap: 2px; margin-left: 4px; }
.layout-btn {
    padding: 4px 8px; font-size: 13px; color: #787b86;
    background: transparent; border: none; cursor: pointer; border-radius: 3px;
}
.layout-btn:hover { color: #d1d4dc; background: #363a45; }
.layout-btn.active { color: #fff; background: #2962ff; }

/* ---- 主题按钮 ---- */
.theme-btn {
    padding: 4px 8px; font-size: 14px; color: #787b86;
    background: transparent; border: none; cursor: pointer; border-radius: 3px;
    margin-left: 4px;
}
.theme-btn:hover { color: #d1d4dc; }

/* ---- 增强响应式 ---- */
@media (max-width: 1024px) {
    .chart-sidebar-right, .drawing-tools { display: none; }
    .chart-sidebar-left { width: 160px; min-width: 160px; }
}
@media (max-width: 768px) {
    .chart-sidebar-left { width: 120px; min-width: 120px; font-size: 10px; }
    .chart-toolbar { gap: 6px; padding: 6px 10px; }
    .period-btn { padding: 3px 8px; font-size: 10px; }
    .layout-btns, .theme-btn { display: none; }
}
@media (max-width: 480px) {
    .chart-sidebar-left { display: none; }
    .chart-toolbar { flex-wrap: wrap; }
}
```

### 所有集成修改的合并 commit:

```bash
git add ashare_review/web/templates/chart.html ashare_review/web/static/chart/chart-core.js ashare_review/web/static/chart/app.js ashare_review/web/static/chart/style.css
git commit -m "feat: add multi-pane split view + keyboard shortcuts + theme toggle + responsive"
```
