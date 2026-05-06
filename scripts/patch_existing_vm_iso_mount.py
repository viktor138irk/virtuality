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

helpers = r'''

def vm_state(name: str) -> str:
    result = run_cmd(["virsh", "domstate", name], timeout=8)
    return (result.get("stdout") or "unknown").strip().lower()


def current_vm_iso(name: str) -> str:
    result = run_cmd(["virsh", "domblklist", name, "--details"], timeout=10)
    if not result.get("ok"):
        return ""
    for line in (result.get("stdout") or "").splitlines():
        if ".iso" not in line.lower():
            continue
        parts = line.split()
        if parts:
            return parts[-1]
    return ""


def detach_vm_iso(name: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    xml = run_cmd(["virsh", "dumpxml", name], timeout=12)
    cdrom_targets: list[str] = []
    if xml.get("ok"):
        try:
            root = ET.fromstring(xml.get("stdout") or "")
            devices = root.find("devices")
            if devices is not None:
                for disk in devices.findall("disk"):
                    if disk.attrib.get("device") != "cdrom":
                        continue
                    target = disk.find("target")
                    dev = target.attrib.get("dev") if target is not None else ""
                    if dev:
                        cdrom_targets.append(dev)
        except Exception:
            pass
    if not cdrom_targets:
        cdrom_targets = ["sda", "hda", "sdb", "hdc"]

    running = vm_state(name) == "running"
    errors = []
    changed_any = False
    for target in cdrom_targets:
        commands = []
        if running:
            commands.append(["virsh", "detach-disk", name, target, "--live"])
        commands.append(["virsh", "detach-disk", name, target, "--config"])
        for cmd in commands:
            result = run_cmd(cmd, timeout=30)
            if result.get("ok"):
                changed_any = True
            elif result.get("stderr"):
                errors.append(result.get("stderr"))
    if changed_any:
        return True, "ISO был отмонтирован."
    return False, errors[-1] if errors else "Подключенный ISO не найден."


def mount_vm_iso(name: str, iso_path: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    iso = Path(iso_path or "").resolve()
    try:
        iso_root = ISO_DIR.resolve()
    except Exception:
        iso_root = Path("/var/lib/virtuality/iso")
    if iso_root not in iso.parents or iso.suffix.lower() != ".iso" or not iso.exists():
        return False, "ISO должен быть существующим .iso файлом из /var/lib/virtuality/iso."

    # Keep only one mounted ISO/CD-ROM device managed by the panel.
    detach_vm_iso(name)

    running = vm_state(name) == "running"
    base = ["virsh", "attach-disk", name, str(iso), "sda", "--type", "cdrom", "--mode", "readonly"]
    if running:
        live = run_cmd(base + ["--live"], timeout=30)
        if not live.get("ok"):
            return False, live.get("stderr") or "Не удалось подключить ISO к запущенной VM."
    config = run_cmd(base + ["--config"], timeout=30)
    if not config.get("ok"):
        return False, config.get("stderr") or "Не удалось сохранить ISO в конфигурации VM."
    return True, "ISO был смонтирован в VM. Если гостевая ОС его не увидела сразу, перезагрузи VM или обнови устройства внутри гостевой ОС."
'''

