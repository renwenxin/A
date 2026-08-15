# Phase 3 — 画线工具 + 分时图 + 策略集成 实现计划

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** 新增交互式画线工具（趋势线/水平线/矩形/斐波那契）+ localStorage持久化 + 分时图模式 + 策略买卖点标记 + 选股联动

**Architecture:** 画线使用 HTML Canvas 叠加层实现（比 lightweight-charts primitives 更灵活）；分时图用 LineSeries 模拟；策略事件后端查询涨停/龙虎榜数据返回标注点

**Tech Stack:** Canvas 2D overlay + localStorage + Flask existing data sources

---

## File Structure

```
# 新增
ashare_review/web/static/chart/
├── chart-drawings.js   # NEW — 画线工具 + localStorage
└── strategy-overlay.js # NEW — 策略事件标记 + 选股联动

# 修改
ashare_review/web/
├── app.py                      # + /api/chart/events + /api/chart/intra
├── templates/chart.html        # 新增分时图按钮 + Canvas overlay
├── templates/screening.html    # 筛选结果增加"图表"链接
└── static/chart/
    ├── style.css               # 画线工具栏样式
    └── app.js                  # init 时加载新模块
```

---

### Task 1: 后端 — 策略事件 API + 分时图 API

**Files:** Modify `ashare_review/web/app.py`

在现有路由之后添加:

```python
@app.route('/api/chart/events')
def api_chart_events():
    """策略事件标注 — 涨停日/龙虎榜/买卖点"""
    code = request.args.get('code', '')
    if not code or len(code) != 6:
        return jsonify({'error': 'code is required'}), 400

    events = []
    try:
        # 1. 涨停日标记（最近60天）
        limit_ups = ak_fetcher.get_limit_up_pool()
        if limit_ups:
            for lu in limit_ups:
                if lu.code == code:
                    events.append({
                        'type': 'limit_up',
                        'date': str(lu.trade_date) if hasattr(lu, 'trade_date') else '',
                        'label': '涨停',
                        'detail': f'连板:{lu.consecutive}' if hasattr(lu, 'consecutive') else '',
                    })

        # 2. 龙虎榜标记（最近30天）
        from datetime import date, timedelta
        for i in range(30):
            d = date.today() - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            try:
                lhb_list = ak_fetcher.get_lhb(d.strftime('%Y%m%d'))
                for l in lhb_list:
                    if l.code == code:
                        events.append({
                            'type': 'lhb',
                            'date': d.isoformat(),
                            'label': '龙虎榜',
                            'detail': f'净买:{l.net_amount/10000:.0f}万' if l.net_amount else '',
                        })
            except Exception:
                continue

        # 3. 炸板标记
        try:
            auctions = ak_fetcher.get_auction_data()
            if auctions:
                for a in auctions:
                    if a.code == code and getattr(a, 'broken', False):
                        events.append({
                            'type': 'broken',
                            'date': str(getattr(a, 'date', '')),
                            'label': '炸板',
                            'detail': '',
                        })
        except Exception:
            pass

    except Exception as e:
        pass  # events API errors shouldn't block chart loading

    return jsonify({'code': code, 'total': len(events), 'events': events})


@app.route('/api/chart/intra')
def api_chart_intra():
    """当日分时图数据"""
    code = request.args.get('code', '')
    if not code or len(code) != 6:
        return jsonify({'error': 'code is required'}), 400
    try:
        import akshare as ak
        from ..data.akshare_fetcher import _clean_proxy
        _clean_proxy()
        symbol = code
        df = ak.stock_zh_a_hist_min_em(symbol=symbol, period='5', adjust='qfq',
            start_date=datetime.now().strftime('%Y%m%d'),
            end_date=datetime.now().strftime('%Y%m%d'))
        if df is None or df.empty:
            return jsonify({'error': 'No intraday data', 'code': code}), 404

        # 标准化列名
        col_map = {'时间': 'time', 'time': 'time', '开盘': 'open', 'open': 'open',
                   '最高': 'high', 'high': 'high', '最低': 'low', 'low': 'low',
                   '收盘': 'close', 'close': 'close', '成交量': 'volume', 'volume': 'volume'}
        df = df.rename(columns={c: col_map[c] for c in df.columns if c in col_map})

        points = []
        prev_close = None
        for _, row in df.iterrows():
            t = row['time']
            ts = t.isoformat() if hasattr(t, 'isoformat') else str(t)
            price = float(row['close'])
            vol = int(row.get('volume', 0))
            if prev_close is None:
                prev_close = price
            points.append({
                'time': ts,
                'price': round(price, 2),
                'volume': vol,
                'avg_price': round(price, 2),
            })

        return jsonify({'code': code, 'total': len(points), 'points': points,
                        'prev_close': round(prev_close or 0, 2)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500
```

