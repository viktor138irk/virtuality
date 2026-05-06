#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

changed = []

text = app_path.read_text()
old_signature = 'def network_forward_add(request: Request, vm_name: str = Form(...), guest_ip: str = Form(...), external_port: int = Form(...), guest_port: int = Form(...), protocol: str = Form("tcp"), note: str = Form("")):'
new_signature = 'def network_forward_add(request: Request, vm_name: str = Form(...), guest_ip: str = Form(...), external_port: str = Form(...), guest_port: str = Form(...), protocol: str = Form("tcp"), note: str = Form("")):'
if old_signature in text:
    text = text.replace(old_signature, new_signature, 1)
    app_path.write_text(text)
    changed.append('app.py forward/add accepts port ranges')
elif new_signature in text:
    changed.append('app.py forward/add already accepts port ranges')
else:
    raise SystemExit('network_forward_add signature marker not found')

core_path = app_path.with_name('network_core.py')
if core_path.exists():
    core_text = core_path.read_text()
    before = core_text
    core_text = core_text.replace(
        'guest_to = f"{guest_ip}:{iptables_port_value(item[\'guest_port_start\'], item[\'guest_port_end\'])}"',
        'guest_to_port = nft_port_value(item[\'guest_port_start\'], item[\'guest_port_end\'])\n        guest_to = f"{guest_ip}:{guest_to_port}"',
    )
    core_text = core_text.replace(
        'f"{vm_ip}:{matching_forward[\'guest_port_label\'].replace(\'-\', \':\')}" in ipt_nat_text',
        '(f"{vm_ip}:{matching_forward[\'guest_port_label\']}" in ipt_nat_text or f"{vm_ip}:{matching_forward[\'guest_port_label\'].replace(\'-\', \':\')}" in ipt_nat_text)',
    )
    if core_text != before:
        core_path.write_text(core_text)
        changed.append('network_core.py iptables DNAT range syntax patched')
    else:
        changed.append('network_core.py range syntax already ok')
else:
    changed.append(f'network_core.py not found near {app_path}')

print('network ranges patch applied:')
for item in changed:
    print(f'- {item}')
