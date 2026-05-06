#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

app_dir = app_path.resolve().parent
template_path = app_dir / 'templates' / 'vm_create.html'
changed = []

text = app_path.read_text()

helpers = r'''

def vm_boot_order_options() -> list[dict[str, str]]:
    return [
        {"value": "auto", "label": "Auto — по источнику VM"},
        {"value": "disk", "label": "Сначала диск"},
        {"value": "cdrom_disk", "label": "Сначала ISO/CD-ROM, потом диск"},
        {"value": "disk_cdrom", "label": "Сначала диск, потом ISO/CD-ROM"},
        {"value": "network_disk", "label": "Сначала сеть/PXE, потом диск"},
    ]


def normalize_boot_order(value: str, source_type: str) -> str:
    value = (value or "auto").strip()
    if value == "auto":
        return "cdrom_disk" if source_type == "iso" else "disk"
    if value in ("disk", "cdrom_disk", "disk_cdrom", "network_disk"):
        return value
    return "cdrom_disk" if source_type == "iso" else "disk"


def virt_boot_arg(boot_order: str, is_arm: bool) -> str:
    mapping = {
        "disk": "hd",
        "cdrom_disk": "cdrom,hd",
        "disk_cdrom": "hd,cdrom",
        "network_disk": "network,hd",
    }
    value = mapping.get(boot_order, "hd")
    if is_arm:
        return "uefi," + value
    return value
'''

if 'def vm_boot_order_options()' not in text:
    marker = '\n\ndef vm_form_context('
    if marker not in text:
        raise SystemExit('vm_form_context marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('boot order helpers added')
else:
    changed.append('boot order helpers already present')

if '"boot_options": vm_boot_order_options()' not in text:
    text = text.replace('"arch_options": vm_arch_options(),', '"arch_options": vm_arch_options(), "boot_options": vm_boot_order_options(),')
    text = text.replace('"guest_arch": "auto", "network_mode"', '"guest_arch": "auto", "boot_order": "auto", "network_mode"')
    changed.append('vm form context gets boot options')
else:
    changed.append('vm form context already has boot options')

if 'boot_order: str = Form("auto")' not in text:
    text = text.replace('guest_arch: str = Form("auto"), network_mode:', 'guest_arch: str = Form("auto"), boot_order: str = Form("auto"), network_mode:', 1)
    changed.append('vm create signature supports boot order')
else:
    changed.append('vm create signature already supports boot order')

if '"boot_order": boot_order' not in text:
    text = text.replace('"guest_arch": guest_arch, "network_mode"', '"guest_arch": guest_arch, "boot_order": boot_order, "network_mode"', 1)
    changed.append('vm create form state supports boot order')
else:
    changed.append('vm create form state already supports boot order')

if 'boot_order not in ("auto", "disk", "cdrom_disk", "disk_cdrom", "network_disk")' not in text:
    text = text.replace(
        'elif guest_arch not in ("auto", "x86_64", "aarch64", "generic"):\n        error = "Некорректная архитектура VM."',
        'elif guest_arch not in ("auto", "x86_64", "aarch64", "generic"):\n        error = "Некорректная архитектура VM."\n    elif boot_order not in ("auto", "disk", "cdrom_disk", "disk_cdrom", "network_disk"):\n        error = "Некорректный порядок загрузки VM."',
        1,
    )
    changed.append('vm create validation supports boot order')
else:
    changed.append('vm create validation already supports boot order')

if 'selected_boot_order = normalize_boot_order(boot_order, source_type)' not in text:
    text = text.replace('selected_arch = normalize_guest_arch(guest_arch, profile)\n', 'selected_arch = normalize_guest_arch(guest_arch, profile)\n    selected_boot_order = normalize_boot_order(boot_order, source_type)\n', 1)
    changed.append('selected boot order added')
else:
    changed.append('selected boot order already present')

# Replace ARM-specific --boot uefi in architecture block with a later unified --boot argument.
text = text.replace(', "--boot", "uefi"]', ']')

if 'cmd += ["--boot", virt_boot_arg(selected_boot_order, is_arm)]' not in text:
    marker = '    if source_type == "disk_image":\n'
    if marker not in text:
        raise SystemExit('source_type command marker not found')
    text = text.replace(marker, '    cmd += ["--boot", virt_boot_arg(selected_boot_order, is_arm)]\n\n' + marker, 1)
    changed.append('virt-install boot argument added')
else:
    changed.append('virt-install boot argument already present')

if '"boot_order": selected_boot_order' not in text:
    text = text.replace('"guest_arch": selected_arch, "host_profile"', '"guest_arch": selected_arch, "boot_order": selected_boot_order, "host_profile"')
    changed.append('operation metadata gets boot order')
else:
    changed.append('operation metadata already has boot order')

app_path.write_text(text)

if template_path.exists():
    tpl = template_path.read_text()
    original = tpl

    if 'name="boot_order"' not in tpl:
        insert = r'''
          <label>
            <span>Порядок загрузки</span>
            <select name="boot_order" required>
              {% for boot in boot_options %}
                <option value="{{ boot.value }}" {% if form.boot_order == boot.value %}selected{% endif %}>{{ boot.label }}</option>
              {% endfor %}
            </select>
          </label>
'''
        marker = '''          <label>
            <span>Режим сети</span>'''
        if marker not in tpl:
            raise SystemExit('network mode marker not found in vm_create.html')
        tpl = tpl.replace(marker, insert + '\n' + marker, 1)
        changed.append('boot order field added to vm_create.html')

    tpl = tpl.replace(
        'Virtuality импортирует .img/.raw/.qcow2 в новый qcow2-диск VM. Поле “Диск, GB” не используется.',
        'Virtuality импортирует .img/.raw/.qcow2 в новый qcow2-диск VM. Поле “Диск, GB” не используется. Для готовых дисков обычно выбирай порядок загрузки “Сначала диск”.',
    )
    tpl = tpl.replace(
        'Virtuality создаст новый диск указанного размера и запустит установку с ISO.',
        'Virtuality создаст новый диск указанного размера и запустит установку с ISO. Для установщика обычно выбирай “Сначала ISO/CD-ROM, потом диск”.',
    )

    if tpl != original:
        template_path.write_text(tpl)

print('VM boot order patch applied:')
for item in changed:
    print(f'- {item}')
