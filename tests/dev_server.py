#!/usr/bin/env python3
"""Run the panel locally with fake virsh/systemctl for UI work.

    python3 tests/dev_server.py [port]      # login: tester / any password
"""
import json
import os
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


def demo_vm_run_cmd(cmd, timeout=12, **kwargs):
    """Три машины в разных состояниях и живая нагрузка; остальное — conftest.fake_run_cmd."""
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
    return demo_vm_run_cmd(cmd, timeout, **kwargs)


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
    core.ISO_DIR, core.IMAGES_DIR, core.DISK_IMAGES_DIR, core.OPERATIONS_DIR, core.STORAGE_DIR = dirs["iso"], dirs["images"], dirs["disk-images"], dirs["operations"], root
    core.BACKUPS_DIR, core.CONFIG_DIR = dirs["backups"], dirs["config"]
    seed_backups(dirs["backups"])
    for name in DEMO_VMS:  # диски машин лежат в хранилище, иначе форма увеличения не появится
        with (dirs["images"] / f"{name}.qcow2").open("wb") as handle:
            handle.truncate(4 * 1024 * 1024)
    network_core.CONFIG_DIR, network_core.NETWORK_DIR, network_core.NFT_DIR = dirs["config"], dirs["network"], dirs["nft"]
    network_core.PORT_FORWARDS_FILE = dirs["network"] / "port_forwards.json"
    network_core.UFW_STATE_FILE = dirs["network"] / "ufw_rules.json"
    network_core.NAT_XML_FILE = dirs["network"] / "virtuality-nat.xml"
    network_core.NFT_FILE = dirs["nft"] / "virtuality.nft"
    network_core.enable_ip_forward = lambda: None
    network_core.disable_rp_filter = lambda: None
    host_profile.PROFILE_FILE = dirs["config"] / "host_profile.json"
    update_core.STATE_DIR, update_core.STATE_FILE, update_core.LOG_FILE = dirs["update"], dirs["update"] / "state.json", dirs["update"] / "update.log"
    update_core.SOURCE_DIR = Path(__file__).resolve().parents[1]
    for module in (app, core, network_core, host_profile, *conftest.feature_modules()):
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
    op_id = str(uuid.uuid4())
    app.write_operation({"id": op_id, "type": "backup", "title": "Резервная копия web01", "status": "success", "progress": 100, "message": "Резервная копия создана: 2.5 ГБ", "created_at": app.utc_now(), "updated_at": app.utc_now(), "created_by": "tester", "vm_name": "web01", "backup_id": "20260924-0300"})
    for line in ("Машина работает — отправляем команду выключения.", "Машина выключена.", "Настройки машины сохранены в vm.xml.", "Копируем диск vda (1 из 1): 100%", "Машина запущена снова."):
        app.append_operation_log(op_id, line)

    # Идущие скачивания — видны на /iso и /disk-images (в каталоге Ubuntu 26.04 помечена «Скачивается»).
    for kind, url, catalog_id, title, progress, message, total, done in (
        ("iso", "https://software-download.microsoft.com/download/pr/windows-server-2025-eval-x64.iso", None, "Скачивание windows-server-2025-eval-x64.iso", 37, "1,9 ГБ из 5,2 ГБ · 24 МБ/с · осталось 2 мин", 5_583_457_280, 2_065_879_193),
        ("disk", "https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img", "ubuntu-26.04", "Скачивание Ubuntu Server 26.04 LTS", 62, "412 МБ из 663 МБ · 31 МБ/с · осталось 8 с", 695_205_888, 431_027_650),
    ):
        filename = url.rsplit("/", 1)[-1]
        target = (dirs["iso"] if kind == "iso" else dirs["disk-images"]) / filename
        op = core.new_operation("download", title, download_kind=kind, url=url, filename=filename, target_path=str(target), page="/iso" if kind == "iso" else "/disk-images", catalog_id=catalog_id, total_bytes=total, downloaded_bytes=done, cancel_requested=False)
        core.update_operation(op, status="running", progress=progress, message=message, started_at=core.utc_now())
        core.append_operation_log(op["id"], f"Ссылка: {url}")
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
