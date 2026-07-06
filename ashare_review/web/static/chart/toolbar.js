/**
 * 右侧面板 — 盘口数据 + 指标开关
 */

import { bus, state } from '/static/chart/app.js';
import { getChart } from '/static/chart/chart-core.js';

export function initToolbar() {
    const container = document.getElementById('sidebarRight');
    if (!container) return;

    container.innerHTML = `
        <div class="panel-header">🔧 工具 & 盘口</div>
        <div class="tb-section">
            <div class="tb-section-title">📊 盘口数据</div>
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
                <label class="tb-toggle"><input type="checkbox" id="indMA" checked> MA均线</label>
                <label class="tb-toggle"><input type="checkbox" id="indMACD" checked> MACD</label>
                <label class="tb-toggle"><input type="checkbox" id="indRSI"> RSI (14)</label>
                <label class="tb-toggle"><input type="checkbox" id="indBOLL"> 布林带 (20,2)</label>
            </div>
        </div>
    `;

    document.getElementById('indMA').addEventListener('change', (e) => {
        import('/static/chart/chart-indicators.js').then(({ addMASeries, removeMASeries }) => {
            const chart = getChart(); if (!chart) return;
            e.target.checked ? addMASeries(chart) : removeMASeries(chart);
        }).catch(() => {});
    });
    document.getElementById('indMACD').addEventListener('change', (e) => {
        import('/static/chart/chart-indicators.js').then(({ addMACDPane, removeMACDPane }) => {
            const chart = getChart(); if (!chart) return;
            e.target.checked ? bus.emit('period:changed', { period: state.period }) : removeMACDPane(chart);
        }).catch(() => {});
    });
    document.getElementById('indRSI').addEventListener('change', (e) => {
        import('/static/chart/chart-indicators.js').then(({ addRSI, removeRSI }) => {
            const chart = getChart(); if (!chart) return;
            e.target.checked ? addRSI(chart) : removeRSI(chart);
        }).catch(() => {});
    });
    document.getElementById('indBOLL').addEventListener('change', (e) => {
        import('/static/chart/chart-indicators.js').then(({ addBollinger, removeBollinger }) => {
            const chart = getChart(); if (!chart) return;
            e.target.checked ? addBollinger(chart) : removeBollinger(chart);
        }).catch(() => {});
    });

    bus.on('toolbar:update', () => refreshDepth());
    bus.on('symbol:changed', () => refreshDepth());
    refreshDepth();
}

async function refreshDepth() {
    if (!state.code) return;
    try {
        const resp = await fetch(`/api/chart/depth?code=${state.code}`);
        const d = await resp.json();
        if (d.error) return;
        document.getElementById('depthPrice').textContent = d.price?.toFixed(2) || '--';
        const chg = document.getElementById('depthChange');
        const pct = d.change_pct || 0;
        chg.textContent = `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
        chg.style.color = pct >= 0 ? '#26a69a' : '#ef5350';
        document.getElementById('depthOpen').textContent = d.open?.toFixed(2) || '--';
        document.getElementById('depthHigh').textContent = d.high?.toFixed(2) || '--';
        document.getElementById('depthLow').textContent = d.low?.toFixed(2) || '--';
        document.getElementById('depthVol').textContent = d.volume ? (d.volume / 10000).toFixed(1) + '万手' : '--';
        document.getElementById('depthAmt').textContent = d.amount ? (d.amount / 1e8).toFixed(2) + '亿' : '--';
    } catch (e) {
        console.error('[toolbar] Depth fetch failed:', e);
    }
}
