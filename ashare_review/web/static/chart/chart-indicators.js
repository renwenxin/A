/**
 * 技术指标 — MA/MACD 计算 + lightweight-charts 叠加
 */

import { getChart, getCandleSeries } from '/static/chart/chart-core.js';

const LC = window.LightweightCharts;

let maLines = {};
let macdSeries = { dif: null, dea: null, bar: null };

const MA_PERIODS = [5, 10, 20, 60, 89];
const MA_COLORS = { 5: '#f9a825', 10: '#ff9800', 20: '#e91e63', 60: '#00bcd4', 89: '#7c4dff' };
const FAST = 12, SLOW = 26, SIGNAL = 9;

export function addMASeries(chart, periods = MA_PERIODS) {
    periods.forEach(p => {
        if (maLines[`ma${p}`]) return;
        maLines[`ma${p}`] = chart.addLineSeries({
            color: MA_COLORS[p] || '#ffffff',
            lineWidth: 1,
            priceScaleId: 'right',
        });
    });
}

export function removeMASeries(chart) {
    Object.values(maLines).forEach(s => { try { chart.removeSeries(s); } catch (e) {} });
    maLines = {};
}

export function calcMA(bars, period) {
    const result = [];
    for (let i = 0; i < bars.length; i++) {
        if (i < period - 1) { result.push(null); continue; }
        let sum = 0;
        for (let j = i - period + 1; j <= i; j++) sum += bars[j].close;
        result.push(sum / period);
    }
    return result;
}

export function calcMACD(bars) {
    const closes = bars.map(b => b.close);
    const ema = (data, period) => {
        const result = new Array(data.length).fill(null);
        const k = 2 / (period + 1);
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
    const kSignal = 2 / (SIGNAL + 1);

    for (let i = 0; i < dif.length; i++) {
        if (dif[i] == null) {
            dea.push(null); bar.push(null);
        } else if (dea.filter(Boolean).length === 0 && i >= SLOW + SIGNAL - 2) {
            const val = dif.slice(SLOW - 1, i + 1).reduce((a, b) => a + b, 0) / SIGNAL;
            dea.push(val); bar.push((dif[i] - val) * 2);
        } else if (dea[dea.length - 1] != null) {
            const val = dif[i] * kSignal + dea[dea.length - 1] * (1 - kSignal);
            dea.push(val); bar.push((dif[i] - val) * 2);
        } else {
            dea.push(null); bar.push(null);
        }
    }

    return { dif, dea, bar };
}

export function addMACDPane(chart, bars) {
    const { dif, dea, bar: macdBar } = calcMACD(bars);
    removeMACDPane(chart);

    macdSeries.bar = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'macd',
    });
    chart.priceScale('macd').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

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

    macdSeries.dif = chart.addLineSeries({ color: '#f9a825', lineWidth: 1, priceScaleId: 'macd' });
    macdSeries.dif.setData(timeValues.map((t, i) => ({ time: t.time, value: dif[i] || 0 })));

    macdSeries.dea = chart.addLineSeries({ color: '#e91e63', lineWidth: 1, priceScaleId: 'macd' });
    macdSeries.dea.setData(timeValues.map((t, i) => ({ time: t.time, value: dea[i] || 0 })));
}

export function removeMACDPane(chart) {
    [macdSeries.bar, macdSeries.dif, macdSeries.dea].forEach(s => {
        if (s) { try { chart.removeSeries(s); } catch (e) {} }
    });
    macdSeries = { dif: null, dea: null, bar: null };
}

export function calcIndicators(bars) {
    return {
        ma: MA_PERIODS.reduce((acc, p) => { acc[`ma${p}`] = calcMA(bars, p); return acc; }, {}),
        macd: calcMACD(bars),
    };
}

export function addIndicatorSeries(chart, bars) {
    removeIndicatorSeries(chart);
    addMASeries(chart);
    const indicators = calcIndicators(bars);
    const timeValues = bars.map(b => ({
        time: typeof b.time === 'string' && b.time.length === 10 ? b.time
             : (typeof b.time === 'number' ? b.time : Math.floor(new Date(b.time).getTime() / 1000)),
    }));
    MA_PERIODS.forEach(p => {
        const series = maLines[`ma${p}`];
        if (!series) return;
        const data = timeValues.map((t, i) => ({ time: t.time, value: indicators.ma[`ma${p}`][i] }))
            .filter(d => d.value != null);
        series.setData(data);
    });
    addMACDPane(chart, bars);
}

export function removeIndicatorSeries(chart) {
    removeMASeries(chart);
    removeMACDPane(chart);
}

// ====== RSI (14) ======
let rsiSeries = null;

export function addRSI(chart, period = 14) {
    try {
        removeRSI(chart);
        rsiSeries = chart.addLineSeries({
            color: '#ffeb3b',
            lineWidth: 1,
            priceScaleId: 'rsi',
        });
        chart.priceScale('rsi').applyOptions({ scaleMargins: { top: 0.92, bottom: 0 } });

        import('/static/chart/chart-core.js').then(({ getCandleSeries }) => {
            const cs = getCandleSeries();
            if (!cs || !cs.data) return;
            const bars = cs.data();
            const closes = bars.map(b => b.close).filter(v => v != null);
            if (closes.length <= period) return;
            const rsiData = calcRSI(closes, period);
            const offset = bars.length - rsiData.length;
            rsiSeries.setData(rsiData.map((v, i) => ({
                time: bars[offset + i]?.time,
                value: v,
            })).filter(d => d.time != null && d.value != null));
        });
    } catch (e) { console.error('[RSI] Error:', e); }
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
    try {
        removeBollinger(chart);
        bollUpper = chart.addLineSeries({ color: 'rgba(255,152,0,0.6)', lineWidth: 1, priceScaleId: 'right' });
        bollMiddle = chart.addLineSeries({ color: 'rgba(255,152,0,0.3)', lineWidth: 1, priceScaleId: 'right' });
        bollLower = chart.addLineSeries({ color: 'rgba(255,152,0,0.6)', lineWidth: 1, priceScaleId: 'right' });

        import('/static/chart/chart-core.js').then(({ getCandleSeries }) => {
            const cs = getCandleSeries();
            if (!cs || !cs.data) return;
            const bars = cs.data();
            const closes = bars.map(b => b.close).filter(v => v != null);
            if (closes.length < period) return;
            const { upper, middle, lower } = calcBollinger(closes, period, stdDev);
            const offset = bars.length - upper.length;
            bollUpper.setData(upper.map((v, i) => ({ time: bars[offset + i]?.time, value: v })).filter(d => d.time != null && d.value != null));
            bollMiddle.setData(middle.map((v, i) => ({ time: bars[offset + i]?.time, value: v })).filter(d => d.time != null && d.value != null));
            bollLower.setData(lower.map((v, i) => ({ time: bars[offset + i]?.time, value: v })).filter(d => d.time != null && d.value != null));
        });
    } catch (e) { console.error('[BOLL] Error:', e); }
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
