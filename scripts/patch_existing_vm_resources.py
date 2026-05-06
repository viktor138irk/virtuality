#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

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

def parse_dominfo_value(dominfo: str, label: str) -> str:
    for line in (dominfo or "").splitlines():
        if line.strip().lower().startswith(label.lower()):
            return line.split(":", 1)[1].strip()
    return ""


def kib_text_to_mb(value: str) -> int:
    match = re.search(r"(\d+)", value or "")
    if not match:
        return 0
    return max(0, int(int(match.group(1)) / 1024))


def current_vm_arch(name: str) -> str:
    result = run_cmd(["virsh", "dumpxml", name], timeout=12)
    if not result.get("ok"):
        return "unknown"
    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception:
        return "unknown"
    os_type = root.find("os/type")
    return (os_type.attrib.get("arch") if os_type is not None else "") or "unknown"


def vm_runtime_state(name: str) -> str:
    result = run_cmd(["virsh", "domstate", name], timeout=8)
    return (result.get("stdout") or "unknown").strip().lower()


def vm_resource_settings(name: str) -> dict[str, Any]:
    dominfo = run_cmd(["virsh", "dominfo", name], timeout=10).get("stdout") or ""
    state = (parse_dominfo_value(dominfo, "State") or vm_runtime_state(name)).lower()
    vcpus_raw = parse_dominfo_value(dominfo, "CPU(s)")
    used_memory_raw = parse_dominfo_value(dominfo, "Used memory")
    max_memory_raw = parse_dominfo_value(dominfo, "Max memory")
    memory_mb = kib_text_to_mb(used_memory_raw) or kib_text_to_mb(max_memory_raw) or 1024
    try:
        vcpus = int(re.search(r"\d+", vcpus_raw or "1").group(0))
    except Exception:
        vcpus = 1
    return {
        "state": state,
        "is_shutoff": state in ("shut off", "shutoff", "shut-off"),
        "memory_mb": memory_mb,
        "vcpus": vcpus,
        "arch": current_vm_arch(name),
    }


def apply_vm_resources(name: str, memory_mb: int, vcpus: int, guest_arch: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    resources = vm_resource_settings(name)
    if not resources.get("is_shutoff"):
        return False, "CPU/RAM/архитектуру можно менять только когда VM выключена. Сначала выключи VM."
    if memory_mb < 512 or memory_mb > 262144:
        return False, "RAM должна быть от 512 MB до 262144 MB."
    if vcpus < 1 or vcpus > 128:
        return False, "CPU должен быть от 1 до 128 vCPU."
    if guest_arch not in ("keep", "x86_64", "aarch64"):
        return False, "Некорректная архитектура VM."

    result = run_cmd(["virsh", "dumpxml", name], timeout=15)
    if not result.get("ok"):
        return False, result.get("stderr") or "Не удалось получить XML VM."
    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception as exc:
        return False, f"Не удалось разобрать XML VM: {exc}"

    memory_kib = str(int(memory_mb) * 1024)
    for tag in ("memory", "currentMemory"):
        node = root.find(tag)
        if node is None:
            node = ET.SubElement(root, tag)
        node.text = memory_kib
        node.set("unit", "KiB")

    vcpu_node = root.find("vcpu")
    if vcpu_node is None:
        vcpu_node = ET.SubElement(root, "vcpu")
    vcpu_node.text = str(int(vcpus))
    vcpu_node.set("placement", "static")

    arch_changed = False
    if guest_arch != "keep":
        os_type = root.find("os/type")
        if os_type is None:
            os_node = root.find("os")
            if os_node is None:
                os_node = ET.SubElement(root, "os")
            os_type = ET.SubElement(os_node, "type")
            os_type.text = "hvm"
        old_arch = os_type.attrib.get("arch", "")
        if old_arch != guest_arch:
            os_type.set("arch", guest_arch)
            if guest_arch == "aarch64":
                os_type.set("machine", "virt")
            arch_changed = True

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

    message = f"Ресурсы VM применены: CPU {vcpus}, RAM {memory_mb} MB"
    if arch_changed:
        message += f", архитектура {guest_arch}. Важно: смена архитектуры может потребовать совместимый диск/загрузчик."
    return True, message
'''

if 'def apply_vm_resources(name: str, memory_mb: int, vcpus: int, guest_arch: str)' not in text:
    marker = '\n\ndef vm_details(name: str) -> dict[str, Any]:'
    if marker not in text:
        raise SystemExit('vm_details marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('existing VM resources helpers added')
else:
    changed.append('existing VM resources helpers already present')

if '"resource_settings": vm_resource_settings(name)' not in text:
    replacements = [
        (
            '"iso_message": request.query_params.get("iso_message", ""), "iso_error": request.query_params.get("iso_error", "")})',
            '"iso_message": request.query_params.get("iso_message", ""), "iso_error": request.query_params.get("iso_error", ""), "resource_settings": vm_resource_settings(name), "resource_message": request.query_params.get("resource_message", ""), "resource_error": request.query_params.get("resource_error", "")})',
        ),
        (
            '"boot_message": request.query_params.get("boot_message", ""), "boot_error": request.query_params.get("boot_error", "")})',
            '"boot_message": request.query_params.get("boot_message", ""), "boot_error": request.query_params.get("boot_error", ""), "resource_settings": vm_resource_settings(name), "resource_message": request.query_params.get("resource_message", ""), "resource_error": request.query_params.get("resource_error", "")})',
        ),
        (
            '"host_ip": system_summary()["ip"]})',
            '"host_ip": system_summary()["ip"], "resource_settings": vm_resource_settings(name), "resource_message": request.query_params.get("resource_message", ""), "resource_error": request.query_params.get("resource_error", "")})',
        ),
    ]
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            changed.append('vm detail context gets resource settings')
            break
    else:
        raise SystemExit('vm detail context marker not found')
else:
    changed.append('vm detail context already has resource settings')

route = r'''

@app.post("/vm/{name}/resources")
def vm_resources_apply(request: Request, name: str, memory_mb: int = Form(...), vcpus: int = Form(...), guest_arch: str = Form("keep")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = apply_vm_resources(name, memory_mb, vcpus, guest_arch)
    if ok:
        return RedirectResponse(url=f"/vm/{name}?resource_message={message}", status_code=303)
    return RedirectResponse(url=f"/vm/{name}?resource_error={message}", status_code=303)
'''

if '@app.post("/vm/{name}/resources")' not in text:
    marker = '\n\n@app.post("/vm/{name}/boot-order")'
    if marker not in text:
        marker = '\n\n@app.post("/vm/{name}/iso/mount")'
    if marker not in text:
        marker = '\n\n@app.post("/vm/{name}/{action}")'
    if marker not in text:
        raise SystemExit('vm action marker not found')
    text = text.replace(marker, route + marker, 1)
    changed.append('existing VM resources route added')
else:
    changed.append('existing VM resources route already present')

app_path.write_text(text)

print('existing VM resources patch applied:')
for item in changed:
    print(f'- {item}')
