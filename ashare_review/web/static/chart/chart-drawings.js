/**
 * 画线工具 — 趋势线/水平线/矩形/斐波那契 + localStorage持久化
 */

import { bus, state } from '/static/chart/app.js';
import { getChart } from '/static/chart/chart-core.js';

const STORAGE_KEY_PREFIX = 'chart_drawings_';
let currentTool = null;
let isDrawing = false;
let startPoint = null;
let canvas = null;
let ctx = null;
let drawings = [];
let drawingIdCounter = 0;

export function initDrawings() {
    const chart = getChart();
    if (!chart) return;

    const chartMain = document.getElementById('chartMain');
    if (!chartMain) return;

    const old = chartMain.querySelector('.drawing-canvas');
    if (old) old.remove();

    canvas = document.createElement('canvas');
    canvas.className = 'drawing-canvas';
    canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:10;';
    chartMain.style.position = 'relative';
    chartMain.appendChild(canvas);

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    chart.timeScale().subscribeVisibleTimeRangeChange(() => redrawAll());

    loadDrawings();

    bus.on('drawing:selected', ({ tool }) => {
        currentTool = tool;
        canvas.style.pointerEvents = tool ? 'auto' : 'none';
        if (!tool) { isDrawing = false; startPoint = null; }
    });

    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);

    bus.on('symbol:changed', () => {
        saveDrawings();
        loadDrawings();
        setTimeout(() => redrawAll(), 100);
    });

    // 初次渲染延时
    setTimeout(() => redrawAll(), 500);
}

function resizeCanvas() {
    const chartMain = document.getElementById('chartMain');
    if (!canvas || !chartMain) return;
    canvas.width = chartMain.clientWidth;
    canvas.height = chartMain.clientHeight;
    redrawAll();
}

function getChartCoords(e) {
    const chart = getChart();
    if (!chart) return null;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
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
    if (!ctx) return;

    ctx.strokeStyle = '#ff9800';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);

    if (currentTool === 'trend') {
        ctx.beginPath(); ctx.moveTo(startPoint.x, startPoint.y);
        ctx.lineTo(coords.x, coords.y); ctx.stroke();
    } else if (currentTool === 'horizontal') {
        ctx.beginPath(); ctx.moveTo(0, coords.y);
        ctx.lineTo(canvas.width, coords.y); ctx.stroke();
        ctx.fillStyle = '#ff9800'; ctx.font = '10px monospace';
        ctx.fillText(coords.price?.toFixed(2) || '', 4, coords.y - 2);
    } else if (currentTool === 'rectangle') {
        ctx.strokeRect(startPoint.x, startPoint.y,
            coords.x - startPoint.x, coords.y - startPoint.y);
    } else if (currentTool === 'fibonacci') {
        const dy = coords.y - startPoint.y;
        [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].forEach(level => {
            const y = startPoint.y + dy * level;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
            const priceAtLevel = startPoint.price + (coords.price - startPoint.price) * level;
            ctx.fillStyle = '#ff9800'; ctx.font = '10px monospace';
            ctx.fillText((level * 100).toFixed(1) + '% ' + (priceAtLevel?.toFixed(2) || ''), 4, y - 2);
        });
    }
}

function onMouseUp(e) {
    if (!isDrawing || !startPoint || !currentTool) return;
    const coords = getChartCoords(e);
    if (!coords) { isDrawing = false; return; }

    drawings.push({
        id: ++drawingIdCounter,
        type: currentTool,
        points: [
            { time: startPoint.time, price: startPoint.price },
            { time: coords.time, price: coords.price },
        ],
        color: '#ff9800',
    });
    saveDrawings();
    isDrawing = false;
    startPoint = null;
    ctx.setLineDash([]);
    redrawAll();
}

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

        if (x1 == null || y1 == null || x2 == null || y2 == null) return;

        if (d.type === 'trend') {
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        } else if (d.type === 'horizontal') {
            ctx.beginPath(); ctx.moveTo(0, y2); ctx.lineTo(canvas.width, y2); ctx.stroke();
            ctx.fillStyle = d.color; ctx.font = '10px monospace';
            ctx.fillText(p2.price?.toFixed(2) || '', 4, y2 - 2);
        } else if (d.type === 'rectangle') {
            ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2),
                Math.abs(x2 - x1), Math.abs(y2 - y1));
        } else if (d.type === 'fibonacci') {
            const dy = y2 - y1;
            [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].forEach(level => {
                const yy = y1 + dy * level;
                ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(canvas.width, yy); ctx.stroke();
                const pl = p1.price + (p2.price - p1.price) * level;
                ctx.fillStyle = d.color; ctx.font = '10px monospace';
                ctx.fillText((level * 100).toFixed(1) + '% ' + (pl?.toFixed(2) || ''), 4, yy - 2);
            });
        }
    });
    ctx.setLineDash([]);
}

function saveDrawings() {
    if (!state.code) return;
    try { localStorage.setItem(STORAGE_KEY_PREFIX + state.code, JSON.stringify(drawings)); } catch (e) {}
}

function loadDrawings() {
    if (!state.code) { drawings = []; return; }
    try {
        const data = localStorage.getItem(STORAGE_KEY_PREFIX + state.code);
        drawings = data ? JSON.parse(data) : [];
        drawingIdCounter = drawings.reduce((max, d) => Math.max(max, d.id || 0), 0);
    } catch (e) { drawings = []; }
}

export function clearAllDrawings() {
    drawings = [];
    saveDrawings();
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}

export function undoDrawing() {
    drawings.pop();
    saveDrawings();
    redrawAll();
}
