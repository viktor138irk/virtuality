#!/usr/bin/env python3
from pathlib import Path
import runpy
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

script_dir = Path(__file__).resolve().parent

# New installs run this patch from install_web_panel.sh. Keep feature patches here too
# so a fresh install gets disk images, conversion progress, VM architecture selector,
# ARM64 emulation XML path, orphan disk replacement, dashboard update notifications and update center.
for optional_patch in ('patch_disk_images.py', 'patch_disk_convert_progress.py', 'patch_vm_architecture.py', 'patch_arm64_emulation_xml.py', 'patch_vm_disk_replace.py', 'patch_update_badge.py'):
    patch_path = script_dir / optional_patch
    if patch_path.exists():
        old_argv = sys.argv[:]
        try:
            sys.argv = [str(patch_path), str(app_path)]
            runpy.run_path(str(patch_path), run_name='__main__')
        finally:
            sys.argv = old_argv

text = app_path.read_text()
changed = []

if 'import update_core' not in text:
    text = text.replace('import network_core\n', 'import network_core\nimport update_core\n', 1)
    changed.append('import update_core added')
else:
    changed.append('import update_core already present')

routes = r'''

@app.get("/update", response_class=HTMLResponse)
def update_page(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    error = None
    try:
        info = update_core.check_updates(fetch=False)
    except Exception as exc:
        error = str(exc)
        info = {
            "ok": False,
            "source_dir": str(update_core.SOURCE_DIR),
            "remote": update_core.REMOTE,
            "branch": update_core.DEFAULT_BRANCH,
            "fetch_ok": False,
            "fetch_error": str(exc),
            "current_commit": "",
            "latest_commit": "",
            "current_version": "unknown",
            "latest_version": "unknown",
            "has_update": False,
            "missing_versions": [],
            "commits": [],
            "checked_at": utc_now(),
            "state": update_core.state(),
            "log_tail": update_core.update_log_tail(),
        }
    return templates.TemplateResponse("update.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "info": info, "error": error})


@app.post("/update/check")
def update_check(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        update_core.check_updates(fetch=True)
    except Exception:
        pass
    return RedirectResponse(url="/update", status_code=303)


@app.post("/update/apply")
def update_apply(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        update_core.start_update()
    except Exception:
        pass
    return RedirectResponse(url="/update", status_code=303)
'''

if '@app.get("/update"' not in text:
    marker = '\n\n@app.get("/operations", response_class=HTMLResponse)'
    if marker not in text:
        raise SystemExit('operations route marker not found')
    text = text.replace(marker, routes + marker, 1)
    changed.append('update routes added')
else:
    changed.append('update routes already present')

app_path.write_text(text)
print('update center patch applied:')
for item in changed:
    print(f'- {item}')
