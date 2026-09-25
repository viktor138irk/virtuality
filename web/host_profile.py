import json
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

PROFILE_FILE = Path('/var/lib/virtuality/config/host_profile.json')
DEFAULT_BRIDGE = 'br0'
PROFILE_SCHEMA = 3


def run_cmd(cmd: list[str], timeout: int = 5) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return {'ok': result.returncode == 0, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip(), 'code': result.returncode}
    except Exception as exc:
        return {'ok': False, 'stdout': '', 'stderr': str(exc), 'code': -1}


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(errors='ignore').strip('\x00\n ')
    except Exception:
        return ''


def detect_board_model() -> str:
    candidates = [
        '/proc/device-tree/model',
        '/sys/firmware/devicetree/base/model',
    ]
    for path in candidates:
        value = read_text(path)
        if value:
            return value
    cpuinfo = read_text('/proc/cpuinfo')
    for prefix in ('model name', 'hardware', 'model'):
        for line in cpuinfo.splitlines():
            key, _, value = line.partition(':')
            value = value.strip()
            if key.strip().lower() == prefix and value and not value.isdigit():
                return value
    return 'unknown'


def list_interfaces() -> list[str]:
    result = run_cmd(['ip', '-o', 'link', 'show'], timeout=5)
    interfaces: list[str] = []
    if not result['ok']:
        return interfaces
    for line in result['stdout'].splitlines():
        match = re.match(r'\d+:\s+([^:@]+)', line)
        if match:
            interfaces.append(match.group(1))
    return interfaces


def list_bridges() -> list[str]:
    result = run_cmd(['sh', '-lc', "ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1"], timeout=5)
    if not result['ok']:
        return []
    return [item.strip() for item in result['stdout'].splitlines() if item.strip()]


def cpu_virtualization_flags_count() -> int:
    cpuinfo = read_text('/proc/cpuinfo')
    return len(re.findall(r'\b(vmx|svm)\b', cpuinfo))


def classify_profile(arch: str, model: str) -> dict[str, str]:
    low = model.lower()
    if arch in ('aarch64', 'arm64'):
        if 'raspberry pi' in low:
            return {
                'profile': 'raspberry-arm64',
                'label': 'Raspberry Pi (ARM64)',
                'recommended_network': 'nat',
                'recommended_guest_arch': 'aarch64',
                'recommended_vm_mode': 'arm64-cloud-image',
            }
        if 'orange pi 5' in low or 'orangepi 5' in low or 'rk3588' in low or 'rockchip' in low:
            return {
                'profile': 'orangepi5-arm64',
                'label': 'Orange Pi 5 (ARM64)',
                'recommended_network': 'nat',
                'recommended_guest_arch': 'aarch64',
                'recommended_vm_mode': 'arm64-cloud-image',
            }
        return {
            'profile': 'generic-arm64',
            'label': 'ARM64-сервер',
            'recommended_network': 'nat',
            'recommended_guest_arch': 'aarch64',
            'recommended_vm_mode': 'arm64-cloud-image',
        }
    return {
        'profile': 'x86_64',
        'label': 'Сервер x86_64',
        'recommended_network': 'bridge',
        'recommended_guest_arch': 'x86_64',
        'recommended_vm_mode': 'x86_64-iso',
    }


def command_exists(name: str) -> bool:
    return run_cmd(['sh', '-lc', f'command -v {name} >/dev/null 2>&1'])['ok']


