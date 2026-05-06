#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

helpers = r'''

def vm_arch_options(profile: dict[str, Any]) -> list[dict[str, str]]:
    host_arch = str(profile.get("arch", ""))
    options = [
        {"value": "auto", "label": "Авто — рекомендовано для этого хоста"},
        {"value": "x86_64", "label": "x86_64 / amd64 — обычные ПК и серверы"},
        {"value": "aarch64", "label": "ARM64 / aarch64 — Raspberry Pi / Orange Pi / ARM Server"},
    ]
    if host_arch not in ("x86_64", "amd64", "aarch64", "arm64"):
        options.append({"value": "generic", "label": "Generic QEMU — экспериментально"})
    return options


def resolve_vm_arch(arch_choice: str, profile: dict[str, Any]) -> str:
    if arch_choice == "auto" or not arch_choice:
        return str(profile.get("recommended_guest_arch") or "x86_64")
    if arch_choice in ("x86_64", "aarch64", "generic"):
        return arch_choice
    return str(profile.get("recommended_guest_arch") or "x86_64")


def append_arch_args(cmd: list[str], guest_arch: str) -> list[str]:
    if guest_arch == "aarch64":
        return cmd + ["--arch", "aarch64", "--machine", "virt", "--cpu", "host", "--virt-type", "kvm", "--boot", "uefi"]
    if guest_arch == "x86_64":
        return cmd + ["--arch", "x86_64"]
    return cmd
'''

if 'def vm_arch_options(profile: dict[str, Any])' not in text:
    marker = '\n\ndef vm_form_context('
    if marker not in text:
        raise SystemExit('vm_form_context marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('architecture helpers added')
else:
    changed.append('architecture helpers already present')

old_context = '"disk_images": list_disk_image_files(), "error": error, "profile": profile, "form": form or {"memory": 2048, "vcpus": 2, "disk_size": 20, "source_type": "iso", "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}'
new_context = '"disk_images": list_disk_image_files(), "arch_options": vm_arch_options(profile), "error": error, "profile": profile, "form": form or {"memory": 2048, "vcpus": 2, "disk_size": 20, "source_type": "iso", "guest_arch": "auto", "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}'
if old_context in text:
    text = text.replace(old_context, new_context, 1)
    changed.append('vm form context gets arch options')
elif '"arch_options": vm_arch_options(profile)' in text:
    changed.append('vm form context already has arch options')
else:
    # support older app without disk_images patch
    old_context2 = '"isos": list_iso_files(), "error": error, "profile": profile, "form": form or {"memory": 2048, "vcpus": 2, "disk_size": 20, "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}'
    new_context2 = '"isos": list_iso_files(), "disk_images": list_disk_image_files() if "list_disk_image_files" in globals() else [], "arch_options": vm_arch_options(profile), "error": error, "profile": profile, "form": form or {"memory": 2048, "vcpus": 2, "disk_size": 20, "source_type": "iso", "guest_arch": "auto", "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}'
    if old_context2 in text:
        text = text.replace(old_context2, new_context2, 1)
        changed.append('vm form context gets arch options fallback')
    else:
        raise SystemExit('vm_form_context payload marker not found')

old_sig = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(""), disk_image_path: str = Form(""), source_type: str = Form("iso"), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
new_sig = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(""), disk_image_path: str = Form(""), source_type: str = Form("iso"), guest_arch: str = Form("auto"), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
if old_sig in text:
    text = text.replace(old_sig, new_sig, 1)
    changed.append('vm create signature gets guest_arch')
elif new_sig in text:
    changed.append('vm create signature already has guest_arch')
else:
    old_sig2 = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(...), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
    new_sig2 = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(...), guest_arch: str = Form("auto"), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
    if old_sig2 in text:
        text = text.replace(old_sig2, new_sig2, 1)
        changed.append('vm create signature gets guest_arch fallback')
    else:
        raise SystemExit('vm_create_submit signature marker not found')

old_form = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "disk_image_path": disk_image_path, "source_type": source_type, "network_mode": network_mode, "bridge": bridge}'
new_form = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "disk_image_path": disk_image_path, "source_type": source_type, "guest_arch": guest_arch, "network_mode": network_mode, "bridge": bridge}'
if old_form in text:
    text = text.replace(old_form, new_form, 1)
    changed.append('form state stores guest_arch')
elif '"guest_arch": guest_arch' in text:
    changed.append('form state already stores guest_arch')
else:
    old_form2 = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "network_mode": network_mode, "bridge": bridge}'
    new_form2 = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "guest_arch": guest_arch, "network_mode": network_mode, "bridge": bridge}'
    if old_form2 in text:
        text = text.replace(old_form2, new_form2, 1)
        changed.append('form state stores guest_arch fallback')

old_validation = 'elif source_type not in ("iso", "disk_image"):\n        error = "Некорректный источник VM."'
new_validation = 'elif source_type not in ("iso", "disk_image"):\n        error = "Некорректный источник VM."\n    elif guest_arch not in ("auto", "x86_64", "aarch64", "generic"):\n        error = "Некорректная архитектура VM."'
if old_validation in text and 'Некорректная архитектура VM' not in text:
    text = text.replace(old_validation, new_validation, 1)
    changed.append('guest_arch validation added')
elif 'Некорректная архитектура VM' in text:
    changed.append('guest_arch validation already present')

old_arch_block = '''    profile = host_profile.load_host_profile()
    is_arm = profile.get("recommended_guest_arch") == "aarch64"
    network_arg = f"network={network_core.NETWORK_NAME},model=virtio" if network_mode == "nat" else f"bridge={bridge},model=virtio"
    cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus)]
    if is_arm:
        cmd += ["--arch", "aarch64", "--machine", "virt", "--cpu", "host", "--virt-type", "kvm", "--boot", "uefi"]
'''
new_arch_block = '''    profile = host_profile.load_host_profile()
    resolved_guest_arch = resolve_vm_arch(guest_arch, profile)
    network_arg = f"network={network_core.NETWORK_NAME},model=virtio" if network_mode == "nat" else f"bridge={bridge},model=virtio"
    cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus)]
    cmd = append_arch_args(cmd, resolved_guest_arch)
'''
if old_arch_block in text:
    text = text.replace(old_arch_block, new_arch_block, 1)
    changed.append('virt-install arch args made selectable')
elif 'resolved_guest_arch = resolve_vm_arch' in text:
    changed.append('virt-install arch args already selectable')
else:
    raise SystemExit('arch command block marker not found')

text = text.replace('"guest_arch": profile.get("recommended_guest_arch"),', '"guest_arch": resolved_guest_arch, "guest_arch_choice": guest_arch,')
text = text.replace('"guest_arch": profile.get("recommended_guest_arch")', '"guest_arch": resolved_guest_arch, "guest_arch_choice": guest_arch')

app_path.write_text(text)
print('vm architecture patch applied:')
for item in changed:
    print(f'- {item}')
