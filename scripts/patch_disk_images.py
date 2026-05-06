#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

if 'DISK_IMAGES_DIR = Path("/var/lib/virtuality/disk-images")' not in text:
    text = text.replace('IMAGES_DIR = Path("/var/lib/virtuality/images")\n', 'IMAGES_DIR = Path("/var/lib/virtuality/images")\nDISK_IMAGES_DIR = Path("/var/lib/virtuality/disk-images")\n', 1)
    changed.append('DISK_IMAGES_DIR added')
else:
    changed.append('DISK_IMAGES_DIR already present')

helpers = r'''

def list_disk_image_files() -> list[dict[str, str]]:
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for item in sorted(list(DISK_IMAGES_DIR.glob("*.img")) + list(DISK_IMAGES_DIR.glob("*.raw")) + list(DISK_IMAGES_DIR.glob("*.qcow2"))):
        try:
            stat = item.stat()
            size_gb = stat.st_size / 1024 / 1024 / 1024
            updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size_gb = 0
            updated = "unknown"
        files.append({"name": item.name, "path": str(item), "format": item.suffix.lower().lstrip('.'), "size": f"{size_gb:.2f} GB", "updated": updated})
    return files


def safe_disk_image_filename(filename: str) -> str | None:
    name = Path(filename or "").name.strip().replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    if not name.lower().endswith((".img", ".raw", ".qcow2")):
        return None
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,180}\.(img|raw|qcow2)", name, re.IGNORECASE):
        return None
    return name


def disk_image_path_by_name(name: str) -> Path | None:
    safe_name = safe_disk_image_filename(name)
    if not safe_name:
        return None
    path = (DISK_IMAGES_DIR / safe_name).resolve()
    if DISK_IMAGES_DIR.resolve() not in path.parents:
        return None
    return path


def disk_image_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip('.')
    if suffix == 'qcow2':
        return 'qcow2'
    return 'raw'


def bridge_exists(name: str) -> bool:
    if not name or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", name):
        return False
    return run_cmd(["ip", "link", "show", name], timeout=5)["ok"]
'''

if 'def list_disk_image_files() -> list[dict[str, str]]:' not in text:
    marker = '\n\ndef valid_vm_name(name: str) -> bool:'
    if marker not in text:
        raise SystemExit('valid_vm_name marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('disk image helpers added')
else:
    changed.append('disk image helpers already present')

text = text.replace('\n\ndef bridge_exists(name: str) -> bool:\n    if not name or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", name):\n        return False\n    return run_cmd(["ip", "link", "show", name], timeout=5)["ok"]\n\n\ndef bridge_exists(name: str) -> bool:\n    if not name or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", name):\n        return False\n    return run_cmd(["ip", "link", "show", name], timeout=5)["ok"]\n', '\n\ndef bridge_exists(name: str) -> bool:\n    if not name or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", name):\n        return False\n    return run_cmd(["ip", "link", "show", name], timeout=5)["ok"]\n')

old_context = '"isos": list_iso_files(), "error": error, "profile": profile, "form": form or {"memory": 2048, "vcpus": 2, "disk_size": 20, "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}'
new_context = '"isos": list_iso_files(), "disk_images": list_disk_image_files(), "error": error, "profile": profile, "form": form or {"memory": 2048, "vcpus": 2, "disk_size": 20, "source_type": "iso", "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}'
if old_context in text:
    text = text.replace(old_context, new_context, 1)
    changed.append('vm form context gets disk images')
elif '"disk_images": list_disk_image_files()' in text:
    changed.append('vm form context already has disk images')
else:
    raise SystemExit('vm_form_context marker not found')

routes = r'''

@app.get("/disk-images", response_class=HTMLResponse)
def disk_images_page(request: Request, error: str | None = None):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": error})


@app.post("/disk-images/upload", response_class=HTMLResponse)
def disk_image_upload(request: Request, image_file: UploadFile = File(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    safe_name = safe_disk_image_filename(image_file.filename or "")
    if not safe_name:
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": "Можно загружать только .img, .raw или .qcow2 файлы с безопасным именем."}, status_code=400)
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = DISK_IMAGES_DIR / safe_name
    if target.exists():
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": f"Образ уже существует: {safe_name}"}, status_code=400)
    tmp_target = DISK_IMAGES_DIR / f".{safe_name}.uploading"
    try:
        with tmp_target.open("wb") as out:
            shutil.copyfileobj(image_file.file, out)
        tmp_target.rename(target)
    except Exception as exc:
        tmp_target.unlink(missing_ok=True)
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": f"Ошибка загрузки образа: {exc}"}, status_code=500)
    return RedirectResponse(url="/disk-images", status_code=303)


@app.post("/disk-images/{name}/delete")
def disk_image_delete(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    path = disk_image_path_by_name(name)
    if path and path.exists() and path.is_file():
        path.unlink()
    return RedirectResponse(url="/disk-images", status_code=303)
'''

if '@app.get("/disk-images"' not in text:
    marker = '\n\n@app.get("/network", response_class=HTMLResponse)'
    if marker not in text:
        raise SystemExit('network route marker not found')
    text = text.replace(marker, routes + marker, 1)
    changed.append('disk image routes added')
else:
    changed.append('disk image routes already present')

old_sig = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(...), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
new_sig = 'def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(""), disk_image_path: str = Form(""), source_type: str = Form("iso"), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):'
if old_sig in text:
    text = text.replace(old_sig, new_sig, 1)
    changed.append('vm create signature supports disk images')
elif new_sig in text:
    changed.append('vm create signature already supports disk images')
else:
    raise SystemExit('vm_create_submit signature marker not found')

old_body = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "network_mode": network_mode, "bridge": bridge}'
new_body = 'form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "disk_image_path": disk_image_path, "source_type": source_type, "network_mode": network_mode, "bridge": bridge}'
if old_body in text:
    text = text.replace(old_body, new_body, 1)
    changed.append('vm create form state supports source type')

