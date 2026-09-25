"""Quick machines from cloud images (cloud-init), OS types for installers, fixed NAT addresses."""
import shlex

import app
import cloudinit
import conftest
import network_core
from test_audit_fixes import find, recording


def create(logged_in, monkeypatch, form):
    captured = {}
    monkeypatch.setattr(app, "start_background_operation", lambda operation, cmd: captured.update(operation=operation, cmd=cmd))
    monkeypatch.setattr(app.network_core, "create_nat_network", lambda: {})
    response = logged_in.post("/vm/create", data=form, follow_redirects=False)
    return response, captured


def cloud_form(image, **extra):
    form = {"name": "cloud-vm", "memory": 2048, "vcpus": 2, "disk_size": 20, "disk_image_path": str(image), "source_type": "disk_image", "network_mode": "nat", "cloud_init": "1", "ci_user": "viktor", "ci_password": "secret123"}
    form.update(extra)
    return form


# ---------------------------------------------------------------- cloud-init seed
def test_user_data_has_user_password_and_keys():
    text = cloudinit.build_user_data("web", "viktor", "$6$abc", ["ssh-ed25519 AAAAC3 me@laptop"])
    assert "hostname: web" in text and "- name: viktor" in text and "passwd: '$6$abc'" in text
    assert "- ssh-ed25519 AAAAC3 me@laptop" in text and "ssh_pwauth: true" in text and "growpart" in text
    assert "packages: [qemu-guest-agent]" in text and "package_update: true" in text
    assert "ssh_pwauth: false" in cloudinit.build_user_data("web", "viktor", "", ["ssh-ed25519 AAAAC3 me"])


def test_ssh_keys_and_users_are_validated():
    assert cloudinit.parse_ssh_keys("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 me\n\nssh-rsa AAAAB3 x\n") == ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 me", "ssh-rsa AAAAB3 x"]
    assert cloudinit.parse_ssh_keys("-----BEGIN OPENSSH PRIVATE KEY-----") is None
    assert cloudinit.parse_ssh_keys("") == []
    assert cloudinit.valid_user("viktor") and cloudinit.valid_user("web_admin-2")
    assert not cloudinit.valid_user("root") and not cloudinit.valid_user("Viktor") and not cloudinit.valid_user("")


def test_password_hash_uses_sha512_crypt():
    digest = cloudinit.hash_password("secret123")
    assert digest.startswith("$6$") and len(digest) > 60


def test_default_login_comes_from_catalog():
    assert cloudinit.default_user("resolute-server-cloudimg-amd64.img") == "ubuntu"
    assert cloudinit.default_user("debian-13-genericcloud-arm64.qcow2") == "debian"
    assert cloudinit.default_user("my-own-image.qcow2") == "admin"


# ---------------------------------------------------------------- creating from a cloud image
def test_cloud_image_vm_gets_seed_resize_and_fixed_address(logged_in, data_dirs, monkeypatch):
    image = data_dirs["disk_images"] / "resolute-server-cloudimg-amd64.img"
    image.write_bytes(b"QFI\xfb")
    monkeypatch.setattr(cloudinit, "hash_password", lambda password: "$6$fake$hash")
    calls = recording(monkeypatch, network_core, [(["virsh", "net-dumpxml"], conftest.fake_result((conftest.FIXTURES / "net-dumpxml.xml").read_text()))])
    response, captured = create(logged_in, monkeypatch, cloud_form(image, disk_size=60))
    assert response.status_code == 303, response.text[:400]
    script = captured["cmd"][-1]
    assert "qemu-img convert -p -f qcow2 -O qcow2" in script
    assert f"qemu-img resize {shlex.quote(str(data_dirs['images'] / 'cloud-vm.qcow2'))} 60G" in script
    assert "--import" in script and "--osinfo ubuntu24.04" in script and "--cloud-init user-data=" in script and "bus=virtio" in script
    operation = captured["operation"]
    assert operation["cloud_init_user"] == "viktor" and operation["nat_ip"] == "192.168.100.50"
    assert "Вход: viktor по паролю" in operation["success_message"] and "192.168.100.50" in operation["success_message"]
    mac = network_core.stable_mac("cloud-vm")
    assert f"mac={mac}" in operation["network"] and mac.startswith("52:54:00:")
    reservation = find(calls, "virsh", "net-update", "virtuality-nat", "add-last", "ip-dhcp-host")[0]
    assert f"mac='{mac}' name='cloud-vm' ip='192.168.100.50'" in reservation[5] and "--live" in reservation and "--config" in reservation
    user_data = open(operation["seed_file"]).read()
    assert "- name: viktor" in user_data and "$6$fake$hash" in user_data and "hostname: cloud-vm" in user_data
    cloudinit.remove_seed(operation["seed_file"])


def test_cloud_image_smaller_disk_is_not_resized(logged_in, data_dirs, monkeypatch):
    image = data_dirs["disk_images"] / "cloud.qcow2"
    image.write_bytes(b"qcow")
    monkeypatch.setattr(cloudinit, "hash_password", lambda password: "$6$fake$hash")
    response, captured = create(logged_in, monkeypatch, cloud_form(image, disk_size=20))
    assert response.status_code == 303
    assert "qemu-img resize" not in captured["cmd"][-1] and "--osinfo detect=on,require=off" in captured["cmd"][-1]
    cloudinit.remove_seed(captured["operation"]["seed_file"])


