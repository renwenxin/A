# Phase 2 — 自选列表 + 盘口 + 面板 实现计划

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** 将 Phase 1 的左右占位面板替换为真实的自选列表和盘口/工具面板，新增 RSI/布林带指标。

**Architecture:** 后端新增 watchlist SQLite 表 + CRUD API + 盘口 API；前端新增 watchlist.js + toolbar.js 两个 ES 模块；chart.html 替换占位符为真实面板容器。

**Tech Stack:** Flask + SQLite + Vanilla JS ES modules + lightweight-charts

---

## File Structure

```
# 新增
ashare_review/web/static/chart/
├── watchlist.js    # NEW — 自选列表：搜索/分组/点击切换
└── toolbar.js      # NEW — 右侧面板：盘口五档 + 指标开关

# 修改
ashare_review/web/
├── app.py                      # + /api/watchlist CRUD + /api/chart/depth
├── templates/chart.html        # 替换左右占位为真实面板
└── static/chart/
    ├── style.css               # 新增面板元素样式
    └── chart-indicators.js     # + RSI + 布林带
```

---

### Task 1: 后端 — watchlist CRUD API

**Files:** Modify `ashare_review/web/app.py`

**代码:**

在 `/api/chart/kline` 路由之后添加:

```python
# ---- 自选股 CRUD ----

def _get_watchlist_db():
    return sqlite3.connect(os.path.join(os.path.dirname(__file__), '..', 'watchlist.db'))

def _init_watchlist():
    db = _get_watchlist_db()
    db.execute('''CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        name TEXT DEFAULT '',
        group_name TEXT DEFAULT '默认',
        sort_order INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(code)
    )''')
    db.commit()
    db.close()

# 添加默认数据（首次初始化）
_init_watchlist()
db = _get_watchlist_db()
if db.execute('SELECT COUNT(*) FROM watchlist').fetchone()[0] == 0:
    defaults = [
        ('000001', '上证指数', '指数'),
        ('600519', '贵州茅台', '默认'),
        ('300750', '宁德时代', '默认'),
        ('000858', '五粮液', '默认'),
    ]
    for i, (code, name, grp) in enumerate(defaults):
        db.execute('INSERT OR IGNORE INTO watchlist (code, name, group_name, sort_order) VALUES (?,?,?,?)',
                   (code, name, grp, i))
    db.commit()
db.close()


@app.route('/api/watchlist')
def api_watchlist():
    """自选列表查询"""
    group = request.args.get('group', '')
    db = _get_watchlist_db()
    if group:
        rows = db.execute('SELECT id, code, name, group_name, sort_order FROM watchlist WHERE group_name=? ORDER BY sort_order', (group,)).fetchall()
    else:
        rows = db.execute('SELECT id, code, name, group_name, sort_order FROM watchlist ORDER BY group_name, sort_order').fetchall()
    db.close()
    items = [{'id': r[0], 'code': r[1], 'name': r[2], 'group': r[3], 'sort_order': r[4]} for r in rows]
    # 附加实时涨跌幅
    try:
        spot_df = ak_fetcher.get_spot_df()
        if spot_df is not None and not spot_df.empty:
            for item in items:
                row = spot_df[spot_df['代码'] == item['code']]
                if not row.empty:
                    item['price'] = float(row.iloc[0].get('最新价', 0))
                    item['change_pct'] = float(row.iloc[0].get('涨跌幅', 0))
    except Exception:
        pass
    return jsonify({'total': len(items), 'items': items})


@app.route('/api/watchlist', methods=['POST'])
def api_watchlist_add():
    """添加自选"""
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    name = body.get('name', '')
    group = body.get('group', '默认')
    if not code or len(code) != 6:
        return jsonify({'error': 'code is required'}), 400
    db = _get_watchlist_db()
    try:
        db.execute('INSERT OR IGNORE INTO watchlist (code, name, group_name) VALUES (?,?,?)', (code, name, group))
        db.commit()
        return jsonify({'ok': True, 'code': code})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/watchlist/<int:item_id>', methods=['DELETE'])
def api_watchlist_delete(item_id):
    """删除自选"""
    db = _get_watchlist_db()
    db.execute('DELETE FROM watchlist WHERE id=?', (item_id,))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/watchlist/<int:item_id>', methods=['PUT'])
def api_watchlist_update(item_id):
    """更新自选（分组/排序）"""
    body = request.get_json(silent=True) or {}
    db = _get_watchlist_db()
    if 'group' in body:
        db.execute('UPDATE watchlist SET group_name=? WHERE id=?', (body['group'], item_id))
    if 'sort_order' in body:
        db.execute('UPDATE watchlist SET sort_order=? WHERE id=?', (body['sort_order'], item_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})
```

