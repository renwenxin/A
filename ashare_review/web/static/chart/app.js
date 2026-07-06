/**
 * 看盘界面 — 主入口
 * 职责：EventBus、模块初始化、全局协调
 */

// ====== EventBus ======
export const bus = {
    _handlers: {},
    on(event, fn) {
        (this._handlers[event] = this._handlers[event] || []).push(fn);
    },
    off(event, fn) {
        const arr = this._handlers[event];
        if (arr) this._handlers[event] = arr.filter(f => f !== fn);
    },
    emit(event, data) {
        (this._handlers[event] || []).forEach(fn => {
            try { fn(data); } catch (e) { console.error(`[EventBus] ${event}`, e); }
        });
    },
};

// ====== 全局状态 ======
export const state = {
    code: null,
    name: '',
    period: 'daily',
};

// ====== 初始化入口 ======
async function init() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code') || document.getElementById('toolbarCode')?.textContent?.trim() || '000001';

    state.code = code;

    console.log('[chart] Initializing with code:', code);

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

document.addEventListener('DOMContentLoaded', init);
