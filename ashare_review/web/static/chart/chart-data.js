/**
 * 数据层 — API调用 + IndexedDB缓存 + 周期切换
 */

import { bus, state } from '/static/chart/app.js';
import { renderData, getChart } from '/static/chart/chart-core.js';

const DB_NAME = 'chart_cache';
const DB_VERSION = 1;
const STORE_NAME = 'kline';

function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, { keyPath: 'cacheKey' });
            }
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    });
}

async function getCache(cacheKey) {
    try {
        const db = await openDB();
        return new Promise((resolve) => {
            const tx = db.transaction(STORE_NAME, 'readonly');
            const store = tx.objectStore(STORE_NAME);
            const req = store.get(cacheKey);
            req.onsuccess = () => {
                const record = req.result;
                if (!record) return resolve(null);
                const age = Date.now() - record.cachedAt;
                const ttl = cacheKey.startsWith('daily_') ? 86400000 : 1800000;
                if (age > ttl) {
                    const delTx = db.transaction(STORE_NAME, 'readwrite');
                    delTx.objectStore(STORE_NAME).delete(cacheKey);
                    resolve(null);
                } else {
                    resolve(record.bars);
                }
            };
            req.onerror = () => resolve(null);
        });
    } catch (e) {
        return null;
    }
}

async function setCache(cacheKey, bars) {
    try {
        const db = await openDB();
        return new Promise((resolve) => {
            const tx = db.transaction(STORE_NAME, 'readwrite');
            tx.objectStore(STORE_NAME).put({ cacheKey, bars, cachedAt: Date.now() });
            tx.oncomplete = () => resolve();
            tx.onerror = () => resolve();
        });
    } catch (e) {}
}

async function fetchKline(code, period) {
    const url = `/api/chart/kline?code=${encodeURIComponent(code)}&period=${encodeURIComponent(period)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
    }
    return await resp.json();
}

export async function loadData(code, period) {
    if (!code) return;
    const cacheKey = `${period}_${code}`;
    console.log(`[chart-data] Loading ${code} ${period}...`);

    let bars = await getCache(cacheKey);

    if (!bars || bars.length === 0) {
        console.log(`[chart-data] Cache miss, fetching from API...`);
        const data = await fetchKline(code, period);
        bars = data.bars || [];
        if (data.name) {
            state.name = data.name;
            bus.emit('toolbar:update', { code, name: data.name });
        }
        if (bars.length > 0) {
            await setCache(cacheKey, bars);
            console.log(`[chart-data] Cached ${bars.length} bars`);
        }
    } else {
        console.log(`[chart-data] Cache hit: ${bars.length} bars`);
    }

    if (bars.length > 0) {
        renderData(bars);

        // 叠加技术指标
        const { addIndicatorSeries, removeIndicatorSeries } = await import('/static/chart/chart-indicators.js');
        const chart = getChart();
        if (chart) {
            removeIndicatorSeries(chart);
            addIndicatorSeries(chart, bars);
        }

        // 更新工具栏
        const lastBar = bars[bars.length - 1];
        const prevClose = bars.length >= 2 ? bars[bars.length - 2].close : lastBar.close;
        const change = prevClose ? ((lastBar.close - prevClose) / prevClose * 100) : 0;
        bus.emit('toolbar:update', {
            code,
            name: state.name || code,
            price: lastBar.close,
            change,
        });
    } else {
        console.warn(`[chart-data] No data for ${code} ${period}`);
    }
}

// 工具栏更新监听
bus.on('toolbar:update', ({ code, name, price, change }) => {
    if (code != null) document.getElementById('toolbarCode').textContent = code;
    if (name != null) document.getElementById('toolbarName').textContent = name;
    if (price != null) document.getElementById('toolbarPrice').textContent = price.toFixed(2);
    if (change != null) {
        const el = document.getElementById('toolbarChange');
        el.textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        el.className = `stock-change ${change >= 0 ? 'up' : 'down'}`;
        document.getElementById('toolbarPrice').style.color = change >= 0 ? '#26a69a' : '#ef5350';
    }
});
