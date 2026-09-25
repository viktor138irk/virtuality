#!/usr/bin/env python3
"""Run the panel locally with fake virsh/systemctl for UI work.

    python3 tests/dev_server.py [port]      # login: tester / any password
"""
import json
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


def seed_backups(backups_dir: Path) -> None:
    """Несколько копий, как после пары недель работы: web01 — три, db01 — одна."""
    vm_xml = conftest.DUMPXML_MIGRATABLE
    demo = [
        ("web01", "20260924-0300", 2_684_354_560, "", [("vda", 6_443_499_520)]),
        ("web01", "20260917-0300", 2_598_000_000, "", [("vda", 6_300_000_000)]),
        ("web01", "20260912-1842", 2_412_000_000, "Перед обновлением сайта", [("vda", 6_100_000_000)]),
        ("db01", "20260921-2215", 9_126_805_504, "После переноса базы", [("vda", 12_884_901_888), ("vdb", 8_589_934_592)]),
    ]
    for vm, backup_id, size, note, disks in demo:
        path = backups_dir / vm / backup_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "vm.xml").write_text(vm_xml.replace("web01", vm))
        for target, _ in disks:
            with (path / f"{target}.qcow2").open("wb") as handle:
                handle.truncate(1024 * 1024)
        meta = {"vm": vm, "created_at": f"{backup_id[:4]}-{backup_id[4:6]}-{backup_id[6:8]} {backup_id[9:11]}:{backup_id[11:]}:00", "disks": [{"target": t, "source": f"/var/lib/virtuality/images/{vm}.qcow2", "size": s} for t, s in disks], "size_bytes": size, "version": "0.11.0", "note": note}
        (path / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))


def seed(root: Path) -> None:
    dirs = {name: root / name for name in ("iso", "images", "disk-images", "backups", "operations", "network", "config", "nft", "update")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    app.ISO_DIR, app.IMAGES_DIR, app.DISK_IMAGES_DIR, app.OPERATIONS_DIR = dirs["iso"], dirs["images"], dirs["disk-images"], dirs["operations"]
    core.ISO_DIR, core.IMAGES_DIR, core.DISK_IMAGES_DIR, core.OPERATIONS_DIR = dirs["iso"], dirs["images"], dirs["disk-images"], dirs["operations"]
    core.BACKUPS_DIR, core.CONFIG_DIR = dirs["backups"], dirs["config"]
    seed_backups(dirs["backups"])
    network_core.CONFIG_DIR, network_core.NETWORK_DIR, network_core.NFT_DIR = dirs["config"], dirs["network"], dirs["nft"]
    network_core.PORT_FORWARDS_FILE = dirs["network"] / "port_forwards.json"
    network_core.NAT_XML_FILE = dirs["network"] / "virtuality-nat.xml"
    network_core.NFT_FILE = dirs["nft"] / "virtuality.nft"
    network_core.enable_ip_forward = lambda: None
    network_core.disable_rp_filter = lambda: None
    host_profile.PROFILE_FILE = dirs["config"] / "host_profile.json"
    update_core.STATE_DIR, update_core.STATE_FILE, update_core.LOG_FILE = dirs["update"], dirs["update"] / "state.json", dirs["update"] / "update.log"
    update_core.SOURCE_DIR = Path(__file__).resolve().parents[1]
    for module in (app, core, network_core, host_profile):
        module.run_cmd = conftest.fake_run_cmd
    app.auth.verify_password = lambda user, password: True

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
    op_id = str(uuid.uuid4())
    app.write_operation({"id": op_id, "type": "backup", "title": "Резервная копия web01", "status": "success", "progress": 100, "message": "Резервная копия создана: 2.5 ГБ", "created_at": app.utc_now(), "updated_at": app.utc_now(), "created_by": "tester", "vm_name": "web01", "backup_id": "20260924-0300"})
    for line in ("Машина работает — отправляем команду выключения.", "Машина выключена.", "Настройки машины сохранены в vm.xml.", "Копируем диск vda (1 из 1): 100%", "Машина запущена снова."):
        app.append_operation_log(op_id, line)


if __name__ == "__main__":
    import uvicorn

    seed(Path(tempfile.mkdtemp(prefix="virtuality-dev-")))
    uvicorn.run(app.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
