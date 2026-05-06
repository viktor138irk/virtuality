#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()

if 'import network_templates' not in text:
    text = text.replace('import network_core\n', 'import network_core\nimport network_templates\n', 1)

text = text.replace('"ctx": network_core.network_context(), "error": error}', '"ctx": network_core.network_context(), "templates": network_templates.list_templates(), "error": error}', 1)
text = text.replace('"ctx": network_core.network_context(), "error": str(exc)}', '"ctx": network_core.network_context(), "templates": network_templates.list_templates(), "error": str(exc)}')
text = text.replace('"ctx": network_core.network_context(), "error": str(exc)}, status_code=400)', '"ctx": network_core.network_context(), "templates": network_templates.list_templates(), "error": str(exc)}, status_code=400)')
text = text.replace('"ctx": network_core.network_context(), "error": str(exc)}, status_code=500)', '"ctx": network_core.network_context(), "templates": network_templates.list_templates(), "error": str(exc)}, status_code=500)')

if 'def network_template_apply(' not in text:
    handler = '''

@app.post("/network/template/apply")
def network_template_apply(request: Request, vm_name: str = Form(...), template_key: str = Form(...), external_base_port: int = Form(0)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_templates.apply_template(vm_name, template_key, external_base_port if external_base_port > 0 else None)
    except NetworkError as exc:
        return templates.TemplateResponse("network.html", {
            "request": request,
            "app_name": APP_NAME,
            "user": AUTH_USER,
            "vms": parse_virsh_list(),
            "ctx": network_core.network_context(),
            "templates": network_templates.list_templates(),
            "error": str(exc),
        }, status_code=400)
    return RedirectResponse(url="/network", status_code=303)
'''
    marker = '\n\n@app.post("/network/forward/add")'
    if marker not in text:
        raise SystemExit('forward/add marker not found')
    text = text.replace(marker, handler + marker, 1)

app_path.write_text(text)
print(f'network templates patch applied: {app_path}')