Commit: `feat: add /api/chart/events + /api/chart/intra endpoints for Phase 3`

---

### Task 2: 前端 — chart-drawings.js 画线工具

**Files:** Create `ashare_review/web/static/chart/chart-drawings.js`

```javascript
/**
 * 画线工具 — 趋势线/水平线/矩形/斐波那契 + localStorage持久化
 * 使用 HTML Canvas 叠加层实现
 */

import { bus, state } from '/static/chart/app.js';
import { getChart, getCandleSeries } from '/static/chart/chart-core.js';

const STORAGE_KEY_PREFIX = 'chart_drawings_';
let currentTool = null;    // 'trend' | 'horizontal' | 'rectangle' | 'fibonacci' | null
let isDrawing = false;
let startPoint = null;     // {x, y, time, price}
let canvas = null;
let ctx = null;
let drawings = [];          // [{id, type, points:[], color}]
let drawingIdCounter = 0;

// ====== Init ======
export function initDrawings() {
    const chart = getChart();
    if (!chart) return;

    // 创建 Canvas 叠加层
    const chartMain = document.getElementById('chartMain');
    if (!chartMain) return;

    // 移除旧 canvas
    const old = chartMain.querySelector('.drawing-canvas');
    if (old) old.remove();

    canvas = document.createElement('canvas');
    canvas.className = 'drawing-canvas';
    canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:10;';
    chartMain.style.position = 'relative';
    chartMain.appendChild(canvas);

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // 加载持久化画线
    loadDrawings();

    // 监听画线工具选择
    bus.on('drawing:selected', ({ tool }) => {
        currentTool = tool;
        canvas.style.pointerEvents = tool ? 'auto' : 'none';
        if (!tool) { isDrawing = false; startPoint = null; }
    });

    // 鼠标/触摸事件
    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('touchstart', onTouchStart);
    canvas.addEventListener('touchmove', onTouchMove);
    canvas.addEventListener('touchend', onMouseUp);

    // 订阅标的切换 → 重新加载该标的画线
    bus.on('symbol:changed', () => {
        saveDrawings();
        loadDrawings();
        redrawAll();
    });
}

function resizeCanvas() {
    const chartMain = document.getElementById('chartMain');
    if (!canvas || !chartMain) return;
    canvas.width = chartMain.clientWidth;
    canvas.height = chartMain.clientHeight;
    redrawAll();
}

// ====== 鼠标事件 ======
function getChartCoords(e) {
    const chart = getChart();
    if (!chart) return null;
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX || e.touches?.[0]?.clientX || 0) - rect.left;
    const y = (e.clientY || e.touches?.[0]?.clientY || 0) - rect.top;
    try {
        const time = chart.timeScale().coordinateToTime(x);
        const price = chart.priceScale('right').coordinateToPrice(y);
        return { x, y, time, price };
    } catch (err) { return null; }
}

function onMouseDown(e) {
    if (!currentTool) return;
    const coords = getChartCoords(e);
    if (!coords) return;
    isDrawing = true;
    startPoint = coords;
}

function onMouseMove(e) {
    if (!isDrawing || !startPoint || !currentTool) return;
    const coords = getChartCoords(e);
    if (!coords) return;
    redrawAll();

    ctx.strokeStyle = '#ff9800';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);

    if (currentTool === 'trend') {
        ctx.beginPath(); ctx.moveTo(startPoint.x, startPoint.y);
        ctx.lineTo(coords.x, coords.y); ctx.stroke();
    } else if (currentTool === 'horizontal') {
        ctx.beginPath(); ctx.moveTo(0, coords.y);
        ctx.lineTo(canvas.width, coords.y); ctx.stroke();
    } else if (currentTool === 'rectangle') {
        ctx.strokeRect(startPoint.x, startPoint.y,
            coords.x - startPoint.x, coords.y - startPoint.y);
    } else if (currentTool === 'fibonacci') {
        const dy = coords.y - startPoint.y;
        [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].forEach(level => {
            const y = startPoint.y + dy * level;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
            ctx.fillStyle = '#ff9800'; ctx.font = '10px monospace';
            ctx.fillText((level * 100).toFixed(1) + '%', 4, y - 2);
        });
    }
}

function onMouseUp(e) {
    if (!isDrawing || !startPoint || !currentTool) return;
    const coords = getChartCoords(e);
    if (!coords) { isDrawing = false; return; }

    // 保存画线
    const drawing = {
        id: ++drawingIdCounter,
        type: currentTool,
        points: [
            { time: startPoint.time, price: startPoint.price },
            { time: coords.time, price: coords.price },
        ],
        color: '#ff9800',
    };
    drawings.push(drawing);
    saveDrawings();

    isDrawing = false;
    startPoint = null;
    ctx.setLineDash([]);
    redrawAll();
}

function onTouchStart(e) { onMouseDown(e); }
function onTouchMove(e) { e.preventDefault(); onMouseMove(e); }

// ====== 渲染 ======
function redrawAll() {
    if (!ctx || !canvas) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const chart = getChart();
    if (!chart) return;

    drawings.forEach(d => {
        ctx.strokeStyle = d.color;
        ctx.lineWidth = d.type === 'horizontal' || d.type === 'fibonacci' ? 1 : 2;
        ctx.setLineDash(d.type === 'horizontal' ? [6, 3] : []);

        const p1 = d.points[0], p2 = d.points[1];
        let x1, y1, x2, y2;
        try {
            x1 = chart.timeScale().timeToCoordinate(p1.time);
            y1 = chart.priceScale('right').priceToCoordinate(p1.price);
            x2 = chart.timeScale().timeToCoordinate(p2.time);
            y2 = chart.priceScale('right').priceToCoordinate(p2.price);
        } catch (e) { return; }

        if (d.type === 'trend') {
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        } else if (d.type === 'horizontal') {
            ctx.beginPath(); ctx.moveTo(0, y2); ctx.lineTo(canvas.width, y2); ctx.stroke();
        } else if (d.type === 'rectangle') {
            ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2),
                Math.abs(x2 - x1), Math.abs(y2 - y1));
        } else if (d.type === 'fibonacci') {
            const dy = y2 - y1;
            [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].forEach(level => {
                const yy = y1 + dy * level;
                ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(canvas.width, yy); ctx.stroke();
                ctx.fillStyle = d.color; ctx.font = '10px monospace';
                ctx.fillText((level * 100).toFixed(1) + '%', 4, yy - 2);
            });
        }
    });
    ctx.setLineDash([]);
}

// ====== 持久化 ======
function saveDrawings() {
    if (!state.code) return;
    try {
        localStorage.setItem(STORAGE_KEY_PREFIX + state.code, JSON.stringify(drawings));
    } catch (e) {}
}

function loadDrawings() {
    if (!state.code) { drawings = []; return; }
    try {
        const data = localStorage.getItem(STORAGE_KEY_PREFIX + state.code);
        drawings = data ? JSON.parse(data) : [];
        drawingIdCounter = drawings.reduce((max, d) => Math.max(max, d.id || 0), 0);
    } catch (e) { drawings = []; }
}

// 清除当前标的所有画线
export function clearAllDrawings() {
    drawings = [];
    saveDrawings();
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}

// 删除最后一条画线
export function undoDrawing() {
    drawings.pop();
    saveDrawings();
    redrawAll();
}
```

