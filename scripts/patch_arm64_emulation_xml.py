#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

helpers = r'''

def is_x86_host(profile: dict[str, Any]) -> bool:
    return str(profile.get("arch", "")) in ("x86_64", "amd64")


def is_arm64_guest_on_x86(guest_arch: str, profile: dict[str, Any]) -> bool:
    return guest_arch == "aarch64" and is_x86_host(profile)


def xml_escape(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def arm64_emulation_xml(name: str, memory: int, vcpus: int, disk_path: Path, network_mode: str, bridge: str) -> str:
    if network_mode == "nat":
        interface_xml = f"""
    <interface type='network'>
      <source network='{xml_escape(network_core.NETWORK_NAME)}'/>
      <model type='virtio'/>
    </interface>"""
    else:
        interface_xml = f"""
    <interface type='bridge'>
      <source bridge='{xml_escape(bridge)}'/>
      <model type='virtio'/>
    </interface>"""
    return f"""<domain type='qemu'>
  <name>{xml_escape(name)}</name>
  <memory unit='MiB'>{int(memory)}</memory>
  <currentMemory unit='MiB'>{int(memory)}</currentMemory>
  <vcpu placement='static'>{int(vcpus)}</vcpu>
  <os>
    <type arch='aarch64' machine='virt'>hvm</type>
  </os>
  <cpu mode='custom' match='exact'>
    <model fallback='allow'>cortex-a57</model>
  </cpu>
  <features>
    <gic version='3'/>
  </features>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-aarch64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{xml_escape(str(disk_path))}'/>
      <target dev='vda' bus='virtio'/>
    </disk>{interface_xml}
    <graphics type='vnc' port='-1' autoport='yes' listen='0.0.0.0'>
      <listen type='address' address='0.0.0.0'/>
    </graphics>
    <video>
      <model type='virtio'/>
    </video>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
  </devices>
</domain>
"""


def make_arm64_emulation_script(name: str, memory: int, vcpus: int, disk_path: Path, network_mode: str, bridge: str) -> str:
    xml_path = Path('/tmp') / f"virtuality-{name}-arm64.xml"
    xml_path.write_text(arm64_emulation_xml(name, memory, vcpus, disk_path, network_mode, bridge))
    return f"virsh define {xml_path} && virsh start {name}"
'''

if 'def arm64_emulation_xml(' not in text:
    marker = '\n\ndef valid_vm_name(name: str) -> bool:'
    if marker not in text:
        raise SystemExit('valid_vm_name marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('ARM64 emulation XML helpers added')
else:
    changed.append('ARM64 emulation XML helpers already present')

old_disk_cmd = '''    if source_type == "disk_image":
        source_disk = Path(disk_image_path).resolve()
        source_format = disk_image_format(source_disk)
        convert_cmd = f"qemu-img convert -p -f {source_format} -O qcow2 {source_disk} {disk_path}"
        virt_cmd = " ".join(cmd + ["--import", "--disk", f"path={disk_path},format=qcow2,bus=virtio", "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"])
        cmd = ["bash", "-lc", f"set -euo pipefail; {convert_cmd}; {virt_cmd}"]
    else:
        cmd += ["--disk", f"path={disk_path},size={disk_size},format=qcow2,bus=virtio", "--cdrom", iso_path, "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"]
'''
new_disk_cmd = '''    if source_type == "disk_image":
        source_disk = Path(disk_image_path).resolve()
        source_format = disk_image_format(source_disk)
        convert_cmd = f"qemu-img convert -p -f {source_format} -O qcow2 {source_disk} {disk_path}"
        if is_arm64_guest_on_x86(resolved_guest_arch, profile):
            create_cmd = make_arm64_emulation_script(name, memory, vcpus, disk_path, network_mode, bridge)
            cmd = ["bash", "-lc", f"set -euo pipefail; {convert_cmd}; {create_cmd}"]
        else:
            virt_cmd = " ".join(cmd + ["--import", "--disk", f"path={disk_path},format=qcow2,bus=virtio", "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"])
            cmd = ["bash", "-lc", f"set -euo pipefail; {convert_cmd}; {virt_cmd}"]
    else:
        if is_arm64_guest_on_x86(resolved_guest_arch, profile):
            return vm_form_context(request, error="ARM64 ISO на x86-хосте пока не поддерживается. Загрузи готовый ARM64 .img/.qcow2 в разделе «Диски» и создай VM из готового диска.", form=form, status_code=400)
        cmd += ["--disk", f"path={disk_path},size={disk_size},format=qcow2,bus=virtio", "--cdrom", iso_path, "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"]
'''
if old_disk_cmd in text:
    text = text.replace(old_disk_cmd, new_disk_cmd, 1)
    changed.append('ARM64 disk image on x86 uses direct libvirt XML')
elif 'make_arm64_emulation_script' in text:
    changed.append('ARM64 disk image XML path already present')
else:
    raise SystemExit('disk image create command marker not found')

app_path.write_text(text)
print('arm64 emulation xml patch applied:')
for item in changed:
    print(f'- {item}')
