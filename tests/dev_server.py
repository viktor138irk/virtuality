#!/usr/bin/env python3
"""Run the panel locally with fake virsh/systemctl for UI work.

    python3 tests/dev_server.py [port]      # login: tester / any password
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402  (sets sys.path and env)

import app  # noqa: E402
import core  # noqa: E402
import host_profile  # noqa: E402
import network_core  # noqa: E402
import update_core  # noqa: E402

# Снимки для демо: имя, дата, состояние, описание.
DEMO_SNAPSHOTS = [
    ("snap-20260925-1012", "2026-09-25 10:12:03 +0000", "shutoff", "Перед обновлением ядра"),
    ("snap-20260924-0900", "2026-09-24 09:00:41 +0000", "running", "Настроен nginx и сертификаты"),
    ("snap-20260918-1830", "2026-09-18 18:30:12 +0000", "running", "Чистая система после установки"),
    ("clean", "2026-09-10 12:05:00 +0000", "shutoff", ""),
]


def demo_run_cmd(cmd, timeout=12, **kwargs):
    """Как conftest.fake_run_cmd, но со списком снимков побогаче."""
    if cmd[:2] == ["virsh", "snapshot-list"]:
        rows = "\n".join(f" {name:<20} {when}   {state}" for name, when, state, _ in DEMO_SNAPSHOTS)
        return conftest.fake_result(" Name                 Creation Time               State\n" + "-" * 62 + "\n" + rows)
    if cmd[:2] == ["virsh", "snapshot-current"]:
        return conftest.fake_result("snap-20260924-0900")
    if cmd[:2] == ["virsh", "snapshot-create-as"] and "uefi" in conftest.option(cmd, "--description").lower():
        # Описание со словом «uefi» показывает, как выглядит ошибка libvirt.
        return conftest.fake_result(ok=False, stderr="error: unsupported configuration: internal snapshots of a VM with pflash based firmware are not supported")
    if cmd[:2] == ["virsh", "snapshot-dumpxml"]:
        snap = conftest.option(cmd, "--snapshotname")
        state, description = next(((s, d) for n, _, s, d in DEMO_SNAPSHOTS if n == snap), ("shutoff", ""))
        memory = "internal" if state == "running" else "no"
        return conftest.fake_result(f"<domainsnapshot><name>{snap}</name><description>{description}</description><state>{state}</state><memory snapshot='{memory}'/></domainsnapshot>")
    return conftest.fake_run_cmd(cmd, timeout, **kwargs)


def seed(root: Path) -> None:
    dirs = {name: root / name for name in ("iso", "images", "disk-images", "operations", "network", "config", "nft", "update")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    app.ISO_DIR, app.IMAGES_DIR, app.DISK_IMAGES_DIR, app.OPERATIONS_DIR = dirs["iso"], dirs["images"], dirs["disk-images"], dirs["operations"]
    core.OPERATIONS_DIR = dirs["operations"]
    network_core.CONFIG_DIR, network_core.NETWORK_DIR, network_core.NFT_DIR = dirs["config"], dirs["network"], dirs["nft"]
    network_core.PORT_FORWARDS_FILE = dirs["network"] / "port_forwards.json"
    network_core.NAT_XML_FILE = dirs["network"] / "virtuality-nat.xml"
    network_core.NFT_FILE = dirs["nft"] / "virtuality.nft"
    network_core.enable_ip_forward = lambda: None
    network_core.disable_rp_filter = lambda: None
    host_profile.PROFILE_FILE = dirs["config"] / "host_profile.json"
    update_core.STATE_DIR, update_core.STATE_FILE, update_core.LOG_FILE = dirs["update"], dirs["update"] / "state.json", dirs["update"] / "update.log"
    update_core.SOURCE_DIR = Path(__file__).resolve().parents[1]
    for module in (app, network_core, host_profile, core, *conftest.feature_modules()):
        module.run_cmd = demo_run_cmd
    app.auth.verify_password = lambda user, password: True
    import nodectl

    nodectl.CONFIG_DIR = dirs["config"]
    nodectl.SETUP_STATE = dirs["config"] / "setup.json"
    nodectl.SETUP_DONE = dirs["config"] / "setup_done"
    nodectl.WIZARD_FILE = dirs["config"] / "wizard.json"
    stage = os.environ.get("VIRTUALITY_DEV_SETUP", "")
    if stage:
        # VIRTUALITY_DEV_SETUP=installing|done shows the setup wizard in the demo; finished = wizard completed (Settings page).
        steps = [("panel", "Панель управления", "done"), ("network-wait", "Подключение к интернету", "done"), ("virtualization", "KVM, QEMU и libvirt", "running" if stage == "installing" else "done"), ("tools", "Инструменты диагностики", "pending" if stage == "installing" else "done"), ("finish", "Завершение", "pending" if stage == "installing" else "done")]
        nodectl.SETUP_STATE.write_text(json.dumps({"stage": "installing" if stage == "installing" else "done", "message": "Устанавливаем qemu-system-x86 (14 из 61)…", "steps": [{"id": i, "title": t, "status": s} for i, t, s in steps]}))
        nodectl.FIRSTBOOT_LOG = dirs["config"] / "firstboot.log"
        nodectl.FIRSTBOOT_LOG.write_text("[2026-09-25 14:02:11] [virtuality-firstboot] step 1/4: web panel\n[2026-09-25 14:02:40] [virtuality-firstboot] step 2/4: virtualization node (KVM, libvirt)\nGet:14 http://archive.ubuntu.com/ubuntu resolute/main amd64 qemu-system-x86 amd64 1:10.2.1+ds-1ubuntu3 [9 812 kB]\n")
        if not nodectl.ctl_available():
            nodectl.spare_disks = lambda: [{"path": "/dev/sdb", "name": "sdb", "size": 2 * 1024 ** 4, "model": "WD Red 2TB", "tran": "sata"}]
            nodectl.network_facts = lambda: {"interface": "enp3s0", "gateway": "192.168.1.1", "address": "192.168.1.10", "prefix": 24, "mac": "aa:bb:cc:dd:ee:ff", "wireless": False, "private": True, "is_vps": False, "virt": "none", "bridge_present": False, "on_bridge": False, "recommended": "bridge", "revert_armed": False, "bridge_possible": True}
            nodectl.hardware_summary = lambda: {"hostname": "home-server", "cpu_model": "Intel Core i5-12400", "cpu_count": 6, "mem_total": 32 * 1024 ** 3, "disk_total": 480 * 1024 ** 3, "disk_free": 401 * 1024 ** 3, "kvm": True}
            nodectl.set_timezone = lambda tz: (True, tz)
            nodectl.timezones = lambda: {"current": "Europe/Moscow", "popular": nodectl.POPULAR_TIMEZONES, "all": nodectl.POPULAR_TIMEZONES}
        if stage == "finished":
            nodectl.mark_wizard_done()
            nodectl.NODE_ENV = dirs["config"] / "web.env"
            nodectl.NODE_ENV.write_text("VIRTUALITY_WEB_PORT=8088\nVIRTUALITY_TLS=1\nVIRTUALITY_AUTO_UPDATE=1\nVIRTUALITY_UPDATE_CHANNEL=stable\n")

    for name, size in (("ubuntu-26.04-live-server-amd64.iso", 3), ("debian-13.7.0-amd64-netinst.iso", 1)):
        with (dirs["iso"] / name).open("wb") as handle:
            handle.truncate(size * 1024 * 1024)
    with (dirs["disk-images"] / "debian-13-genericcloud-amd64.qcow2").open("wb") as handle:
        handle.truncate(2 * 1024 * 1024)
    network_core.PORT_FORWARDS_FILE.write_text(json.dumps([
        {"id": "f1", "vm_name": "web01", "guest_ip": "192.168.100.51", "external_port_start": 2222, "external_port_end": 2222, "guest_port_start": 22, "guest_port_end": 22, "protocol": "tcp", "note": "SSH"},
        {"id": "f2", "vm_name": "web01", "guest_ip": "192.168.100.51", "external_port_start": 443, "external_port_end": 443, "guest_port_start": 443, "guest_port_end": 443, "protocol": "tcp", "note": "HTTPS"},
    ]))
    for status, title, progress in (("success", "Создание VM web01", 100), ("running", "Конвертация debian-13.img", 64), ("error", "Создание VM test", 100)):
        op_id = str(uuid.uuid4())
        app.write_operation({"id": op_id, "type": "vm_create", "title": title, "status": status, "progress": progress, "message": "Готово" if status == "success" else "qemu-img: 64%" if status == "running" else "virt-install завершился с ошибкой: 1", "created_at": app.utc_now(), "updated_at": app.utc_now(), "created_by": "tester"})
        app.append_operation_log(op_id, "virt-install --name web01 --memory 2048 ...")
    # Идущий снимок машины db01 — видно на /vm/db01/snapshots.
    snap_op = core.new_operation("snapshot_create", "Снимок машины db01", vm_name="db01", snapshot="snap-20260925-1500", vm_running=True)
    core.update_operation(snap_op, status="running", progress=15, message="Сохраняем состояние машины и памяти — она на несколько секунд замрёт…")


if __name__ == "__main__":
    import uvicorn

    seed(Path(tempfile.mkdtemp(prefix="virtuality-dev-")))
    uvicorn.run(app.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
