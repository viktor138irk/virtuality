#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

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
    marker = '\n\ndef parse_virsh_list() -> list[dict[str, str]]:'
    if marker not in text:
        raise SystemExit('parse_virsh_list marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('log center helpers added')
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
    marker = '\n\n@app.get("/vm/create", response_class=HTMLResponse)'
    if marker not in text:
        marker = '\n\n@app.get("/api/operations")'
    if marker not in text:
        raise SystemExit('route insert marker not found')
    text = text.replace(marker, route + marker, 1)
    changed.append('logs routes added')
else:
    changed.append('logs routes already present')

app_path.write_text(text)
print('logs center patch applied:')
for item in changed:
    print(f'- {item}')
