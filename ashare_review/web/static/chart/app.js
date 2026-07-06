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

    // 初始化左右面板 (Phase 2)
    import('/static/chart/watchlist.js').then(m => m.initWatchlist());
    import('/static/chart/toolbar.js').then(m => m.initToolbar());

    // 初始化画线 + 策略事件 (Phase 3)
    import('/static/chart/chart-drawings.js').then(m => m.initDrawings());
    import('/static/chart/strategy-overlay.js').then(m => m.initStrategyOverlay());

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
        if (period === 'intra') {
            import('/static/chart/chart-data.js').then(m => m.loadIntraData(state.code));
            return;
        }
        bus.emit('period:changed', { period });
    });

    console.log('[chart] Initialization complete');
}

// 画线工具栏
const dtEl = document.getElementById('drawingTools');
if (dtEl) {
    dtEl.addEventListener('click', (e) => {
        const btn = e.target.closest('.dt-btn');
        if (!btn) return;
        const tool = btn.dataset.tool;

        if (btn.id === 'dtUndo') {
            import('/static/chart/chart-drawings.js').then(m => m.undoDrawing());
            return;
        }
        if (btn.id === 'dtClear') {
            import('/static/chart/chart-drawings.js').then(m => m.clearAllDrawings());
            return;
        }

        // tool selection
        document.querySelectorAll('.dt-btn[data-tool]').forEach(b => b.classList.remove('active'));
        if (tool) btn.classList.add('active');
        bus.emit('drawing:selected', { tool: tool || null });

        // Also switch back to kline mode when selecting a drawing tool
        if (tool) {
            import('/static/chart/chart-core.js').then(m => m.switchToKlineMode());
        }
    });
}

document.addEventListener('DOMContentLoaded', init);
