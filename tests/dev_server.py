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
import host_profile  # noqa: E402
import network_core  # noqa: E402
import update_core  # noqa: E402


def seed(root: Path) -> None:
    dirs = {name: root / name for name in ("iso", "images", "disk-images", "operations", "network", "config", "nft", "update")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    app.ISO_DIR, app.IMAGES_DIR, app.DISK_IMAGES_DIR, app.OPERATIONS_DIR = dirs["iso"], dirs["images"], dirs["disk-images"], dirs["operations"]
    network_core.CONFIG_DIR, network_core.NETWORK_DIR, network_core.NFT_DIR = dirs["config"], dirs["network"], dirs["nft"]
    network_core.PORT_FORWARDS_FILE = dirs["network"] / "port_forwards.json"
    network_core.NAT_XML_FILE = dirs["network"] / "virtuality-nat.xml"
    network_core.NFT_FILE = dirs["nft"] / "virtuality.nft"
    network_core.enable_ip_forward = lambda: None
    network_core.disable_rp_filter = lambda: None
    host_profile.PROFILE_FILE = dirs["config"] / "host_profile.json"
    update_core.STATE_DIR, update_core.STATE_FILE, update_core.LOG_FILE = dirs["update"], dirs["update"] / "state.json", dirs["update"] / "update.log"
    update_core.SOURCE_DIR = Path(__file__).resolve().parents[1]
    for module in (app, network_core, host_profile):
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


if __name__ == "__main__":
    import uvicorn

    seed(Path(tempfile.mkdtemp(prefix="virtuality-dev-")))
    uvicorn.run(app.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
