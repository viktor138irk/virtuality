#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

template_path = app_path.parent / 'templates' / 'network.html'
dashboard_template_path = app_path.parent / 'templates' / 'dashboard.html'
if not template_path.exists():
    raise SystemExit(f'network.html not found: {template_path}')
if not dashboard_template_path.exists():
    raise SystemExit(f'dashboard.html not found: {dashboard_template_path}')

changed: list[str] = []
text = app_path.read_text(encoding='utf-8')

helpers = r'''

def parse_dhcp_leases_output(output: str) -> list[dict[str, str]]:
    leases: list[dict[str, str]] = []
    for line in (output or '').splitlines():
        raw = line.strip()
        if not raw or raw.startswith('Expiry') or raw.startswith('-'):
            continue
        parts = raw.split()
        if len(parts) < 5:
            continue
        leases.append({
            'expiry': ' '.join(parts[0:2]),
            'mac': parts[2].lower(),
            'protocol': parts[3],
            'ip': parts[4].split('/')[0],
            'ip_with_prefix': parts[4],
            'hostname': parts[5] if len(parts) > 5 else '—',
            'client_id': parts[6] if len(parts) > 6 else '—',
        })
    return leases


def dhcp_leases_hint(info: dict[str, Any], leases: list[dict[str, str]]) -> str:
    if leases:
        return 'DHCP leases найдены. IP можно использовать для проброса портов.'
    if not info.get('exists'):
        return 'NAT-сеть ещё не создана. Нажми «Создать / починить NAT-сеть».'
    info_text = str(info.get('info', ''))
    if 'Active: yes' not in info_text:
        return 'NAT-сеть есть, но не активна. Нажми «Создать / починить NAT-сеть» или проверь virsh net-start virtuality-nat.'
    return 'Leases пусты: запусти VM с сетью virtuality-nat, дождись загрузки ОС и DHCP. У статических IP и некоторых готовых образов lease может не появиться.'


def vm_mac_addresses(vm_name: str) -> list[str]:
    result = run_cmd(['virsh', 'domiflist', vm_name], timeout=8)
    if not result.get('ok'):
        return []
    macs: list[str] = []
    for line in result.get('stdout', '').splitlines():
        match = re.search(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', line)
        if match:
            macs.append(match.group(0).lower())
    return macs


def resolve_vm_ip_for_table(vm_name: str) -> str:
    if not vm_name:
        return '—'

    try:
        result = run_cmd(['virsh', 'domifaddr', vm_name], timeout=8)
        if result.get('ok'):
            for ip in re.findall(r'\b(\d{1,3}(?:\.\d{1,3}){3})/\d+', result.get('stdout', '')):
                if not ip.startswith('127.') and not ip.startswith('169.254.'):
                    return ip
    except Exception:
        pass

    try:
        resolved = network_core.resolve_vm_ip(vm_name)
        if resolved:
            return str(resolved)
    except Exception:
        pass

    try:
        leases = run_cmd(['virsh', 'net-dhcp-leases', network_core.NETWORK_NAME], timeout=8)
        if not leases.get('ok'):
            return '—'
        macs = set(vm_mac_addresses(vm_name))
        for lease in parse_dhcp_leases_output(leases.get('stdout', '')):
            if macs and lease.get('mac', '').lower() not in macs:
                continue
            ip = lease.get('ip') or ''
            if ip:
                return ip
    except Exception:
        pass

    return '—'


def enrich_vms_with_ips(vms: list[dict[str, str]]) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for vm in vms:
        item = dict(vm)
        item['ip'] = resolve_vm_ip_for_table(item.get('name', '')) if item.get('name') else '—'
        enriched.append(item)
    return enriched
'''

# Remove older helper versions from previous patches to avoid stale NETWORK_NAME references.
text = re.sub(
    r"\n\ndef parse_dhcp_leases_output\(output: str\).*?\n\ndef parse_virsh_list\(\) -> list\[dict\[str, str\]\]:",
    "\n\ndef parse_virsh_list() -> list[dict[str, str]]:",
    text,
    count=1,
    flags=re.S,
)

marker = '\n\ndef parse_virsh_list() -> list[dict[str, str]]:'
if marker not in text:
    raise SystemExit('parse_virsh_list marker not found')
text = text.replace(marker, helpers + marker, 1)
changed.append('VM IP helpers were injected before parse_virsh_list')

# Make parse_virsh_list always return enriched rows.
text, count = re.subn(
    r"(def parse_virsh_list\(\) -> list\[dict\[str, str\]\]:.*?\n)    return rows\n\n\ndef parse_pool_list",
    r"\1    return enrich_vms_with_ips(rows)\n\n\ndef parse_pool_list",
    text,
    count=1,
    flags=re.S,
)
if count:
    changed.append('parse_virsh_list return was replaced with enrich_vms_with_ips')
elif 'return enrich_vms_with_ips(rows)' in text:
    changed.append('parse_virsh_list was already enriched')
else:
    raise SystemExit('parse_virsh_list return marker not found')

