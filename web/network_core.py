import json
import re
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any

CONFIG_DIR = Path('/var/lib/virtuality/config')
NETWORK_DIR = Path('/var/lib/virtuality/network')
NFT_DIR = Path('/etc/virtuality/nftables')
PORT_FORWARDS_FILE = NETWORK_DIR / 'port_forwards.json'
NFT_FILE = NFT_DIR / 'virtuality.nft'
NETWORK_NAME = 'virtuality-nat'
NAT_BRIDGE = 'virbr100'
NAT_SUBNET = '192.168.100.0/24'
NAT_GATEWAY = '192.168.100.1'
DHCP_START = '192.168.100.50'
DHCP_END = '192.168.100.200'


class NetworkError(Exception):
    pass


def run_cmd(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return {'ok': result.returncode == 0, 'code': result.returncode, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip(), 'cmd': ' '.join(cmd)}
    except Exception as exc:
        return {'ok': False, 'code': -1, 'stdout': '', 'stderr': str(exc), 'cmd': ' '.join(cmd)}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    NFT_DIR.mkdir(parents=True, exist_ok=True)


def valid_port(value: int) -> bool:
    return 1 <= int(value) <= 65535


def valid_port_range(start: int, end: int) -> bool:
    return valid_port(start) and valid_port(end) and int(start) <= int(end)


def parse_port_range(value: Any, field_label: str = 'Порт') -> tuple[int, int]:
    raw = str(value or '').strip().replace(' ', '')
    if not raw:
        raise NetworkError(f'{field_label} не указан')
    match = re.fullmatch(r'(\d{1,5})(?:[-:](\d{1,5}))?', raw)
    if not match:
        raise NetworkError(f'{field_label} должен быть числом или диапазоном, например 80 или 10000-20000')
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    if not valid_port_range(start, end):
        raise NetworkError(f'{field_label} должен быть в диапазоне 1-65535, начало не больше конца')
    return start, end


def port_range_size(start: int, end: int) -> int:
    return int(end) - int(start) + 1


def nft_port_value(start: int, end: int) -> str:
    return str(int(start)) if int(start) == int(end) else f'{int(start)}-{int(end)}'


def iptables_port_value(start: int, end: int) -> str:
    return str(int(start)) if int(start) == int(end) else f'{int(start)}:{int(end)}'


def port_label(start: int, end: int) -> str:
    return str(int(start)) if int(start) == int(end) else f'{int(start)}-{int(end)}'


def normalize_forward(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)

    external_start = normalized.get('external_port_start', normalized.get('external_port'))
    external_end = normalized.get('external_port_end', normalized.get('external_port'))
    guest_start = normalized.get('guest_port_start', normalized.get('guest_port'))
    guest_end = normalized.get('guest_port_end', normalized.get('guest_port'))

    try:
        external_start_i, external_end_i = int(external_start), int(external_end)
        guest_start_i, guest_end_i = int(guest_start), int(guest_end)
    except (TypeError, ValueError):
        external_start_i, external_end_i = parse_port_range(normalized.get('external_port', ''), 'Внешний порт')
        guest_start_i, guest_end_i = parse_port_range(normalized.get('guest_port', ''), 'Внутренний порт')

    normalized['external_port_start'] = external_start_i
    normalized['external_port_end'] = external_end_i
    normalized['guest_port_start'] = guest_start_i
    normalized['guest_port_end'] = guest_end_i

    # Backward compatibility for old templates and diagnostics.
    normalized['external_port'] = external_start_i
    normalized['guest_port'] = guest_start_i

    normalized['external_port_label'] = port_label(external_start_i, external_end_i)
    normalized['guest_port_label'] = port_label(guest_start_i, guest_end_i)
    normalized['mapping_label'] = f"{normalized['external_port_label']} → {normalized['guest_port_label']}"
    normalized['is_range'] = external_start_i != external_end_i or guest_start_i != guest_end_i
    normalized['range_size'] = max(port_range_size(external_start_i, external_end_i), port_range_size(guest_start_i, guest_end_i))
    return normalized


def ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return int(a_start) <= int(b_end) and int(b_start) <= int(a_end)


def valid_ip(value: str) -> bool:
    return bool(re.fullmatch(r'(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}', value or ''))


def valid_proto(value: str) -> bool:
    return value in {'tcp', 'udp'}


def external_interface() -> str:
    result = run_cmd(['ip', 'route', 'show', 'default'], timeout=5)
    if not result['ok']:
        return 'eth0'
    match = re.search(r'\bdev\s+([^\s]+)', result['stdout'])
    return match.group(1) if match else 'eth0'


def ip_forward_state() -> str:
    path = Path('/proc/sys/net/ipv4/ip_forward')
    if not path.exists():
        return 'unknown'
    return 'enabled' if path.read_text().strip() == '1' else 'disabled'


def nat_network_xml() -> str:
    return f"""<network>
  <name>{NETWORK_NAME}</name>
  <forward mode='nat'/>
  <bridge name='{NAT_BRIDGE}' stp='on' delay='0'/>
  <ip address='{NAT_GATEWAY}' netmask='255.255.255.0'>
    <dhcp>
      <range start='{DHCP_START}' end='{DHCP_END}'/>
    </dhcp>
  </ip>
</network>
"""


def libvirt_network_info() -> dict[str, Any]:
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


def list_libvirt_networks() -> list[dict[str, str]]:
    result = run_cmd(['virsh', 'net-list', '--all'], timeout=8)
    rows = []
    if not result['ok']:
        return rows
    for line in result['stdout'].splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 3:
            rows.append({'name': parts[0], 'state': parts[1], 'autostart': parts[2]})
    return rows


def create_nat_network() -> dict[str, Any]:
    ensure_dirs()
    Path('/tmp/virtuality-nat.xml').write_text(nat_network_xml())
    existing = run_cmd(['virsh', 'net-info', NETWORK_NAME], timeout=8)
    if not existing['ok']:
        defined = run_cmd(['virsh', 'net-define', '/tmp/virtuality-nat.xml'], timeout=15)
        if not defined['ok']:
            raise NetworkError(defined['stderr'] or defined['stdout'] or 'Не удалось создать libvirt NAT-сеть')
    started = run_cmd(['virsh', 'net-start', NETWORK_NAME], timeout=15)
    if not started['ok'] and 'already active' not in (started['stderr'] + started['stdout']).lower():
        raise NetworkError(started['stderr'] or started['stdout'] or 'Не удалось запустить libvirt NAT-сеть')
    autostart = run_cmd(['virsh', 'net-autostart', NETWORK_NAME], timeout=15)
    if not autostart['ok']:
        raise NetworkError(autostart['stderr'] or autostart['stdout'] or 'Не удалось включить autostart для NAT-сети')
    enable_ip_forward()
    disable_rp_filter()
    return libvirt_network_info()


def enable_ip_forward() -> None:
    Path('/etc/sysctl.d/99-virtuality-forward.conf').write_text('net.ipv4.ip_forward=1\n')
    run_cmd(['sysctl', '-p', '/etc/sysctl.d/99-virtuality-forward.conf'], timeout=10)


def disable_rp_filter() -> None:
    ext = external_interface()
    Path('/etc/sysctl.d/98-virtuality-rpfilter.conf').write_text(
        'net.ipv4.conf.all.rp_filter=0\n'
        f'net.ipv4.conf.{ext}.rp_filter=0\n'
        f'net.ipv4.conf.{NAT_BRIDGE}.rp_filter=0\n'
    )
    run_cmd(['sysctl', '-p', '/etc/sysctl.d/98-virtuality-rpfilter.conf'], timeout=10)


def load_port_forwards() -> list[dict[str, Any]]:
    ensure_dirs()
    if not PORT_FORWARDS_FILE.exists():
        PORT_FORWARDS_FILE.write_text('[]')
    try:
        data = json.loads(PORT_FORWARDS_FILE.read_text())
    except Exception:
        data = []
    if not isinstance(data, list):
        return []

    items: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            items.append(normalize_forward(item))
        except NetworkError:
            continue
    return items


def save_port_forwards(items: list[dict[str, Any]]) -> None:
    ensure_dirs()
    cleaned: list[dict[str, Any]] = []
    for item in items:
        normalized = normalize_forward(item)
        cleaned.append({
            'id': normalized.get('id') or str(uuid.uuid4()),
            'vm_name': normalized['vm_name'],
            'guest_ip': normalized['guest_ip'],
            'external_port_start': int(normalized['external_port_start']),
            'external_port_end': int(normalized['external_port_end']),
            'guest_port_start': int(normalized['guest_port_start']),
            'guest_port_end': int(normalized['guest_port_end']),
            'protocol': normalized['protocol'],
            'note': str(normalized.get('note', '')).strip()[:120],
        })
    PORT_FORWARDS_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2))


