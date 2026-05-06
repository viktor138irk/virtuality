#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
changed: list[str] = []
warnings: list[str] = []

if not app_path.exists():
    print(f'WARN: app.py not found: {app_path}')
    raise SystemExit(0)

dashboard_template_path = app_path.parent / 'templates' / 'dashboard.html'
if not dashboard_template_path.exists():
    print(f'WARN: dashboard.html not found: {dashboard_template_path}')
    raise SystemExit(0)

text = app_path.read_text(encoding='utf-8')

# Older versions of this patch injected helper blocks. Remove them first so repeated
# installs stay deterministic and do not accumulate stale references.
text, removed_helpers = re.subn(
    r"\n\ndef parse_dhcp_leases_output\(output: str\).*?\n\ndef parse_virsh_list\(\) -> list\[dict\[str, str\]\]:",
    "\n\ndef parse_virsh_list() -> list[dict[str, str]]:",
    text,
    count=1,
    flags=re.S,
)
if removed_helpers:
    changed.append('old VM IP helper block was removed')

new_parse_virsh_list = '''def parse_virsh_list() -> list[dict[str, str]]:
    def resolve_ip(name: str) -> str:
        if not name:
            return "—"
        try:
            result = run_cmd(["virsh", "domifaddr", name], timeout=8)
            if result.get("ok"):
                for ip in re.findall(r"\\b(\\d{1,3}(?:\\.\\d{1,3}){3})/\\d+", result.get("stdout", "")):
                    if not ip.startswith("127.") and not ip.startswith("169.254."):
                        return ip
        except Exception:
            pass
        try:
            resolved = network_core.resolve_vm_ip(name)
            if resolved:
                return str(resolved)
        except Exception:
            pass
        return "—"

    result = run_cmd(["virsh", "list", "--all"])
    rows = []
    if not result["ok"]:
        return rows
    for line in result["stdout"].splitlines()[2:]:
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            rows.append({"id": parts[0], "name": parts[1], "state": parts[2]})
        elif len(parts) == 2:
            rows.append({"id": "-", "name": parts[0], "state": parts[1]})
    for row in rows:
        row["ip"] = resolve_ip(row.get("name", ""))
    return rows
'''

pattern = r"def parse_virsh_list\(\) -> list\[dict\[str, str\]\]:.*?\n\ndef parse_pool_list\(\) -> list\[dict\[str, str\]\]:"
replacement = new_parse_virsh_list + "\n\ndef parse_pool_list() -> list[dict[str, str]]:"
text, replaced = re.subn(
    pattern,
    lambda _match: replacement,
    text,
    count=1,
    flags=re.S,
)
if replaced:
    changed.append('parse_virsh_list was replaced with a safe inline IP resolver')
else:
    warnings.append('parse_virsh_list block not found, app.py IP injection skipped')

app_path.write_text(text, encoding='utf-8')

dashboard_html = dashboard_template_path.read_text(encoding='utf-8')

header_old = '<tr><th>ID</th><th>Name</th><th>State</th><th>Actions</th></tr>'
header_new = '<tr><th>ID</th><th>Name</th><th>IP</th><th>State</th><th>Actions</th></tr>'
name_cell = '''<td class="strong"><a class="table-link" href="/vm/{{ vm.name }}">{{ vm.name }}</a></td>
              <td><span'''
name_ip_cell = '''<td class="strong"><a class="table-link" href="/vm/{{ vm.name }}">{{ vm.name }}</a></td>
              <td class="strong">{{ vm.ip|default("—") }}</td>
              <td><span'''

if '<th>IP</th>' not in dashboard_html:
    dashboard_html = dashboard_html.replace(header_old, header_new)
    dashboard_html = dashboard_html.replace(name_cell, name_ip_cell)
    dashboard_html = dashboard_html.replace('colspan="4" class="muted">Виртуальных машин пока нет', 'colspan="5" class="muted">Виртуальных машин пока нет')
    changed.append('Dashboard VM IP column was added')
elif 'vm.ip' not in dashboard_html:
    dashboard_html = dashboard_html.replace(name_cell, name_ip_cell)
    changed.append('Dashboard VM IP cell was added')
else:
    changed.append('Dashboard VM IP column was already present')

dashboard_template_path.write_text(dashboard_html, encoding='utf-8')

print('DHCP leases empty-state patch completed:')
for item in changed:
    print(f'- {item}')
for item in warnings:
    print(f'WARN: {item}')

raise SystemExit(0)
