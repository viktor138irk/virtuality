(() => {
  const app = {
    loading: false,
    refreshTimers: [],
  };

  const fullLoadPages = ['/iso', '/disk-images', '/vm/create', '/update'];

  function qs(selector, root = document) { return root.querySelector(selector); }
  function qsa(selector, root = document) { return Array.from(root.querySelectorAll(selector)); }

  function sameOriginLink(link) {
    if (!link || !link.href) return false;
    const url = new URL(link.href, window.location.href);
    return url.origin === window.location.origin;
  }

  function requiresFullLoad(pathname) {
    return fullLoadPages.some((path) => pathname === path || pathname.startsWith(path + '/'));
  }

  function isAjaxSafeLink(link) {
    if (!sameOriginLink(link)) return false;
    const url = new URL(link.href, window.location.href);
    if (requiresFullLoad(url.pathname)) return false;
    if (url.pathname.startsWith('/vm/') && url.pathname.endsWith('/console')) return false;
    if (url.pathname.startsWith('/static/')) return false;
    if (url.pathname.startsWith('/api/')) return false;
    if (link.target && link.target !== '_self') return false;
    if (link.hasAttribute('download')) return false;
    if (link.dataset.noAjax === '1') return false;
    return true;
  }

  function setLoading(active) {
    app.loading = active;
    document.documentElement.classList.toggle('ajax-loading', active);
    let bar = qs('#ajax-progress');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'ajax-progress';
      document.body.appendChild(bar);
    }
    bar.classList.toggle('active', active);
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

  function stopRefreshTimers() {
    app.refreshTimers.forEach((timer) => clearInterval(timer));
    app.refreshTimers = [];
  }

  function swapPage(html, url, push = true) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const targetPath = new URL(url, window.location.href).pathname;
    if (requiresFullLoad(targetPath)) {
      window.location.href = url;
      return;
    }
    const newMain = qs('.v-main', doc) || qs('.shell', doc);
    const currentMain = qs('.v-main') || qs('.shell');

    if (!newMain || !currentMain) {
      window.location.href = url;
      return;
    }

    stopRefreshTimers();
    document.title = doc.title || document.title;
    currentMain.classList.add('page-exit');
    setTimeout(() => {
      currentMain.replaceWith(newMain);
      newMain.classList.add('page-enter');
      setTimeout(() => newMain.classList.remove('page-enter'), 220);
      activeSidebar(targetPath);
      if (push) history.pushState({ ajax: true }, '', url);
      initDynamicPage();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }, 80);
  }

  async function ajaxNavigate(url, push = true) {
    const targetPath = new URL(url, window.location.href).pathname;
    if (requiresFullLoad(targetPath)) {
      window.location.href = url;
      return;
    }
    if (app.loading) return;
    setLoading(true);
    try {
      const response = await fetch(url, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'text/html,application/xhtml+xml',
        },
        cache: 'no-store',
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const html = await response.text();
      swapPage(html, url, push);
    } catch (error) {
      window.location.href = url;
    } finally {
      setLoading(false);
    }
  }

  function wireAjaxLinks(root = document) {
    qsa('a', root).forEach((link) => {
      if (link.dataset.ajaxWired === '1') return;
      link.dataset.ajaxWired = '1';
      link.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (!isAjaxSafeLink(link)) return;
        event.preventDefault();
        ajaxNavigate(link.href);
      });
    });
  }

  function wireAjaxForms(root = document) {
    qsa('form', root).forEach((form) => {
      if (form.dataset.ajaxWired === '1') return;
      if (form.dataset.noAjax === '1') return;
      const method = String(form.method || 'GET').toUpperCase();
      if (method !== 'POST') return;
      if (form.enctype && form.enctype.includes('multipart')) return;
      form.dataset.ajaxWired = '1';
      form.addEventListener('submit', async (event) => {
        if (event.defaultPrevented) return;
        const submitter = event.submitter;
        if (submitter && submitter.classList.contains('danger') && !confirm('Выполнить действие?')) return;
        event.preventDefault();
        setLoading(true);
        try {
          const response = await fetch(form.action, {
            method: 'POST',
            body: new FormData(form),
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            redirect: 'follow',
          });
          const finalUrl = response.url || window.location.href;
          if (response.headers.get('content-type')?.includes('text/html')) {
            const html = await response.text();
            swapPage(html, finalUrl, true);
          } else {
            await ajaxNavigate(window.location.href, false);
          }
        } catch (error) {
          form.submit();
        } finally {
          setLoading(false);
        }
      });
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

  function initDynamicPage() {
    wireAjaxLinks();
    wireAjaxForms();
    wireNavGroups();
    wireMobileMenu();
    activeSidebar();
    startAutoRefresh();
  }

  window.addEventListener('popstate', () => ajaxNavigate(window.location.href, false));
  document.addEventListener('DOMContentLoaded', initDynamicPage);
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