def detect_host_profile() -> dict[str, Any]:
    arch = platform.machine() or run_cmd(['uname', '-m'])['stdout'] or 'unknown'
    model = detect_board_model()
    base = classify_profile(arch, model)
    is_arm = arch in ('aarch64', 'arm64')
    interfaces = list_interfaces()
    bridges = list_bridges()
    bridge_available = DEFAULT_BRIDGE in interfaces or DEFAULT_BRIDGE in bridges
    recommended_network = base['recommended_network']
    if recommended_network == 'bridge' and not bridge_available:
        recommended_network = 'nat'

    kvm_device = Path('/dev/kvm').exists()
    virtualization_flags = cpu_virtualization_flags_count()
    virtualization_mode = 'kvm' if kvm_device else 'qemu'

    data: dict[str, Any] = {
        'arch': arch,
        'model': model,
        'profile': base['profile'],
        'label': base['label'],
        'recommended_network': recommended_network,
        'recommended_guest_arch': base['recommended_guest_arch'],
        'recommended_vm_mode': base['recommended_vm_mode'],
        'network_interfaces': interfaces,
        'bridges': bridges,
        'default_bridge': DEFAULT_BRIDGE,
        'bridge_available': bridge_available,
        'kvm_device': kvm_device,
        'virtualization_flags': virtualization_flags,
        'virtualization_mode': virtualization_mode,
        'virtualization_label': 'KVM hardware acceleration' if virtualization_mode == 'kvm' else 'QEMU software emulation',
        'virtualization_warning': '' if virtualization_mode == 'kvm' else 'KVM недоступен: VM будут запускаться через медленный QEMU fallback без аппаратного ускорения. На физическом сервере включи Intel VT-x / AMD-V в BIOS/UEFI.',
        'qemu_system_x86_64': command_exists('qemu-system-x86_64'),
        'qemu_system_aarch64': command_exists('qemu-system-aarch64'),
        'virt_install': command_exists('virt-install'),
        'nft': command_exists('nft'),
        'cloud_image_utils': command_exists('cloud-localds'),
        'uefi_aarch64_hint': any(Path(path).exists() for path in [
            '/usr/share/AAVMF/AAVMF_CODE.fd',
            '/usr/share/qemu-efi-aarch64/QEMU_EFI.fd',
            '/usr/share/AAVMF/AAVMF_VARS.fd',
        ]),
    }
    checks = []
    checks.append({'name': 'Аппаратное ускорение (KVM)', 'ok': data['kvm_device'], 'level': 'ok' if data['kvm_device'] else 'warn', 'hint': 'KVM недоступен. На физическом сервере включи Intel VT-x / AMD-V в BIOS/UEFI; на VPS проверь nested virtualization. Пока доступен медленный QEMU fallback.'})
    if not is_arm:  # ARM CPUs have no vmx/svm flags; /dev/kvm above is what matters there
        checks.append({'name': 'Поддержка виртуализации процессором', 'ok': virtualization_flags > 0, 'level': 'ok' if virtualization_flags > 0 else 'warn', 'hint': 'CPU-флаги vmx/svm не видны. Для реального сервера это обычно значит, что виртуализация выключена в BIOS/UEFI.'})
    if is_arm:
        checks.append({'name': 'Эмулятор ARM64 (QEMU)', 'ok': data['qemu_system_aarch64'], 'level': 'ok' if data['qemu_system_aarch64'] else 'err', 'hint': 'Пакет qemu-system-arm / qemu-system-aarch64.'})
        checks.append({'name': 'Загрузчик UEFI для ARM64', 'ok': data['uefi_aarch64_hint'], 'level': 'ok' if data['uefi_aarch64_hint'] else 'warn', 'hint': 'Пакет qemu-efi-aarch64 или AAVMF.'})
    else:
        checks.append({'name': 'Эмулятор x86_64 (QEMU)', 'ok': data['qemu_system_x86_64'], 'level': 'ok' if data['qemu_system_x86_64'] else 'err', 'hint': 'Пакет qemu-system-x86.'})
    checks.append({'name': 'Создание машин (virt-install)', 'ok': data['virt_install'], 'level': 'ok' if data['virt_install'] else 'err', 'hint': 'Пакет virtinst.'})
    checks.append({'name': 'Брандмауэр nftables', 'ok': data['nft'], 'level': 'ok' if data['nft'] else 'err', 'hint': 'Пакет nftables для NAT port forwarding.'})
    checks.append({'name': f'Сетевой мост {DEFAULT_BRIDGE}', 'ok': bridge_available, 'level': 'ok' if bridge_available else 'warn', 'hint': 'Если br0 отсутствует, Virtuality будет использовать VPS NAT Router / virtuality-nat.'})
    data['checks'] = checks
    data['schema'] = PROFILE_SCHEMA
    data['ready'] = data['virt_install'] and (data['qemu_system_aarch64'] if is_arm else data['qemu_system_x86_64']) and data['nft']
    return data


def save_host_profile(profile: dict[str, Any]) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2))


def load_host_profile() -> dict[str, Any]:
    if PROFILE_FILE.exists():
        try:
            profile = json.loads(PROFILE_FILE.read_text())
            if profile.get('schema') != PROFILE_SCHEMA or 'bridge_available' not in profile or 'virtualization_mode' not in profile or any('level' not in item for item in profile.get('checks', [])):
                profile = detect_host_profile()
                save_host_profile(profile)
            return profile
        except Exception:
            pass
    profile = detect_host_profile()
    save_host_profile(profile)
    return profile
