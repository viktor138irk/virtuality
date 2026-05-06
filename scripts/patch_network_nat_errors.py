#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

helper = r'''

def safe_network_template(request: Request, error: str | None = None, status_code: int = 200, diagnostics: dict[str, Any] | None = None):
    try:
        ctx = network_core.network_context()
    except Exception as exc:
        ctx = {
            "nat": {
                "name": network_core.NETWORK_NAME,
                "bridge": network_core.NAT_BRIDGE,
                "subnet": network_core.NAT_SUBNET,
                "gateway": network_core.NAT_GATEWAY,
                "dhcp": f"{network_core.DHCP_START} - {network_core.DHCP_END}",
                "exists": False,
                "info": str(exc),
                "leases": "",
            },
            "networks": [],
            "forwards": [],
            "external_interface": "unknown",
            "ip_forward": "unknown",
            "nft_rules": "",
        }
        error = error or f"Ошибка чтения сетевого состояния: {exc}"
    return templates.TemplateResponse("network.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": AUTH_USER,
        "vms": parse_virsh_list(),
        "ctx": ctx,
        "error": error,
        "diagnostics": diagnostics,
    }, status_code=status_code)
'''

if 'def safe_network_template(' not in text:
    marker = '\n\n@app.get("/network", response_class=HTMLResponse)'
    if marker not in text:
        raise SystemExit('network route marker not found')
    text = text.replace(marker, helper + marker, 1)
    changed.append('safe network template helper added')
else:
    changed.append('safe network template helper already present')

old_get = '''@app.get("/network", response_class=HTMLResponse)
def network_page(request: Request, error: str | None = None):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": error})
'''
new_get = '''@app.get("/network", response_class=HTMLResponse)
def network_page(request: Request, error: str | None = None):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return safe_network_template(request, error=error)
'''
if old_get in text:
    text = text.replace(old_get, new_get, 1)
    changed.append('network page now uses safe template')
elif 'return safe_network_template(request, error=error)' in text:
    changed.append('network page already safe')
else:
    changed.append('network page replacement skipped')

old_nat = '''@app.post("/network/nat/setup")
def network_nat_setup(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.create_nat_network()
        network_core.apply_port_forwards()
    except NetworkError as exc:
        return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": str(exc)}, status_code=500)
    return RedirectResponse(url="/network", status_code=303)
'''
new_nat = '''@app.post("/network/nat/setup")
def network_nat_setup(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.create_nat_network()
        network_core.apply_port_forwards()
    except NetworkError as exc:
        return safe_network_template(request, error=str(exc), status_code=500)
    except Exception as exc:
        return safe_network_template(request, error=f"Внутренняя ошибка настройки NAT: {exc}", status_code=500)
    return RedirectResponse(url="/network", status_code=303)
'''
if old_nat in text:
    text = text.replace(old_nat, new_nat, 1)
    changed.append('NAT setup catches all exceptions')
elif 'Внутренняя ошибка настройки NAT' in text:
    changed.append('NAT setup already catches all exceptions')
else:
    raise SystemExit('network nat setup route marker not found')

replacements = {
    'return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": str(exc)}, status_code=400)': 'return safe_network_template(request, error=str(exc), status_code=400)',
    'return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": str(exc)}, status_code=500)': 'return safe_network_template(request, error=str(exc), status_code=500)',
    'return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": str(exc), "diagnostics": None}, status_code=400)': 'return safe_network_template(request, error=str(exc), status_code=400)',
    'return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": None, "diagnostics": diagnostics})': 'return safe_network_template(request, diagnostics=diagnostics)',
}
for old, new in replacements.items():
    if old in text:
        text = text.replace(old, new)
        changed.append('network error response hardened')

app_path.write_text(text)
print('network NAT error patch applied:')
for item in changed:
    print(f'- {item}')