if 'def mount_vm_iso(name: str, iso_path: str)' not in text:
    marker = '\n\ndef vm_details(name: str) -> dict[str, Any]:'
    if marker not in text:
        raise SystemExit('vm_details marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('existing VM ISO mount helpers added')
else:
    changed.append('existing VM ISO mount helpers already present')

if '"current_iso": current_vm_iso(name)' not in text:
    old = '"boot_message": request.query_params.get("boot_message", ""), "boot_error": request.query_params.get("boot_error", "")})'
    new = '"boot_message": request.query_params.get("boot_message", ""), "boot_error": request.query_params.get("boot_error", ""), "isos": list_iso_files(), "current_iso": current_vm_iso(name), "iso_message": request.query_params.get("iso_message", ""), "iso_error": request.query_params.get("iso_error", "")})'
    if old not in text:
        # Fallback for installations without boot-order patch context yet.
        old = '"host_ip": system_summary()["ip"]})'
        new = '"host_ip": system_summary()["ip"], "isos": list_iso_files(), "current_iso": current_vm_iso(name), "iso_message": request.query_params.get("iso_message", ""), "iso_error": request.query_params.get("iso_error", "")})'
    if old not in text:
        raise SystemExit('vm detail context marker not found')
    text = text.replace(old, new, 1)
    changed.append('vm detail context gets ISO mount data')
else:
    changed.append('vm detail context already has ISO mount data')

routes = r'''

@app.post("/vm/{name}/iso/mount")
def vm_iso_mount_apply(request: Request, name: str, iso_path: str = Form(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = mount_vm_iso(name, iso_path)
    if ok:
        return RedirectResponse(url=f"/vm/{name}?iso_message={message}", status_code=303)
    return RedirectResponse(url=f"/vm/{name}?iso_error={message}", status_code=303)


@app.post("/vm/{name}/iso/unmount")
def vm_iso_unmount_apply(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = detach_vm_iso(name)
    if ok:
        return RedirectResponse(url=f"/vm/{name}?iso_message={message}", status_code=303)
    return RedirectResponse(url=f"/vm/{name}?iso_error={message}", status_code=303)
'''

if '@app.post("/vm/{name}/iso/mount")' not in text:
    marker = '\n\n@app.post("/vm/{name}/{action}")'
    if marker not in text:
        raise SystemExit('generic vm action marker not found')
    text = text.replace(marker, routes + marker, 1)
    changed.append('existing VM ISO mount routes added')
else:
    changed.append('existing VM ISO mount routes already present')

app_path.write_text(text)

if template_path.exists():
    tpl = template_path.read_text()
    original = tpl
    if 'action="/vm/{{ vm.name }}/iso/mount"' not in tpl:
        block = r'''

    {% if iso_message %}
      <div class="alert success">{{ iso_message }}</div>
    {% endif %}
    {% if iso_error %}
      <div class="alert danger">{{ iso_error }}</div>
    {% endif %}

    <section class="card">
      <div class="card-head">
        <h2>ISO-привод</h2>
        <span class="pill">cdrom</span>
      </div>
      {% if current_iso %}
        <div class="notice">Сейчас подключен ISO: <b>{{ current_iso }}</b></div>
      {% else %}
        <div class="muted small-note">ISO сейчас не подключён.</div>
      {% endif %}
      <form method="post" action="/vm/{{ vm.name }}/iso/mount" class="form-grid">
        <label>
          <span>ISO образ</span>
          <select name="iso_path" required>
            {% for iso in isos %}
              <option value="{{ iso.path }}" {% if current_iso == iso.path %}selected{% endif %}>{{ iso.name }} — {{ iso.size }}</option>
            {% endfor %}
          </select>
        </label>
        <button class="primary wide" type="submit" {% if not isos %}disabled{% endif %}>Смонтировать ISO в VM</button>
      </form>
      <form method="post" action="/vm/{{ vm.name }}/iso/unmount" class="form-grid" onsubmit="return confirm('Отмонтировать ISO из VM {{ vm.name }}?');">
        <button type="submit" class="ghost wide">Отмонтировать ISO</button>
      </form>
      {% if not isos %}
        <div class="alert danger">ISO-образов пока нет. Сначала загрузи .iso в разделе ISO.</div>
      {% endif %}
      <p class="muted small-note">Для запущенной VM ISO подключается live и сохраняется в конфигурации. Для выключенной VM ISO будет доступен при следующем старте. Чтобы загрузиться с ISO, выставь порядок загрузки “Сначала ISO/CD-ROM, потом диск”.</p>
    </section>
'''
        marker = '\n\n    <section class="grid two">'
        if marker not in tpl:
            raise SystemExit('vm detail grid marker not found')
        tpl = tpl.replace(marker, block + marker, 1)
        changed.append('ISO mount card added to vm_detail.html')
    if tpl != original:
        template_path.write_text(tpl)

print('existing VM ISO mount patch applied:')
for item in changed:
    print(f'- {item}')
