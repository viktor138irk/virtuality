import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
sys.path.insert(0, str(WEB_DIR))

os.environ.setdefault("VIRTUALITY_AUTH_USER", "tester")
os.environ.setdefault("VIRTUALITY_SESSION_SECRET", "test-secret-" + "0" * 32)

DOMINFO = """Id:             1
Name:           web01
UUID:           6c1b3c1e-0000-0000-0000-000000000001
OS Type:        hvm
State:          running
CPU(s):         2
Max memory:     2097152 KiB
Used memory:    2097152 KiB
Persistent:     yes
Autostart:      enable
"""

DUMPXML = """<domain type='kvm'>
  <name>web01</name>
  <memory unit='KiB'>2097152</memory>
  <vcpu placement='static'>2</vcpu>
  <os><type arch='x86_64' machine='pc-q35'>hvm</type><boot dev='hd'/></os>
  <devices>
    <disk type='file' device='disk'><target dev='vda' bus='virtio'/></disk>
  </devices>
</domain>
"""


FIXTURES = ROOT / "tests" / "fixtures"
DOMBLKLIST_DETAILS = (FIXTURES / "domblklist-details.txt").read_text()
DUMPXML_MIGRATABLE = (FIXTURES / "dumpxml-web01.xml").read_text()
QEMU_IMG_INFO = (FIXTURES / "qemu-img-info.json").read_text()

# Снимки web01: имя → (состояние, описание); список берётся из fixtures/snapshot-list.txt.
SNAPSHOTS = {"before-upd": ("shutoff", "Перед обновлением ядра"), "clean": ("running", "Чистая система после установки")}


def fake_result(stdout: str = "", ok: bool = True, stderr: str = "") -> dict:
    return {"ok": ok, "code": 0 if ok else 1, "stdout": stdout, "stderr": stderr, "cmd": ""}


def option(cmd, name):
    """Значение опции virsh вида --name VALUE (или '')."""
    return cmd[cmd.index(name) + 1] if name in cmd and cmd.index(name) + 1 < len(cmd) else ""


def fake_snapshot_xml(snap: str) -> str:
    state, description = SNAPSHOTS.get(snap, ("shutoff", ""))
    memory = "<memory snapshot='internal'/>" if state == "running" else "<memory snapshot='no'/>"
    return f"<domainsnapshot><name>{snap}</name><description>{description}</description><state>{state}</state>{memory}<creationTime>1758794400</creationTime></domainsnapshot>"


def fake_run_cmd(cmd, timeout=12, **_kwargs):
    joined = " ".join(cmd)
    if cmd[:2] == ["virsh", "snapshot-list"]:
        return fake_result((FIXTURES / "snapshot-list.txt").read_text())
    if cmd[:2] == ["virsh", "snapshot-current"]:
        return fake_result("clean")
    if cmd[:2] == ["virsh", "snapshot-dumpxml"]:
        return fake_result(fake_snapshot_xml(option(cmd, "--snapshotname")))
    if cmd[:2] in (["virsh", "snapshot-create-as"], ["virsh", "snapshot-revert"], ["virsh", "snapshot-delete"]):
        return fake_result("Domain snapshot %s created" % option(cmd, "--name"))
    if cmd[:2] == ["virsh", "list"]:
        if "--name" in cmd:
            return fake_result("web01\ndb01\n")
        return fake_result(" Id   Name    State\n-----------------------\n 1    web01   running\n -    db01    shut off")
    if cmd[:2] == ["virsh", "dominfo"]:
        return fake_result(DOMINFO) if cmd[-1] in ("web01", "db01") else fake_result(ok=False, stderr="failed to get domain")
    if cmd[:2] == ["virsh", "dumpxml"]:
        return fake_result(DUMPXML_MIGRATABLE if "--migratable" in cmd else DUMPXML)
    if cmd[:2] == ["virsh", "domblklist"]:
        return fake_result(DOMBLKLIST_DETAILS)
    if cmd[:2] == ["qemu-img", "info"]:
        return fake_result(QEMU_IMG_INFO)
    if cmd[:2] == ["virsh", "domstate"]:
        return fake_result("running")
    if cmd[:2] == ["virsh", "domifaddr"]:
        return fake_result(" Name  MAC address  Protocol  Address\n vnet0 52:54:00:aa:bb:cc ipv4 192.168.100.51/24")
    if cmd[:2] == ["virsh", "domiflist"]:
        return fake_result(" Interface  Type  Source  Model  MAC\n vnet0  network  virtuality-nat  virtio  52:54:00:aa:bb:cc")
    if cmd[:2] == ["virsh", "vncdisplay"]:
        return fake_result(":0")
    if cmd[:2] == ["virsh", "pool-list"]:
        return fake_result(" Name  State  Autostart\n---\n virtuality-images  active  yes")
    if cmd[:2] == ["virsh", "net-list"]:
        return fake_result(" Name  State  Autostart  Persistent\n---\n virtuality-nat  active  yes  yes")
    if cmd[:2] == ["systemctl", "is-active"]:
        return fake_result("active")
    if cmd[:1] == ["hostname"]:
        return fake_result("192.168.1.10" if "-I" in cmd else "virtuality-test")
    if cmd[:3] == ["ip", "route", "show"]:
        return fake_result("default via 192.168.1.1 dev eth0")
    if cmd[:1] in (["uptime"], ["uname"]):
        return fake_result("up 1 hour" if cmd[0] == "uptime" else "6.8.0")
    if "nft" in joined or "iptables" in joined or "ufw" in joined or "sysctl" in joined:
        return fake_result()
    return fake_result()