需要确保文件顶部已有 `import sqlite3, os`。app.py 第 8-10 行已有 `import sqlite3` 和 `import os`。

Commit: `feat: add /api/watchlist CRUD API for stock watchlist`

---

### Task 2: 后端 — 盘口五档 API

**Files:** Modify `ashare_review/web/app.py`

在 watchlist API 之后添加:

```python
@app.route('/api/chart/depth')
def api_chart_depth():
    """盘口五档数据"""
    code = request.args.get('code', '')
    if not code or len(code) != 6:
        return jsonify({'error': 'code is required'}), 400
    try:
        spot_df = ak_fetcher.get_spot_df()
        if spot_df is None or spot_df.empty:
            return jsonify({'error': 'No depth data available'}), 404
        row = spot_df[spot_df['代码'] == code]
        if row.empty:
            return jsonify({'error': f'No data for {code}'}), 404
        r = row.iloc[0]
        depth = {
            'code': code,
            'price': float(r.get('最新价', 0)),
            'open': float(r.get('今开', 0)),
            'high': float(r.get('最高', 0)),
            'low': float(r.get('最低', 0)),
            'volume': int(r.get('成交量', 0)),
            'amount': float(r.get('成交额', 0)),
            'change_pct': float(r.get('涨跌幅', 0)),
            'change': float(r.get('涨跌额', 0)),
            'turnover': float(r.get('换手率', 0)) if '换手率' in r else 0,
        }
        return jsonify(depth)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

Commit: `feat: add /api/chart/depth endpoint for real-time market depth`

---

### Task 3: 前端 — watchlist.js 自选列表面板

**Files:** Create `ashare_review/web/static/chart/watchlist.js`

```javascript
/**
 * 自选列表 — 搜索/分组/点击切换/拖拽排序
 */

import { bus, state } from '/static/chart/app.js';
import { loadData } from '/static/chart/chart-data.js';

let allItems = [];
let currentGroup = '全部';

export async function initWatchlist() {
    const container = document.getElementById('sidebarLeft');
    if (!container) return;

    // 构建面板 HTML
    container.innerHTML = `
        <div class="panel-header">📋 自选股</div>
        <div class="wl-search"><input type="text" id="wlSearch" placeholder="🔍 搜索代码/名称..."></div>
        <div class="wl-groups" id="wlGroups">
            <span class="wl-group-tag active" data-group="全部">全部</span>
            <span class="wl-group-tag" data-group="默认">默认</span>
        </div>
        <div class="wl-list" id="wlList"></div>
        <div class="wl-add-bar">
            <input type="text" id="wlAddCode" placeholder="输入6位代码" maxlength="6">
            <button id="wlAddBtn">+ 添加</button>
        </div>
    `;

    // 事件
    document.getElementById('wlSearch').addEventListener('input', renderList);
    document.getElementById('wlGroups').addEventListener('click', (e) => {
        if (e.target.classList.contains('wl-group-tag')) {
            document.querySelectorAll('.wl-group-tag').forEach(t => t.classList.remove('active'));
            e.target.classList.add('active');
            currentGroup = e.target.dataset.group;
            renderList();
        }
    });
    document.getElementById('wlAddBtn').addEventListener('click', addStock);
    document.getElementById('wlAddCode').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addStock();
    });

    // 初始加载
    await refreshWatchlist();
}

export async function refreshWatchlist() {
    try {
        const resp = await fetch('/api/watchlist');
        const data = await resp.json();
        allItems = data.items || [];
        updateGroupTags();
        renderList();
    } catch (e) {
        console.error('[watchlist] Load failed:', e);
    }
}

function updateGroupTags() {
    const groups = new Set(allItems.map(i => i.group || '默认'));
    const el = document.getElementById('wlGroups');
    if (!el) return;
    el.innerHTML = '<span class="wl-group-tag active" data-group="全部">全部</span>';
    groups.forEach(g => {
        el.innerHTML += `<span class="wl-group-tag" data-group="${g}">${g}</span>`;
    });
}

