#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

templates_dir = app_path.parent / 'templates'
static_dir = app_path.parent / 'static'
text = app_path.read_text()
changed = []

helpers = r'''

LOG_SOURCES = {
    "web": {"title": "Virtuality Web", "kind": "journal", "unit": "virtuality-web.service"},
    "update": {"title": "Update Center", "kind": "file", "path": "/var/log/virtuality/update.log"},
    "install": {"title": "Последняя установка", "kind": "glob", "pattern": "/var/log/virtuality/install_web_panel_*.log"},
    "operations": {"title": "Операции", "kind": "operations"},
    "auto-update": {"title": "Auto Update", "kind": "journal", "unit": "virtuality-auto-update.service"},
    "libvirtd": {"title": "libvirtd", "kind": "journal", "unit": "libvirtd.service"},
    "virtlogd": {"title": "virtlogd", "kind": "journal", "unit": "virtlogd.service"},
    "telegram": {"title": "Telegram notifier", "kind": "file", "path": "/var/log/virtuality/telegram_version_bot.log"},
}


def read_log_source(source: str, lines: int = 220) -> dict[str, Any]:
    key = source if source in LOG_SOURCES else "web"
    cfg = LOG_SOURCES[key]
    lines = max(20, min(int(lines or 220), 2000))
    content = ""
    path = ""
    cmd = ""
    if cfg["kind"] == "journal":
        unit = cfg["unit"]
        cmd = f"journalctl -u {unit} -n {lines} --no-pager"
        result = run_cmd(["journalctl", "-u", unit, "-n", str(lines), "--no-pager"], timeout=15)
        content = result["stdout"] or result["stderr"] or "Лог пуст или journalctl недоступен"
    elif cfg["kind"] == "file":
        path = cfg["path"]
        cmd = f"tail -n {lines} {path}"
        content = tail_text(Path(path), max_lines=lines) or "Файл лога пока пуст или не найден"
    elif cfg["kind"] == "glob":
        pattern = cfg["pattern"]
        files = sorted(Path('/').glob(pattern.lstrip('/')), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
        if files:
            path = str(files[0])
            cmd = f"tail -n {lines} {path}"
            content = tail_text(files[0], max_lines=lines) or "Файл лога пуст"
        else:
            cmd = f"ls {pattern}"
            content = "Логи установки ещё не найдены"
    elif cfg["kind"] == "operations":
        cmd = f"tail -n {lines} /var/log/virtuality/operations/*.log"
        parts = []
        ensure_operations_dir()
        for item in sorted(OPERATIONS_DIR.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            parts.append(f"===== {item.name} =====\n" + tail_text(item, max_lines=max(20, lines // 5)))
        content = "\n\n".join(parts) or "Журналы операций пока пусты"
    return {"key": key, "title": cfg["title"], "content": content, "path": path, "cmd": cmd, "lines": lines}
'''

if 'LOG_SOURCES = {' not in text:
    markers = [
        '\n\ndef parse_virsh_list() -> list[dict[str, str]]:',
        '\n\ndef list_recent_operations(limit: int = 20) -> list[dict[str, Any]]:',
        '\n\ndef safe_iso_filename(filename: str) -> str | None:',
    ]
    for marker in markers:
        if marker in text:
            text = text.replace(marker, helpers + marker, 1)
            changed.append('log center helpers added')
            break
    else:
        print('WARN: log helper marker not found, skip helper injection')
        changed.append('log center helpers skipped')
else:
    changed.append('log center helpers already present')

route = r'''

@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, source: str = "web", lines: int = 220):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    selected = read_log_source(source, lines)
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": AUTH_USER,
        "sources": LOG_SOURCES,
        "selected": selected,
    })


@app.get("/api/logs")
def api_logs(request: Request, source: str = "web", lines: int = 220):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return {"ok": True, "log": read_log_source(source, lines)}
'''

if '@app.get("/logs"' not in text:
    markers = ['\n\n@app.get("/vm/create", response_class=HTMLResponse)', '\n\n@app.get("/api/operations")', '\n\n@app.get("/iso", response_class=HTMLResponse)']
    for marker in markers:
        if marker in text:
            text = text.replace(marker, route + marker, 1)
            changed.append('logs routes added')
            break
    else:
        print('WARN: logs route insert marker not found, skip route injection')
        changed.append('logs routes skipped')
else:
    changed.append('logs routes already present')

app_path.write_text(text)

sidebar_html = '''{% set path = request.url.path %}
<aside class="v-sidebar">
  <div class="v-logo">
    <strong>Virtuality</strong>
    <span>control panel</span>
  </div>
  <nav class="v-nav">
    <a class="{{ 'active' if path == '/' else '' }}" href="/">Обзор</a>
    <a class="{{ 'active' if path.startswith('/vm/create') else '' }}" href="/vm/create">Создать VM</a>
    <div class="v-nav-group {{ 'open' if path.startswith('/iso') or path.startswith('/disk-images') else '' }}">
      <div class="v-nav-group-title">Образы</div>
      <a class="{{ 'active' if path.startswith('/iso') else '' }}" href="/iso">ISO</a>
      <a class="{{ 'active' if path.startswith('/disk-images') else '' }}" href="/disk-images">Диски</a>
    </div>
    <a class="{{ 'active' if path.startswith('/network') else '' }}" href="/network">Сеть</a>
    <a class="{{ 'active' if path.startswith('/operations') else '' }}" href="/operations">Операции</a>
    <a class="{{ 'active' if path.startswith('/logs') else '' }}" href="/logs">Журналы</a>
    <a class="{{ 'active' if path.startswith('/update') else '' }}" href="/update">Обновления</a>
    <a class="{{ 'active' if path.startswith('/host') else '' }}" href="/host">Хост</a>
  </nav>
</aside>
'''

if templates_dir.exists():
    sidebar_path = templates_dir / '_sidebar.html'
    sidebar_path.write_text(sidebar_html)
    changed.append('_sidebar.html ensured')

    for path in sorted(templates_dir.glob('*.html')):
        if path.name in {'login.html', 'console.html', '_sidebar.html'}:
            continue
        html = path.read_text()
        if '<script src="/static/panel.js" defer></script>' not in html:
            html = html.replace('</body>', '  <script src="/static/panel.js" defer></script>\n</body>', 1)
            changed.append(f'{path.name} panel.js attached')
        if '{% include "_sidebar.html" %}' not in html and '<div class="v-layout">' not in html and '<div class="shell">' in html:
            html = html.replace('<body>\n  <div class="shell">', '<body>\n  <div class="v-layout">\n    {% include "_sidebar.html" %}\n    <main class="v-main">\n      <div class="shell v-shell-embedded">', 1)
            script_marker = '\n\n  <script>'
            search_end = html.find(script_marker) if script_marker in html else html.find('\n</body>')
            if search_end == -1:
                search_end = len(html)
            close_idx = html.rfind('\n  </div>', 0, search_end)
            if close_idx != -1:
                html = html[:close_idx] + '\n      </div>\n    </main>\n  </div>' + html[close_idx + len('\n  </div>'):]
                changed.append(f'{path.name} wrapped with sidebar')
            else:
                print(f'WARN: sidebar close marker not found for {path.name}')
        path.write_text(html)

if static_dir.exists() and (static_dir / 'panel.js').exists():
    changed.append('panel.js found')
else:
    print('WARN: panel.js not found in installed static dir')

print('logs center and UI dynamics patch applied:')
for item in changed:
    print(f'- {item}')
