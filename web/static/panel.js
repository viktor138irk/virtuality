(() => {
  const app = {
    refreshTimers: [],
  };

  function qs(selector, root = document) { return root.querySelector(selector); }
  function qsa(selector, root = document) { return Array.from(root.querySelectorAll(selector)); }

  function ensureThemeStyles() {
    if (qs('link[data-virtuality-themes="1"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/themes.css';
    link.dataset.virtualityThemes = '1';
    document.head.appendChild(link);
  }

  function getTheme() {
    return localStorage.getItem('virtualityTheme') || 'neon';
  }

  function applyTheme(theme) {
    const selected = theme === 'macos' ? 'macos' : 'neon';
    document.documentElement.dataset.theme = selected;
    localStorage.setItem('virtualityTheme', selected);
    qsa('[data-theme-select]').forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.themeSelect === selected);
    });
  }

  function wireThemeSwitcher() {
    ensureThemeStyles();
    const nav = qs('.v-nav');
    if (!nav || qs('.theme-switcher')) {
      applyTheme(getTheme());
      return;
    }
    const switcher = document.createElement('div');
    switcher.className = 'theme-switcher';
    switcher.innerHTML = '<div class="theme-switcher-label">Тема</div><div class="theme-buttons"><button type="button" class="theme-button" data-theme-select="neon">Neon</button><button type="button" class="theme-button" data-theme-select="macos">MacOS</button></div>';
    nav.appendChild(switcher);
    qsa('[data-theme-select]', switcher).forEach((btn) => {
      btn.addEventListener('click', () => applyTheme(btn.dataset.themeSelect));
    });
    applyTheme(getTheme());
  }

  function activeSidebar(pathname = window.location.pathname) {
    qsa('.v-nav a').forEach((item) => {
      const url = new URL(item.href, window.location.href);
      const path = url.pathname;
      const active = path === '/' ? pathname === '/' : pathname.startsWith(path);
      item.classList.toggle('active', active);
    });
    qsa('.v-nav-group').forEach((group) => {
      const hasActive = qsa('a.active', group).length > 0;
      group.classList.toggle('open', hasActive);
    });
  }

  async function refreshFragments() {
    const fragments = qsa('[data-refresh-url]');
    for (const fragment of fragments) {
      const url = fragment.dataset.refreshUrl;
      if (!url) continue;
      try {
        const response = await fetch(url, { cache: 'no-store', headers: { 'X-Requested-With': 'XMLHttpRequest' }});
        const payload = await response.json();
        if (!payload.ok) continue;
        if (payload.html) fragment.innerHTML = payload.html;
        if (payload.text) fragment.textContent = payload.text;
      } catch (_) {}
    }
  }

  function startAutoRefresh(root = document) {
    const interval = Number(qs('[data-auto-refresh]', root)?.dataset.autoRefresh || 0);
    if (interval > 0) {
      app.refreshTimers.forEach((timer) => clearInterval(timer));
      app.refreshTimers = [];
      const timer = setInterval(refreshFragments, interval * 1000);
      app.refreshTimers.push(timer);
      refreshFragments();
    }
  }

  function wireNavGroups(root = document) {
    qsa('.v-nav-group-title', root).forEach((title) => {
      if (title.dataset.groupWired === '1') return;
      title.dataset.groupWired = '1';
      title.addEventListener('click', () => title.closest('.v-nav-group')?.classList.toggle('open'));
    });
  }

  function wireMobileMenu() {
    const sidebar = qs('.v-sidebar');
    if (!sidebar || qs('.mobile-menu-toggle')) return;
    const btn = document.createElement('button');
    btn.className = 'mobile-menu-toggle';
    btn.type = 'button';
    btn.textContent = '☰ Меню';
    btn.addEventListener('click', () => sidebar.classList.toggle('open'));
    document.body.appendChild(btn);
  }

  function initPanel() {
    wireThemeSwitcher();
    wireNavGroups();
    wireMobileMenu();
    activeSidebar();
    startAutoRefresh();
  }

  document.addEventListener('DOMContentLoaded', initPanel);
  applyTheme(getTheme());
})();

