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
warnings = []

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

def replace_once(source: str, old: str, new: str, label: str) -> str:
    if old in source:
        changed.append(label)
        return source.replace(old, new, 1)
    warnings.append(f'{label}: marker not found, skipped')
    return source

if 'def vm_boot_order_options()' not in text:
    marker = '\n\ndef vm_form_context('
    if marker in text:
        text = text.replace(marker, helpers + marker, 1)
        changed.append('boot order helpers added')
    else:
        warnings.append('vm_form_context marker not found, helpers skipped')
else:
    changed.append('boot order helpers already present')

if '"boot_options": vm_boot_order_options()' not in text:
    if '"arch_options": vm_arch_options(),' in text:
        text = text.replace('"arch_options": vm_arch_options(),', '"arch_options": vm_arch_options(), "boot_options": vm_boot_order_options(),', 1)
        changed.append('vm form context gets boot options')
    else:
        warnings.append('arch_options marker not found, boot_options skipped')
else:
    changed.append('vm form context already has boot options')

if '"boot_order": "auto"' not in text:
    if '"guest_arch": "auto", "network_mode"' in text:
        text = text.replace('"guest_arch": "auto", "network_mode"', '"guest_arch": "auto", "boot_order": "auto", "network_mode"', 1)
        changed.append('default form boot_order added')
    elif '"guest_arch": "auto"' in text:
        text = text.replace('"guest_arch": "auto"', '"guest_arch": "auto", "boot_order": "auto"', 1)
        changed.append('default form boot_order added near guest_arch')
    else:
        warnings.append('default form marker not found, boot_order default skipped')
else:
    changed.append('default form boot_order already present')

if 'boot_order: str = Form("auto")' not in text:
    signature_patterns = [
        ('guest_arch: str = Form("auto"), network_mode:', 'guest_arch: str = Form("auto"), boot_order: str = Form("auto"), network_mode:'),
        ('guest_arch: str = Form("auto"),\n    network_mode:', 'guest_arch: str = Form("auto"),\n    boot_order: str = Form("auto"),\n    network_mode:'),
    ]
    for old, new in signature_patterns:
        if old in text:
            text = text.replace(old, new, 1)
            changed.append('vm create signature supports boot order')
            break
    else:
        warnings.append('vm create signature marker not found, skipped')
else:
    changed.append('vm create signature already supports boot order')

if '"boot_order": boot_order' not in text:
    if '"guest_arch": guest_arch, "network_mode"' in text:
        text = text.replace('"guest_arch": guest_arch, "network_mode"', '"guest_arch": guest_arch, "boot_order": boot_order, "network_mode"', 1)
        changed.append('vm create form state supports boot order')
    elif '"guest_arch": guest_arch' in text:
        text = text.replace('"guest_arch": guest_arch', '"guest_arch": guest_arch, "boot_order": boot_order', 1)
        changed.append('vm create form state supports boot order near guest_arch')
    else:
        warnings.append('vm create form state marker not found, skipped')
else:
    changed.append('vm create form state already supports boot order')

if 'boot_order not in ("auto", "disk", "cdrom_disk", "disk_cdrom", "network_disk")' not in text:
    validation_old = 'elif guest_arch not in ("auto", "x86_64", "aarch64", "generic"):\n        error = "Некорректная архитектура VM."'
    validation_new = validation_old + '\n    elif boot_order not in ("auto", "disk", "cdrom_disk", "disk_cdrom", "network_disk"):\n        error = "Некорректный порядок загрузки VM."'
    if validation_old in text:
        text = text.replace(validation_old, validation_new, 1)
        changed.append('vm create validation supports boot order')
    else:
        warnings.append('vm create validation marker not found, skipped')
else:
    changed.append('vm create validation already supports boot order')

if 'selected_boot_order = normalize_boot_order(boot_order, source_type)' not in text:
    if 'selected_arch = normalize_guest_arch(guest_arch, profile)\n' in text:
        text = text.replace('selected_arch = normalize_guest_arch(guest_arch, profile)\n', 'selected_arch = normalize_guest_arch(guest_arch, profile)\n    selected_boot_order = normalize_boot_order(boot_order, source_type)\n', 1)
        changed.append('selected boot order added')
    else:
        warnings.append('selected_arch marker not found, selected boot order skipped')
else:
    changed.append('selected boot order already present')

# Replace legacy ARM-specific --boot uefi in architecture block with unified --boot argument if present.
if ', "--boot", "uefi"]' in text:
    text = text.replace(', "--boot", "uefi"]', ']')
    changed.append('legacy ARM --boot uefi removed')

if 'cmd += ["--boot", virt_boot_arg(selected_boot_order, is_arm)]' not in text:
    marker = '    if source_type == "disk_image":\n'
    if marker in text:
        text = text.replace(marker, '    cmd += ["--boot", virt_boot_arg(selected_boot_order, is_arm)]\n\n' + marker, 1)
        changed.append('virt-install boot argument added')
    else:
        warnings.append('source_type command marker not found, boot argument skipped')
else:
    changed.append('virt-install boot argument already present')

if '"boot_order": selected_boot_order' not in text:
    if '"guest_arch": selected_arch, "host_profile"' in text:
        text = text.replace('"guest_arch": selected_arch, "host_profile"', '"guest_arch": selected_arch, "boot_order": selected_boot_order, "host_profile"', 1)
        changed.append('operation metadata gets boot order')
    elif '"guest_arch": selected_arch' in text:
        text = text.replace('"guest_arch": selected_arch', '"guest_arch": selected_arch, "boot_order": selected_boot_order', 1)
        changed.append('operation metadata gets boot order near guest_arch')
    else:
        warnings.append('operation metadata marker not found, skipped')
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
        markers = [
            '            <label>\n              <span>Режим сети</span>',
            '          <label>\n            <span>Режим сети</span>',
        ]
        for marker in markers:
            if marker in tpl:
                tpl = tpl.replace(marker, insert + '\n' + marker, 1)
                changed.append('boot order field added to vm_create.html')
                break
        else:
            warnings.append('network mode marker not found in vm_create.html, boot field skipped')
    else:
        changed.append('boot order field already present in vm_create.html')

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
else:
    warnings.append(f'vm_create.html not found: {template_path}')

print('VM boot order patch applied:')
for item in changed:
    print(f'- {item}')
if warnings:
    print('Warnings:')
    for item in warnings:
        print(f'- {item}')
