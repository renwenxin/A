/**
 * 策略事件标记 — 涨停/炸板/龙虎榜
 */

import { bus, state } from '/static/chart/app.js';

export function initStrategyOverlay() {
    bus.on('symbol:changed', () => loadEvents());
    bus.on('toolbar:update', () => loadEvents());
}

async function loadEvents() {
    if (!state.code) return;
    try {
        const resp = await fetch(`/api/chart/events?code=${state.code}`);
        const data = await resp.json();
        const markers = (data.events || []).filter(e => e.date);
        renderMarkers(markers);
    } catch (e) {
        console.error('[strategy] Events load failed:', e);
    }
}

function renderMarkers(markers) {
    const container = document.getElementById('strategyMarkers');
    if (!container) return;

    const typeMap = {
        'limit_up': { icon: '🔴', label: '涨停', color: '#ef5350' },
        'lhb': { icon: '🐉', label: '龙虎榜', color: '#ff9800' },
        'broken': { icon: '💥', label: '炸板', color: '#9c27b0' },
    };

    if (markers.length === 0) {
        container.innerHTML = '<div class="tb-section-title">🎯 策略事件</div><p class="placeholder-text">暂无事件</p>';
        return;
    }

    container.innerHTML = '<div class="tb-section-title">🎯 策略事件 (' + markers.length + ')</div>' +
        markers.map(m => {
            const t = typeMap[m.type] || { icon: '📌', label: m.type, color: '#787b86' };
            return `<div class="strategy-event" style="border-left:2px solid ${t.color}">
                <span>${t.icon}</span>
                <span class="se-label">${t.label}</span>
                <span class="se-date">${m.date}</span>
                ${m.detail ? `<span class="se-detail">${m.detail}</span>` : ''}
            </div>`;
        }).join('');
}
