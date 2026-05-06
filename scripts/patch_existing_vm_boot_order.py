#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

app_dir = app_path.resolve().parent
template_path = app_dir / 'templates' / 'vm_detail.html'
changed = []

text = app_path.read_text()

if 'import tempfile' not in text:
    text = text.replace('import subprocess\n', 'import subprocess\nimport tempfile\n', 1)
    changed.append('tempfile import added')
else:
    changed.append('tempfile import already present')

if 'import xml.etree.ElementTree as ET' not in text:
    text = text.replace('from typing import Any\n', 'from typing import Any\nimport xml.etree.ElementTree as ET\n', 1)
    changed.append('ElementTree import added')
else:
    changed.append('ElementTree import already present')

helpers = r'''

def vm_boot_order_label(value: str) -> str:
    labels = {item["value"]: item["label"] for item in vm_boot_order_options()}
    return labels.get(value or "auto", "Auto — по источнику VM")


def boot_order_to_devs(value: str) -> list[str]:
    value = normalize_boot_order(value, "disk_image")
    mapping = {
        "disk": ["hd"],
        "cdrom_disk": ["cdrom", "hd"],
        "disk_cdrom": ["hd", "cdrom"],
        "network_disk": ["network", "hd"],
    }
    return mapping.get(value, ["hd"])


def boot_devs_to_order(devs: list[str]) -> str:
    clean = [item for item in devs if item in ("hd", "cdrom", "network")]
    if clean[:2] == ["cdrom", "hd"]:
        return "cdrom_disk"
    if clean[:2] == ["hd", "cdrom"]:
        return "disk_cdrom"
    if clean[:2] == ["network", "hd"]:
        return "network_disk"
    if clean[:1] == ["hd"]:
        return "disk"
    return "auto"


def current_vm_boot_order(name: str) -> str:
    result = run_cmd(["virsh", "dumpxml", name], timeout=12)
    if not result.get("ok"):
        return "auto"
    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception:
        return "auto"
    os_node = root.find("os")
    if os_node is None:
        return "auto"
    devs = []
    for boot in os_node.findall("boot"):
        dev = boot.attrib.get("dev", "").strip()
        if dev:
            devs.append(dev)
    return boot_devs_to_order(devs)


def apply_vm_boot_order(name: str, boot_order: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    if boot_order not in ("auto", "disk", "cdrom_disk", "disk_cdrom", "network_disk"):
        return False, "Некорректный порядок загрузки VM."

    selected = normalize_boot_order(boot_order, "disk_image")
    result = run_cmd(["virsh", "dumpxml", name], timeout=15)
    if not result.get("ok"):
        return False, result.get("stderr") or "Не удалось получить XML VM."

    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception as exc:
        return False, f"Не удалось разобрать XML VM: {exc}"

    os_node = root.find("os")
    if os_node is None:
        os_node = ET.SubElement(root, "os")

    # Remove only boot-order entries. Keep loader, nvram, type and firmware-related nodes intact.
    for boot in list(os_node.findall("boot")):
        os_node.remove(boot)

    insert_at = 0
    for idx, child in enumerate(list(os_node)):
        if child.tag in ("type", "loader", "nvram", "firmware", "smbios", "bootmenu"):
            insert_at = idx + 1

    for dev in reversed(boot_order_to_devs(selected)):
        boot_node = ET.Element("boot", {"dev": dev})
        os_node.insert(insert_at, boot_node)

    xml_text = ET.tostring(root, encoding="unicode")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as handle:
        handle.write(xml_text)
        tmp_name = handle.name
    try:
        define = run_cmd(["virsh", "define", tmp_name], timeout=30)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    if not define.get("ok"):
        return False, define.get("stderr") or "virsh define завершился ошибкой."
    return True, f"Порядок загрузки применён: {vm_boot_order_label(selected)}. Если VM запущена, изменение сработает после перезапуска."
'''

if 'def apply_vm_boot_order(name: str, boot_order: str)' not in text:
    marker = '\n\ndef vm_details(name: str) -> dict[str, Any]:'
    if marker not in text:
        raise SystemExit('vm_details marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('existing VM boot order helpers added')
else:
    changed.append('existing VM boot order helpers already present')

old = '"host_ip": system_summary()["ip"]})'
new = '"host_ip": system_summary()["ip"], "boot_options": vm_boot_order_options(), "current_boot_order": current_vm_boot_order(name), "boot_message": request.query_params.get("boot_message", ""), "boot_error": request.query_params.get("boot_error", "")})'
if '"current_boot_order": current_vm_boot_order(name)' not in text:
    if old not in text:
        raise SystemExit('vm_detail context marker not found')
    text = text.replace(old, new, 1)
    changed.append('vm detail context gets boot order')
else:
    changed.append('vm detail context already has boot order')

route = r'''

@app.post("/vm/{name}/boot-order")
def vm_boot_order_apply(request: Request, name: str, boot_order: str = Form("auto")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = apply_vm_boot_order(name, boot_order)
    if ok:
        return RedirectResponse(url=f"/vm/{name}?boot_message={message}", status_code=303)
    return RedirectResponse(url=f"/vm/{name}?boot_error={message}", status_code=303)
'''

if '@app.post("/vm/{name}/boot-order")' not in text:
    marker = '\n\n@app.post("/vm/{name}/{action}")'
    if marker not in text:
        raise SystemExit('generic vm action marker not found')
    text = text.replace(marker, route + marker, 1)
    changed.append('existing VM boot order route added')
else:
    changed.append('existing VM boot order route already present')

app_path.write_text(text)

if template_path.exists():
    tpl = template_path.read_text()
    original = tpl
    if 'action="/vm/{{ vm.name }}/boot-order"' not in tpl:
        block = r'''

    {% if boot_message %}
      <div class="alert success">{{ boot_message }}</div>
    {% endif %}
    {% if boot_error %}
      <div class="alert danger">{{ boot_error }}</div>
    {% endif %}

    <section class="card">
      <div class="card-head">
        <h2>Порядок загрузки</h2>
        <span class="pill">boot order</span>
      </div>
      <form method="post" action="/vm/{{ vm.name }}/boot-order" class="form-grid">
        <label>
          <span>Порядок загрузки VM</span>
          <select name="boot_order" required>
            {% for boot in boot_options %}
              <option value="{{ boot.value }}" {% if current_boot_order == boot.value %}selected{% endif %}>{{ boot.label }}</option>
            {% endfor %}
          </select>
        </label>
        <button class="primary wide" type="submit">Применить порядок загрузки</button>
      </form>
      <p class="muted small-note">Настройка меняет XML-конфигурацию VM через virsh define. Если машина сейчас запущена, новый порядок загрузки сработает после перезапуска.</p>
    </section>
'''
        marker = '\n\n    <section class="grid two">'
        if marker not in tpl:
            raise SystemExit('vm detail grid marker not found')
        tpl = tpl.replace(marker, block + marker, 1)
        changed.append('boot order card added to vm_detail.html')
    if tpl != original:
        template_path.write_text(tpl)

print('existing VM boot order patch applied:')
for item in changed:
    print(f'- {item}')