def vm_mac_addresses(vm_name: str) -> list[str]:
    result = run_cmd(['virsh', 'domiflist', vm_name], timeout=8)
    if not result['ok']:
        return []
    macs: list[str] = []
    for line in result['stdout'].splitlines():
        match = re.search(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', line)
        if match:
            macs.append(match.group(0).lower())
    return macs


def resolve_vm_ip(vm_name: str) -> str | None:
    result = run_cmd(['virsh', 'domifaddr', vm_name], timeout=8)
    if result['ok']:
        match = re.search(r'\b(192\.168\.100\.\d+|\d+\.\d+\.\d+\.\d+)/\d+', result['stdout'])
        if match:
            return match.group(1)

    leases = run_cmd(['virsh', 'net-dhcp-leases', NETWORK_NAME], timeout=8)
    if not leases['ok']:
        return None
    macs = set(vm_mac_addresses(vm_name))
    for line in leases['stdout'].splitlines():
        low = line.lower()
        if macs and not any(mac in low for mac in macs):
            continue
        match = re.search(r'\b(192\.168\.100\.\d+|\d+\.\d+\.\d+\.\d+)/\d+', line)
        if match:
            return match.group(1)
    return None


def tcp_connect_check(host: str, port: int, timeout: float = 2.0) -> dict[str, Any]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {'ok': True, 'message': f'{host}:{port} reachable'}
    except Exception as exc:
        return {'ok': False, 'message': str(exc)}


def add_port_forward(vm_name: str, guest_ip: str, external_port: Any, guest_port: Any, protocol: str, note: str = '') -> dict[str, Any]:
    if not vm_name or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}', vm_name):
        raise NetworkError('Некорректное имя VM')
    if guest_ip == 'auto':
        resolved_ip = resolve_vm_ip(vm_name)
        if not resolved_ip:
            raise NetworkError('Не удалось автоматически определить IP VM. Запусти VM, дождись DHCP, установи qemu-guest-agent или проверь DHCP leases на странице сети.')
        guest_ip = resolved_ip
    if not valid_ip(guest_ip):
        raise NetworkError('Некорректный внутренний IP VM')

    external_start, external_end = parse_port_range(external_port, 'Внешний порт')
    guest_start, guest_end = parse_port_range(guest_port, 'Внутренний порт')
    external_size = port_range_size(external_start, external_end)
    guest_size = port_range_size(guest_start, guest_end)
    if external_size != guest_size:
        raise NetworkError('Диапазоны внешних и внутренних портов должны быть одинаковой длины. Например: 10000-20000 → 10000-20000.')
    if not valid_proto(protocol):
        raise NetworkError('Протокол должен быть tcp или udp')

    items = load_port_forwards()
    for item in items:
        if item['protocol'] != protocol:
            continue
        if ranges_overlap(external_start, external_end, int(item['external_port_start']), int(item['external_port_end'])):
            raise NetworkError(f"Внешний порт/диапазон пересекается с {item['external_port_label']}/{protocol}")

    forward = {
        'id': str(uuid.uuid4()),
        'vm_name': vm_name,
        'guest_ip': guest_ip,
        'external_port_start': external_start,
        'external_port_end': external_end,
        'guest_port_start': guest_start,
        'guest_port_end': guest_end,
        'protocol': protocol,
        'note': note.strip()[:120],
    }
    items.append(forward)
    save_port_forwards(items)
    apply_port_forwards()
    return normalize_forward(forward)