function renderList() {
    const search = (document.getElementById('wlSearch')?.value || '').toLowerCase();
    const el = document.getElementById('wlList');
    if (!el) return;

    let filtered = allItems;
    if (currentGroup !== '全部') {
        filtered = filtered.filter(i => (i.group || '默认') === currentGroup);
    }
    if (search) {
        filtered = filtered.filter(i => i.code.includes(search) || (i.name || '').toLowerCase().includes(search));
    }

    el.innerHTML = filtered.map(i => {
        const chg = i.change_pct || 0;
        const price = i.price || 0;
        const isActive = i.code === state.code;
        return `<div class="wl-item ${isActive ? 'active' : ''}" data-code="${i.code}" data-id="${i.id}">
            <div class="wl-item-main">
                <span class="wl-item-name">${i.name || i.code}</span>
                <span class="wl-item-code">${i.code}</span>
            </div>
            <div class="wl-item-price">${price ? price.toFixed(2) : '--'}</div>
            <div class="wl-item-change ${chg >= 0 ? 'up' : 'down'}">${chg ? (chg > 0 ? '+' : '') + chg.toFixed(2) + '%' : '--'}</div>
            <button class="wl-item-del" data-id="${i.id}" title="删除">×</button>
        </div>`;
    }).join('');

    // 点击切换标的
    el.querySelectorAll('.wl-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.classList.contains('wl-item-del')) return;
            const code = item.dataset.code;
            const name = item.querySelector('.wl-item-name')?.textContent || '';
            bus.emit('symbol:changed', { code, name });
        });
    });

    // 删除按钮
    el.querySelectorAll('.wl-item-del').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const id = btn.dataset.id;
            await fetch(`/api/watchlist/${id}`, { method: 'DELETE' });
            await refreshWatchlist();
        });
    });
}

async function addStock() {
    const input = document.getElementById('wlAddCode');
    const code = input.value.trim();
    if (code.length !== 6 || !/^\d{6}$/.test(code)) return;
    await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, group: currentGroup === '全部' ? '默认' : currentGroup }),
    });
    input.value = '';
    await refreshWatchlist();
}
```

Commit: `feat: add watchlist.js — interactive stock watchlist panel with search/groups`

---

### Task 4: 前端 — toolbar.js 盘口 + 指标开关

**Files:** Create `ashare_review/web/static/chart/toolbar.js`

```javascript
/**
 * 右侧面板 — 盘口五档 + 指标开关
 */

import { bus, state } from '/static/chart/app.js';
import { getChart } from '/static/chart/chart-core.js';

let indicatorState = { rsi: false, boll: false };

export function initToolbar() {
    const container = document.getElementById('sidebarRight');
    if (!container) return;

    container.innerHTML = `
        <div class="panel-header">🔧 工具 & 盘口</div>
        <div class="tb-section">
            <div class="tb-section-title">📊 五档盘口</div>
            <div class="tb-depth" id="tbDepth">
                <div class="tb-depth-row"><span class="label">最新价</span><span class="value" id="depthPrice">--</span></div>
                <div class="tb-depth-row"><span class="label">涨跌幅</span><span class="value" id="depthChange">--</span></div>
                <div class="tb-depth-row"><span class="label">今开</span><span class="value" id="depthOpen">--</span></div>
                <div class="tb-depth-row"><span class="label">最高</span><span class="value" id="depthHigh">--</span></div>
                <div class="tb-depth-row"><span class="label">最低</span><span class="value" id="depthLow">--</span></div>
                <div class="tb-depth-row"><span class="label">成交量</span><span class="value" id="depthVol">--</span></div>
                <div class="tb-depth-row"><span class="label">成交额</span><span class="value" id="depthAmt">--</span></div>
            </div>
        </div>
        <div class="tb-section">
            <div class="tb-section-title">📈 技术指标</div>
            <div class="tb-indicators" id="tbIndicators">
                <label class="tb-toggle"><input type="checkbox" id="toggleMA" checked> MA均线</label>
                <label class="tb-toggle"><input type="checkbox" id="toggleMACD" checked> MACD</label>
                <label class="tb-toggle"><input type="checkbox" id="toggleRSI"> RSI (14)</label>
                <label class="tb-toggle"><input type="checkbox" id="toggleBOLL"> 布林带 (20,2)</label>
            </div>
        </div>
    `;

    // 指标开关事件
    document.getElementById('toggleMA').addEventListener('change', (e) => toggleMA(e.target.checked));
    document.getElementById('toggleMACD').addEventListener('change', (e) => toggleMACD(e.target.checked));
    document.getElementById('toggleRSI').addEventListener('change', (e) => toggleRSI(e.target.checked));
    document.getElementById('toggleBOLL').addEventListener('change', (e) => toggleBOLL(e.target.checked));

    // 订阅标的切换 → 更新盘口
    bus.on('toolbar:update', () => refreshDepth());
    bus.on('symbol:changed', () => refreshDepth());
}

