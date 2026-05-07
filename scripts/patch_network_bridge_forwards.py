#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
core_path = app_path.with_name('network_core.py')
if not core_path.exists():
    raise SystemExit(f'network_core.py not found: {core_path}')

text = core_path.read_text()
changed = []

if 'def route_interface_for_ip(' not in text:
    marker = '''def external_interface() -> str:
    result = run_cmd(['ip', 'route', 'show', 'default'], timeout=5)
    if not result['ok']:
        return 'eth0'
    match = re.search(r'\\bdev\\s+([^\\s]+)', result['stdout'])
    return match.group(1) if match else 'eth0'
'''
    helper = marker + '''

def route_interface_for_ip(ip: str) -> str:
    if not valid_ip(ip):
        return NAT_BRIDGE
    result = run_cmd(['ip', 'route', 'get', ip], timeout=5)
    if not result['ok']:
        return NAT_BRIDGE
    match = re.search(r'\\bdev\\s+([^\\s]+)', result['stdout'])
    return match.group(1) if match else NAT_BRIDGE


def forward_guest_interface(item: dict[str, Any]) -> str:
    return route_interface_for_ip(str(item.get('guest_ip', '')))
'''
    if marker not in text:
        raise SystemExit('external_interface marker not found')
    text = text.replace(marker, helper, 1)
    changed.append('route_interface_for_ip helper added')
else:
    changed.append('route_interface_for_ip helper already present')

old_render = '''def render_nft_rules() -> str:
    ext = external_interface()
    lines = [
        'table ip virtuality {',
        '  chain prerouting {',
        '    type nat hook prerouting priority dstnat; policy accept;',
    ]
    for item in load_port_forwards():
        external_ports = nft_port_value(item['external_port_start'], item['external_port_end'])
        guest_ports = nft_port_value(item['guest_port_start'], item['guest_port_end'])
        lines.append(f"    iifname \\"{ext}\\" {item['protocol']} dport {external_ports} dnat to {item['guest_ip']}:{guest_ports}")
    lines += [
        '  }',
        '  chain postrouting {',
        '    type nat hook postrouting priority srcnat; policy accept;',
        f'    ip saddr {NAT_SUBNET} oifname "{ext}" masquerade',
        '  }',
        '  chain forward {',
        '    type filter hook forward priority filter; policy accept;',
        f'    ip saddr {NAT_SUBNET} accept',
        f'    ip daddr {NAT_SUBNET} accept',
        '  }',
        '}',
    ]
    return '\\n'.join(lines) + '\\n'
'''
new_render = '''def render_nft_rules() -> str:
    ext = external_interface()
    forwards = load_port_forwards()
    lines = [
        'table ip virtuality {',
        '  chain prerouting {',
        '    type nat hook prerouting priority dstnat; policy accept;',
    ]
    for item in forwards:
        external_ports = nft_port_value(item['external_port_start'], item['external_port_end'])
        guest_ports = nft_port_value(item['guest_port_start'], item['guest_port_end'])
        lines.append(f"    iifname \\"{ext}\\" {item['protocol']} dport {external_ports} dnat to {item['guest_ip']}:{guest_ports}")
    lines += [
        '  }',
        '  chain postrouting {',
        '    type nat hook postrouting priority srcnat; policy accept;',
        f'    ip saddr {NAT_SUBNET} oifname "{ext}" masquerade',
    ]
    for item in forwards:
        guest_iface = forward_guest_interface(item)
        lines.append(f'    ip daddr {item["guest_ip"]} oifname "{guest_iface}" masquerade')
    lines += [
        '  }',
        '  chain forward {',
        '    type filter hook forward priority filter; policy accept;',
        f'    ip saddr {NAT_SUBNET} accept',
        f'    ip daddr {NAT_SUBNET} accept',
    ]
    for item in forwards:
        guest_iface = forward_guest_interface(item)
        lines.append(f'    iifname "{ext}" oifname "{guest_iface}" ip daddr {item["guest_ip"]} accept')
        lines.append(f'    iifname "{guest_iface}" oifname "{ext}" ip saddr {item["guest_ip"]} ct state established,related accept')
    lines += [
        '  }',
        '}',
    ]
    return '\\n'.join(lines) + '\\n'
'''
if old_render in text:
    text = text.replace(old_render, new_render, 1)
    changed.append('nft rules now support bridge/static VM interfaces')
elif 'forward_guest_interface(item)' in text and 'ip daddr {item["guest_ip"]}' in text:
    changed.append('nft bridge/static rules already present')
else:
    raise SystemExit('render_nft_rules marker not found')

text = text.replace(
    "'out', 'on', NAT_BRIDGE,\n            'to', item['guest_ip'],",
    "'out', 'on', forward_guest_interface(item),\n            'to', item['guest_ip'],",
)
if "'out', 'on', forward_guest_interface(item)" in text:
    changed.append('UFW route rules now use guest route interface')

old_iptables_line = """        results.append(ensure_iptables_rule(['iptables', '-I', 'FORWARD', '1', '-i', ext, '-o', NAT_BRIDGE, '-p', proto, '-d', guest_ip, '-m', proto, '--dport', guest_port, '-j', 'ACCEPT']))
        results.append(ensure_iptables_rule(['iptables', '-I', 'FORWARD', '1', '-i', NAT_BRIDGE, '-o', ext, '-s', guest_ip, '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT']))
        results.append(ensure_iptables_rule(['iptables', '-t', 'nat', '-I', 'PREROUTING', '1', '-i', ext, '-p', proto, '-m', proto, '--dport', external_port, '-j', 'DNAT', '--to-destination', guest_to]))
"""
new_iptables_line = """        guest_iface = forward_guest_interface(item)
        results.append(ensure_iptables_rule(['iptables', '-I', 'FORWARD', '1', '-i', ext, '-o', guest_iface, '-p', proto, '-d', guest_ip, '-m', proto, '--dport', guest_port, '-j', 'ACCEPT']))
        results.append(ensure_iptables_rule(['iptables', '-I', 'FORWARD', '1', '-i', guest_iface, '-o', ext, '-s', guest_ip, '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT']))
        results.append(ensure_iptables_rule(['iptables', '-t', 'nat', '-I', 'PREROUTING', '1', '-i', ext, '-p', proto, '-m', proto, '--dport', external_port, '-j', 'DNAT', '--to-destination', guest_to]))
        results.append(ensure_iptables_rule(['iptables', '-t', 'nat', '-I', 'POSTROUTING', '1', '-d', guest_ip, '-o', guest_iface, '-j', 'MASQUERADE']))
"""
if old_iptables_line in text:
    text = text.replace(old_iptables_line, new_iptables_line, 1)
    changed.append('iptables fallback now supports bridge/static VM interfaces')
elif "guest_iface = forward_guest_interface(item)" in text and "'-o', guest_iface" in text:
    changed.append('iptables bridge/static fallback already present')
else:
    raise SystemExit('iptables fallback marker not found')

text = text.replace(
    "'watch_internal': f\"sudo tcpdump -ni {NAT_BRIDGE} 'host {vm_ip or '<VM_IP>'} and {protocol} port {int(guest_port)}'\",",
    "'watch_internal': f\"sudo tcpdump -ni {route_interface_for_ip(vm_ip) if vm_ip else NAT_BRIDGE} 'host {vm_ip or '<VM_IP>'} and {protocol} port {int(guest_port)}'\",",
)

core_path.write_text(text)
print('network bridge forward patch applied:')
for item in changed:
    print(f'- {item}')