def delete_port_forward(forward_id: str) -> None:
    items = [item for item in load_port_forwards() if item.get('id') != forward_id]
    save_port_forwards(items)
    apply_port_forwards()


def render_nft_rules() -> str:
    ext = external_interface()
    lines = [
        'table ip virtuality {',
        '  chain prerouting {',
        '    type nat hook prerouting priority dstnat; policy accept;',
    ]
    for item in load_port_forwards():
        external_ports = nft_port_value(item['external_port_start'], item['external_port_end'])
        guest_ports = nft_port_value(item['guest_port_start'], item['guest_port_end'])
        lines.append(f"    iifname \"{ext}\" {item['protocol']} dport {external_ports} dnat to {item['guest_ip']}:{guest_ports}")
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
    return '\n'.join(lines) + '\n'


def apply_ufw_route_rules(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ext = external_interface()
    results: list[dict[str, Any]] = []
    if not run_cmd(['sh', '-lc', 'command -v ufw >/dev/null 2>&1'], timeout=5)['ok']:
        return results
    status = run_cmd(['ufw', 'status'], timeout=8)
    if 'Status: active' not in status['stdout']:
        return results
    for raw_item in items:
        item = normalize_forward(raw_item)
        guest_port = iptables_port_value(item['guest_port_start'], item['guest_port_end'])
        external_port = iptables_port_value(item['external_port_start'], item['external_port_end'])
        cmd = [
            'ufw', 'route', 'allow',
            'in', 'on', ext,
            'out', 'on', NAT_BRIDGE,
            'to', item['guest_ip'],
            'port', guest_port,
            'proto', item['protocol'],
        ]
        results.append(run_cmd(cmd, timeout=15))
        results.append(run_cmd(['ufw', 'allow', f"{external_port}/{item['protocol']}"], timeout=15))
    run_cmd(['ufw', 'reload'], timeout=20)
    return results


def iptables_rule_exists(cmd: list[str]) -> bool:
    check_cmd = cmd.copy()
    if '-I' in check_cmd:
        check_cmd[check_cmd.index('-I')] = '-C'
        if check_cmd[check_cmd.index('-C') + 2].isdigit():
            del check_cmd[check_cmd.index('-C') + 2]
    return run_cmd(check_cmd, timeout=8)['ok']


def ensure_iptables_rule(cmd: list[str]) -> dict[str, Any]:
    if iptables_rule_exists(cmd):
        return {'ok': True, 'code': 0, 'stdout': 'exists', 'stderr': '', 'cmd': ' '.join(cmd)}
    return run_cmd(cmd, timeout=10)


def apply_iptables_fallback(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ext = external_interface()
    results: list[dict[str, Any]] = []
    if not run_cmd(['sh', '-lc', 'command -v iptables >/dev/null 2>&1'], timeout=5)['ok']:
        return results
    for raw_item in items:
        item = normalize_forward(raw_item)
        proto = item['protocol']
        guest_ip = item['guest_ip']
        external_port = iptables_port_value(item['external_port_start'], item['external_port_end'])
        guest_port = iptables_port_value(item['guest_port_start'], item['guest_port_end'])
        guest_to = f"{guest_ip}:{iptables_port_value(item['guest_port_start'], item['guest_port_end'])}"
        results.append(ensure_iptables_rule(['iptables', '-I', 'FORWARD', '1', '-i', ext, '-o', NAT_BRIDGE, '-p', proto, '-d', guest_ip, '-m', proto, '--dport', guest_port, '-j', 'ACCEPT']))
        results.append(ensure_iptables_rule(['iptables', '-I', 'FORWARD', '1', '-i', NAT_BRIDGE, '-o', ext, '-s', guest_ip, '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT']))
        results.append(ensure_iptables_rule(['iptables', '-t', 'nat', '-I', 'PREROUTING', '1', '-i', ext, '-p', proto, '-m', proto, '--dport', external_port, '-j', 'DNAT', '--to-destination', guest_to]))
    results.append(ensure_iptables_rule(['iptables', '-t', 'nat', '-I', 'POSTROUTING', '1', '-s', NAT_SUBNET, '-o', ext, '-j', 'MASQUERADE']))
    return results


def apply_port_forwards() -> dict[str, Any]:
    ensure_dirs()
    enable_ip_forward()
    disable_rp_filter()
    items = load_port_forwards()
    NFT_FILE.write_text(render_nft_rules())
    run_cmd(['nft', 'delete', 'table', 'ip', 'virtuality'], timeout=8)
    result = run_cmd(['nft', '-f', str(NFT_FILE)], timeout=15)
    if not result['ok']:
        raise NetworkError(result['stderr'] or result['stdout'] or 'Не удалось применить nftables-правила')
    ufw_results = apply_ufw_route_rules(items)
    iptables_results = apply_iptables_fallback(items)
    return {
        'ok': True,
        'file': str(NFT_FILE),
        'rules': render_nft_rules(),
        'ufw': ufw_results,
        'iptables': iptables_results,
    }


def find_matching_forward(forwards: list[dict[str, Any]], vm_name: str, external_port: int, guest_port: int, protocol: str) -> dict[str, Any] | None:
    for raw_item in forwards:
        item = normalize_forward(raw_item)
        if item.get('vm_name') != vm_name or item.get('protocol') != protocol:
            continue
        if int(item['external_port_start']) <= int(external_port) <= int(item['external_port_end']) and int(item['guest_port_start']) <= int(guest_port) <= int(item['guest_port_end']):
            return item
    return None


def diagnose_public_access(vm_name: str, external_port: int, guest_port: int, protocol: str = 'tcp') -> dict[str, Any]:
    if not vm_name or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}', vm_name):
        raise NetworkError('Некорректное имя VM')
    if not valid_port(external_port) or not valid_port(guest_port):
        raise NetworkError('Порт должен быть от 1 до 65535')
    if not valid_proto(protocol):
        raise NetworkError('Протокол должен быть tcp или udp')

    ext = external_interface()
    vm_ip = resolve_vm_ip(vm_name)
    forwards = load_port_forwards()
    matching_forward = find_matching_forward(forwards, vm_name, int(external_port), int(guest_port), protocol)

    nft_rules = run_cmd(['nft', 'list', 'ruleset'], timeout=12)
    ipt_forward = run_cmd(['iptables', '-S', 'FORWARD'], timeout=8)
    ipt_nat = run_cmd(['iptables', '-t', 'nat', '-S'], timeout=8)
    ufw_status = run_cmd(['ufw', 'status', 'numbered'], timeout=10)
    domiflist = run_cmd(['virsh', 'domiflist', vm_name], timeout=8)
    domifaddr = run_cmd(['virsh', 'domifaddr', vm_name], timeout=8)
    net_info = run_cmd(['virsh', 'net-info', NETWORK_NAME], timeout=8)

    vm_port_check = {'ok': False, 'message': 'VM IP not found'}
    if vm_ip and protocol == 'tcp':
        vm_port_check = tcp_connect_check(vm_ip, int(guest_port))
    elif vm_ip and protocol == 'udp':
        vm_port_check = {'ok': True, 'message': 'UDP cannot be reliably checked with TCP connect; rules only'}

    nft_text = nft_rules['stdout']
    ipt_forward_text = ipt_forward['stdout']
    ipt_nat_text = ipt_nat['stdout']
    ufw_text = ufw_status['stdout']

    expected_dnat = f'dport {int(external_port)} dnat to {vm_ip}:{int(guest_port)}' if vm_ip else ''
    nft_has_single_rule = bool(vm_ip and expected_dnat in nft_text)
    nft_has_range_rule = bool(matching_forward and vm_ip and matching_forward['external_port_label'] in nft_text and f"{vm_ip}:{matching_forward['guest_port_label']}" in nft_text)
    nft_has_rule = nft_has_single_rule or nft_has_range_rule
    iptables_has_prerouting = bool(vm_ip and f'--dport {int(external_port)} -j DNAT --to-destination {vm_ip}:{int(guest_port)}' in ipt_nat_text)
    iptables_has_range_prerouting = bool(matching_forward and vm_ip and matching_forward['external_port_label'].replace('-', ':') in ipt_nat_text and f"{vm_ip}:{matching_forward['guest_port_label'].replace('-', ':')}" in ipt_nat_text)
    iptables_has_prerouting = iptables_has_prerouting or iptables_has_range_prerouting
    iptables_has_forward = bool(vm_ip and f'-d {vm_ip}/32' in ipt_forward_text and (f'--dport {int(guest_port)}' in ipt_forward_text or (matching_forward and matching_forward['guest_port_label'].replace('-', ':') in ipt_forward_text)))
    ufw_has_route = bool(vm_ip and vm_ip in ufw_text and (str(int(guest_port)) in ufw_text or (matching_forward and matching_forward['guest_port_label'].replace('-', ':') in ufw_text)))

    checks = [
        {'name': 'VM exists/interface', 'ok': domiflist['ok'], 'detail': domiflist['stdout'] or domiflist['stderr']},
        {'name': 'VM IP resolved', 'ok': bool(vm_ip), 'detail': vm_ip or 'not found'},
        {'name': 'virtuality-nat active', 'ok': net_info['ok'] and 'Active: yes' in net_info['stdout'], 'detail': net_info['stdout'] or net_info['stderr']},
        {'name': 'ip_forward enabled', 'ok': ip_forward_state() == 'enabled', 'detail': ip_forward_state()},
        {'name': 'port forward config', 'ok': bool(matching_forward), 'detail': json.dumps(matching_forward, ensure_ascii=False) if matching_forward else 'not found'},
        {'name': f'VM service {vm_ip}:{guest_port}', 'ok': vm_port_check['ok'], 'detail': vm_port_check['message']},
        {'name': 'nft DNAT rule', 'ok': nft_has_rule, 'detail': expected_dnat or (matching_forward['mapping_label'] if matching_forward else 'VM IP not found')},
        {'name': 'iptables DNAT fallback', 'ok': iptables_has_prerouting, 'detail': 'present' if iptables_has_prerouting else 'not found'},
        {'name': 'iptables FORWARD fallback', 'ok': iptables_has_forward, 'detail': 'present' if iptables_has_forward else 'not found'},
        {'name': 'UFW route allow', 'ok': ufw_has_route or 'Status: inactive' in ufw_text, 'detail': 'present/inactive' if ufw_has_route or 'Status: inactive' in ufw_text else 'not found'},
    ]

    if not vm_ip:
        verdict = 'VM не получила IP. Запусти VM, дождись DHCP или проверь virtuality-nat.'
    elif not vm_port_check['ok']:
        verdict = f'VM найдена, но сервис внутри VM не отвечает на {vm_ip}:{guest_port}.'
    elif not nft_has_rule and not iptables_has_prerouting:
        verdict = 'Сервис VM отвечает, но DNAT-правило не найдено. Нажми «Применить правила».'
    elif not iptables_has_forward and not ufw_has_route:
        verdict = 'DNAT есть, но FORWARD/route-правила выглядят неполными. Нажми «Применить правила».'
    else:
        verdict = 'Локальная цепочка Virtuality выглядит готовой. Если снаружи не открывается — проверь входящий трафик tcpdump на внешнем интерфейсе и firewall провайдера.'

    return {
        'vm_name': vm_name,
        'vm_ip': vm_ip,
        'external_interface': ext,
        'external_port': int(external_port),
        'guest_port': int(guest_port),
        'protocol': protocol,
        'verdict': verdict,
        'checks': checks,
        'commands': {
            'watch_external': f"sudo tcpdump -ni {ext} '{protocol} port {int(external_port)}'",
            'watch_internal': f"sudo tcpdump -ni {NAT_BRIDGE} 'host {vm_ip or '<VM_IP>'} and {protocol} port {int(guest_port)}'",
        },
        'raw': {
            'domifaddr': domifaddr['stdout'] or domifaddr['stderr'],
            'ufw': ufw_text,
            'iptables_forward': ipt_forward_text,
            'iptables_nat': ipt_nat_text,
        },
    }


def network_context() -> dict[str, Any]:
    return {
        'nat': libvirt_network_info(),
        'networks': list_libvirt_networks(),
        'forwards': load_port_forwards(),
        'external_interface': external_interface(),
        'ip_forward': ip_forward_state(),
        'nft_rules': render_nft_rules(),
    }