async function refreshDepth() {
    if (!state.code) return;
    try {
        const resp = await fetch(`/api/chart/depth?code=${state.code}`);
        const d = await resp.json();
        if (d.error) return;
        document.getElementById('depthPrice').textContent = d.price?.toFixed(2) || '--';
        const chg = document.getElementById('depthChange');
        chg.textContent = d.change_pct != null ? `${d.change_pct > 0 ? '+' : ''}${d.change_pct.toFixed(2)}%` : '--';
        chg.style.color = d.change_pct >= 0 ? '#26a69a' : '#ef5350';
        document.getElementById('depthOpen').textContent = d.open?.toFixed(2) || '--';
        document.getElementById('depthHigh').textContent = d.high?.toFixed(2) || '--';
        document.getElementById('depthLow').textContent = d.low?.toFixed(2) || '--';
        document.getElementById('depthVol').textContent = d.volume ? (d.volume / 10000).toFixed(1) + '万手' : '--';
        document.getElementById('depthAmt').textContent = d.amount ? (d.amount / 1e8).toFixed(2) + '亿' : '--';
    } catch (e) {
        console.error('[toolbar] Depth fetch failed:', e);
    }
}

function toggleMA(on) {
    const chart = getChart();
    if (!chart) return;
    // 动态导入 indicators
    import('/static/chart/chart-indicators.js').then(({ addMASeries, removeMASeries }) => {
        if (on) addMASeries(chart);
        else removeMASeries(chart);
    }).catch(() => {});
}

function toggleMACD(on) {
    const chart = getChart();
    if (!chart) return;
    import('/static/chart/chart-indicators.js').then(({ addMACDPane, removeMACDPane }) => {
        if (on) {
            // MACD needs bars — re-trigger load or store bars
            // For now: toggle is visual, loadData handles full refresh
            bus.emit('period:changed', { period: state.period });
        } else removeMACDPane(chart);
    }).catch(() => {});
}

function toggleRSI(on) {
    const chart = getChart();
    if (!chart) return;
    import('/static/chart/chart-indicators.js').then(({ addRSI, removeRSI }) => {
        if (on) addRSI(chart);
        else removeRSI(chart);
    }).catch(() => {});
}

function toggleBOLL(on) {
    const chart = getChart();
    if (!chart) return;
    import('/static/chart/chart-indicators.js').then(({ addBollinger, removeBollinger }) => {
        if (on) addBollinger(chart);
        else removeBollinger(chart);
    }).catch(() => {});
}
```

Commit: `feat: add toolbar.js — depth panel + indicator toggle switches`

---

### Task 5: 前端 — chart-indicators.js 追加 RSI + 布林带

**Files:** Modify `ashare_review/web/static/chart/chart-indicators.js`

在文件末尾追加:

```javascript
// ====== RSI (14) ======
let rsiSeries = null;

export function addRSI(chart, period = 14) {
    removeRSI(chart);
    rsiSeries = chart.addLineSeries({
        color: '#ffeb3b',
        lineWidth: 1,
        priceScaleId: 'rsi',
    });
    chart.priceScale('rsi').applyOptions({ scaleMargins: { top: 0.92, bottom: 0 } });

    // 从 K 线 series 获取数据计算 RSI
    import('/static/chart/chart-core.js').then(({ getCandleSeries }) => {
        const cs = getCandleSeries();
        if (!cs) return;
        const bars = cs.data();
        const closes = bars.map(b => b.close).filter(Boolean);
        const rsiData = calcRSI(closes, period);
        rsiSeries.setData(bars.slice(bars.length - rsiData.length).map((b, i) => ({
            time: b.time,
            value: rsiData[i],
        })).filter(d => d.value != null));
    });
}

