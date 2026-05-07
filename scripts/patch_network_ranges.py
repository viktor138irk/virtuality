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
    changed.append('app.py forward/add accepts port ranges as text')
elif new_signature in text:
    changed.append('app.py forward/add already accepts port ranges as text')
else:
    raise SystemExit('network_forward_add signature marker not found')

core_path = app_path.with_name('network_core.py')
if core_path.exists():
    core_text = core_path.read_text()
    before = core_text

    if 'def iptables_dnat_port_value(' not in core_text:
        marker = """def iptables_port_value(start: int, end: int) -> str:
    return str(int(start)) if int(start) == int(end) else f'{int(start)}:{int(end)}'


def port_label"""
        replacement = """def iptables_port_value(start: int, end: int) -> str:
    return str(int(start)) if int(start) == int(end) else f'{int(start)}:{int(end)}'


def iptables_dnat_port_value(start: int, end: int) -> str:
    return str(int(start)) if int(start) == int(end) else f'{int(start)}-{int(end)}'


def successful_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in results if item.get('ok')]


def failed_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in results if not item.get('ok')]


def port_label"""
        if marker not in core_text:
            raise SystemExit('network_core.py port helper marker not found')
        core_text = core_text.replace(marker, replacement, 1)
        changed.append('network_core.py iptables DNAT range helper added')
    else:
        changed.append('network_core.py iptables DNAT range helper already present')

    core_text = core_text.replace(
        'guest_to = f"{guest_ip}:{iptables_port_value(item[\'guest_port_start\'], item[\'guest_port_end\'])}"',
        'guest_to_port = iptables_dnat_port_value(item[\'guest_port_start\'], item[\'guest_port_end\'])\n        guest_to = f"{guest_ip}:{guest_to_port}"',
    )
    core_text = core_text.replace(
        'guest_to_port = nft_port_value(item[\'guest_port_start\'], item[\'guest_port_end\'])\n        guest_to = f"{guest_ip}:{guest_to_port}"',
        'guest_to_port = iptables_dnat_port_value(item[\'guest_port_start\'], item[\'guest_port_end\'])\n        guest_to = f"{guest_ip}:{guest_to_port}"',
    )

    old_apply = """def apply_port_forwards() -> dict[str, Any]:
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
"""
    new_apply = """def apply_port_forwards() -> dict[str, Any]:
    ensure_dirs()
    enable_ip_forward()
    disable_rp_filter()
    items = load_port_forwards()
    NFT_FILE.write_text(render_nft_rules())

    nft_delete = run_cmd(['nft', 'delete', 'table', 'ip', 'virtuality'], timeout=8)
    nft_apply = run_cmd(['nft', '-f', str(NFT_FILE)], timeout=15)
    ufw_results = apply_ufw_route_rules(items)
    iptables_results = apply_iptables_fallback(items)

    iptables_ok = bool(successful_results(iptables_results)) or not items
    if not nft_apply['ok'] and not iptables_ok:
        details = [
            nft_apply.get('stderr') or nft_apply.get('stdout') or 'nftables не применился',
            *[
                item.get('stderr') or item.get('stdout') or item.get('cmd', 'iptables rule failed')
                for item in failed_results(iptables_results)
            ],
        ]
        raise NetworkError('Не удалось применить правила проброса: ' + ' | '.join([d for d in details if d]))

    return {
        'ok': nft_apply['ok'] or iptables_ok,
        'file': str(NFT_FILE),
        'rules': render_nft_rules(),
        'nft_delete': nft_delete,
        'nft_apply': nft_apply,
        'ufw': ufw_results,
        'iptables': iptables_results,
    }
"""
    if old_apply in core_text:
        core_text = core_text.replace(old_apply, new_apply, 1)
        changed.append('network_core.py apply_port_forwards now falls back to iptables when nft fails')
    elif 'nft_apply = run_cmd([\'nft\', \'-f\', str(NFT_FILE)]' in core_text or "nft_apply = run_cmd(['nft', '-f', str(NFT_FILE)]" in core_text:
        changed.append('network_core.py apply_port_forwards fallback already present')
    else:
        raise SystemExit('network_core.py apply_port_forwards marker not found')

    core_text = core_text.replace(
        'f"{vm_ip}:{matching_forward[\'guest_port_label\'].replace(\'-\', \':\')}" in ipt_nat_text',
        '(f"{vm_ip}:{matching_forward[\'guest_port_label\']}" in ipt_nat_text or f"{vm_ip}:{matching_forward[\'guest_port_label\'].replace(\'-\', \':\')}" in ipt_nat_text)',
    )

    if core_text != before:
        core_path.write_text(core_text)
        changed.append('network_core.py port forwarding rules patched')
    else:
        changed.append('network_core.py port forwarding rules already ok')
else:
    changed.append(f'network_core.py not found near {app_path}')

print('network ranges patch applied:')
for item in changed:
    print(f'- {item}')
