import json
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

PROFILE_FILE = Path('/var/lib/virtuality/config/host_profile.json')


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
    for line in cpuinfo.splitlines():
        if line.lower().startswith(('model', 'hardware')) and ':' in line:
            return line.split(':', 1)[1].strip()
    return 'unknown'


def classify_profile(arch: str, model: str) -> dict[str, str]:
    low = model.lower()
    if arch in ('aarch64', 'arm64'):
        if 'raspberry pi' in low:
            return {
                'profile': 'raspberry-arm64',
                'label': 'Raspberry Pi ARM64 Edge Node',
                'recommended_network': 'nat',
                'recommended_guest_arch': 'aarch64',
                'recommended_vm_mode': 'arm64-cloud-image',
            }
        if 'orange pi 5' in low or 'orangepi 5' in low or 'rk3588' in low or 'rockchip' in low:
            return {
                'profile': 'orangepi5-arm64',
                'label': 'Orange Pi 5 ARM64 Edge Node',
                'recommended_network': 'nat',
                'recommended_guest_arch': 'aarch64',
                'recommended_vm_mode': 'arm64-cloud-image',
            }
        return {
            'profile': 'generic-arm64',
            'label': 'Generic ARM64 Edge Node',
            'recommended_network': 'nat',
            'recommended_guest_arch': 'aarch64',
            'recommended_vm_mode': 'arm64-cloud-image',
        }
    return {
        'profile': 'x86_64',
        'label': 'x86_64 KVM Node',
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
    data: dict[str, Any] = {
        'arch': arch,
        'model': model,
        'profile': base['profile'],
        'label': base['label'],
        'recommended_network': base['recommended_network'],
        'recommended_guest_arch': base['recommended_guest_arch'],
        'recommended_vm_mode': base['recommended_vm_mode'],
        'kvm_device': Path('/dev/kvm').exists(),
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
    checks.append({'name': '/dev/kvm', 'ok': data['kvm_device'], 'hint': 'Нужен KVM. На ARM-платах проверь ядро и виртуализацию.'})
    if is_arm:
        checks.append({'name': 'qemu-system-aarch64', 'ok': data['qemu_system_aarch64'], 'hint': 'Пакет qemu-system-arm / qemu-system-aarch64.'})
        checks.append({'name': 'AArch64 UEFI', 'ok': data['uefi_aarch64_hint'], 'hint': 'Пакет qemu-efi-aarch64 или AAVMF.'})
    else:
        checks.append({'name': 'qemu-system-x86_64', 'ok': data['qemu_system_x86_64'], 'hint': 'Пакет qemu-system-x86.'})
    checks.append({'name': 'virt-install', 'ok': data['virt_install'], 'hint': 'Пакет virtinst.'})
    checks.append({'name': 'nftables', 'ok': data['nft'], 'hint': 'Пакет nftables для NAT port forwarding.'})
    data['checks'] = checks
    data['ready'] = all(item['ok'] for item in checks if item['name'] != 'AArch64 UEFI')
    return data


def save_host_profile(profile: dict[str, Any]) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2))


def load_host_profile() -> dict[str, Any]:
    if PROFILE_FILE.exists():
        try:
            return json.loads(PROFILE_FILE.read_text())
        except Exception:
            pass
    profile = detect_host_profile()
    save_host_profile(profile)
    return profile