export function removeRSI(chart) {
    if (rsiSeries) { try { chart.removeSeries(rsiSeries); } catch (e) {} rsiSeries = null; }
}

function calcRSI(closes, period = 14) {
    const result = [];
    let gains = 0, losses = 0;
    for (let i = 1; i <= period; i++) {
        const diff = closes[i] - closes[i - 1];
        if (diff >= 0) gains += diff; else losses -= diff;
    }
    let avgGain = gains / period, avgLoss = losses / period;
    result.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
    for (let i = period + 1; i < closes.length; i++) {
        const diff = closes[i] - closes[i - 1];
        avgGain = (avgGain * (period - 1) + (diff > 0 ? diff : 0)) / period;
        avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
        result.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
    }
    return result;
}

// ====== 布林带 (20, 2) ======
let bollUpper = null, bollMiddle = null, bollLower = null;

export function addBollinger(chart, period = 20, stdDev = 2) {
    removeBollinger(chart);
    bollUpper = chart.addLineSeries({ color: 'rgba(255,152,0,0.6)', lineWidth: 1, priceScaleId: 'right' });
    bollMiddle = chart.addLineSeries({ color: 'rgba(255,152,0,0.3)', lineWidth: 1, priceScaleId: 'right' });
    bollLower = chart.addLineSeries({ color: 'rgba(255,152,0,0.6)', lineWidth: 1, priceScaleId: 'right' });

    import('/static/chart/chart-core.js').then(({ getCandleSeries }) => {
        const cs = getCandleSeries();
        if (!cs) return;
        const bars = cs.data();
        const closes = bars.map(b => b.close).filter(Boolean);
        const { upper, middle, lower } = calcBollinger(closes, period, stdDev);
        const offset = bars.length - upper.length;
        bollUpper.setData(upper.map((v, i) => ({ time: bars[offset + i]?.time, value: v })).filter(d => d.time && d.value != null));
        bollMiddle.setData(middle.map((v, i) => ({ time: bars[offset + i]?.time, value: v })).filter(d => d.time && d.value != null));
        bollLower.setData(lower.map((v, i) => ({ time: bars[offset + i]?.time, value: v })).filter(d => d.time && d.value != null));
    });
}

export function removeBollinger(chart) {
    [bollUpper, bollMiddle, bollLower].forEach(s => {
        if (s) { try { chart.removeSeries(s); } catch (e) {} }
    });
    bollUpper = bollMiddle = bollLower = null;
}

function calcBollinger(closes, period = 20, stdDev = 2) {
    const upper = [], middle = [], lower = [];
    for (let i = period - 1; i < closes.length; i++) {
        const slice = closes.slice(i - period + 1, i + 1);
        const avg = slice.reduce((a, b) => a + b, 0) / period;
        const variance = slice.reduce((a, b) => a + (b - avg) ** 2, 0) / period;
        const std = Math.sqrt(variance);
        upper.push(avg + stdDev * std);
        middle.push(avg);
        lower.push(avg - stdDev * std);
    }
    return { upper, middle, lower };
}
```

Commit: `feat: add RSI(14) + Bollinger Bands to chart-indicators.js`

---

### Task 6: 集成 — 更新 chart.html + style.css + app.js

**Files:**
- Modify: `ashare_review/web/templates/chart.html` — 替换占位
- Modify: `ashare_review/web/static/chart/style.css` — 新增面板样式
- Modify: `ashare_review/web/static/chart/app.js` — init 时加载新模块

**Step 1: chart.html — 替换占位面板为真实面板容器**

左侧 `sidebarLeft` 内容替换为空容器（由 watchlist.js 动态填充）:

```html
        <aside class="chart-sidebar-left" id="sidebarLeft"></aside>
```

右侧 `sidebarRight` 同样:

```html
        <aside class="chart-sidebar-right" id="sidebarRight"></aside>
