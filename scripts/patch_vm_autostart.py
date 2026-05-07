#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

helper = r'''

def vm_autostart_status(name: str) -> dict[str, str | bool]:
    result = run_cmd(["virsh", "dominfo", name], timeout=8)
    output = result.get("stdout", "") or ""
    match = re.search(r"^Autostart:\s*(.+)$", output, re.MULTILINE | re.IGNORECASE)
    raw = match.group(1).strip() if match else "unknown"
    enabled = raw.lower() in ("enable", "enabled", "yes", "on")
    label = "enabled" if enabled else "disabled" if raw != "unknown" else "unknown"
    css = "ok" if enabled else "warn"
    return {"enabled": enabled, "label": label, "css": css, "raw": raw}
'''

if 'def vm_autostart_status(' not in text:
    marker = '\n\ndef parse_virsh_list() -> list[dict[str, str]]:'
    if marker not in text:
        raise SystemExit('parse_virsh_list marker not found')
    text = text.replace(marker, helper + marker, 1)
    changed.append('vm_autostart_status helper added')
else:
    changed.append('vm_autostart_status helper already present')

old_rows = '''        if len(parts) == 3:\n            rows.append({"id": parts[0], "name": parts[1], "state": parts[2]})\n        elif len(parts) == 2:\n            rows.append({"id": "-", "name": parts[0], "state": parts[1]})'''
new_rows = '''        if len(parts) == 3:\n            autostart = vm_autostart_status(parts[1])\n            rows.append({"id": parts[0], "name": parts[1], "state": parts[2], "autostart_enabled": autostart["enabled"], "autostart_label": autostart["label"], "autostart_css": autostart["css"]})\n        elif len(parts) == 2:\n            autostart = vm_autostart_status(parts[0])\n            rows.append({"id": "-", "name": parts[0], "state": parts[1], "autostart_enabled": autostart["enabled"], "autostart_label": autostart["label"], "autostart_css": autostart["css"]})'''
if old_rows in text:
    text = text.replace(old_rows, new_rows, 1)
    changed.append('parse_virsh_list enriched with autostart')
elif 'autostart_enabled' in text and 'autostart_label' in text:
    changed.append('parse_virsh_list already has autostart')
else:
    raise SystemExit('parse_virsh_list row marker not found')

old_details = '''def vm_details(name: str) -> dict[str, Any]:\n    return {\n        "name": name,\n        "dominfo": run_cmd(["virsh", "dominfo", name], timeout=10)["stdout"],\n        "vnc": run_cmd(["virsh", "vncdisplay", name], timeout=8)["stdout"] or "not available",\n        "ip": vm_ip(name),\n        "disks": run_cmd(["virsh", "domblklist", name, "--details"], timeout=10)["stdout"],\n        "interfaces": run_cmd(["virsh", "domiflist", name], timeout=10)["stdout"],\n        "autostart": run_cmd(["virsh", "dominfo", name], timeout=10)["stdout"],\n    }'''
new_details = '''def vm_details(name: str) -> dict[str, Any]:\n    dominfo = run_cmd(["virsh", "dominfo", name], timeout=10)["stdout"]\n    autostart = vm_autostart_status(name)\n    return {\n        "name": name,\n        "dominfo": dominfo,\n        "vnc": run_cmd(["virsh", "vncdisplay", name], timeout=8)["stdout"] or "not available",\n        "ip": vm_ip(name),\n        "disks": run_cmd(["virsh", "domblklist", name, "--details"], timeout=10)["stdout"],\n        "interfaces": run_cmd(["virsh", "domiflist", name], timeout=10)["stdout"],\n        "autostart": dominfo,\n        "autostart_enabled": autostart["enabled"],\n        "autostart_label": autostart["label"],\n        "autostart_css": autostart["css"],\n    }'''
if old_details in text:
    text = text.replace(old_details, new_details, 1)
    changed.append('vm_details enriched with autostart')
elif '"autostart_enabled": autostart["enabled"]' in text:
    changed.append('vm_details already has autostart')
else:
    raise SystemExit('vm_details marker not found')

old_live = '''        vms.append({"id": vm.get("id", "-"), "name": name, "state": state, "state_css": css, "ip": ip if ip and ip != "not available" else "—"})'''
new_live = '''        vms.append({"id": vm.get("id", "-"), "name": name, "state": state, "state_css": css, "ip": ip if ip and ip != "not available" else "—", "autostart_enabled": vm.get("autostart_enabled", False), "autostart_label": vm.get("autostart_label", "unknown"), "autostart_css": vm.get("autostart_css", "warn")})'''
if old_live in text:
    text = text.replace(old_live, new_live, 1)
    changed.append('live status enriched with autostart')
elif '"autostart_label": vm.get("autostart_label", "unknown")' in text:
    changed.append('live status already has autostart')
else:
    changed.append('live status marker not found, skipped')

app_path.write_text(text)
print('vm autostart patch applied:')
for item in changed:
    print(f'- {item}')
