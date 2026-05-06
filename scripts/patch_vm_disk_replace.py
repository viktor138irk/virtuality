#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

helper = r'''

def vm_exists(name: str) -> bool:
    if not name or not valid_vm_name(name):
        return False
    return run_cmd(["virsh", "dominfo", name], timeout=8)["ok"]
'''
if 'def vm_exists(name: str) -> bool:' not in text:
    marker = '\n\ndef list_vms() -> list[dict[str, str]]:'
    if marker not in text:
        raise SystemExit('list_vms marker not found')
    text = text.replace(marker, helper + marker, 1)
    changed.append('vm_exists helper added')
else:
    changed.append('vm_exists helper already present')

old_sig = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(""), disk_image_path: str = Form(""), source_type: str = Form("iso"), guest_arch: str = Form("auto"), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
new_sig = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(""), disk_image_path: str = Form(""), source_type: str = Form("iso"), guest_arch: str = Form("auto"), replace_existing_disk: str = Form("0"), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
if old_sig in text:
    text = text.replace(old_sig, new_sig, 1)
    changed.append('replace_existing_disk added to signature')
elif 'replace_existing_disk: str = Form("0")' in text:
    changed.append('replace_existing_disk already in signature')
else:
    raise SystemExit('vm_create_submit signature marker not found')

old_form = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "disk_image_path": disk_image_path, "source_type": source_type, "guest_arch": guest_arch, "network_mode": network_mode, "bridge": bridge}'
new_form = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "disk_image_path": disk_image_path, "source_type": source_type, "guest_arch": guest_arch, "replace_existing_disk": replace_existing_disk, "network_mode": network_mode, "bridge": bridge}'
if old_form in text:
    text = text.replace(old_form, new_form, 1)
    changed.append('replace_existing_disk stored in form')
elif '"replace_existing_disk": replace_existing_disk' in text:
    changed.append('replace_existing_disk already stored in form')

old_disk_check = '''    disk_path = IMAGES_DIR / f"{name}.qcow2"
    if disk_path.exists():
        return vm_form_context(request, error=f"Диск уже существует: {disk_path}", form=form, status_code=400)

    profile = host_profile.load_host_profile()
'''
new_disk_check = '''    disk_path = IMAGES_DIR / f"{name}.qcow2"
    if vm_exists(name):
        return vm_form_context(request, error=f"VM с именем {name} уже существует. Выбери другое имя или удали существующую VM.", form=form, status_code=400)
    if disk_path.exists():
        if replace_existing_disk == "1":
            disk_path.unlink()
        else:
            return vm_form_context(request, error=f"Диск уже существует: {disk_path}. Это может быть остаток неудачной установки. Включи опцию «Заменить существующий диск», если VM с таким именем не нужна.", form=form, status_code=400)

    profile = host_profile.load_host_profile()
'''
if old_disk_check in text:
    text = text.replace(old_disk_check, new_disk_check, 1)
    changed.append('disk replace logic added')
elif 'Заменить существующий диск' in text and 'replace_existing_disk == "1"' in text:
    changed.append('disk replace logic already present')
else:
    raise SystemExit('disk_path exists marker not found')

app_path.write_text(text)
print('vm disk replace patch applied:')
for item in changed:
    print(f'- {item}')
