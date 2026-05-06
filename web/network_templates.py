from __future__ import annotations

from typing import Any

import network_core
from network_core import NetworkError


PUBLIC_ACCESS_TEMPLATES: dict[str, dict[str, Any]] = {
    'web': {
        'title': 'Web 80/443',
        'description': 'HTTP и HTTPS к web-сервису внутри VM',
        'rules': [
            {'external_port': 80, 'guest_port': 80, 'protocol': 'tcp', 'note': 'web-http'},
            {'external_port': 443, 'guest_port': 443, 'protocol': 'tcp', 'note': 'web-https'},
        ],
    },
    'ssh': {
        'title': 'SSH 2222 → 22',
        'description': 'SSH-доступ к VM через нестандартный внешний порт',
        'rules': [
            {'external_port': 2222, 'guest_port': 22, 'protocol': 'tcp', 'note': 'ssh'},
        ],
    },
    'mikopbx-web': {
        'title': 'MikoPBX Web',
        'description': 'Web-интерфейс MikoPBX: 80 и 443',
        'rules': [
            {'external_port': 80, 'guest_port': 80, 'protocol': 'tcp', 'note': 'mikopbx-http'},
            {'external_port': 443, 'guest_port': 443, 'protocol': 'tcp', 'note': 'mikopbx-https'},
        ],
    },
    'mikopbx-voip-basic': {
        'title': 'MikoPBX SIP basic',
        'description': 'Базовые SIP-порты. RTP-диапазон добавляй по необходимости.',
        'rules': [
            {'external_port': 5060, 'guest_port': 5060, 'protocol': 'udp', 'note': 'sip-udp'},
            {'external_port': 5060, 'guest_port': 5060, 'protocol': 'tcp', 'note': 'sip-tcp'},
            {'external_port': 5061, 'guest_port': 5061, 'protocol': 'tcp', 'note': 'sip-tls'},
        ],
    },
}


def list_templates() -> dict[str, dict[str, Any]]:
    return PUBLIC_ACCESS_TEMPLATES


def apply_template(vm_name: str, template_key: str, external_base_port: int | None = None) -> dict[str, Any]:
    if template_key not in PUBLIC_ACCESS_TEMPLATES:
        raise NetworkError('Неизвестный шаблон публичного доступа')
    if not vm_name:
        raise NetworkError('Не выбрана VM')

    vm_ip = network_core.resolve_vm_ip(vm_name)
    if not vm_ip:
        raise NetworkError('Не удалось определить IP VM. Запусти VM и дождись DHCP.')

    template = PUBLIC_ACCESS_TEMPLATES[template_key]
    existing = network_core.load_port_forwards()
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for rule in template['rules']:
        external_port = int(rule['external_port'])
        if external_base_port and template_key == 'ssh':
            external_port = int(external_base_port)
        protocol = rule['protocol']
        guest_port = int(rule['guest_port'])

        conflict = next((item for item in existing if int(item['external_port']) == external_port and item['protocol'] == protocol), None)
        if conflict:
            skipped.append({'reason': 'external port already used', 'rule': rule, 'conflict': conflict})
            continue

        forward = {
            'id': network_core.uuid.uuid4().__str__(),
            'vm_name': vm_name,
            'guest_ip': vm_ip,
            'external_port': external_port,
            'guest_port': guest_port,
            'protocol': protocol,
            'note': rule.get('note', template_key),
        }
        existing.append(forward)
        created.append(forward)

    network_core.save_port_forwards(existing)
    apply_result = network_core.apply_port_forwards()

    return {
        'template_key': template_key,
        'template': template,
        'vm_name': vm_name,
        'vm_ip': vm_ip,
        'created': created,
        'skipped': skipped,
        'apply_result': apply_result,
    }