/* VM visual cards from raw virsh output */
(() => {
  function qs(selector, root = document) { return root.querySelector(selector); }
  function qsa(selector, root = document) { return Array.from(root.querySelectorAll(selector)); }
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
  }
  function rawPreBySummary(summaryText) {
    const details = qsa('.raw-details').find((item) => (qs('summary', item)?.textContent || '').includes(summaryText));
    return qs('pre', details)?.textContent.trim() || '';
  }
  function parseKeyValues(raw) {
    const data = {};
    raw.split('\n').forEach((line) => {
      const idx = line.indexOf(':');
      if (idx < 0) return;
      const key = line.slice(0, idx).trim().toLowerCase();
      const value = line.slice(idx + 1).trim();
      if (key) data[key] = value;
    });
    return data;
  }
  function sizeToGiB(value) {
    const raw = String(value || '').trim();
    const n = Number(raw.replace(',', '.').replace(/[^0-9.]/g, ''));
    if (!Number.isFinite(n) || n <= 0) return 0;
    if (/kib|kb/i.test(raw)) return n / 1024 / 1024;
    if (/mib|mb/i.test(raw)) return n / 1024;
    if (/gib|gb/i.test(raw)) return n;
    return n / 1024 / 1024;
  }
  function renderInfo() {
    const box = qs('#vm-info-visual');
    if (!box) return;
    const raw = rawPreBySummary('dominfo');
    const data = parseKeyValues(raw);
    const cpu = data['cpu(s)'] || data['cpu'] || '—';
    const state = data.state || 'unknown';
    const osType = data['os type'] || data['os'] || '—';
    const maxMem = sizeToGiB(data['max memory']);
    const usedMem = sizeToGiB(data['used memory']);
    const memPct = maxMem > 0 ? Math.max(0, Math.min(100, Math.round((usedMem / maxMem) * 100))) : 0;
    const cpuPct = Math.max(8, Math.min(100, Number(cpu) ? Number(cpu) * 12 : 18));
    const autostartText = (qsa('.top-meta .status').find((item) => item.textContent.toLowerCase().includes('autostart'))?.textContent || 'autostart: —').replace(/^autostart:\s*/i, '');
    box.innerHTML = `
      <div class="metric-strip">
        <div class="metric-tile"><span>Состояние</span><strong>${escapeHtml(state)}</strong></div>
        <div class="metric-tile"><span>CPU</span><strong>${escapeHtml(cpu)}</strong></div>
        <div class="metric-tile"><span>Тип ОС</span><strong>${escapeHtml(osType)}</strong></div>
      </div>
      <div class="chart-row">
        <div class="donut" style="--value:${memPct};--donut-color:var(--accent)"><div class="donut-value">${memPct}%</div></div>
        <div class="bar-list">
          <div class="bar-item"><div class="bar-head"><span>Память</span><b>${usedMem ? usedMem.toFixed(1) : '—'} / ${maxMem ? maxMem.toFixed(1) : '—'} GB</b></div><div class="mini-bar"><span style="--value:${memPct}%"></span></div></div>
          <div class="bar-item"><div class="bar-head"><span>vCPU capacity</span><b>${escapeHtml(cpu)} vCPU</b></div><div class="mini-bar"><span style="--value:${cpuPct}%"></span></div></div>
          <div class="bar-item"><div class="bar-head"><span>Автозапуск</span><b>${escapeHtml(autostartText)}</b></div><div class="mini-bar"><span style="--value:${/enable|включ/i.test(autostartText) ? 100 : 18}%"></span></div></div>
        </div>
      </div>`;
  }
  function parseDisks(raw) {
    const rows = [];
    raw.split('\n').map((line) => line.trim()).filter(Boolean).forEach((line) => {
      if (/^(target|type|[-\s]+$)/i.test(line)) return;
      const parts = line.split(/\s+/);
      if (parts.length >= 4 && /^(file|block|dir|network|volume)$/i.test(parts[0])) {
        rows.push({ type: parts[0], device: parts[1], target: parts[2], source: parts.slice(3).join(' ') });
      } else if (parts.length >= 2) {
        rows.push({ type: 'file', device: 'disk', target: parts[0], source: parts.slice(1).join(' ') });
      }
    });
    return rows;
  }
  function renderDisks() {
    const box = qs('#vm-disk-visual');
    if (!box) return;
    const rows = parseDisks(rawPreBySummary('domblklist'));
    if (!rows.length) {
      box.innerHTML = '<div class="visual-empty">Диски не найдены в выводе libvirt.</div>';
      return;
    }
    box.innerHTML = `<div class="visual-list">${rows.map((row) => `
      <div class="visual-item">
        <div class="visual-icon">▣</div>
        <div><b>${escapeHtml(row.target)}</b><small>${escapeHtml(row.device)} · ${escapeHtml(row.type)}<br>${escapeHtml(row.source)}</small></div>
        <span class="status ok">disk</span>
      </div>`).join('')}</div>`;
  }
  function parseInterfaces(raw) {
    const rows = [];
    raw.split('\n').map((line) => line.trim()).filter(Boolean).forEach((line) => {
      if (/^(interface|[-\s]+$)/i.test(line)) return;
      const parts = line.split(/\s+/);
      if (parts.length >= 5) rows.push({ iface: parts[0], type: parts[1], source: parts[2], model: parts[3], mac: parts[4] });
      else if (parts.length >= 2) rows.push({ iface: parts[0], type: parts[1] || '—', source: parts[2] || '—', model: parts[3] || '—', mac: parts[4] || '—' });
    });
    return rows;
  }
  function renderNetwork() {
    const box = qs('#vm-network-visual');
    if (!box) return;
    const rows = parseInterfaces(rawPreBySummary('domiflist'));
    if (!rows.length) {
      box.innerHTML = '<div class="visual-empty">Сетевые интерфейсы не найдены в выводе libvirt.</div>';
      return;
    }
    box.innerHTML = `<div class="visual-list">${rows.map((row) => `
      <div class="visual-item">
        <div class="visual-icon">⇄</div>
        <div><b>${escapeHtml(row.iface)}</b><small>${escapeHtml(row.type)} · ${escapeHtml(row.source)} · ${escapeHtml(row.model)}<br>${escapeHtml(row.mac)}</small></div>
        <span class="status ok">link</span>
      </div>`).join('')}</div>`;
  }
  function renderVmVisuals() {
    if (!qs('#vm-info-visual') && !qs('#vm-disk-visual') && !qs('#vm-network-visual')) return;
    renderInfo();
    renderDisks();
    renderNetwork();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderVmVisuals); else renderVmVisuals();
})();

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
    qsa('table.vm-table tbody tr').forEach((row) => {
      const name = vmNameFromRow(row);
      if (!name || !map.has(name)) return;
      const vm = map.get(name);
      const cells = qsa('td', row);
      if (cells[0]) cells[0].textContent = vm.id || '-';
      if (cells[2] && vm.ip && vm.ip !== '—' && vm.ip !== 'not available') cells[2].textContent = vm.ip;
      if (cells[3]) {
        let badge = qs('.status', cells[3]);
        if (!badge) { badge = document.createElement('span'); cells[3].textContent = ''; cells[3].appendChild(badge); }
        badge.className = 'status ' + (vm.state_css || stateClass(vm.state));
        badge.textContent = vm.state || 'unknown';
      }
      if (cells[4] && typeof vm.autostart_enabled !== 'undefined') {
        const button = qs('button.status-button', cells[4]);
        if (button) {
          button.textContent = vm.autostart_label || (vm.autostart_enabled ? 'enabled' : 'disabled');
          button.className = 'status status-button ' + (vm.autostart_enabled ? 'ok' : 'err');
        }
      }
    });
  }
  function updateDetailHeader(vms) {
    const title = qs('.v-title');
    if (!title || !Array.isArray(vms)) return;
    const vm = vms.find((item) => item.name === title.textContent.trim());
    if (!vm) return;
    let badge = qs('#vm-live-badge');
    if (!badge) { badge = document.createElement('span'); badge.id = 'vm-live-badge'; title.insertAdjacentElement('afterend', badge); }
    badge.className = 'status live-badge ' + (vm.state_css || stateClass(vm.state));
    badge.textContent = vm.state || 'unknown';
  }
  async function refreshLiveStatus() {
    if (document.hidden || !qs('.v-main')) return;
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