Commit: `feat: add chart-drawings.js — trend/horizontal/rectangle/Fibonacci with localStorage`

---

### Task 3: 前端 — strategy-overlay.js 策略事件标记

**Files:** Create `ashare_review/web/static/chart/strategy-overlay.js`

```javascript
/**
 * 策略事件标记 — 涨停/炸板/龙虎榜 图标标注
 */

import { bus, state } from '/static/chart/app.js';

let markers = [];

export function initStrategyOverlay() {
    bus.on('symbol:changed', () => loadEvents());
    // 数据加载完成后也刷新
    const origOn = bus.on.bind(bus);
    bus.on('toolbar:update', () => loadEvents());
}

async function loadEvents() {
    if (!state.code) return;
    try {
        const resp = await fetch(`/api/chart/events?code=${state.code}`);
        const data = await resp.json();
        markers = (data.events || []).filter(e => e.date);
        renderMarkers();
    } catch (e) {
        console.error('[strategy] Events load failed:', e);
    }
}

function renderMarkers() {
    const container = document.getElementById('strategyMarkers');
    if (!container) return;

    const typeMap = {
        'limit_up': { icon: '🔴', label: '涨停', color: '#ef5350' },
        'lhb': { icon: '🐉', label: '龙虎榜', color: '#ff9800' },
        'broken': { icon: '💥', label: '炸板', color: '#9c27b0' },
    };

    container.innerHTML = markers.length > 0
        ? markers.map(m => {
            const t = typeMap[m.type] || { icon: '📌', label: m.type, color: '#787b86' };
            return `<div class="strategy-event" style="border-left:2px solid ${t.color}">
                <span>${t.icon}</span>
                <span class="se-label">${t.label}</span>
                <span class="se-date">${m.date}</span>
                ${m.detail ? `<span class="se-detail">${m.detail}</span>` : ''}
            </div>`;
        }).join('')
        : '<p class="placeholder-text">暂无策略事件</p>';
}
```

