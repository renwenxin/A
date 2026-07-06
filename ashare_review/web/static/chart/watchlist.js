/**
 * 自选列表 — 搜索/分组/点击切换
 */

import { bus, state } from '/static/chart/app.js';
import { loadData } from '/static/chart/chart-data.js';

let allItems = [];
let currentGroup = '全部';

export async function initWatchlist() {
    const container = document.getElementById('sidebarLeft');
    if (!container) return;

    container.innerHTML = `
        <div class="panel-header">📋 自选股</div>
        <div class="wl-search"><input type="text" id="wlSearch" placeholder="🔍 搜索代码/名称..."></div>
        <div class="wl-groups" id="wlGroups">
            <span class="wl-group-tag active" data-group="全部">全部</span>
        </div>
        <div class="wl-list" id="wlList"></div>
        <div class="wl-add-bar">
            <input type="text" id="wlAddCode" placeholder="输入6位代码" maxlength="6">
            <button id="wlAddBtn">+ 添加</button>
        </div>
    `;

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
            <div class="wl-item-change ${chg >= 0 ? 'up' : 'down'}">${chg != null ? (chg > 0 ? '+' : '') + chg.toFixed(2) + '%' : '--'}</div>
            <button class="wl-item-del" data-id="${i.id}" title="删除">×</button>
        </div>`;
    }).join('');

    el.querySelectorAll('.wl-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.classList.contains('wl-item-del')) return;
            const code = item.dataset.code;
            const name = item.querySelector('.wl-item-name')?.textContent || '';
            bus.emit('symbol:changed', { code, name });
        });
    });

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