old_info = '''def libvirt_network_info() -> dict[str, Any]:
    info = run_cmd(['virsh', 'net-info', NETWORK_NAME], timeout=8)
    leases = run_cmd(['virsh', 'net-dhcp-leases', NETWORK_NAME], timeout=8)
    return {
        'name': NETWORK_NAME,
        'bridge': NAT_BRIDGE,
        'subnet': NAT_SUBNET,
        'gateway': NAT_GATEWAY,
        'dhcp': f'{DHCP_START} - {DHCP_END}',
        'exists': info['ok'],
        'info': info['stdout'] if info['ok'] else info['stderr'],
        'leases': leases['stdout'] if leases['ok'] else leases['stderr'],
    }
'''
new_info = '''def libvirt_network_info() -> dict[str, Any]:
    info = run_cmd(['virsh', 'net-info', NETWORK_NAME], timeout=8)
    leases = run_cmd(['virsh', 'net-dhcp-leases', NETWORK_NAME], timeout=8)
    leases_text = leases['stdout'] if leases['ok'] else leases['stderr']
    lease_rows = parse_dhcp_leases_output(leases['stdout'] if leases['ok'] else '') if 'parse_dhcp_leases_output' in globals() else []
    data = {
        'name': NETWORK_NAME,
        'bridge': NAT_BRIDGE,
        'subnet': NAT_SUBNET,
        'gateway': NAT_GATEWAY,
        'dhcp': f'{DHCP_START} - {DHCP_END}',
        'exists': info['ok'],
        'info': info['stdout'] if info['ok'] else info['stderr'],
        'leases': leases_text,
        'lease_rows': lease_rows,
        'lease_count': len(lease_rows),
        'lease_hint': '',
    }
    data['lease_hint'] = dhcp_leases_hint(data, lease_rows) if 'dhcp_leases_hint' in globals() else 'DHCP leases пусты.'
    return data
'''
if old_info in text:
    text = text.replace(old_info, new_info, 1)
    changed.append('libvirt_network_info was enriched with parsed lease rows')
elif "'lease_rows':" in text:
    changed.append('libvirt_network_info was already enriched')
else:
    changed.append('libvirt_network_info was not found in app.py, skipped')

app_path.write_text(text, encoding='utf-8')

html = template_path.read_text(encoding='utf-8')
old_html = '''      <article class="card">
        <div class="card-head">
          <h2>DHCP leases</h2>
          <span class="pill">virsh net-dhcp-leases</span>
        </div>
        <pre>{{ ctx.nat.leases }}</pre>
        <div class="notice top-space">IP для проброса определяется автоматически: сначала через qemu-guest-agent, затем по DHCP leases и MAC-адресу VM.</div>
      </article>
'''
new_html = '''      <article class="card">
        <div class="card-head">
          <h2>DHCP leases</h2>
          <span class="pill">{{ ctx.nat.lease_count|default(0) }} active</span>
        </div>
        {% if ctx.nat.lease_rows %}
          <table>
            <thead><tr><th>IP</th><th>MAC</th><th>Host</th><th>Expiry</th></tr></thead>
            <tbody>
            {% for lease in ctx.nat.lease_rows %}
              <tr>
                <td class="strong">{{ lease.ip }}</td>
                <td>{{ lease.mac }}</td>
                <td>{{ lease.hostname }}</td>
                <td class="muted">{{ lease.expiry }}</td>
              </tr>
            {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="notice">{{ ctx.nat.lease_hint|default('DHCP leases пусты.') }}</div>
          <pre>virsh net-dhcp-leases {{ ctx.nat.name }}
virsh net-info {{ ctx.nat.name }}
virsh domiflist ИМЯ_VM
virsh domifaddr ИМЯ_VM</pre>
        {% endif %}
        <div class="notice top-space">IP для проброса определяется автоматически: сначала через qemu-guest-agent, затем по DHCP leases и MAC-адресу VM. Если внутри образа статический IP, leases может быть пустым — тогда лучше добавить ручной IP-режим следующим шагом.</div>
      </article>
'''
if old_html in html:
    html = html.replace(old_html, new_html, 1)
    changed.append('DHCP leases UI was replaced with structured table')
elif 'ctx.nat.lease_rows' in html:
    changed.append('DHCP leases UI was already structured')
else:
    changed.append('DHCP leases UI marker was not found, skipped')
template_path.write_text(html, encoding='utf-8')

dashboard_html = dashboard_template_path.read_text(encoding='utf-8')
if '<th>IP</th>' not in dashboard_html:
    dashboard_html = dashboard_html.replace('<tr><th>ID</th><th>Name</th><th>State</th><th>Actions</th></tr>', '<tr><th>ID</th><th>Name</th><th>IP</th><th>State</th><th>Actions</th></tr>')
    dashboard_html = dashboard_html.replace('<td class="strong"><a class="table-link" href="/vm/{{ vm.name }}">{{ vm.name }}</a></td>\n              <td><span', '<td class="strong"><a class="table-link" href="/vm/{{ vm.name }}">{{ vm.name }}</a></td>\n              <td class="strong">{{ vm.ip|default(\'—\') }}</td>\n              <td><span')
    dashboard_html = dashboard_html.replace('colspan="4" class="muted">Виртуальных машин пока нет', 'colspan="5" class="muted">Виртуальных машин пока нет')
    changed.append('Dashboard VM IP column was added')
else:
    changed.append('Dashboard VM IP column was already present')
dashboard_template_path.write_text(dashboard_html, encoding='utf-8')

print('DHCP leases and VM IP patch applied:')
for item in changed:
    print(f'- {item}')
