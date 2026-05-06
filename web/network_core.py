import json
import re
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
    return libvirt_network_info()


def enable_ip_forward() -> None:
    Path('/etc/sysctl.d/99-virtuality-forward.conf').write_text('net.ipv4.ip_forward=1\n')
    run_cmd(['sysctl', '-p', '/etc/sysctl.d/99-virtuality-forward.conf'], timeout=10)


def load_port_forwards() -> list[dict[str, Any]]:
    ensure_dirs()
    if not PORT_FORWARDS_FILE.exists():
        PORT_FORWARDS_FILE.write_text('[]')
    try:
        data = json.loads(PORT_FORWARDS_FILE.read_text())
    except Exception:
        data = []
    return data if isinstance(data, list) else []


def save_port_forwards(items: list[dict[str, Any]]) -> None:
    ensure_dirs()
    PORT_FORWARDS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2))


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


def add_port_forward(vm_name: str, guest_ip: str, external_port: int, guest_port: int, protocol: str, note: str = '') -> dict[str, Any]:
    if not vm_name or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}', vm_name):
        raise NetworkError('Некорректное имя VM')
    if guest_ip == 'auto':
        resolved_ip = resolve_vm_ip(vm_name)
        if not resolved_ip:
            raise NetworkError('Не удалось автоматически определить IP VM. Запусти VM, дождись DHCP, установи qemu-guest-agent или проверь DHCP leases на странице сети.')
        guest_ip = resolved_ip
    if not valid_ip(guest_ip):
        raise NetworkError('Некорректный внутренний IP VM')
    if not valid_port(external_port) or not valid_port(guest_port):
        raise NetworkError('Порт должен быть от 1 до 65535')
    if not valid_proto(protocol):
        raise NetworkError('Протокол должен быть tcp или udp')
    items = load_port_forwards()
    for item in items:
        if int(item['external_port']) == int(external_port) and item['protocol'] == protocol:
            raise NetworkError(f'Внешний порт {external_port}/{protocol} уже занят')
    forward = {
        'id': str(uuid.uuid4()),
        'vm_name': vm_name,
        'guest_ip': guest_ip,
        'external_port': int(external_port),
        'guest_port': int(guest_port),
        'protocol': protocol,
        'note': note.strip()[:120],
    }
    items.append(forward)
    save_port_forwards(items)
    apply_port_forwards()
    return forward


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
        lines.append(f"    iifname \"{ext}\" {item['protocol']} dport {int(item['external_port'])} dnat to {item['guest_ip']}:{int(item['guest_port'])}")
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


def apply_port_forwards() -> dict[str, Any]:
    ensure_dirs()
    enable_ip_forward()
    NFT_FILE.write_text(render_nft_rules())
    run_cmd(['nft', 'delete', 'table', 'ip', 'virtuality'], timeout=8)
    result = run_cmd(['nft', '-f', str(NFT_FILE)], timeout=15)
    if not result['ok']:
        raise NetworkError(result['stderr'] or result['stdout'] or 'Не удалось применить nftables-правила')
    return {'ok': True, 'file': str(NFT_FILE), 'rules': render_nft_rules()}


def network_context() -> dict[str, Any]:
    return {
        'nat': libvirt_network_info(),
        'networks': list_libvirt_networks(),
        'forwards': load_port_forwards(),
        'external_interface': external_interface(),
        'ip_forward': ip_forward_state(),
        'nft_rules': render_nft_rules(),
    }