Commit: `feat: add strategy-overlay.js — limit-up/LHB/broken event markers`

---

### Task 4: 前端 — 分时图模式

**Files:** Modify chart-core.js (新增 `renderIntraChart`), chart-data.js (新增 `loadIntraData`), chart.html (新增分时按钮)

**chart-core.js — 末尾追加:**

```javascript
// ====== 分时图模式 ======
let intraLineSeries = null;

export function switchToIntraMode() {
    if (!chart) return;
    // 隐藏K线和成交量
    if (candleSeries) candleSeries.applyOptions({ visible: false });
    if (volumeSeries) volumeSeries.applyOptions({ visible: false });

    if (!intraLineSeries) {
        intraLineSeries = chart.addLineSeries({
            color: '#2962ff',
            lineWidth: 1,
            priceScaleId: 'right',
        });
    } else {
        intraLineSeries.applyOptions({ visible: true });
    }
    chart.timeScale().fitContent();
}

export function switchToKlineMode() {
    if (candleSeries) candleSeries.applyOptions({ visible: true });
    if (volumeSeries) volumeSeries.applyOptions({ visible: true });
    if (intraLineSeries) intraLineSeries.applyOptions({ visible: false });
}

export function renderIntraData(points) {
    if (!intraLineSeries) return;
    const data = points.map(p => ({
        time: formatTime(p.time),
        value: p.price,
    }));
    intraLineSeries.setData(data);
    if (chart) chart.timeScale().fitContent();
}
```

**chart-data.js — 末尾追加:**

```javascript
// ====== 分时图数据加载 ======
export async function loadIntraData(code) {
    try {
        const resp = await fetch(`/api/chart/intra?code=${code}`);
        const data = await resp.json();
        if (data.points && data.points.length > 0) {
            const { switchToIntraMode, renderIntraData } = await import('/static/chart/chart-core.js');
            switchToIntraMode();
            renderIntraData(data.points);
            bus.emit('toolbar:update', {
                code,
                name: state.name || code,
                price: data.points[data.points.length - 1].price,
                change: data.prev_close
                    ? ((data.points[data.points.length - 1].price - data.prev_close) / data.prev_close * 100)
                    : 0,
            });
        }
    } catch (e) {
        console.error('[chart-data] Intra load failed:', e);
    }
}
```

**chart.html — 在 period-switcher 中添加分时按钮:**

在日线按钮之后，`</div>` 之前添加:
```html
            <button class="period-btn" data-period="intra">分时</button>
```

**app.js — 在 period-switcher click handler 中处理分时:**

在 `init()` 函数的 period click handler 中，`bus.emit('period:changed', { period })` 之前添加:
```javascript
        if (period === 'intra') {
            import('/static/chart/chart-data.js').then(m => m.loadIntraData(state.code));
            return; // skip normal period change
        }
```