```

**Step 2: style.css — 追加新样式**

在 `ashare_review/web/static/chart/style.css` 末尾追加:

```css
/* ---- 自选列表 ---- */
.wl-search { padding: 6px 8px; }
.wl-search input {
    width: 100%; padding: 5px 8px; background: #131722; border: 1px solid #2a2e39;
    border-radius: 3px; color: #d1d4dc; font-size: 11px; outline: none;
}
.wl-search input:focus { border-color: #2962ff; }
.wl-groups { display: flex; gap: 4px; padding: 4px 8px; flex-wrap: wrap; }
.wl-group-tag {
    padding: 2px 8px; font-size: 10px; color: #787b86; cursor: pointer;
    border-radius: 3px; background: #2a2e39;
}
.wl-group-tag.active { color: #fff; background: #2962ff; }
.wl-list { flex: 1; overflow-y: auto; padding: 4px 0; }
.wl-item {
    display: grid; grid-template-columns: 1fr auto 24px; align-items: center;
    padding: 6px 10px; cursor: pointer; font-size: 11px;
    border-left: 2px solid transparent; gap: 4px;
}
.wl-item:hover { background: rgba(41,98,255,0.08); }
.wl-item.active { border-left-color: #2962ff; background: rgba(41,98,255,0.12); }
.wl-item-main { display: flex; flex-direction: column; min-width: 0; }
.wl-item-name { color: #d1d4dc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wl-item-code { color: #787b86; font-size: 10px; }
.wl-item-price { color: #d1d4dc; text-align: right; }
.wl-item-change { text-align: right; font-size: 10px; }
.wl-item-change.up { color: #26a69a; }
.wl-item-change.down { color: #ef5350; }
.wl-item-del { background: none; border: none; color: #787b86; cursor: pointer; font-size: 14px; padding: 0; opacity: 0; }
.wl-item:hover .wl-item-del { opacity: 1; }
.wl-item-del:hover { color: #ef5350; }
.wl-add-bar { display: flex; gap: 4px; padding: 8px; border-top: 1px solid #2a2e39; }
.wl-add-bar input {
    flex: 1; padding: 5px 8px; background: #131722; border: 1px solid #2a2e39;
    border-radius: 3px; color: #d1d4dc; font-size: 11px; width: 80px;
}
.wl-add-bar button {
    padding: 5px 10px; background: #2962ff; color: #fff; border: none;
    border-radius: 3px; font-size: 11px; cursor: pointer;
}

/* ---- 右侧面板 ---- */
.tb-section { padding: 10px 12px; border-bottom: 1px solid #2a2e39; }
.tb-section-title { font-size: 10px; color: #787b86; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
.tb-depth { font-size: 11px; }
.tb-depth-row { display: flex; justify-content: space-between; padding: 3px 0; }
.tb-depth-row .label { color: #787b86; }
.tb-depth-row .value { color: #d1d4dc; font-weight: 500; font-family: monospace; }
.tb-toggle { display: flex; align-items: center; gap: 6px; padding: 4px 0; font-size: 11px; color: #d1d4dc; cursor: pointer; }
.tb-toggle input { accent-color: #2962ff; }
```

**Step 3: app.js — init 时加载 watchlist 和 toolbar**

在 `app.js` 的 `init()` 函数中，在初始化图表之前添加:

```javascript
    // 初始化左右面板
    import('/static/chart/watchlist.js').then(m => m.initWatchlist());
    import('/static/chart/toolbar.js').then(m => m.initToolbar());
```

完整替换 `init()` 函数:

```javascript
async function init() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code') || document.getElementById('toolbarCode')?.textContent?.trim() || '000001';
    state.code = code;
    console.log('[chart] Initializing with code:', code);

    // 初始化左右面板 (Phase 2)
    import('/static/chart/watchlist.js').then(m => m.initWatchlist());
    import('/static/chart/toolbar.js').then(m => m.initToolbar());

    const [{ initChart }, { loadData }] = await Promise.all([
        import('/static/chart/chart-core.js'),
        import('/static/chart/chart-data.js'),
    ]);
    initChart(document.getElementById('chartMain'));
    await loadData(code, state.period);

    bus.on('period:changed', async ({ period }) => {
        state.period = period;
        await loadData(state.code, period);
    });
    bus.on('symbol:changed', async ({ code, name }) => {
        state.code = code;
        state.name = name || '';
        await loadData(code, state.period);
    });
    document.getElementById('periodSwitcher').addEventListener('click', (e) => {
        const btn = e.target.closest('.period-btn');
        if (!btn) return;
        const period = btn.dataset.period;
        document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        bus.emit('period:changed', { period });
    });
    console.log('[chart] Initialization complete');
}
```

Commit: `feat: integrate watchlist + toolbar panels; add RSI/Bollinger indicators`
