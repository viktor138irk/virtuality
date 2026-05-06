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
    def clean_ip(ip: str) -> str:
        ip = (ip or "").split("/")[0].strip()
        if not ip or ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0":
            return ""
        return ip

    def vm_macs(name: str) -> list[str]:
        try:
            result = run_cmd(["virsh", "domiflist", name], timeout=8)
            if not result.get("ok"):
                return []
            macs = []
            for mac in re.findall(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", result.get("stdout", "")):
                macs.append(mac.lower())
            return macs
        except Exception:
            return []

    def ip_from_domifaddr(name: str) -> str:
        try:
            result = run_cmd(["virsh", "domifaddr", name], timeout=8)
            if result.get("ok"):
                for ip in re.findall(r"\\b(\\d{1,3}(?:\\.\\d{1,3}){3})/\\d+", result.get("stdout", "")):
                    ip = clean_ip(ip)
                    if ip:
                        return ip
        except Exception:
            pass
        return ""

    def ip_from_network_core(name: str) -> str:
        try:
            resolved = network_core.resolve_vm_ip(name)
            if resolved:
                return clean_ip(str(resolved))
        except Exception:
            pass
        return ""

    def ip_from_dnsmasq_leases(macs: list[str]) -> str:
        if not macs:
            return ""
        try:
            for lease_file in Path("/var/lib/libvirt/dnsmasq").glob("*.leases"):
                for line in lease_file.read_text(errors="ignore").splitlines():
                    low = line.lower()
                    if not any(mac in low for mac in macs):
                        continue
                    parts = line.split()
                    if len(parts) >= 3:
                        ip = clean_ip(parts[2])
                        if ip:
                            return ip
        except Exception:
            pass
        return ""

    def ip_from_neighbor_tables(macs: list[str]) -> str:
        if not macs:
            return ""
        commands = [
            ["ip", "neigh", "show"],
            ["ip", "neigh", "show", "dev", "virbr100"],
            ["ip", "neigh", "show", "dev", "br0"],
            ["arp", "-an"],
        ]
        for cmd in commands:
            try:
                result = run_cmd(cmd, timeout=8)
                if not result.get("ok"):
                    continue
                for line in result.get("stdout", "").splitlines():
                    low = line.lower()
                    if not any(mac in low for mac in macs):
                        continue
                    matches = re.findall(r"\\b(\\d{1,3}(?:\\.\\d{1,3}){3})\\b", line)
                    for value in matches:
                        ip = clean_ip(value)
                        if ip:
                            return ip
            except Exception:
                pass
        return ""

    def resolve_ip(name: str) -> str:
        if not name:
            return "—"
        macs = vm_macs(name)
        for resolver in (
            lambda: ip_from_domifaddr(name),
            lambda: ip_from_network_core(name),
            lambda: ip_from_dnsmasq_leases(macs),
            lambda: ip_from_neighbor_tables(macs),
        ):
            try:
                ip = resolver()
                if ip:
                    return ip
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
    changed.append('parse_virsh_list was replaced with multi-source VM IP resolver')
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