def test_cloud_init_can_be_skipped(logged_in, data_dirs, monkeypatch):
    image = data_dirs["disk_images"] / "cloud.qcow2"
    image.write_bytes(b"qcow")
    response, captured = create(logged_in, monkeypatch, cloud_form(image, cloud_init="0", ci_user="", ci_password=""))
    assert response.status_code == 303 and "--cloud-init" not in captured["cmd"][-1] and captured["operation"]["seed_file"] == ""


def test_cloud_init_validation(logged_in, data_dirs, monkeypatch):
    image = data_dirs["disk_images"] / "cloud.qcow2"
    image.write_bytes(b"qcow")
    assert create(logged_in, monkeypatch, cloud_form(image, ci_password="123"))[0].status_code == 400
    assert create(logged_in, monkeypatch, cloud_form(image, ci_user="Root User"))[0].status_code == 400
    assert create(logged_in, monkeypatch, cloud_form(image, ci_user="root"))[0].status_code == 400
    assert create(logged_in, monkeypatch, cloud_form(image, ci_ssh_key="not a key"))[0].status_code == 400
    monkeypatch.setattr(cloudinit, "hash_password", lambda password: "$6$fake$hash")
    ok, captured = create(logged_in, monkeypatch, cloud_form(image, ci_password="", ci_ssh_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA me@pc"))
    assert ok.status_code == 303 and "по SSH-ключу" in captured["operation"]["success_message"]
    cloudinit.remove_seed(captured["operation"]["seed_file"])


# ---------------------------------------------------------------- installers by OS type
def iso_form(iso, **extra):
    form = {"name": "new-vm", "memory": 4096, "vcpus": 2, "disk_size": 64, "iso_path": str(iso), "source_type": "iso", "network_mode": "nat"}
    form.update(extra)
    return form


def test_windows11_gets_uefi_tpm_sata_and_e1000e(logged_in, data_dirs, monkeypatch):
    iso = data_dirs["iso"] / "win11.iso"
    iso.write_bytes(b"iso")
    response, captured = create(logged_in, monkeypatch, iso_form(iso, os_type="windows11"))
    assert response.status_code == 303
    cmd = captured["cmd"]
    assert "--osinfo" in cmd and cmd[cmd.index("--osinfo") + 1] == "win11"
    assert cmd[cmd.index("--boot") + 1] == "uefi,cdrom,hd"
    assert "--tpm" in cmd and "backend.version=2.0" in cmd[cmd.index("--tpm") + 1]
    assert "bus=sata" in cmd[cmd.index("--disk") + 1] and "model=e1000e" in cmd[cmd.index("--network") + 1]
    assert "--channel" not in cmd  # Windows has no guest agent without extra drivers


def test_linux_installer_detects_os_and_uses_virtio(logged_in, data_dirs, monkeypatch):
    iso = data_dirs["iso"] / "debian.iso"
    iso.write_bytes(b"iso")
    response, captured = create(logged_in, monkeypatch, iso_form(iso))
    cmd = captured["cmd"]
    assert cmd[cmd.index("--osinfo") + 1] == "detect=on,require=off" and cmd[cmd.index("--boot") + 1] == "cdrom,hd"
    assert "bus=virtio" in cmd[cmd.index("--disk") + 1] and "model=virtio" in cmd[cmd.index("--network") + 1] and "--tpm" not in cmd
    assert cmd[cmd.index("--channel") + 1] == "unix,target.type=virtio,target.name=org.qemu.guest_agent.0"
    assert create(logged_in, monkeypatch, iso_form(iso, os_type="bsd"))[0].status_code == 400


# ---------------------------------------------------------------- fixed addresses
def test_reservation_is_reused_and_released(data_dirs, monkeypatch):
    calls = recording(monkeypatch, network_core, [(["virsh", "net-dumpxml"], conftest.fake_result((conftest.FIXTURES / "net-dumpxml.xml").read_text()))])
    assert network_core.reserve_nat_address("web01", "52:54:00:aa:bb:cc") == "192.168.100.51"
    assert not find(calls, "virsh", "net-update")
    network_core.release_nat_address("web01")
    deleted = find(calls, "virsh", "net-update", "virtuality-nat", "delete")[0]
    assert "name='web01' ip='192.168.100.51'" in deleted[5]


def test_vm_delete_releases_address(logged_in, data_dirs, monkeypatch):
    calls = recording(monkeypatch, app, [])
    net_calls = recording(monkeypatch, network_core, [(["virsh", "net-dumpxml"], conftest.fake_result((conftest.FIXTURES / "net-dumpxml.xml").read_text()))])
    assert logged_in.post("/vm/web01/delete", follow_redirects=False).status_code == 303
    assert find(calls, "virsh", "undefine") and find(net_calls, "virsh", "net-update", "virtuality-nat", "delete")
