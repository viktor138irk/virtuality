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

templates_dir = app_path.parent / 'templates'
dashboard_template_path = templates_dir / 'dashboard.html'
network_template_path = templates_dir / 'network.html'
if not dashboard_template_path.exists():
    print(f'WARN: dashboard.html not found: {dashboard_template_path}')
    raise SystemExit(0)

text = app_path.read_text(encoding='utf-8')

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

    def manual_ip_map() -> dict[str, str]:
        path = Path("/var/lib/virtuality/network/vm_ips.json")
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): clean_ip(str(v)) for k, v in data.items() if clean_ip(str(v))}
        except Exception:
            pass
        return {}

    def vm_macs(name: str) -> list[str]:
        try:
            result = run_cmd(["virsh", "domiflist", name], timeout=8)
            if not result.get("ok"):
                return []
            return [mac.lower() for mac in re.findall(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", result.get("stdout", ""))]
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
        commands = [["ip", "neigh", "show"], ["ip", "neigh", "show", "dev", "virbr100"], ["ip", "neigh", "show", "dev", "br0"], ["arp", "-an"]]
        for cmd in commands:
            try:
                result = run_cmd(cmd, timeout=8)
                if not result.get("ok"):
                    continue
                for line in result.get("stdout", "").splitlines():
                    low = line.lower()
                    if not any(mac in low for mac in macs):
                        continue
                    for value in re.findall(r"\\b(\\d{1,3}(?:\\.\\d{1,3}){3})\\b", line):
                        ip = clean_ip(value)
                        if ip:
                            return ip
            except Exception:
                pass
        return ""

    manual_ips = manual_ip_map()

    def resolve_ip(name: str) -> str:
        if not name:
            return "—"
        if manual_ips.get(name):
            return manual_ips[name]
        macs = vm_macs(name)
        for resolver in (lambda: ip_from_domifaddr(name), lambda: ip_from_network_core(name), lambda: ip_from_dnsmasq_leases(macs), lambda: ip_from_neighbor_tables(macs)):
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
        row["manual_ip"] = manual_ips.get(row.get("name", ""), "")
    return rows
'''

pattern = r"def parse_virsh_list\(\) -> list\[dict\[str, str\]\]:.*?\n\ndef parse_pool_list\(\) -> list\[dict\[str, str\]\]:"
replacement = new_parse_virsh_list + "\n\ndef parse_pool_list() -> list[dict[str, str]]:"
text, replaced = re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.S)
if replaced:
    changed.append('parse_virsh_list was replaced with manual-first VM IP resolver')
else:
    warnings.append('parse_virsh_list block not found, app.py IP injection skipped')

manual_route = '''

@app.post("/network/vm-ip/save")
def network_vm_ip_save(request: Request, vm_name: str = Form(...), manual_ip: str = Form("")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not valid_vm_name(vm_name):
        return RedirectResponse(url="/network", status_code=303)
    manual_ip = (manual_ip or "").strip()
    if manual_ip and not re.fullmatch(r"(25[0-5]|2[0-4]\\d|1?\\d?\\d)(\\.(25[0-5]|2[0-4]\\d|1?\\d?\\d)){3}", manual_ip):
        return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": "Некорректный ручной IP VM"}, status_code=400)
    path = Path("/var/lib/virtuality/network/vm_ips.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if manual_ip:
        data[vm_name] = manual_ip
    else:
        data.pop(vm_name, None)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return RedirectResponse(url="/network", status_code=303)
'''

if '/network/vm-ip/save' not in text:
    marker = '\n\n@app.post("/network/nat/setup")'
    if marker in text:
        text = text.replace(marker, manual_route + marker, 1)
        changed.append('manual VM IP route was added')
    else:
        warnings.append('network NAT route marker not found, manual IP route skipped')
else:
    changed.append('manual VM IP route was already present')

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
    dashboard_html = dashboard_html.replace(header_old, header_new).replace(name_cell, name_ip_cell).replace('colspan="4" class="muted">Виртуальных машин пока нет', 'colspan="5" class="muted">Виртуальных машин пока нет')
    changed.append('Dashboard VM IP column was added')
elif 'vm.ip' not in dashboard_html:
    dashboard_html = dashboard_html.replace(name_cell, name_ip_cell)
    changed.append('Dashboard VM IP cell was added')
else:
    changed.append('Dashboard VM IP column was already present')
dashboard_template_path.write_text(dashboard_html, encoding='utf-8')

if network_template_path.exists():
    network_html = network_template_path.read_text(encoding='utf-8')
    manual_card = '''
      <article class="card">
        <div class="card-head">
          <h2>Ручные IP VM</h2>
          <span class="pill">override</span>
        </div>
        <div class="notice">Если VM в bridge/static-сети и IP не виден через DHCP/ARP, укажи адрес вручную. Этот IP будет первым источником для таблицы и проброса портов.</div>
        <table class="top-space">
          <thead><tr><th>VM</th><th>Текущий IP</th><th>Ручной IP</th><th>Действие</th></tr></thead>
          <tbody>
          {% for vm in vms %}
            <tr>
              <td class="strong">{{ vm.name }}</td>
              <td>{{ vm.ip|default("—") }}</td>
              <td>
                <form method="post" action="/network/vm-ip/save" class="inline-form">
                  <input type="hidden" name="vm_name" value="{{ vm.name }}">
                  <input type="text" name="manual_ip" value="{{ vm.manual_ip|default("") }}" placeholder="например 10.0.0.50" pattern="(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}">
              </td>
              <td><button>Сохранить</button></form></td>
            </tr>
          {% else %}
            <tr><td colspan="4" class="muted">VM пока нет</td></tr>
          {% endfor %}
          </tbody>
        </table>
      </article>
'''
    if 'Ручные IP VM' not in network_html:
        marker = '      <article class="card">\n        <div class="card-head">\n          <h2>Диагностика публичного доступа</h2>'
        if marker in network_html:
            network_html = network_html.replace(marker, manual_card + '\n' + marker, 1)
            changed.append('manual VM IP card was added to network page')
        else:
            warnings.append('network diagnostics card marker not found, manual IP card skipped')
    else:
        changed.append('manual VM IP card was already present')
    network_template_path.write_text(network_html, encoding='utf-8')
else:
    warnings.append('network.html not found, manual IP card skipped')

print('DHCP leases empty-state patch completed:')
for item in changed:
    print(f'- {item}')
for item in warnings:
    print(f'WARN: {item}')

raise SystemExit(0)