def feature_modules():
    """Загруженные модули web/features/* — им тоже подменяем run_cmd."""
    return [module for name, module in sys.modules.items() if name.startswith("features.") and hasattr(module, "run_cmd")]


@pytest.fixture()
def data_dirs(tmp_path, monkeypatch):
    import app
    import core
    import host_profile
    import network_core
    import update_core

    dirs = {
        "iso": tmp_path / "iso",
        "images": tmp_path / "images",
        "disk_images": tmp_path / "disk-images",
        "backups": tmp_path / "backups",
        "operations": tmp_path / "operations",
        "network": tmp_path / "network",
        "config": tmp_path / "config",
        "nft": tmp_path / "nft",
        "update": tmp_path / "update",
    }
    for path in dirs.values():
        path.mkdir()
    for module in (app, core):
        monkeypatch.setattr(module, "ISO_DIR", dirs["iso"])
        monkeypatch.setattr(module, "IMAGES_DIR", dirs["images"])
        monkeypatch.setattr(module, "DISK_IMAGES_DIR", dirs["disk_images"])
        monkeypatch.setattr(module, "OPERATIONS_DIR", dirs["operations"])
    monkeypatch.setattr(core, "BACKUPS_DIR", dirs["backups"])
    monkeypatch.setattr(core, "CONFIG_DIR", dirs["config"])
    monkeypatch.setattr(network_core, "CONFIG_DIR", dirs["config"])
    monkeypatch.setattr(network_core, "NETWORK_DIR", dirs["network"])
    monkeypatch.setattr(network_core, "NFT_DIR", dirs["nft"])
    monkeypatch.setattr(network_core, "PORT_FORWARDS_FILE", dirs["network"] / "port_forwards.json")
    monkeypatch.setattr(network_core, "UFW_STATE_FILE", dirs["network"] / "ufw_rules.json")
    monkeypatch.setattr(network_core, "NAT_XML_FILE", dirs["network"] / "virtuality-nat.xml")
    monkeypatch.setattr(network_core, "NFT_FILE", dirs["nft"] / "virtuality.nft")
    monkeypatch.setattr(network_core, "enable_ip_forward", lambda: None)
    monkeypatch.setattr(network_core, "disable_rp_filter", lambda: None)
    monkeypatch.setattr(host_profile, "PROFILE_FILE", dirs["config"] / "host_profile.json")
    monkeypatch.setattr(update_core, "STATE_DIR", dirs["update"])
    monkeypatch.setattr(update_core, "STATE_FILE", dirs["update"] / "state.json")
    monkeypatch.setattr(update_core, "LOG_FILE", dirs["update"] / "update.log")
    monkeypatch.setattr(update_core, "SOURCE_DIR", ROOT)
    for module in (app, core, network_core, host_profile, *feature_modules()):
        monkeypatch.setattr(module, "run_cmd", fake_run_cmd)
    return dirs


@pytest.fixture()
def client(data_dirs):
    from fastapi.testclient import TestClient

    import app

    app.login_throttle = app.auth.LoginThrottle()
    return TestClient(app.app, base_url="http://panel.test")


@pytest.fixture()
def logged_in(client, monkeypatch):
    import app

    monkeypatch.setattr(app.auth, "verify_password", lambda user, password: password == "correct")
    response = client.post("/login", data={"username": "tester", "password": "correct"}, follow_redirects=False)
    assert response.status_code == 303
    return client
