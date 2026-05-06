#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

if 'import update_core' not in text:
    if 'import network_core\n' in text:
        text = text.replace('import network_core\n', 'import network_core\nimport update_core\n', 1)
    elif 'from network_core import NetworkError\n' in text:
        text = text.replace('from network_core import NetworkError\n', 'from network_core import NetworkError\nimport update_core\n', 1)
    else:
        raise SystemExit('network_core import marker not found')
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


@app.get("/update/status")
def update_status(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    return JSONResponse({
        "ok": True,
        "state": update_core.state(),
        "log_tail": update_core.update_log_tail(260),
        "checked_at": utc_now(),
    })


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
    changed.append('update route already present')
    if '@app.get("/update/status"' not in text:
        marker = '\n\n@app.post("/update/check")'
        if marker not in text:
            raise SystemExit('update/check route marker not found')
        status_route = r'''

@app.get("/update/status")
def update_status(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    return JSONResponse({
        "ok": True,
        "state": update_core.state(),
        "log_tail": update_core.update_log_tail(260),
        "checked_at": utc_now(),
    })
'''
        text = text.replace(marker, status_route + marker, 1)
        changed.append('update status route added')
    else:
        changed.append('update status route already present')

app_path.write_text(text)
print('update center patch applied:')
for item in changed:
    print(f'- {item}')
