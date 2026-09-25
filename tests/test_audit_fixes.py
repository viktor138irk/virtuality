"""Regressions for the defects found by the 0.11 audit."""
import json
from pathlib import Path
from urllib.parse import quote

import app
import conftest
import network_core

CDROM_XML = conftest.DUMPXML.replace(
    "<disk type='file' device='disk'><target dev='vda' bus='virtio'/></disk>",
    "<disk type='file' device='disk'><target dev='vda' bus='virtio'/></disk><disk type='file' device='cdrom'><source file='/var/lib/virtuality/iso/old.iso'/><target dev='sda' bus='sata'/></disk>",
)


def recording(monkeypatch, module, overrides=()):
    calls: list[list[str]] = []

    def run(cmd, timeout=12, **kwargs):
        calls.append(list(cmd))
        for prefix, result in overrides:
            if cmd[: len(prefix)] == list(prefix):
                return result
        return conftest.fake_run_cmd(cmd, timeout, **kwargs)

    monkeypatch.setattr(module, "run_cmd", run)
    return calls


def find(calls, *prefix):
    return [cmd for cmd in calls if cmd[: len(prefix)] == list(prefix)]


# ---------------------------------------------------------------- disk image format (app.py:620)
def test_disk_image_format_asks_qemu_img(data_dirs, monkeypatch):
    # Ubuntu cloud images are qcow2 files named *.img: the suffix must not decide the format.
    assert app.disk_image_format(Path("/tmp/noble-server-cloudimg-amd64.img")) == "qcow2"
    recording(monkeypatch, app, [(["qemu-img", "info"], conftest.fake_result(ok=False, stderr="no such file"))])
    assert app.disk_image_format(Path("/tmp/disk.img")) == "raw"
    assert app.disk_image_format(Path("/tmp/disk.qcow2")) == "qcow2"


