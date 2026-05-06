#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
if 'Virtuality noVNC console patch' in text:
    print('noVNC console patch already applied')
    raise SystemExit(0)

text = text.replace('import crypt\n', 'import asyncio\nimport crypt\n', 1)
text = text.replace('from fastapi import FastAPI, Request, Form, UploadFile, File\n', 'from fastapi import FastAPI, Request, Form, UploadFile, File, WebSocket, WebSocketDisconnect\n', 1)
text = text.replace('serializer = URLSafeSerializer(SESSION_SECRET, salt="virtuality-session")\n', 'serializer = URLSafeSerializer(SESSION_SECRET, salt="virtuality-session")\nconsole_serializer = URLSafeSerializer(SESSION_SECRET, salt="virtuality-console")\n', 1)
text = text.replace('DEFAULT_BRIDGE = "br0"\n', 'DEFAULT_BRIDGE = "br0"\nNOVNC_DIR = next((p for p in [Path("/usr/share/novnc"), Path("/usr/share/novnc/app")] if p.exists()), None)\n', 1)
text = text.replace('app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")\n', 'app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")\nif NOVNC_DIR:\n    app.mount("/novnc", StaticFiles(directory=str(NOVNC_DIR)), name="novnc")\n', 1)

old_get_user = '''def get_current_user(request: Request) -> str | None:\n    token = request.cookies.get("virtuality_session")\n    if not token:\n        return None\n    try:\n        data = serializer.loads(token)\n    except BadSignature:\n        return None\n    return AUTH_USER if data.get("user") == AUTH_USER else None\n'''
new_get_user = '''def user_from_session_token(token: str | None) -> str | None:\n    if not token:\n        return None\n    try:\n        data = serializer.loads(token)\n    except BadSignature:\n        return None\n    return AUTH_USER if data.get("user") == AUTH_USER else None\n\n\ndef get_current_user(request: Request) -> str | None:\n    return user_from_session_token(request.cookies.get("virtuality_session"))\n'''
text = text.replace(old_get_user, new_get_user, 1)

insert_after_vm_ip = '''def vm_vnc_display(name: str) -> str:\n    return run_cmd(["virsh", "vncdisplay", name], timeout=8)["stdout"] or "not available"\n\n\ndef vnc_display_to_port(display: str) -> int | None:\n    value = (display or "").strip()\n    if not value or value == "not available":\n        return None\n    match = re.search(r":(\\d+)$", value)\n    if not match:\n        return None\n    display_number = int(match.group(1))\n    if display_number >= 5900:\n        return display_number\n    return 5900 + display_number\n\n\ndef console_info(name: str) -> dict[str, Any]:\n    display = vm_vnc_display(name)\n    port = vnc_display_to_port(display)\n    has_novnc = bool(NOVNC_DIR and (NOVNC_DIR / "vnc.html").exists())\n    token = None\n    url = None\n    if port and has_novnc:\n        token = console_serializer.dumps({"vm": name, "port": port})\n        url = f"/novnc/vnc.html?autoconnect=1&resize=scale&path=console/ws/{token}"\n    return {"vm": name, "display": display, "port": port, "has_novnc": has_novnc, "novnc_dir": str(NOVNC_DIR) if NOVNC_DIR else "not installed", "url": url}\n\n\n'''
text = text.replace('def vm_details(name: str) -> dict[str, Any]:\n', insert_after_vm_ip + 'def vm_details(name: str) -> dict[str, Any]:\n', 1)
text = text.replace('"vnc": run_cmd(["virsh", "vncdisplay", name], timeout=8)["stdout"] or "not available",', '"vnc": vm_vnc_display(name),', 1)

proxy_funcs = '''\n# Virtuality noVNC console patch\nasync def proxy_vnc_to_websocket(reader: asyncio.StreamReader, websocket: WebSocket) -> None:\n    while True:\n        data = await reader.read(65536)\n        if not data:\n            break\n        await websocket.send_bytes(data)\n\n\nasync def proxy_websocket_to_vnc(websocket: WebSocket, writer: asyncio.StreamWriter) -> None:\n    while True:\n        message = await websocket.receive()\n        if message.get("type") == "websocket.disconnect":\n            break\n        if message.get("bytes") is not None:\n            writer.write(message["bytes"])\n        elif message.get("text") is not None:\n            writer.write(message["text"].encode())\n        await writer.drain()\n\n'''
text = text.replace('@app.get("/login", response_class=HTMLResponse)\n', proxy_funcs + '\n@app.get("/login", response_class=HTMLResponse)\n', 1)

console_routes = '''\n\n@app.get("/vm/{name}/console", response_class=HTMLResponse)\ndef vm_console_page(request: Request, name: str):\n    auth_redirect = require_auth(request)\n    if auth_redirect:\n        return auth_redirect\n    if not valid_vm_name(name) or not vm_exists(name):\n        return RedirectResponse(url="/", status_code=303)\n    return templates.TemplateResponse("console.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vm": vm_details(name), "console": console_info(name)})\n\n\n@app.websocket("/console/ws/{token}")\nasync def console_websocket(websocket: WebSocket, token: str):\n    if user_from_session_token(websocket.cookies.get("virtuality_session")) != AUTH_USER:\n        await websocket.close(code=1008)\n        return\n    try:\n        payload = console_serializer.loads(token)\n        vm_name = payload.get("vm")\n        target_port = int(payload.get("port"))\n    except Exception:\n        await websocket.close(code=1008)\n        return\n    if not valid_vm_name(vm_name) or not vm_exists(vm_name) or target_port < 5900 or target_port > 5999:\n        await websocket.close(code=1008)\n        return\n    await websocket.accept()\n    try:\n        reader, writer = await asyncio.open_connection("127.0.0.1", target_port)\n    except Exception:\n        await websocket.close(code=1011)\n        return\n    try:\n        await asyncio.gather(proxy_vnc_to_websocket(reader, websocket), proxy_websocket_to_vnc(websocket, writer))\n    except (WebSocketDisconnect, asyncio.CancelledError, ConnectionError):\n        pass\n    finally:\n        writer.close()\n        await writer.wait_closed()\n'''
text = text.replace('\n\n@app.get("/vm/{name}", response_class=HTMLResponse)\n', console_routes + '\n\n@app.get("/vm/{name}", response_class=HTMLResponse)\n', 1)

app_path.write_text(text)
print(f'noVNC console patch applied: {app_path}')