old_iso_validation = '''    else:
        iso = Path(iso_path).resolve()
        if ISO_DIR.resolve() not in iso.parents or iso.suffix.lower() != ".iso" or not iso.exists():
            error = "ISO должен быть существующим .iso файлом из /var/lib/virtuality/iso."
'''
new_iso_validation = '''    elif source_type not in ("iso", "disk_image"):
        error = "Некорректный источник VM."
    elif network_mode == "bridge" and not bridge_exists(bridge):
        error = f"Bridge {bridge} не найден на сервере. Для VPS выбери режим NAT Router — virtuality-nat, либо сначала создай bridge {bridge}."
    else:
        if source_type == "iso":
            iso = Path(iso_path).resolve()
            if ISO_DIR.resolve() not in iso.parents or iso.suffix.lower() != ".iso" or not iso.exists():
                error = "ISO должен быть существующим .iso файлом из /var/lib/virtuality/iso."
        else:
            disk_image = Path(disk_image_path).resolve()
            if DISK_IMAGES_DIR.resolve() not in disk_image.parents or disk_image.suffix.lower() not in (".img", ".raw", ".qcow2") or not disk_image.exists():
                error = "Образ диска должен быть существующим .img, .raw или .qcow2 файлом из /var/lib/virtuality/disk-images."
'''
if old_iso_validation in text:
    text = text.replace(old_iso_validation, new_iso_validation, 1)
    changed.append('vm create validation supports disk image source')
elif 'source_type == "iso"' in text and 'DISK_IMAGES_DIR.resolve()' in text:
    changed.append('vm create validation already supports disk image source')
else:
    raise SystemExit('vm validation marker not found')

old_cmd = '''    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = IMAGES_DIR / f"{name}.qcow2"
    if disk_path.exists():
        return vm_form_context(request, error=f"Диск уже существует: {disk_path}", form=form, status_code=400)

    profile = host_profile.load_host_profile()
    is_arm = profile.get("recommended_guest_arch") == "aarch64"
    network_arg = f"network={network_core.NETWORK_NAME},model=virtio" if network_mode == "nat" else f"bridge={bridge},model=virtio"
    cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus)]
    if is_arm:
        cmd += ["--arch", "aarch64", "--machine", "virt", "--cpu", "host", "--virt-type", "kvm", "--boot", "uefi"]
    cmd += ["--disk", f"path={disk_path},size={disk_size},format=qcow2,bus=virtio", "--cdrom", iso_path, "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"]
'''
new_cmd = '''    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = IMAGES_DIR / f"{name}.qcow2"
    if disk_path.exists():
        return vm_form_context(request, error=f"Диск уже существует: {disk_path}", form=form, status_code=400)

    profile = host_profile.load_host_profile()
    is_arm = profile.get("recommended_guest_arch") == "aarch64"
    virt_type = "kvm" if profile.get("kvm_device") else "qemu"
    network_arg = f"network={network_core.NETWORK_NAME},model=virtio" if network_mode == "nat" else f"bridge={bridge},model=virtio"
    cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus), "--virt-type", virt_type]
    if is_arm:
        cmd += ["--arch", "aarch64", "--machine", "virt", "--cpu", "host" if virt_type == "kvm" else "cortex-a57", "--boot", "uefi"]

    if source_type == "disk_image":
        source_disk = Path(disk_image_path).resolve()
        source_format = disk_image_format(source_disk)
        convert_cmd = f"qemu-img convert -p -f {source_format} -O qcow2 {source_disk} {disk_path}"
        virt_cmd = " ".join(cmd + ["--import", "--disk", f"path={disk_path},format=qcow2,bus=virtio", "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"])
        cmd = ["bash", "-lc", f"set -euo pipefail; {convert_cmd}; {virt_cmd}"]
    else:
        cmd += ["--disk", f"path={disk_path},size={disk_size},format=qcow2,bus=virtio", "--cdrom", iso_path, "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"]
'''
if old_cmd in text:
    text = text.replace(old_cmd, new_cmd, 1)
    changed.append('vm create command supports qemu fallback and disk image import')
elif 'virt_type = "kvm" if profile.get("kvm_device") else "qemu"' in text:
    changed.append('vm create command already supports qemu fallback')
elif 'source_type == "disk_image"' in text and 'qemu-img convert' in text:
    # Upgrade existing disk-image-aware command to qemu fallback in-place.
    text = text.replace('cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus)]', 'virt_type = "kvm" if profile.get("kvm_device") else "qemu"\n    cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus), "--virt-type", virt_type]', 1)
    text = text.replace('["--arch", "aarch64", "--machine", "virt", "--cpu", "host", "--virt-type", "kvm", "--boot", "uefi"]', '["--arch", "aarch64", "--machine", "virt", "--cpu", "host" if virt_type == "kvm" else "cortex-a57", "--boot", "uefi"]', 1)
    changed.append('existing vm create command was upgraded with qemu fallback')
else:
    raise SystemExit('vm command marker not found')

text = text.replace('"iso_path": iso_path, "host_profile"', '"iso_path": iso_path, "disk_image_path": disk_image_path, "source_type": source_type, "host_profile"')

app_path.write_text(text)
print('disk images patch applied:')
for item in changed:
    print(f'- {item}')