# ---------------------------------------------------------------- VM actions (app.py:1977, 1981)
def test_vm_action_reports_virsh_error(logged_in, data_dirs, monkeypatch):
    recording(monkeypatch, app, [(["virsh", "start"], conftest.fake_result(ok=False, stderr="error: Domain is already active"))])
    response = logged_in.post("/vm/web01/start", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/vm/web01?error=" + quote("Машина уже запущена")
    assert "Машина уже запущена" in logged_in.get("/vm/web01?error=" + quote("Машина уже запущена")).text


def test_vm_action_success_redirects_without_error(logged_in, data_dirs, monkeypatch):
    recording(monkeypatch, app)
    assert logged_in.post("/vm/web01/shutdown", follow_redirects=False).headers["location"] == "/vm/web01"


def test_vm_delete_undefines_uefi_machines(logged_in, data_dirs, monkeypatch):
    calls = recording(monkeypatch, app)
    response = logged_in.post("/vm/web01/delete", follow_redirects=False)
    assert response.headers["location"].startswith("/?message=")
    assert find(calls, "virsh", "destroy", "web01")
    undefine = find(calls, "virsh", "undefine", "web01")[0]
    assert "--nvram" in undefine and "--remove-all-storage" in undefine and "--snapshots-metadata" in undefine


def test_vm_delete_failure_is_reported(logged_in, data_dirs, monkeypatch):
    recording(monkeypatch, app, [(["virsh", "undefine"], conftest.fake_result(ok=False, stderr="error: Requested operation is not valid: cannot undefine domain with nvram"))])
    response = logged_in.post("/vm/web01/delete", follow_redirects=False)
    assert response.headers["location"].startswith("/vm/web01?error=")


# ---------------------------------------------------------------- ISO in the CD-ROM drive (app.py:1150, 1158)
def test_iso_mount_uses_change_media_for_existing_drive(logged_in, data_dirs, monkeypatch):
    iso = data_dirs["iso"] / "debian.iso"
    iso.write_bytes(b"iso")
    calls = recording(monkeypatch, app, [(["virsh", "dumpxml"], conftest.fake_result(CDROM_XML))])
    ok, message = app.mount_vm_iso("web01", str(iso))
    assert ok, message
    assert find(calls, "virsh", "change-media", "web01", "sda", str(iso), "--update", "--config", "--live")
    assert not find(calls, "virsh", "detach-disk") and not find(calls, "virsh", "attach-disk")
    ok, message = app.detach_vm_iso("web01")
    assert ok and find(calls, "virsh", "change-media", "web01", "sda", "--eject", "--config", "--live")


def test_iso_unmount_never_guesses_targets(data_dirs, monkeypatch):
    # web01 has only a virtio data disk: nothing may be detached blindly.
    calls = recording(monkeypatch, app)
    ok, message = app.detach_vm_iso("web01")
    assert not ok and message == "Подключенный ISO не найден."
    assert not find(calls, "virsh", "detach-disk") and not find(calls, "virsh", "change-media")


def test_iso_mount_adds_drive_to_config_when_missing(data_dirs, monkeypatch):
    iso = data_dirs["iso"] / "debian.iso"
    iso.write_bytes(b"iso")
    calls = recording(monkeypatch, app)
    ok, message = app.mount_vm_iso("web01", str(iso))
    assert ok and "перезапуска" in message
    attach = find(calls, "virsh", "attach-disk", "web01")[0]
    assert "--config" in attach and "--live" not in attach


# ---------------------------------------------------------------- port forwards leave no stale firewall rules (network_core.py:459)
def test_iptables_fallback_removes_stale_rules(data_dirs, monkeypatch):
    listing = "\n".join([
        "-A FORWARD -i eth0 -o virbr1 -p tcp -d 192.168.100.51 -m tcp --dport 22 -m comment --comment virtuality-forward -j ACCEPT",
        "-A FORWARD -j SOMETHING_ELSE",
    ])
    calls = recording(monkeypatch, network_core, [(["iptables", "-t", "filter", "-S", "FORWARD"], conftest.fake_result(listing))])
    network_core.apply_iptables_fallback([])
    deleted = find(calls, "iptables", "-t", "filter", "-D", "FORWARD")
    assert deleted == [["iptables", "-t", "filter", "-D", "FORWARD", "-i", "eth0", "-o", "virbr1", "-p", "tcp", "-d", "192.168.100.51", "-m", "tcp", "--dport", "22", "-m", "comment", "--comment", "virtuality-forward", "-j", "ACCEPT"]]
    checked = find(calls, "iptables", "-t", "nat", "-C", "POSTROUTING")
    assert checked and "virtuality-forward" in checked[0]


def test_ufw_rules_follow_forwards(data_dirs, monkeypatch):
    old_rule = ["allow", "2222/tcp"]
    network_core.UFW_STATE_FILE.write_text(json.dumps([old_rule, ["route", "allow", "in", "on", "eth0", "out", "on", "virbr1", "to", "192.168.100.51", "port", "22", "proto", "tcp"]]))
    calls = recording(monkeypatch, network_core, [(["ufw", "status"], conftest.fake_result("Status: active"))])
    network_core.apply_ufw_route_rules([])
    assert find(calls, "ufw", "delete", "allow", "2222/tcp")
    assert find(calls, "ufw", "route", "delete", "allow", "in", "on", "eth0")
    assert json.loads(network_core.UFW_STATE_FILE.read_text()) == []


# ---------------------------------------------------------------- operations orphaned by a restart
def test_orphaned_operations_are_closed_on_startup(data_dirs):
    import core

    running = core.new_operation("snapshot_create", "Снимок web01", vm_name="web01")
    core.update_operation(running, status="running")
    done = core.new_operation("backup", "Копия web01", vm_name="web01")
    core.finish_operation(done, True, "Готово")
    assert core.active_operations_for("web01")
    assert core.interrupt_orphaned_operations() == 1
    assert core.read_operation(running["id"])["status"] == "error" and core.read_operation(running["id"])["interrupted"]
    assert core.read_operation(done["id"])["status"] == "success"
    assert core.active_operations_for("web01") == []


def test_snapshot_names_with_spaces_are_listed():
    from features import snapshots

    rows = snapshots.parse_snapshot_list(" Name              Creation Time               State\n------\n before update     2026-09-25 10:12:03 +0000   shutoff\n clean             2026-09-10 12:05:00 +0000   running\n")
    assert [row["name"] for row in rows] == ["before update", "clean"]
