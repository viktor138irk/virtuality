(() => {
  const app = {
    loading: false,
    refreshTimers: [],
  };

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function qsa(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function sameOriginLink(link) {
    if (!link || !link.href) return false;
    const url = new URL(link.href, window.location.href);
    return url.origin === window.location.origin;
  }

  function isAjaxSafeLink(link) {
    if (!sameOriginLink(link)) return false;
    const url = new URL(link.href, window.location.href);
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
  }

  function stopRefreshTimers() {
    app.refreshTimers.forEach((timer) => clearInterval(timer));
    app.refreshTimers = [];
  }

  function swapPage(html, url, push = true) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
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
      activeSidebar(new URL(url, window.location.href).pathname);
      if (push) history.pushState({ ajax: true }, '', url);
      initDynamicPage();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }, 80);
  }

  async function ajaxNavigate(url, push = true) {
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
    wireMobileMenu();
    activeSidebar();
    startAutoRefresh();
  }

  window.addEventListener('popstate', () => ajaxNavigate(window.location.href, false));
  document.addEventListener('DOMContentLoaded', initDynamicPage);
})();
