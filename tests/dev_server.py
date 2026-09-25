#!/usr/bin/env python3
"""Run the panel locally with fake virsh/systemctl for UI work.

    python3 tests/dev_server.py [port]      # login: tester / any password
"""
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402  (sets sys.path and env)

import app  # noqa: E402
import core  # noqa: E402
import host_profile  # noqa: E402
import network_core  # noqa: E402
import update_core  # noqa: E402

# Машины демо-сервера: имя → состояние virsh (web01 работает, db01 выключена, win11 на паузе).
DEMO_VMS = {"web01": "running", "db01": "shut off", "win11": "paused"}
DEMO_NOTES = {"web01": ("Сайт компании", "Nginx + база. Бэкап по пятницам.\nЗа сервер отвечает Иван."), "win11": ("Бухгалтерия 1С", "")}
DEMO_DOMSTATS = """Domain: 'web01'
  cpu.time={cpu_web}
  balloon.current=2097152
  balloon.maximum=2097152
  balloon.rss=1534208
  vcpu.current=2
  vcpu.maximum=2

Domain: 'win11'
  cpu.time={cpu_win}
  balloon.current=4194304
  balloon.maximum=4194304
  balloon.rss=3102720
  vcpu.current=4
  vcpu.maximum=4

"""
_cpu_clock = {"web": 48_123_456_789, "win": 90_000_000_000, "at": time.monotonic()}


def demo_run_cmd(cmd, timeout=12, **kwargs):
    """Как conftest.fake_run_cmd, но с тремя машинами в разных состояниях и живой нагрузкой."""
    if cmd[:2] == ["virsh", "list"]:
        if "--title" in cmd:
            rows = "\n".join(f" {'1' if state == 'running' else '2' if state == 'paused' else '-':<4} {name:<7} {state:<10} {DEMO_NOTES.get(name, ('', ''))[0]}" for name, state in DEMO_VMS.items())
            return conftest.fake_result(" Id   Name    State      Title\n" + "-" * 44 + "\n" + rows + "\n")
        if "--name" in cmd:
            names = [name for name, state in DEMO_VMS.items() if "--all" in cmd or state != "shut off"]
            return conftest.fake_result("\n".join(names) + "\n")
        rows = "\n".join(f" {'1' if state == 'running' else '2' if state == 'paused' else '-':<4} {name:<7} {state}" for name, state in DEMO_VMS.items())
        return conftest.fake_result(" Id   Name    State\n" + "-" * 25 + "\n" + rows)
    if cmd[:2] in (["virsh", "dominfo"], ["virsh", "domstate"]):
        name = cmd[-1]
        if name not in DEMO_VMS:
            return conftest.fake_result(ok=False, stderr=f"error: failed to get domain '{name}'")
        if cmd[1] == "domstate":
            return conftest.fake_result(DEMO_VMS[name])
        info = conftest.DOMINFO.replace("web01", name).replace("State:          running", f"State:          {DEMO_VMS[name]}")
        if name == "db01":
            info += "Managed save:   yes\n"
        return conftest.fake_result(info)
    if cmd[:2] == ["virsh", "domstats"]:
        # Процессорное время растёт вместе с реальным: web01 нагружена на ~37 % (2 ядра), win11 на ~8 % (4 ядра).
        now = time.monotonic()
        elapsed_ns = (now - _cpu_clock["at"]) * 1e9
        _cpu_clock.update(web=_cpu_clock["web"] + int(elapsed_ns * 2 * 0.37), win=_cpu_clock["win"] + int(elapsed_ns * 4 * 0.08), at=now)
        return conftest.fake_result(DEMO_DOMSTATS.format(cpu_web=_cpu_clock["web"], cpu_win=_cpu_clock["win"]))
    if cmd[:2] == ["virsh", "domblklist"]:
        return conftest.fake_result((conftest.FIXTURES / "domblklist-details.txt").read_text().replace("/var/lib/virtuality/images/web01", str(core.IMAGES_DIR / cmd[2])))
    if cmd[:1] == ["virsh"] and "desc" in cmd[:3] and not any(arg.startswith("--new-desc") for arg in cmd):
        title, description = DEMO_NOTES.get(cmd[cmd.index("desc") + 1], ("", ""))
        return conftest.fake_result(title if "--title" in cmd else description)
    return conftest.fake_run_cmd(cmd, timeout, **kwargs)


def seed(root: Path) -> None:
    dirs = {name: root / name for name in ("iso", "images", "disk-images", "operations", "network", "config", "nft", "update")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    app.ISO_DIR, app.IMAGES_DIR, app.DISK_IMAGES_DIR, app.OPERATIONS_DIR = dirs["iso"], dirs["images"], dirs["disk-images"], dirs["operations"]
    core.ISO_DIR, core.IMAGES_DIR, core.DISK_IMAGES_DIR, core.OPERATIONS_DIR, core.STORAGE_DIR = dirs["iso"], dirs["images"], dirs["disk-images"], dirs["operations"], root
    for name in DEMO_VMS:  # диски машин лежат в хранилище, иначе форма увеличения не появится
        with (dirs["images"] / f"{name}.qcow2").open("wb") as handle:
            handle.truncate(4 * 1024 * 1024)
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
        module.run_cmd = demo_run_cmd
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
    # Завершённое клонирование web01 → web01-copy: видно на обзоре и на странице /operations/{id}.
    clone_op = core.new_operation("vm_clone", "Клонирование web01 → web01-copy", vm_name="web01-copy", source_vm="web01", targets=[str(dirs["images"] / "web01-copy.qcow2")], expected_bytes=6443499520)
    core.append_operation_log(clone_op["id"], "virt-clone --original web01 --name web01-copy --file /var/lib/virtuality/images/web01-copy.qcow2")
    core.append_operation_log(clone_op["id"], "Allocating 'web01-copy.qcow2' | 6.0 GB  00:01:12")
    core.update_operation(clone_op, cmd="virt-clone --original web01 --name web01-copy --file " + str(dirs["images"] / "web01-copy.qcow2"))
    core.finish_operation(clone_op, True, "Машина web01-copy создана — это копия web01. Запустите её и смените имя компьютера внутри системы.")


if __name__ == "__main__":
    import uvicorn

    seed(Path(tempfile.mkdtemp(prefix="virtuality-dev-")))
    uvicorn.run(app.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765, log_level="warning")
