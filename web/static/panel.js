/* Virtuality panel UI behaviour. No dependencies. */
(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const ICONS = '/static/icons.svg';
  const icon = (name, cls = 'icon') => `<svg class="${cls}" aria-hidden="true"><use href="${ICONS}#i-${name}"></use></svg>`;
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  const VM_STATES = {
    'running': ['Работает', 'success'], 'idle': ['Работает', 'success'], 'blocked': ['Работает', 'success'],
    'paused': ['Приостановлена', 'warning'], 'pmsuspended': ['Спящий режим', 'warning'],
    'in shutdown': ['Выключается', 'warning'], 'shut off': ['Выключена', 'neutral'], 'shutoff': ['Выключена', 'neutral'],
    'crashed': ['Сбой', 'danger'], 'dying': ['Останавливается', 'warning'],
  };
  const OP_STATES = { success: ['Готово', 'success'], error: ['Ошибка', 'danger'], running: ['Выполняется', 'info'], queued: ['В очереди', 'neutral'] };
  const vmState = (state) => VM_STATES[String(state || '').toLowerCase()] || [state || 'Неизвестно', 'neutral'];
  const opState = (status) => OP_STATES[String(status || '').toLowerCase()] || [status || '—', 'neutral'];
  const badge = ([label, tone]) => `<span class="dot"></span>${escapeHtml(label)}`;

  function bytes(value) {
    if (!value) return '0 Б';
    const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
    let n = value; let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  /* ---------------------------------------------------- theme */
  function applyTheme(mode) {
    const dark = mode === 'dark' || (mode === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    $$('[data-theme-toggle]').forEach((btn) => {
      btn.innerHTML = icon(dark ? 'sun' : 'moon');
      btn.title = dark ? 'Светлая тема' : 'Тёмная тема';
    });
  }
  function currentMode() { try { return localStorage.getItem('virtualityTheme') || 'system'; } catch (_) { return 'system'; } }
  function wireTheme() {
    applyTheme(currentMode());
    $$('[data-theme-toggle]').forEach((btn) => btn.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('virtualityTheme', next); } catch (_) {}
      applyTheme(next);
    }));
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (currentMode() === 'system') applyTheme('system'); });
  }

  /* ---------------------------------------------------- toasts */
  function toast(message, tone = 'success') {
    if (!message) return;
    let wrap = $('#toasts');
    if (!wrap) { wrap = document.createElement('div'); wrap.id = 'toasts'; document.body.appendChild(wrap); }
    const icons = { success: 'circle-check', warning: 'triangle-alert', danger: 'circle-x', info: 'info' };
    const item = document.createElement('div');
    item.className = `toast ${tone}`;
    item.setAttribute('role', 'status');
    item.innerHTML = `${icon(icons[tone] || 'info')}<div>${escapeHtml(message)}</div>`;
    wrap.appendChild(item);
    requestAnimationFrame(() => item.classList.add('show'));
    setTimeout(() => { item.classList.remove('show'); setTimeout(() => item.remove(), 250); }, 4200);
  }
  window.vToast = toast;
  function flashToast() {
    try {
      const raw = sessionStorage.getItem('virtualityToast');
      if (!raw) return;
      sessionStorage.removeItem('virtualityToast');
      const data = JSON.parse(raw);
      toast(data.message, data.tone);
    } catch (_) {}
  }

  /* ---------------------------------------------------- navigation & menus */
  function wireNav() {
    $$('[data-nav-toggle]').forEach((btn) => btn.addEventListener('click', () => document.body.classList.toggle('nav-open')));
    document.addEventListener('click', (event) => {
      if (document.body.classList.contains('nav-open') && !event.target.closest('.sidebar, [data-nav-toggle]')) document.body.classList.remove('nav-open');
      $$('details.menu[open]').forEach((menu) => { if (!menu.contains(event.target)) menu.removeAttribute('open'); });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      document.body.classList.remove('nav-open');
      $$('details.menu[open]').forEach((menu) => menu.removeAttribute('open'));
    });
    $$('details.menu').forEach((menu) => menu.addEventListener('toggle', () => {
      if (menu.open) $$('details.menu[open]').forEach((other) => { if (other !== menu) other.removeAttribute('open'); });
    }));
  }

  /* ---------------------------------------------------- forms: confirm, busy, toast */
  function confirmDialog(form) {
    return new Promise((resolve) => {
      const dialog = $('#confirm-dialog');
      if (!dialog || typeof dialog.showModal !== 'function') { resolve(window.confirm(form.dataset.confirm)); return; }
      const danger = form.dataset.confirmTone !== 'brand';
      $('[data-confirm-title]', dialog).textContent = form.dataset.confirmTitle || 'Подтвердите действие';
      $('[data-confirm-text]', dialog).textContent = form.dataset.confirm;
      const ok = $('[data-confirm-ok]', dialog);
      ok.textContent = form.dataset.confirmButton || 'Продолжить';
      ok.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
      $('.dialog-icon', dialog).className = `dialog-icon ${danger ? '' : 'brand'}`;
      const done = (value) => { dialog.close(); ok.removeEventListener('click', onOk); dialog.removeEventListener('close', onClose); resolve(value); };
      const onOk = () => done(true);
      const onClose = () => resolve(false);
      ok.addEventListener('click', onOk);
      dialog.addEventListener('close', onClose, { once: true });
      dialog.showModal();
    });
  }
  function markBusy(form, submitter) {
    const button = submitter || $('button[type=submit], button:not([type])', form);
    if (!button || form.dataset.noBusy !== undefined) return;
    button.disabled = true;
    if (!button.classList.contains('switch') && !button.classList.contains('menu-item')) {
      button.dataset.label = button.innerHTML;
      button.innerHTML = `<span class="spinner"></span>${escapeHtml(button.dataset.busy || button.textContent.trim())}`;
    }
  }
  function wireForms() {
    document.addEventListener('submit', async (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.dataset.uploader !== undefined) return;
      if (form.dataset.confirm && form.dataset.confirmed !== '1') {
        event.preventDefault();
        const submitter = event.submitter;
        if (await confirmDialog(form)) {
          form.dataset.confirmed = '1';
          if (form.dataset.toast) sessionStorage.setItem('virtualityToast', JSON.stringify({ message: form.dataset.toast, tone: form.dataset.toastTone || 'success' }));
          markBusy(form, submitter);
          form.submit();
        }
        return;
      }
      if (form.dataset.toast) sessionStorage.setItem('virtualityToast', JSON.stringify({ message: form.dataset.toast, tone: form.dataset.toastTone || 'success' }));
      const submitter = event.submitter;
      setTimeout(() => markBusy(form, submitter), 0);
    });
    window.addEventListener('pageshow', (event) => {
      if (!event.persisted) return;
      $$('button[data-label]').forEach((btn) => { btn.innerHTML = btn.dataset.label; btn.disabled = false; });
    });
  }

  /* ---------------------------------------------------- tabs */
  function wireTabs() {
    $$('[data-tabs]').forEach((tabs) => {
      const buttons = $$('[data-tab]', tabs);
      const select = (id, push) => {
        buttons.forEach((btn) => btn.setAttribute('aria-selected', String(btn.dataset.tab === id)));
        $$('[data-tab-panel]').forEach((panel) => { panel.hidden = panel.dataset.tabPanel !== id; });
        if (push) history.replaceState(null, '', `#${id}`);
      };
      buttons.forEach((btn) => btn.addEventListener('click', () => select(btn.dataset.tab, true)));
      const initial = location.hash.slice(1);
      select(buttons.some((btn) => btn.dataset.tab === initial) ? initial : buttons[0]?.dataset.tab, false);
    });
  }

  /* ---------------------------------------------------- copy */
  function wireCopy() {
    document.addEventListener('click', async (event) => {
      const btn = event.target.closest('[data-copy]');
      if (!btn) return;
      try { await navigator.clipboard.writeText(btn.dataset.copy); toast('Скопировано'); } catch (_) { toast('Не удалось скопировать', 'warning'); }
    });
  }

  /* ---------------------------------------------------- live VM status */
  function applyVmState(vm) {
    $$(`[data-live-vm="${CSS.escape(vm.name)}"]`).forEach((root) => {
      const [label, tone] = vmState(vm.state);
      $$('[data-live="badge"]', root).forEach((el) => { el.className = `badge ${tone}${el.classList.contains('lg') ? ' lg' : ''}`; el.innerHTML = badge([label, tone]); });
      $$('[data-live="dot"]', root).forEach((el) => { el.className = `status-dot ${tone}`; });
      $$('[data-live="ip"]', root).forEach((el) => { if (vm.ip && vm.ip !== '—') el.textContent = vm.ip; });
      const wasRunning = root.dataset.running === '1';
      const nowRunning = tone === 'success';
      if (root.dataset.reloadOnChange !== undefined && wasRunning !== nowRunning && !document.querySelector('details.menu[open]')) location.reload();
    });
  }
  async function refreshLive() {
    if (document.hidden) return;
    try {
      const response = await fetch('/live/status', { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload.ok) return;
      payload.vms.forEach(applyVmState);
      const running = payload.vms.filter((vm) => vmState(vm.state)[1] === 'success').length;
      $$('[data-live-count="running"]').forEach((el) => { el.textContent = running; });
    } catch (_) {}
  }
  function wireLive() {
    if (!$('[data-live-vm]')) return;
    setInterval(refreshLive, 5000);
  }

  /* ---------------------------------------------------- live VM load (CPU / RAM meters) */
  const levelTone = (pct) => (pct >= 90 ? 'danger' : pct >= 75 ? 'warning' : 'success');
  const formatMb = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} ГБ` : `${Math.round(mb)} МБ`);
  function applyStatMeter(root, key, pct, label) {
    const bar = $(`[data-stat="${key}-bar"]`, root);
    const meter = $(`[data-stat="${key}-meter"]`, root);
    const text = $(`[data-stat="${key}"]`, root);
    if (bar) bar.style.setProperty('--value', `${pct == null ? 0 : Math.round(pct)}%`);
    if (meter) meter.className = `meter ${pct == null ? '' : levelTone(pct)}`;
    if (text) text.textContent = label;
  }
  async function refreshStats() {
    if (document.hidden) return;
    try {
      const response = await fetch('/live/stats', { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload.ok) return;
      $$('[data-live-stats]').forEach((root) => {
        const stat = payload.stats[root.dataset.liveStats];
        root.hidden = !stat;
        if (!stat) return;
        const cpu = stat.cpu_pct == null ? null : Math.round(stat.cpu_pct);
        const memPct = stat.mem_total_mb ? Math.min(100, Math.round((stat.mem_used_mb / stat.mem_total_mb) * 100)) : null;
        applyStatMeter(root, 'cpu', cpu, cpu == null ? '…' : `${cpu}%`);
        applyStatMeter(root, 'mem', memPct, stat.mem_total_mb ? (root.classList.contains('vm-meters') ? `${memPct}%` : `${formatMb(stat.mem_used_mb)} из ${formatMb(stat.mem_total_mb)}`) : '—');
      });
    } catch (_) {}
  }
  function wireStats() {
    if (!$('[data-live-stats]')) return;
    refreshStats();
    setInterval(refreshStats, 5000);
  }

  /* ---------------------------------------------------- uploads */
  function wireUploader(form) {
    const input = $('input[type=file]', form);
    const zone = $('.dropzone', form);
    const fileBox = $('[data-upload-file]', form);
    const nameEl = $('[data-upload-name]', form);
    const sizeEl = $('[data-upload-size]', form);
    const bar = $('[data-upload-progress] > span', form);
    const progress = $('[data-upload-progress]', form);
    const statusEl = $('[data-upload-status]', form);
    const statsEl = $('[data-upload-stats]', form);
    const errorEl = $('[data-upload-error]', form);
    const submit = $('[data-upload-submit]', form);
    const cancel = $('[data-upload-cancel]', form);
    const accept = (form.dataset.accept || '').split(',').map((item) => item.trim().toLowerCase()).filter(Boolean);
    let xhr = null;

    const valid = (file) => file && (!accept.length || accept.some((ext) => file.name.toLowerCase().endsWith(ext)));
    const setError = (message) => { errorEl.hidden = !message; $('[data-upload-error-text]', errorEl).textContent = message || ''; };
    const setActive = (active) => {
      window.VirtualityUploadActive = active;
      submit.hidden = active; cancel.hidden = !active; input.disabled = active;
      zone.hidden = active || Boolean(input.files[0]);
    };
    const showFile = () => {
      const file = input.files[0];
      setError('');
      if (!file) { fileBox.hidden = true; zone.hidden = false; submit.disabled = true; return; }
      if (!valid(file)) {
        setError(`Файл «${file.name}» не подходит. Допустимые форматы: ${accept.join(', ')}`);
        input.value = ''; fileBox.hidden = true; zone.hidden = false; submit.disabled = true; return;
      }
      nameEl.textContent = file.name; sizeEl.textContent = bytes(file.size);
      fileBox.hidden = false; zone.hidden = true; progress.hidden = true; statsEl.textContent = '';
      statusEl.textContent = 'Готов к загрузке'; submit.disabled = false;
    };

    input.addEventListener('change', showFile);
    ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, () => zone.classList.remove('dragover')));
    zone.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; showFile(); } });
    $('[data-upload-clear]', form)?.addEventListener('click', () => { input.value = ''; showFile(); });
    cancel.addEventListener('click', () => xhr && xhr.abort());
    window.addEventListener('beforeunload', (event) => { if (window.VirtualityUploadActive) { event.preventDefault(); event.returnValue = ''; } });

    async function pollOperation(id) {
      statusEl.textContent = 'Файл загружен. Готовим образ…';
      bar.style.width = '0%';
      for (;;) {
        await new Promise((r) => setTimeout(r, 1200));
        try {
          const response = await fetch(`/api/operations/${id}`, { cache: 'no-store' });
          const data = await response.json();
          const op = data.operation || {};
          bar.style.width = `${op.progress || 0}%`;
          statusEl.textContent = `Конвертация в формат qcow2 — ${op.progress || 0}%`;
          if (op.status === 'success') return true;
          if (op.status === 'error') { setError(op.message || 'Ошибка конвертации'); return false; }
        } catch (_) {}
      }
    }

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const file = input.files[0];
      if (!valid(file)) { setError('Выберите файл'); return; }
      const started = Date.now();
      xhr = new XMLHttpRequest();
      setActive(true); progress.hidden = false; progress.classList.remove('success', 'danger');
      statusEl.textContent = 'Загрузка на сервер…';
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round((e.loaded / e.total) * 100);
        const speed = e.loaded / Math.max(1, (Date.now() - started) / 1000);
        const left = speed > 0 ? Math.round((e.total - e.loaded) / speed) : 0;
        bar.style.width = `${pct}%`;
        statsEl.textContent = `${bytes(e.loaded)} из ${bytes(e.total)} · ${bytes(speed)}/с${left > 2 ? ` · осталось ~${left > 90 ? Math.round(left / 60) + ' мин' : left + ' с'}` : ''}`;
        statusEl.textContent = pct >= 100 ? 'Сохраняем файл на сервере…' : `Загрузка на сервер — ${pct}%`;
      };
      xhr.onload = async () => {
        if (xhr.status >= 200 && xhr.status < 400) {
          let payload = null;
          try { payload = JSON.parse(xhr.responseText); } catch (_) {}
          if (payload && payload.mode === 'converting' && payload.operation_id) {
            const ok = await pollOperation(payload.operation_id);
            window.VirtualityUploadActive = false;
            if (!ok) { setActive(false); progress.classList.add('danger'); return; }
          }
          window.VirtualityUploadActive = false;
          progress.classList.add('success'); bar.style.width = '100%'; statusEl.textContent = 'Готово';
          sessionStorage.setItem('virtualityToast', JSON.stringify({ message: `Файл ${file.name} загружен`, tone: 'success' }));
          location.href = form.dataset.redirect || location.pathname;
        } else {
          setActive(false); progress.classList.add('danger');
          const doc = new DOMParser().parseFromString(xhr.responseText || '', 'text/html');
          setError(doc.querySelector('[data-error-message]')?.textContent.trim() || `Сервер ответил ошибкой (${xhr.status})`);
          statusEl.textContent = 'Загрузка не удалась';
        }
      };
      xhr.onerror = () => { setActive(false); setError('Связь с сервером прервалась. Попробуйте ещё раз.'); };
      xhr.onabort = () => { setActive(false); statusEl.textContent = 'Загрузка отменена'; bar.style.width = '0%'; statsEl.textContent = ''; };
      xhr.open('POST', form.action);
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      xhr.send(new FormData(form));
    });
    showFile();
  }

  /* ---------------------------------------------------- operation page */
  function wireOperation() {
    const root = $('[data-operation-id]');
    if (!root) return;
    const id = root.dataset.operationId;
    const statusEl = $('[data-op="status"]', root);
    const bar = $('[data-op="bar"]', root);
    const progress = $('[data-op="progress"]', root);
    const pctEl = $('[data-op="pct"]', root);
    const messageEl = $('[data-op="message"]', root);
    const logEl = $('[data-op="log"]');
    let shown = Number(root.dataset.progress || 0);
    let status = root.dataset.status;
    const done = () => status === 'success' || status === 'error';
    const render = () => {
      bar.style.width = `${Math.round(shown)}%`;
      pctEl.textContent = `${Math.round(shown)}%`;
      progress.classList.toggle('success', status === 'success');
      progress.classList.toggle('danger', status === 'error');
    };
    const tick = setInterval(() => {
      if (done()) return;
      shown = Math.min(status === 'queued' ? 10 : 95, shown + (shown > 80 ? 0.15 : 0.5));
      render();
    }, 1000);
    const poll = async () => {
      try {
        const response = await fetch(`/api/operations/${id}`, { cache: 'no-store' });
        const data = await response.json();
        if (!data.ok) return;
        const op = data.operation;
        const wasDone = done();
        status = op.status;
        shown = status === 'success' ? 100 : Math.max(shown, Number(op.progress || 0));
        const [label, tone] = opState(status);
        statusEl.className = `badge lg ${tone}`; statusEl.innerHTML = badge([label, tone]);
        messageEl.textContent = op.message || '';
        if (logEl) { const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40; logEl.textContent = op.log_tail || ''; if (atBottom) logEl.scrollTop = logEl.scrollHeight; }
        render();
        if (done()) { clearInterval(timer); clearInterval(tick); if (!wasDone) { toast(status === 'success' ? 'Операция завершена' : 'Операция завершилась с ошибкой', status === 'success' ? 'success' : 'danger'); $$('[data-op-done]').forEach((el) => { el.hidden = false; }); } }
      } catch (_) {}
    };
    const timer = setInterval(poll, 1500);
    render(); poll();
    if (logEl) logEl.scrollTop = logEl.scrollHeight;
  }

  /* ---------------------------------------------------- logs & update pollers */
  function wireLogs() {
    const box = $('[data-log-source]');
    if (!box) return;
    const refresh = async () => {
      if (document.hidden || !$('[data-log-live]')?.checked) return;
      try {
        const response = await fetch(`/api/logs?source=${encodeURIComponent(box.dataset.logSource)}&lines=${encodeURIComponent(box.dataset.logLines)}`, { cache: 'no-store' });
        const data = await response.json();
        if (!data.ok) return;
        const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
        box.textContent = data.log.content || 'Журнал пуст';
        if (atBottom) box.scrollTop = box.scrollHeight;
      } catch (_) {}
    };
    box.scrollTop = box.scrollHeight;
    setInterval(refresh, 4000);
  }
  function wireUpdate() {
    const logBox = $('[data-update-log]');
    if (!logBox) return;
    const stateEl = $('[data-update-state]');
    const poll = async () => {
      try {
        const response = await fetch('/update/status', { cache: 'no-store' });
        const data = await response.json();
        if (!data.ok) return;
        logBox.textContent = data.log_tail || 'Журнал пока пуст.';
        const state = data.state || {};
        if (stateEl) stateEl.textContent = state.message || '';
        if (state.status === 'running') { $$('[data-update-running]').forEach((el) => { el.hidden = false; }); }
      } catch (_) {}
    };
    poll();
    setInterval(poll, 2500);
  }

  /* ---------------------------------------------------- VM wizard */
  function wireWizard() {
    const form = $('form[data-wizard]');
    if (!form) return;
    const field = (name) => form.elements[name];
    const value = (name) => { const el = field(name); if (!el) return ''; if (el instanceof RadioNodeList) return el.value; return el.value; };
    const setSummary = (key, text) => $$(`[data-summary="${key}"]`).forEach((el) => { el.textContent = text || '—'; });
    const optionText = (select) => select && select.selectedIndex >= 0 ? select.options[select.selectedIndex].dataset.label || select.options[select.selectedIndex].text : '';

    function sync() {
      const source = value('source_type');
      $$('[data-show-when]', form).forEach((el) => {
        const [key, expected] = el.dataset.showWhen.split('=');
        const visible = value(key) === expected;
        el.hidden = !visible;
        $$('input, select', el).forEach((input) => { input.disabled = !visible; });
      });
      $$('[data-visible-when]', form).forEach((el) => {
        const [key, expected] = el.dataset.visibleWhen.split('=');
        el.hidden = value(key) !== expected;
      });
      const preset = value('preset');
      const custom = preset === 'custom';
      const presetInput = $(`input[name=preset][value="${preset}"]`, form);
      if (!custom && presetInput) {
        field('vcpus').value = presetInput.dataset.vcpus;
        field('memory').value = presetInput.dataset.memory;
        field('disk_size').value = presetInput.dataset.disk;
      }
      $$('[data-custom-resources] input', form).forEach((input) => { input.readOnly = !custom; });
      const memory = Number(value('memory')) || 0;
      setSummary('name', value('name'));
      setSummary('source', source === 'disk_image' ? optionText(field('disk_image_path')) : optionText(field('iso_path')));
      setSummary('cpu', `${value('vcpus')} ${Number(value('vcpus')) === 1 ? 'ядро' : 'ядра'}`);
      setSummary('memory', memory >= 1024 ? `${+(memory / 1024).toFixed(1)} ГБ` : `${memory} МБ`);
      setSummary('disk', source === 'disk_image' ? 'из образа' : `${value('disk_size')} ГБ`);
      setSummary('network', value('network_mode') === 'bridge' ? 'Локальная сеть' : 'Автоматически (NAT)');
      const hasSource = source === 'disk_image' ? form.dataset.hasDisks === '1' : form.dataset.hasIsos === '1';
      $$('[data-create-button]').forEach((btn) => { btn.disabled = !hasSource || !value('name'); });
      $$('[data-missing-source]').forEach((el) => { el.hidden = hasSource || el.dataset.missingSource !== source; });
    }
    form.addEventListener('input', sync);
    form.addEventListener('change', sync);
    sync();
  }

  /* ---------------------------------------------------- boot order list */
  function wireBootOrder() {
    const list = $('[data-boot-list]');
    if (!list) return;
    const input = $('[data-boot-value]');
    const orders = { cdrom_disk: ['cdrom', 'hd', 'network'], disk_cdrom: ['hd', 'cdrom', 'network'], network_disk: ['network', 'hd', 'cdrom'], disk: ['hd', 'cdrom', 'network'], auto: ['hd', 'cdrom', 'network'] };
    (orders[list.dataset.current] || orders.auto).forEach((device) => { const node = $(`[data-device="${device}"]`, list); if (node) list.appendChild(node); });
    const update = () => {
      const devices = $$('[data-device]', list).map((item) => item.dataset.device);
      const [first, second] = devices;
      input.value = first === 'cdrom' && second === 'hd' ? 'cdrom_disk' : first === 'hd' && second === 'cdrom' ? 'disk_cdrom' : first === 'network' && second === 'hd' ? 'network_disk' : first === 'hd' ? 'disk' : 'auto';
      $$('[data-device]', list).forEach((item, index) => { $('[data-position]', item).textContent = index + 1; });
    };
    list.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-move]');
      if (!btn) return;
      const item = btn.closest('[data-device]');
      if (btn.dataset.move === 'up' && item.previousElementSibling) list.insertBefore(item, item.previousElementSibling);
      if (btn.dataset.move === 'down' && item.nextElementSibling) list.insertBefore(item.nextElementSibling, item);
      update();
    });
    update();
  }

  document.addEventListener('DOMContentLoaded', () => {
    wireTheme();
    wireNav();
    wireForms();
    wireTabs();
    wireCopy();
    wireLive();
    wireStats();
    $$('form[data-uploader]').forEach(wireUploader);
    wireOperation();
    wireLogs();
    wireUpdate();
    wireWizard();
    wireBootOrder();
    flashToast();
  });
})();
