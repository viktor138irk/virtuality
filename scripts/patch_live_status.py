#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

app_dir = app_path.parent
static_dir = app_dir / 'static'
static_dir.mkdir(exist_ok=True)
panel_js_path = static_dir / 'panel.js'
app_css_path = static_dir / 'app.css'
changed = []

text = app_path.read_text()
live_routes = r'''

@app.get("/live/status")
def live_status(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    vms = []
    for vm in parse_virsh_list():
        name = vm.get("name", "")
        state = vm.get("state", "unknown")
        try:
            ip = vm_ip(name) if name else "—"
        except Exception:
            ip = "—"
        css = "ok" if "running" in state else "err" if "shut" in state else "warn"
        vms.append({"id": vm.get("id", "-"), "name": name, "state": state, "state_css": css, "ip": ip if ip and ip != "not available" else "—"})
    return JSONResponse({"ok": True, "generated_at": utc_now(), "vms": vms, "services": {"libvirtd": service_state("libvirtd.service"), "virtlogd": service_state("virtlogd.service"), "cockpit": service_state("cockpit.socket"), "web": service_state("virtuality-web.service")}, "operations": list_operations(5)})


@app.get("/live/operations")
def live_operations(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return JSONResponse({"ok": True, "generated_at": utc_now(), "operations": list_operations(25)})
'''
if '@app.get("/live/status"' not in text:
    marker = '\n\n@app.get("/api/health")'
    text = text.replace(marker, live_routes + marker, 1) if marker in text else text.rstrip() + live_routes + '\n'
    changed.append('live routes added')
else:
    changed.append('live routes already present')
app_path.write_text(text)

panel_js = panel_js_path.read_text() if panel_js_path.exists() else ''
live_js = r'''

/* Virtuality live status + toast layer */
(() => {
  if (window.VirtualityLiveStatus) return;
  window.VirtualityLiveStatus = true;
  function qs(s, r = document) { return r.querySelector(s); }
  function qsa(s, r = document) { return Array.from(r.querySelectorAll(s)); }
  function toast(message, type = 'ok') {
    if (!message) return;
    let wrap = qs('#v-toast-wrap');
    if (!wrap) { wrap = document.createElement('div'); wrap.id = 'v-toast-wrap'; document.body.appendChild(wrap); }
    const item = document.createElement('div');
    item.className = 'v-toast ' + type;
    item.textContent = message;
    wrap.appendChild(item);
    setTimeout(() => item.classList.add('show'), 20);
    setTimeout(() => { item.classList.remove('show'); setTimeout(() => item.remove(), 260); }, 3600);
  }
  function vmNameFromRow(row) {
    const link = qs('a[href^="/vm/"]', row);
    if (!link) return '';
    try { const p = new URL(link.href, location.href).pathname.split('/').filter(Boolean); return p[0] === 'vm' ? decodeURIComponent(p[1] || '') : ''; } catch (_) { return ''; }
  }
  function stateClass(state) { const v = String(state || '').toLowerCase(); return v.includes('running') ? 'ok' : v.includes('shut') ? 'err' : 'warn'; }
  function updateDashboardRows(vms) {
    if (!Array.isArray(vms)) return;
    const map = new Map(vms.map((vm) => [vm.name, vm]));
    qsa('table tbody tr').forEach((row) => {
      const name = vmNameFromRow(row);
      if (!name || !map.has(name)) return;
      const vm = map.get(name);
      const cells = qsa('td', row);
      if (cells[0]) cells[0].textContent = vm.id || '-';
      if (cells[2]) cells[2].textContent = vm.ip || '—';
      if (cells[3]) {
        let badge = qs('.status', cells[3]);
        if (!badge) { badge = document.createElement('span'); cells[3].textContent = ''; cells[3].appendChild(badge); }
        badge.className = 'status ' + (vm.state_css || stateClass(vm.state));
        badge.textContent = vm.state || 'unknown';
      }
    });
  }
  function updateDetailHeader(vms) {
    const title = qs('.brand');
    if (!title || !Array.isArray(vms)) return;
    const vm = vms.find((item) => item.name === title.textContent.trim());
    if (!vm) return;
    let badge = qs('#vm-live-badge');
    if (!badge) { badge = document.createElement('span'); badge.id = 'vm-live-badge'; title.insertAdjacentElement('afterend', badge); }
    badge.className = 'status live-badge ' + (vm.state_css || stateClass(vm.state));
    badge.textContent = vm.state || 'unknown';
  }
  async function refreshLiveStatus() {
    if (document.hidden || (!qs('.v-main') && !qs('.shell'))) return;
    try {
      const r = await fetch('/live/status', { cache: 'no-store', headers: { 'Accept': 'application/json' }});
      if (!r.ok) return;
      const p = await r.json();
      if (!p.ok) return;
      updateDashboardRows(p.vms);
      updateDetailHeader(p.vms);
    } catch (_) {}
  }
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!form || !form.action) return;
    const parts = new URL(form.action, location.href).pathname.split('/').filter(Boolean);
    if (parts[0] !== 'vm' || parts.length < 3) return;
    const labels = { start: 'Команда запуска VM отправлена', shutdown: 'Команда мягкого выключения VM отправлена', reboot: 'Команда перезагрузки VM отправлена', destroy: 'Команда принудительного выключения VM отправлена', autostart: 'Автозапуск VM включается', 'autostart-disable': 'Автозапуск VM отключается', delete: 'Удаление VM запущено' };
    const action = parts[2];
    if (labels[action]) { const type = action === 'destroy' || action === 'delete' ? 'warn' : 'ok'; sessionStorage.setItem('virtualityToast', labels[action]); toast(labels[action], type); setTimeout(refreshLiveStatus, 900); setTimeout(refreshLiveStatus, 2500); }
  }, true);
  function boot() { const m = sessionStorage.getItem('virtualityToast'); if (m) { sessionStorage.removeItem('virtualityToast'); toast(m, 'ok'); } refreshLiveStatus(); setInterval(refreshLiveStatus, 5000); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
'''
if 'Virtuality live status + toast layer' not in panel_js:
    panel_js_path.write_text(panel_js.rstrip() + live_js + '\n')
    changed.append('live javascript added')
else:
    changed.append('live javascript already present')

app_css = app_css_path.read_text() if app_css_path.exists() else ''
live_css = r'''

/* Virtuality live status + toast layer */
#v-toast-wrap { position: fixed; top: 14px; right: 14px; z-index: 10000; display: grid; gap: 8px; width: min(360px, calc(100vw - 28px)); pointer-events: none; }
.v-toast { transform: translateY(-8px); opacity: 0; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--line); background: #fff; color: var(--text); box-shadow: 0 12px 32px rgba(15,23,42,.18); font-size: 13px; font-weight: 800; transition: .22s ease; }
.v-toast.show { transform: translateY(0); opacity: 1; }
.v-toast.ok { border-color: rgba(22,163,74,.28); box-shadow: 0 12px 32px rgba(22,163,74,.16); }
.v-toast.warn { border-color: rgba(217,119,6,.32); box-shadow: 0 12px 32px rgba(217,119,6,.16); }
.v-toast.err { border-color: rgba(220,38,38,.32); box-shadow: 0 12px 32px rgba(220,38,38,.16); }
.live-badge { margin-left: 10px; vertical-align: middle; }
'''
if 'Virtuality live status + toast layer' not in app_css:
    app_css_path.write_text(app_css.rstrip() + live_css + '\n')
    changed.append('live css added')
else:
    changed.append('live css already present')

print('live status patch applied:')
for item in changed:
    print(f'- {item}')
