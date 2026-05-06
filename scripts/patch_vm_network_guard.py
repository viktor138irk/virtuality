#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

helper = '''

def bridge_exists(name: str) -> bool:
    if not name or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", name):
        return False
    return run_cmd(["ip", "link", "show", name], timeout=5)["ok"]
'''

if 'def bridge_exists(name: str) -> bool:' not in text:
    marker = '\n\ndef default_network_mode() -> str:'
    if marker not in text:
        raise SystemExit('default_network_mode marker not found')
    text = text.replace(marker, helper + marker, 1)
    changed.append('bridge_exists helper added')
else:
    changed.append('bridge_exists helper already present')

old_validation = '''    elif network_mode == "bridge" and (not bridge or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", bridge)):
        error = "Некорректное имя bridge."
    else:
        iso = Path(iso_path).resolve()
'''
new_validation = '''    elif network_mode == "bridge" and (not bridge or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", bridge)):
        error = "Некорректное имя bridge."
    elif network_mode == "bridge" and not bridge_exists(bridge):
        error = f"Bridge {bridge} не найден на сервере. Для VPS выбери режим NAT Router — virtuality-nat, либо сначала создай bridge {bridge}."
    else:
        iso = Path(iso_path).resolve()
'''

if old_validation in text:
    text = text.replace(old_validation, new_validation, 1)
    changed.append('bridge existence validation added')
elif new_validation in text:
    changed.append('bridge existence validation already present')
else:
    raise SystemExit('bridge validation marker not found')

app_path.write_text(text)
print('vm network guard patch applied:')
for item in changed:
    print(f'- {item}')
