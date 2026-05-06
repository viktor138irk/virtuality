#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

template_path = app_path.parent / 'templates' / 'network.html'
if not template_path.exists():
    raise SystemExit(f'network.html not found: {template_path}')

text = app_path.read_text()
changed = []

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
            'mac': parts[2],
            'protocol': parts[3],
            'ip': parts[4],
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
'''

if 'def parse_dhcp_leases_output(' not in text:
    marker = '\n\ndef list_libvirt_networks() -> list[dict[str, str]]:'
    if marker not in text:
        raise SystemExit('list_libvirt_networks marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('DHCP leases parser added')
else:
    changed.append('DHCP leases parser already present')

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
    lease_rows = parse_dhcp_leases_output(leases['stdout'] if leases['ok'] else '')
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
    data['lease_hint'] = dhcp_leases_hint(data, lease_rows)
    return data
'''
if old_info in text:
    text = text.replace(old_info, new_info, 1)
    changed.append('libvirt network info enriched with lease rows and hint')
elif "'lease_rows':" in text:
    changed.append('libvirt network info already enriched')
else:
    raise SystemExit('libvirt_network_info marker not found')

app_path.write_text(text)

html = template_path.read_text()
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
    changed.append('DHCP leases UI replaced with structured table and empty hint')
elif 'ctx.nat.lease_rows' in html:
    changed.append('DHCP leases UI already updated')
else:
    raise SystemExit('DHCP leases template marker not found')

template_path.write_text(html)

print('DHCP leases empty-state patch applied:')
for item in changed:
    print(f'- {item}')
