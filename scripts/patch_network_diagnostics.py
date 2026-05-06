#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
if 'def network_diagnose(' in text:
    print('network diagnostics handler already applied')
    raise SystemExit(0)

handler = '''

@app.post("/network/diagnose", response_class=HTMLResponse)
def network_diagnose(request: Request, vm_name: str = Form(...), external_port: int = Form(...), guest_port: int = Form(...), protocol: str = Form("tcp")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    diagnostics = None
    error = None
    try:
        diagnostics = network_core.diagnose_public_access(vm_name, external_port, guest_port, protocol)
    except NetworkError as exc:
        error = str(exc)
    return templates.TemplateResponse("network.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": AUTH_USER,
        "vms": parse_virsh_list(),
        "ctx": network_core.network_context(),
        "error": error,
        "diagnostics": diagnostics,
    }, status_code=400 if error else 200)
'''

marker = '\n\n@app.get("/operations", response_class=HTMLResponse)'
if marker not in text:
    raise SystemExit('operations marker not found in app.py')
text = text.replace(marker, handler + marker, 1)
app_path.write_text(text)
print(f'network diagnostics handler applied: {app_path}')
