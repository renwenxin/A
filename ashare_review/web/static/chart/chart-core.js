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
