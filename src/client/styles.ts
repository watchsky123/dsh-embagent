/**
 * 工作台的样式，一次注入。
 *
 * 全部类名带 `hwv-` 前缀，避免与宿主主题的类名互撞。颜色尽量引用宿主主题
 * 变量（`--dsh-*`），拿不到时退回本插件的一套深色值——面板在浅色主题下也
 * 不能变成一块看不懂的白。
 *
 * @module dsh-hw-velxio/client/styles
 */

const CSS = `
.hwv-root {
  --hwv-bg: var(--dsh-color-bg, #14161a);
  --hwv-panel: var(--dsh-color-bg-elevated, #1b1e24);
  --hwv-panel-2: var(--dsh-color-bg-sunken, #101216);
  --hwv-line: var(--dsh-color-border, #2b3038);
  --hwv-text: var(--dsh-color-text, #d7dbe2);
  --hwv-dim: var(--dsh-color-text-secondary, #8b93a1);
  --hwv-accent: var(--dsh-color-accent, #4c9aff);
  --hwv-ok: #3fb950;
  --hwv-warn: #d29922;
  --hwv-err: #f85149;
  --hwv-est: #a371f7;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--hwv-bg);
  color: var(--hwv-text);
  font-size: 12px;
  overflow: hidden;
}

.hwv-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--hwv-line);
  background: var(--hwv-panel);
  flex: 0 0 auto;
  flex-wrap: wrap;
}
.hwv-title { font-weight: 600; letter-spacing: .3px; }
.hwv-spacer { flex: 1 1 auto; }
.hwv-chip {
  padding: 2px 7px; border-radius: 10px; font-size: 11px;
  border: 1px solid var(--hwv-line); color: var(--hwv-dim);
  white-space: nowrap;
}
.hwv-chip.ok { color: var(--hwv-ok); border-color: color-mix(in srgb, var(--hwv-ok) 45%, transparent); }
.hwv-chip.warn { color: var(--hwv-warn); border-color: color-mix(in srgb, var(--hwv-warn) 45%, transparent); }
.hwv-chip.err { color: var(--hwv-err); border-color: color-mix(in srgb, var(--hwv-err) 45%, transparent); }
.hwv-chip.est { color: var(--hwv-est); border-color: color-mix(in srgb, var(--hwv-est) 45%, transparent); }

.hwv-btn {
  background: var(--hwv-panel-2); color: var(--hwv-text);
  border: 1px solid var(--hwv-line); border-radius: 6px;
  padding: 4px 9px; font-size: 12px; cursor: pointer; line-height: 1.4;
}
.hwv-btn:hover:not(:disabled) { border-color: var(--hwv-accent); }
.hwv-btn:disabled { opacity: .45; cursor: not-allowed; }
.hwv-btn.primary { border-color: var(--hwv-accent); color: var(--hwv-accent); }
.hwv-btn.danger { border-color: var(--hwv-err); color: var(--hwv-err); }
.hwv-btn.on { background: color-mix(in srgb, var(--hwv-accent) 18%, transparent); border-color: var(--hwv-accent); }

.hwv-select {
  background: var(--hwv-panel-2); color: var(--hwv-text);
  border: 1px solid var(--hwv-line); border-radius: 6px; padding: 4px 6px; font-size: 12px;
}

.hwv-main { display: flex; flex: 1 1 auto; min-height: 0; }
.hwv-panes { display: flex; flex: 1 1 auto; min-width: 0; min-height: 0; gap: 1px; background: var(--hwv-line); }
.hwv-pane { position: relative; flex: 1 1 0; min-width: 0; min-height: 0; background: var(--hwv-panel-2); display: flex; flex-direction: column; }
.hwv-pane.hidden { display: none; }
.hwv-pane-head {
  display: flex; align-items: center; gap: 6px; padding: 4px 8px;
  border-bottom: 1px solid var(--hwv-line); color: var(--hwv-dim);
  font-size: 11px; flex: 0 0 auto;
}
.hwv-canvas-wrap { position: relative; flex: 1 1 auto; min-height: 0; }
.hwv-canvas { display: block; width: 100%; height: 100%; touch-action: none; }

.hwv-inspector {
  width: 268px; flex: 0 0 268px; border-left: 1px solid var(--hwv-line);
  background: var(--hwv-panel); overflow: auto; padding: 8px;
}
.hwv-inspector h4 { margin: 2px 0 8px; font-size: 12px; color: var(--hwv-dim); font-weight: 600; }
.hwv-kv { display: grid; grid-template-columns: 78px 1fr; gap: 3px 8px; font-size: 12px; }
.hwv-kv dt { color: var(--hwv-dim); }
.hwv-kv dd { margin: 0; word-break: break-word; }
.hwv-src { font-size: 11px; }
.hwv-src.datasheet, .hwv-src.measured { color: var(--hwv-ok); }
.hwv-src.derived { color: var(--hwv-accent); }
.hwv-src.estimated { color: var(--hwv-est); }

.hwv-input {
  width: 100%; box-sizing: border-box; background: var(--hwv-panel-2);
  color: var(--hwv-text); border: 1px solid var(--hwv-line); border-radius: 5px;
  padding: 3px 6px; font-size: 12px;
}

.hwv-bottom { flex: 0 0 auto; border-top: 1px solid var(--hwv-line); background: var(--hwv-panel); display: flex; flex-direction: column; max-height: 42%; }
.hwv-tabs { display: flex; gap: 2px; padding: 4px 8px 0; flex-wrap: wrap; }
.hwv-tab {
  padding: 4px 10px; border: 1px solid transparent; border-bottom: none;
  border-radius: 6px 6px 0 0; background: transparent; color: var(--hwv-dim);
  cursor: pointer; font-size: 12px;
}
.hwv-tab.active { background: var(--hwv-panel-2); color: var(--hwv-text); border-color: var(--hwv-line); }
.hwv-tab .count { margin-left: 5px; font-size: 10px; opacity: .8; }
.hwv-tab .count.err { color: var(--hwv-err); }
.hwv-tab .count.warn { color: var(--hwv-warn); }

.hwv-body { overflow: auto; padding: 8px 10px; min-height: 90px; }
.hwv-row { display: flex; gap: 8px; padding: 3px 0; border-bottom: 1px dashed color-mix(in srgb, var(--hwv-line) 60%, transparent); align-items: baseline; }
.hwv-row:last-child { border-bottom: none; }
.hwv-row .mark { width: 14px; flex: 0 0 14px; text-align: center; }
.hwv-row.err .mark { color: var(--hwv-err); }
.hwv-row.warn .mark { color: var(--hwv-warn); }
.hwv-row.ok .mark { color: var(--hwv-ok); }
.hwv-row.info .mark { color: var(--hwv-dim); }
.hwv-row .msg { flex: 1 1 auto; }
.hwv-row .refs { color: var(--hwv-accent); cursor: pointer; white-space: nowrap; }
.hwv-dim { color: var(--hwv-dim); }
.hwv-mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; }

.hwv-banner {
  padding: 6px 10px; border-bottom: 1px solid var(--hwv-line);
  background: color-mix(in srgb, var(--hwv-err) 14%, var(--hwv-panel));
  color: var(--hwv-text); display: flex; gap: 8px; align-items: center;
}
.hwv-banner.info { background: color-mix(in srgb, var(--hwv-accent) 12%, var(--hwv-panel)); }

.hwv-empty { padding: 22px; text-align: center; color: var(--hwv-dim); line-height: 1.9; }
.hwv-empty strong { color: var(--hwv-text); display: block; font-size: 14px; margin-bottom: 6px; }

.hwv-legend { display: flex; gap: 10px; flex-wrap: wrap; padding: 4px 8px; color: var(--hwv-dim); font-size: 11px; border-top: 1px solid var(--hwv-line); }
.hwv-legend i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }

.hwv-progress { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: color-mix(in srgb, var(--hwv-panel-2) 70%, transparent); color: var(--hwv-dim); }

.hwv-split { display: flex; gap: 1px; background: var(--hwv-line); flex: 1 1 auto; min-height: 0; }
.hwv-split > * { flex: 1 1 0; min-width: 0; min-height: 0; }
`

let injected = false

/** 注入一次样式表（多次调用无副作用）。 */
export function ensureStyles(): void {
  if (injected) return
  injected = true
  const doc = globalThis.document
  if (doc === undefined) return
  const style = doc.createElement('style')
  style.setAttribute('data-hw-velxio', '')
  style.textContent = CSS
  doc.head.appendChild(style)
}