Commit: `feat: add intraday chart mode — line chart for current day's price movement`

---

### Task 5: 集成 — 更新 chart.html + app.js + style.css + screening

**chart.html — 左侧新增画线工具栏:**

在 `chart-toolbar` 中，period-switcher 之前添加:
```html
        <div class="drawing-tools" id="drawingTools">
            <button class="dt-btn" data-tool="trend" title="趋势线">↗</button>
            <button class="dt-btn" data-tool="horizontal" title="水平线">—</button>
            <button class="dt-btn" data-tool="rectangle" title="矩形">▭</button>
            <button class="dt-btn" data-tool="fibonacci" title="斐波那契">%</button>
            <button class="dt-btn" data-tool="" title="取消">✕</button>
            <button class="dt-btn" id="dtUndo" title="撤销">↩</button>
            <button class="dt-btn" id="dtClear" title="清除全部">🗑</button>
        </div>
```

**chart.html — 右侧面板新增策略事件区:**

在 `sidebarRight` 的 `</aside>` 之前添加:
```html
            <div class="tb-section" id="strategyMarkers">
                <div class="tb-section-title">🎯 策略事件</div>
                <p class="placeholder-text">加载中...</p>
            </div>
```

**style.css — 追加画线工具栏样式:**

```css
/* ---- 画线工具栏 ---- */
.drawing-tools {
    display: flex; gap: 2px; margin-left: 12px;
    background: #2a2e39; border-radius: 4px; padding: 2px;
}
.dt-btn {
    padding: 4px 8px; font-size: 14px; color: #787b86;
    background: transparent; border: none; cursor: pointer; border-radius: 3px;
}
.dt-btn:hover { color: #d1d4dc; background: #363a45; }
.dt-btn.active { color: #ff9800; background: rgba(255,152,0,0.15); }

/* ---- 策略事件 ---- */
.strategy-event {
    display: flex; align-items: center; gap: 6px; padding: 4px 0;
    font-size: 10px; padding-left: 8px; margin: 2px 0;
}
.se-label { color: #d1d4dc; font-weight: 500; }
.se-date { color: #787b86; margin-left: auto; font-family: monospace; }
.se-detail { color: #8b949e; font-size: 9px; }
```

**app.js — init 时加载画线和策略模块 + 画线工具栏事件:**

```javascript
    // 初始化策略事件面板
    import('/static/chart/strategy-overlay.js').then(m => m.initStrategyOverlay());

    // 画线工具栏
    document.getElementById('drawingTools').addEventListener('click', (e) => {
        const btn = e.target.closest('.dt-btn');
        if (!btn) return;
        const tool = btn.dataset.tool;
        if (tool === '') {
            // 取消画线
            document.querySelectorAll('.dt-btn').forEach(b => b.classList.remove('active'));
            if (!document.getElementById('dtUndo').contains(btn) && !document.getElementById('dtClear').contains(btn)) {
                bus.emit('drawing:selected', { tool: null });
            }
        } else if (btn.id === 'dtUndo') {
            import('/static/chart/chart-drawings.js').then(m => m.undoDrawing());
        } else if (btn.id === 'dtClear') {
            import('/static/chart/chart-drawings.js').then(m => m.clearAllDrawings());
        } else {
            document.querySelectorAll('.dt-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            bus.emit('drawing:selected', { tool });
        }
    });
```

实际代码需仔细合并到已有 `init()` 函数中。

**screening.html — 筛选结果增加"图表"链接 (仅修改关键行):**

找到筛选结果展示的代码区域，在每行代码后增加:
```html
<a href="/chart?code={{ r.code }}" class="chart-link" title="查看K线图">📈</a>
```

Commit: `feat: integrate drawing tools + strategy events + intraday chart into UI`

---

### Task 6: 端到端验证

```bash
python -c "
from ashare_review.web.app import app
routes = [r.rule for r in app.url_map.iter_rules()]
print('/api/chart/events' in routes, '/api/chart/intra' in routes)
```
→ `True True`

启动: `python -m flask --app ashare_review.web.app run --host 0.0.0.0 --port 5000 --debug`

验证: 打开 `/chart?code=600519`，测试画线工具 + 分时图切换
