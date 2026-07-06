/**
 * 图表核心 — lightweight-charts 主图/副图创建和更新
 */

import { bus, state } from '/static/chart/app.js';

const LC = window.LightweightCharts;
if (!LC) throw new Error('lightweight-charts not loaded. Check CDN script in chart.html.');

let chart = null;
let candleSeries = null;
let volumeSeries = null;

export function initChart(container) {
    if (chart) {
        container.innerHTML = '';
        chart = null;
        candleSeries = null;
        volumeSeries = null;
    }

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
            mode: 1,
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

    candleSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
    });

    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });

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

export function renderData(bars) {
    if (!candleSeries) {
        console.error('[chart-core] candleSeries not initialized');
        return;
    }
    if (!bars || bars.length === 0) {
        console.warn('[chart-core] No bars to render');
        return;
    }

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

    chart.timeScale().fitContent();

    // 如果有多面板也同步渲染
    if (activePaneCount > 1) {
        renderDataToAllPanes(bars);
    }

    console.log(`[chart-core] Rendered ${bars.length} bars`);
}

function formatTime(t) {
    if (typeof t === 'number') return t;
    if (typeof t === 'string') {
        if (t.length === 10) return t;
        if (t.length >= 19) {
            const d = new Date(t);
            if (!isNaN(d.getTime())) return Math.floor(d.getTime() / 1000);
        }
        return t;
    }
    return t;
}

export function destroyChart() {
    if (chart) { chart.remove(); chart = null; }
}

export function getChart() { return chart; }
export function getCandleSeries() { return candleSeries; }
export function getVolumeSeries() { return volumeSeries; }

// ====== 分时图模式 ======
let intraLineSeries = null;

export function switchToIntraMode() {
    if (!chart) return;
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
    switchToIntraMode();
    if (!intraLineSeries) return;
    const data = points.map(p => ({
        time: formatTime(p.time),
        value: p.price,
    }));
    intraLineSeries.setData(data);
    if (chart) chart.timeScale().fitContent();
}

// ====== 多面板分屏支持 ======
let panes = {};
let activePaneCount = 1;

export function setPaneLayout(count) {
    const main = document.getElementById('chartMain');
    if (!main) return;

    if (Object.keys(panes).length > 0) {
        destroyAllPanes();
    }

    activePaneCount = count;
    const cols = count <= 2 ? count : 2;
    const rows = count <= 2 ? 1 : Math.ceil(count / 2);
    main.innerHTML = '';
    main.style.display = 'grid';
    main.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    main.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
    main.style.gap = '2px';

    for (let i = 0; i < count; i++) {
        const cell = document.createElement('div');
        cell.id = `chartPane${i}`;
        cell.style.cssText = 'min-width:0;min-height:0;position:relative;overflow:hidden;';
        main.appendChild(cell);

        const ch = LightweightCharts.createChart(cell, {
            layout: { background: { color: '#131722' }, textColor: '#d1d4dc' },
            grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
            crosshair: { mode: 1 },
            rightPriceScale: { borderColor: '#2a2e39' },
            timeScale: { borderColor: '#2a2e39', timeVisible: count <= 2 },
            width: cell.clientWidth,
            height: cell.clientHeight,
        });

        const candle = ch.addCandlestickSeries({
            upColor: '#26a69a', downColor: '#ef5350',
            borderUpColor: '#26a69a', borderDownColor: '#ef5350',
            wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        });
        const volScaleId = `vol_pane${i}`;
        const vol = ch.addHistogramSeries({
            priceFormat: { type: 'volume' }, priceScaleId: volScaleId,
        });
        ch.priceScale(volScaleId).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

        panes[`pane${i}`] = { chart: ch, candle, volume, element: cell };
    }

    // 更新主引用到 pane0
    chart = panes['pane0']?.chart || null;
    candleSeries = panes['pane0']?.candle || null;
    volumeSeries = panes['pane0']?.volume || null;

    // resize handler
    const onResize = () => {
        Object.values(panes).forEach(p => {
            try {
                p.chart.applyOptions({ width: p.element.clientWidth, height: p.element.clientHeight });
            } catch (e) {}
        });
    };
    window.addEventListener('resize', onResize);
}

export function renderDataToAllPanes(bars) {
    Object.keys(panes).forEach((key, i) => {
        const pane = panes[key];
        if (!pane || !bars || bars.length === 0) return;
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
    });
}

export function destroyAllPanes() {
    Object.values(panes).forEach(p => { try { p.chart.remove(); } catch (e) {} });
    panes = {};
}

export function getActivePaneCount() { return activePaneCount; }
