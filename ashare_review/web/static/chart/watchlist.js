/**
 * 自选列表 — 全市场标的 + 分组/搜索/点击切换
 */

import { bus, state } from '/static/chart/app.js';

let allItems = [];
let currentGroup = '';
let offset = 0;
const LIMIT = 200;
let hasMore = true;

const MARKET_GROUPS = ['沪市主板', '深市主板', '创业板', '科创板', '北交所'];

export async function initWatchlist() {
    const container = document.getElementById('sidebarLeft');
    if (!container) return;

    container.innerHTML = `
        <div class="panel-header">📋 自选股 <span id="wlCount" style="font-weight:400;color:#787b86;font-size:10px;"></span></div>
        <div class="wl-search"><input type="text" id="wlSearch" placeholder="🔍 搜索 9336 只标的..."></div>
        <div class="wl-groups" id="wlGroups">
            <span class="wl-group-tag active" data-group="">全部</span>
            ${MARKET_GROUPS.map(g => `<span class="wl-group-tag" data-group="${g}">${g.replace('主板','')}</span>`).join('')}
        </div>
        <div class="wl-list" id="wlList"></div>
    `;

    // 搜索 — 服务端搜索
    let searchTimer;
    document.getElementById('wlSearch').addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            offset = 0;
            allItems = [];
            hasMore = true;
            loadMore();
        }, 200);
    });

    // 分组切换
    document.getElementById('wlGroups').addEventListener('click', (e) => {
        if (e.target.classList.contains('wl-group-tag')) {
            document.querySelectorAll('.wl-group-tag').forEach(t => t.classList.remove('active'));
            e.target.classList.add('active');
            currentGroup = e.target.dataset.group;
            offset = 0;
            allItems = [];
            hasMore = true;
            document.getElementById('wlList').innerHTML = '';
            loadMore();
        }
    });

    // 滚动加载更多
    document.getElementById('wlList').addEventListener('scroll', () => {
        const el = document.getElementById('wlList');
        if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40 && hasMore) {
            loadMore();
        }
    });

    await loadMore();
}

async function loadMore() {
    if (!hasMore) return;

    const search = document.getElementById('wlSearch')?.value?.trim() || '';
    let url = `/api/watchlist?limit=${LIMIT}&offset=${offset}`;
    if (currentGroup) url += `&group=${encodeURIComponent(currentGroup)}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;

    try {
        const resp = await fetch(url);
        const data = await resp.json();
        const items = data.items || [];

        if (offset === 0) allItems = [];
        allItems.push(...items);
        offset += items.length;
        hasMore = items.length >= LIMIT;

        document.getElementById('wlCount').textContent =
            data.total > LIMIT ? `(${allItems.length}/${data.total})` : `(${data.total})`;

        renderList();
    } catch (e) {
        console.error('[watchlist] Load failed:', e);
    }
}

function renderList() {
    const el = document.getElementById('wlList');
    if (!el) return;

    el.innerHTML = allItems.map(i => {
        const chg = i.change_pct || 0;
        const price = i.price || 0;
        const isActive = i.code === state.code;
        const name = i.name || i.code;
        return `<div class="wl-item ${isActive ? 'active' : ''}" data-code="${i.code}" data-id="${i.id}">
            <div class="wl-item-main">
                <span class="wl-item-name">${name}</span>
                <span class="wl-item-code">${i.code}</span>
            </div>
            <div class="wl-item-price">${price ? price.toFixed(2) : '--'}</div>
            <div class="wl-item-change ${chg >= 0 ? 'up' : 'down'}">${chg != null ? (chg > 0 ? '+' : '') + chg.toFixed(2) + '%' : '--'}</div>
        </div>`;
    }).join('');

    if (hasMore && allItems.length > 0) {
        el.innerHTML += '<div class="wl-loading">加载更多...</div>';
    }

    // 点击切换标的
    el.querySelectorAll('.wl-item').forEach(item => {
        item.addEventListener('click', () => {
            const code = item.dataset.code;
            const name = item.querySelector('.wl-item-name')?.textContent || '';
            bus.emit('symbol:changed', { code, name });
        });
    });
}
