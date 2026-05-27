/**
 * codex.js — Enoch Alpine stores + global components
 * Loaded deferred; runs after Alpine initialises.
 */

document.addEventListener('alpine:init', () => {

    // ── Toast store ───────────────────────────────────────────────
    // Usage: Alpine.store('toast').show('Night well spent.')
    // HTMX posts fire it automatically via X-Enoch-Toast response header.
    Alpine.store('toast', {
        visible: false,
        message: '',
        kind: 'info',   // 'info' | 'success' | 'danger'
        _timer: null,

        show(msg, kind = 'info', duration = 3800) {
            this.message = msg;
            this.kind    = kind;
            this.visible = true;
            clearTimeout(this._timer);
            this._timer = setTimeout(() => { this.visible = false; }, duration);
        },

        dismiss() {
            this.visible = false;
            clearTimeout(this._timer);
        },
    });

});

// ── HTMX global hooks ─────────────────────────────────────────────

// Fire toast from X-Enoch-Toast response header
document.addEventListener('htmx:afterRequest', (e) => {
    const msg = e.detail.xhr.getResponseHeader('X-Enoch-Toast');
    if (!msg) return;
    const kind = e.detail.xhr.getResponseHeader('X-Enoch-Toast-Kind') || 'info';
    Alpine.store('toast').show(msg, kind);
});

// Attach CSRF token to every HTMX request
document.addEventListener('htmx:configRequest', (e) => {
    const token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (token) e.detail.headers['X-CSRF-Token'] = token;
});

// Clickable table rows (data-href)
document.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-href]');
    if (row) window.location.href = row.dataset.href;
});
