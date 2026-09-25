import json
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import pytest

import core
import features.backups as backups
from conftest import DOMBLKLIST_DETAILS, DUMPXML_MIGRATABLE, QEMU_IMG_INFO, fake_result, fake_run_cmd

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_backup(dirs, vm="web01", backup_id="20260924-0300", size=2_684_354_560, note="", disks=("vda",), complete=True) -> Path:
    path = dirs["backups"] / vm / backup_id
    path.mkdir(parents=True)
    (path / "vm.xml").write_text(DUMPXML_MIGRATABLE)
    for target in disks:
        if complete:
            (path / f"{target}.qcow2").write_bytes(b"QFI\xfb" + b"\0" * 60)
    meta = {"vm": vm, "created_at": "2026-09-24 03:00:00", "disks": [{"target": t, "source": f"/var/lib/virtuality/images/{vm}.qcow2", "size": 6_443_499_520} for t in disks], "size_bytes": size, "version": "0.11.0", "note": note}
    (path / "meta.json").write_text(json.dumps(meta))
    return path


class FakeVirsh:
    """Имитация virsh с состоянием: машина выключается после `virsh shutdown`."""

    def __init__(self, running=True, shuts_down=True, exists=("web01", "db01"), paused=False, rejects_shutdown=False):
        self.running = running
        self.paused = paused
        self.shuts_down = shuts_down
        self.rejects_shutdown = rejects_shutdown
        self.exists = set(exists)
        self.calls: list[list[str]] = []
        self.defined_xml = ""

    def __call__(self, cmd, timeout=12, **_kwargs):
        self.calls.append(list(cmd))
        if cmd[:2] == ["virsh", "domstate"]:
            return fake_result("paused" if self.running and self.paused else "running" if self.running else "shut off")
        if cmd[:2] == ["virsh", "shutdown"]:
            if self.rejects_shutdown:
                return fake_result(ok=False, stderr="error: Requested operation is not valid: domain is not running")
            if self.shuts_down:
                self.running = False
            return fake_result()
        if cmd[:2] == ["virsh", "managedsave"]:
            self.running = False
            return fake_result(f"Domain '{cmd[2]}' state saved by libvirt")
        if cmd[:2] == ["virsh", "destroy"]:
            self.running = False
            return fake_result()
        if cmd[:2] == ["virsh", "start"]:
            self.running = True
            return fake_result()
        if cmd[:2] == ["virsh", "dominfo"]:
            return fake_result(f"Name: {cmd[-1]}\nState: running\n") if cmd[-1] in self.exists else fake_result(ok=False, stderr="error: failed to get domain")
        if cmd[:2] == ["virsh", "define"]:
            self.defined_xml = Path(cmd[2]).read_text()
            return fake_result(f"Domain defined from {cmd[2]}")
        if cmd[:2] == ["virsh", "undefine"]:
            self.exists.discard(cmd[2])
            return fake_result()
        return fake_run_cmd(cmd, timeout)


def fake_stream(progress_lines=("(0.00/100%)", "(48.50/100%)", "(100.00/100%)"), code=0, calls=None):
    """Имитация qemu-img convert. calls — общий список с FakeVirsh, если важен порядок команд."""
    calls = [] if calls is None else calls

    def stream(cmd, on_line, timeout=None):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"QFI\xfb" + b"\0" * 1000)
        for line in progress_lines:
            on_line(line)
        return code

    stream.calls = calls
    return stream


class FixedDatetime(datetime):
    """datetime.now() всегда 24 сентября 2026, 03:00 — чтобы id копий были предсказуемы."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 24, 3, 0, 0)


@pytest.fixture()
def fast_worker(monkeypatch, data_dirs):
    """Операции и фоновые потоки выполняются синхронно, ожидание выключения — мгновенно."""
    monkeypatch.setattr(core, "run_operation", lambda operation, worker: worker(operation))
    monkeypatch.setattr(backups, "start_thread", lambda target, *args: target(*args))
    monkeypatch.setattr(backups, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(backups, "SHUTDOWN_TIMEOUT", 0.05)
    monkeypatch.setattr(backups, "LATE_SHUTDOWN_WATCH", 0.05)
    monkeypatch.setattr(backups, "free_bytes", lambda path: 500 * 1024 ** 3)
    return data_dirs


# ---------------------------------------------------------------- парсеры
def test_parse_progress():
    assert backups.parse_progress("    (12.34/100%)") == 12
    assert backups.parse_progress("(100.00/100%)") == 100
    assert backups.parse_progress("qemu-img: Could not open") is None


def test_parse_qemu_img_info_fixture():
    info = backups.parse_qemu_img_info(QEMU_IMG_INFO)
    assert info == {"virtual_size": 42949672960, "actual_size": 6443499520, "format": "qcow2"}
    assert backups.parse_qemu_img_info("not json")["actual_size"] == 0
    assert backups.parse_qemu_img_info("[1, 2]")["format"] == ""


def test_vm_disks_skips_cdrom(data_dirs):
    disks = backups.vm_disks("web01")
    assert [(d["target"], d["source"]) for d in disks] == [("vda", str(core.IMAGES_DIR / "web01.qcow2"))]
    assert DOMBLKLIST_DETAILS.count("cdrom") == 1


def test_stream_cmd_splits_carriage_returns():
    lines: list[str] = []
    code = backups.stream_cmd(["python3", "-c", "import sys; sys.stdout.write('a\\r    (10.00/100%)\\r(55.50/100%)\\nend')"], lines.append)
    assert code == 0
    assert lines == ["a", "(10.00/100%)", "(55.50/100%)", "end"]


def test_stream_cmd_stops_hung_command():
    lines: list[str] = []
    code = backups.stream_cmd(["python3", "-c", "import time; time.sleep(30)"], lines.append, timeout=0.2)
    assert code != 0
    assert any("остановлена" in line for line in lines)


def test_disk_info_uses_shared_lock_and_raises_on_failure(monkeypatch):
    calls: list[list[str]] = []

    def info_ok(cmd, timeout=12, **_kw):
        calls.append(list(cmd))
        return fake_result(QEMU_IMG_INFO)

    monkeypatch.setattr(core, "run_cmd", info_ok)
    assert backups.disk_info("/x/web01.qcow2")["actual_size"] == 6443499520
    assert calls == [["qemu-img", "info", "-U", "--output=json", "/x/web01.qcow2"]]  # -U: диск занят работающей машиной
    monkeypatch.setattr(core, "run_cmd", lambda cmd, timeout=12, **_kw: fake_result(ok=False, stderr='qemu-img: Could not open: Failed to get shared "write" lock'))
    with pytest.raises(backups.BackupError) as exc:
        backups.disk_info("/x/web01.qcow2")
    assert "размер диска" in str(exc.value) and "write" in str(exc.value)


def test_rewrite_xml_renames_and_detaches_identity():
    xml = backups.rewrite_xml((FIXTURES / "dumpxml-web01.xml").read_text(), "web02", {"vda": "/var/lib/virtuality/images/web02.qcow2"})
    assert "<name>web02</name>" in xml
    assert "<uuid>" not in xml and "id=" not in xml.split(">", 1)[0]
    assert "<mac" not in xml
    assert "file=\"/var/lib/virtuality/images/web02.qcow2\"" in xml
    assert "Сайт компании" in xml  # заголовок и описание сохраняются
    assert "<readonly />" in xml or "<readonly/>" in xml  # привод CD остаётся как был


def test_rewrite_xml_keeps_identity_on_replace_and_drops_stale_chain(tmp_path):
    present = tmp_path / "debian.iso"
    present.write_bytes(b"iso")
    source = f"""<domain type='kvm' id='7'><name>web01</name><uuid>6c1b3c1e-0000-0000-0000-000000000001</uuid>
    <devices>
      <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/var/lib/virtuality/images/web01.snap.qcow2'/>
        <backingStore type='file'><format type='qcow2'/><source file='/var/lib/virtuality/images/web01.qcow2'/></backingStore>
        <target dev='vda' bus='virtio'/></disk>
      <disk type='file' device='cdrom'><driver name='qemu' type='raw'/><source file='/var/lib/virtuality/iso/gone.iso'/><target dev='sda' bus='sata'/><readonly/></disk>
      <disk type='file' device='cdrom'><driver name='qemu' type='raw'/><source file='{present}'/><target dev='sdb' bus='sata'/><readonly/></disk>
      <interface type='network'><mac address='52:54:00:aa:bb:cc'/><source network='virtuality-nat'/></interface>
    </devices></domain>"""
    replaced = backups.rewrite_xml(source, "web01", {"vda": "/var/lib/virtuality/images/web01.qcow2"}, keep_identity=True)
    assert "<uuid>6c1b3c1e-0000-0000-0000-000000000001</uuid>" in replaced and 'mac address="52:54:00:aa:bb:cc"' in replaced
    assert "backingStore" not in replaced and "web01.snap.qcow2" not in replaced
    assert "gone.iso" not in replaced and str(present) in replaced  # пропавший образ вынут, существующий остаётся
    assert 'id="7"' not in replaced
    fresh = backups.rewrite_xml(source, "web02", {"vda": "/var/lib/virtuality/images/web02.qcow2"})
    assert "<uuid>" not in fresh and "<mac" not in fresh and "backingStore" not in fresh


def test_rewrite_xml_converts_block_disk_and_drops_nvram():
    source = """<domain type='kvm'><name>old</name><os><type>hvm</type><loader type='pflash'>/usr/share/OVMF/OVMF_CODE.fd</loader><nvram>/var/lib/libvirt/qemu/nvram/old_VARS.fd</nvram></os>
    <devices><disk type='block' device='disk'><driver name='qemu' type='raw'/><source dev='/dev/vg/old'/><target dev='vda' bus='virtio'/></disk></devices></domain>"""
    xml = backups.rewrite_xml(source, "new", {"vda": "/var/lib/virtuality/images/new.qcow2"})
    assert "<nvram>" not in xml
    assert 'type="file"' in xml and 'file="/var/lib/virtuality/images/new.qcow2"' in xml
    assert 'type="qcow2"' in xml and "source dev=" not in xml and 'type="raw"' not in xml


def test_format_backup_date():
    assert backups.format_backup_date("20260924-0300") == "24 сентября 2026, 03:00"
    assert backups.format_backup_date("20260924-0300-2") == "24 сентября 2026, 03:00"  # вторая копия в ту же минуту
    assert backups.format_backup_date("garbage") == "garbage"


def test_clean_note_limits_length_and_control_chars():
    assert backups.clean_note("  до\x00обновления\n ") == "до обновления"
    assert len(backups.clean_note("x" * 500)) == backups.NOTE_MAX


# ---------------------------------------------------------------- хранилище
def test_list_and_group_backups(data_dirs):
    make_backup(data_dirs, "web01", "20260917-0300", size=100)
    make_backup(data_dirs, "web01", "20260924-0300", size=200, note="перед обновлением")
    make_backup(data_dirs, "old-vm", "20260901-1200", size=50)
    (data_dirs["backups"] / "web01" / "junk").mkdir()
    (data_dirs["backups"] / "..hidden").mkdir()
    items = backups.list_backups()
    assert [(i["vm"], i["id"]) for i in items] == [("web01", "20260924-0300"), ("web01", "20260917-0300"), ("old-vm", "20260901-1200")]
    assert items[0]["note"] == "перед обновлением" and items[0]["complete"] is True
    groups = backups.group_by_vm(items, {"web01"})
    assert [(g["vm"], g["count"], g["size_bytes"], g["exists"]) for g in groups] == [("old-vm", 1, 50, False), ("web01", 2, 300, True)]
    assert backups.list_backups("web01")[0]["id"] == "20260924-0300"
    assert backups.storage_summary(items)["used_bytes"] == 350


def test_backup_path_rejects_traversal(data_dirs):
    assert backups.backup_path("../etc", "20260924-0300") is None
    assert backups.backup_path("web01", "../../x") is None
    assert backups.backup_path("web01", "20260924-0300") == data_dirs["backups"] / "web01" / "20260924-0300"


def test_incomplete_backup_is_flagged(data_dirs):
    make_backup(data_dirs, complete=False)
    assert backups.read_backup("web01", "20260924-0300")["complete"] is False


def test_backup_with_unsafe_disk_target_is_incomplete(data_dirs):
    path = make_backup(data_dirs)
    meta = json.loads((path / "meta.json").read_text())
    meta["disks"].append({"target": "../../etc/shadow", "source": "/x", "size": 1})
    (path / "meta.json").write_text(json.dumps(meta))
    backup = backups.read_backup("web01", "20260924-0300")
    assert [disk["target"] for disk in backup["disks"]] == ["vda"] and backup["complete"] is False


def test_storage_summary_percent_is_share_of_whole_disk(data_dirs, monkeypatch):
    monkeypatch.setattr(backups, "total_bytes", lambda path: 1000)
    monkeypatch.setattr(backups, "free_bytes", lambda path: 400)
    summary = backups.storage_summary([{"size_bytes": 350}])
    assert summary["used_pct"] == 35 and summary["free_bytes"] == 400


# ---------------------------------------------------------------- создание копии
def test_create_backup_refuses_without_space(data_dirs, monkeypatch):
    monkeypatch.setattr(backups, "free_bytes", lambda path: 1024)
    with pytest.raises(backups.BackupError) as exc:
        backups.create_backup("web01")
    assert "Недостаточно места" in str(exc.value) and "6.0 ГБ" in str(exc.value)


def test_create_backup_rejects_unknown_vm(fast_worker):
    with pytest.raises(backups.BackupError):
        backups.create_backup("nope")
    with pytest.raises(backups.BackupError):
        backups.create_backup("bad name")


def test_create_backup_shuts_down_copies_and_restarts(fast_worker, monkeypatch):
    virsh = FakeVirsh(running=True)
    monkeypatch.setattr(core, "run_cmd", virsh)
    stream = fake_stream()
    monkeypatch.setattr(backups, "stream_cmd", stream)
    queued = backups.create_backup("web01", "  ночная\tкопия ")
    operation = core.read_operation(queued["id"])
    assert operation["status"] == "success", operation["message"]
    assert operation["progress"] == 100 and "Резервная копия создана" in operation["message"]
    assert ["virsh", "shutdown", "web01"] in virsh.calls
    assert ["virsh", "dumpxml", "web01", "--migratable"] in virsh.calls
    assert ["qemu-img", "info", "-U", "--output=json", str(core.IMAGES_DIR / "web01.qcow2")] in virsh.calls
    assert virsh.calls[-1] == ["virsh", "start", "web01"]
    assert ["virsh", "destroy", "web01"] not in virsh.calls
    assert stream.calls == [["qemu-img", "convert", "-p", "-O", "qcow2", "-c", str(core.IMAGES_DIR / "web01.qcow2"), str(fast_worker["backups"] / "web01" / operation["backup_id"] / "vda.qcow2")]]
    path = fast_worker["backups"] / "web01" / operation["backup_id"]
    meta = json.loads((path / "meta.json").read_text())
    assert meta["vm"] == "web01" and meta["note"] == "ночная копия" and meta["disks"][0]["target"] == "vda"
    assert meta["size_bytes"] == sum(f.stat().st_size for f in path.iterdir() if f.name != "meta.json")
    assert "Сайт компании" in (path / "vm.xml").read_text()
    log = core.read_operation(operation["id"])["log_tail"]
    assert "48%" in log and "Машина запущена снова" in log


def test_create_backup_wait_returns_finished_operation(fast_worker, monkeypatch):
    monkeypatch.setattr(core, "run_cmd", FakeVirsh(running=False))
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    operation = backups.create_backup("db01", wait=True)
    assert operation["status"] == "success" and operation["type"] == "backup"


def test_backup_fails_when_vm_does_not_shut_down(fast_worker, monkeypatch):
    virsh = FakeVirsh(running=True, shuts_down=False)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    operation = backups.create_backup("web01")
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "error" and "не выключилась" in fresh["message"] and "запустит её снова" in fresh["message"]
    assert ["virsh", "destroy", "web01"] not in virsh.calls
    assert ["virsh", "start", "web01"] not in virsh.calls  # так и не выключилась — запускать нечего
    assert "так и не выключилась" in fresh["log_tail"]
    assert not (fast_worker["backups"] / "web01").exists()


def test_backup_restarts_vm_that_shuts_down_late(fast_worker, monkeypatch):
    virsh = FakeVirsh(running=True, shuts_down=False)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())

    def shut_down_after_giving_up(target, *args):
        virsh.running = False  # ACPI-выключение дошло уже после того, как задача сдалась
        target(*args)

    monkeypatch.setattr(backups, "start_thread", shut_down_after_giving_up)
    operation = backups.create_backup("web01")
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "error" and "не выключилась" in fresh["message"]
    assert virsh.calls[-1] == ["virsh", "start", "web01"] and virsh.running
    assert "запускаем её снова" in fresh["log_tail"]


def test_backup_reports_rejected_shutdown_command(fast_worker, monkeypatch):
    virsh = FakeVirsh(running=True, rejects_shutdown=True)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    monkeypatch.setattr(backups, "start_thread", lambda target, *args: pytest.fail("нечего ждать — команда не принята"))
    operation = backups.create_backup("web01")
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "error"
    assert "не приняла команду выключения" in fresh["message"] and "domain is not running" in fresh["message"]
    assert "не выключилась" not in fresh["message"]
    assert ["virsh", "start", "web01"] not in virsh.calls
    assert not (fast_worker["backups"] / "web01").exists()


def test_backup_of_paused_vm_saves_state_instead_of_shutdown(fast_worker, monkeypatch):
    virsh = FakeVirsh(running=True, paused=True)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    operation = backups.create_backup("web01")
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "success", fresh["message"]
    assert ["virsh", "managedsave", "web01"] in virsh.calls and ["virsh", "shutdown", "web01"] not in virsh.calls
    assert virsh.calls.index(["virsh", "managedsave", "web01"]) < virsh.calls.index(["virsh", "dumpxml", "web01", "--migratable"])
    assert virsh.calls[-1] == ["virsh", "start", "web01"]
    assert "на паузе" in fresh["log_tail"]


def test_backup_cleans_up_and_restarts_after_copy_error(fast_worker, monkeypatch):
    virsh = FakeVirsh(running=True)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream(code=1))
    operation = backups.create_backup("web01")
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "error" and "vda" in fresh["message"]
    assert not (fast_worker["backups"] / "web01").exists()
    assert virsh.calls[-1] == ["virsh", "start", "web01"]


def test_create_backup_refuses_while_another_runs(fast_worker, monkeypatch):
    monkeypatch.setattr(core, "run_operation", lambda operation, worker: None)  # остаётся в очереди
    backups.create_backup("web01")
    with pytest.raises(backups.BackupError) as exc:
        backups.create_backup("web01")
    assert "уже идёт" in str(exc.value)


def test_second_backup_in_same_minute_gets_own_folder_and_keeps_first(fast_worker, monkeypatch):
    monkeypatch.setattr(backups, "datetime", FixedDatetime)
    monkeypatch.setattr(core, "run_cmd", FakeVirsh(running=False))
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    first = core.read_operation(backups.create_backup("web01", "первая")["id"])
    assert first["status"] == "success" and first["backup_id"] == "20260924-0300"
    monkeypatch.setattr(backups, "stream_cmd", fake_stream(code=1))
    second = core.read_operation(backups.create_backup("web01", "вторая")["id"])
    assert second["status"] == "error" and second["backup_id"] == "20260924-0300-2"
    first_dir = fast_worker["backups"] / "web01" / "20260924-0300"
    assert (first_dir / "meta.json").exists() and (first_dir / "vda.qcow2").exists()  # чужую папку неудачная задача не трогает
    assert not (fast_worker["backups"] / "web01" / "20260924-0300-2").exists()
    assert [item["id"] for item in backups.list_backups("web01")] == ["20260924-0300"]
    assert backups.backup_path("web01", "20260924-0300-2") == fast_worker["backups"] / "web01" / "20260924-0300-2"


# ---------------------------------------------------------------- восстановление
def test_restore_under_new_name(fast_worker, monkeypatch):
    make_backup(fast_worker)
    virsh = FakeVirsh(running=False)
    monkeypatch.setattr(core, "run_cmd", virsh)
    stream = fake_stream()
    monkeypatch.setattr(backups, "stream_cmd", stream)
    operation = backups.start_restore("web01", "20260924-0300", "web02", False)
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "success", fresh["message"]
    assert fresh["vm_name"] == "web02"
    target = fast_worker["images"] / "web02.qcow2"
    assert stream.calls[0][:6] == ["qemu-img", "convert", "-p", "-O", "qcow2", str(fast_worker["backups"] / "web01" / "20260924-0300" / "vda.qcow2")]
    assert stream.calls[0][6].startswith(str(fast_worker["images"] / "web02.restore-"))  # сначала во временный файл
    assert target.exists() and not list(fast_worker["images"].glob("*.restore-*"))
    assert "<name>web02</name>" in virsh.defined_xml and str(target) in virsh.defined_xml and "<mac" not in virsh.defined_xml
    assert not any(cmd[:2] == ["virsh", "undefine"] for cmd in virsh.calls)
    assert not list((fast_worker["backups"] / "web01" / "20260924-0300").glob("restore-*.xml"))


def test_restore_requires_replace_confirmation(fast_worker):
    make_backup(fast_worker)
    with pytest.raises(backups.BackupError) as exc:
        backups.start_restore("web01", "20260924-0300", "web01", False)
    assert exc.value.code == "exists"


UNDEFINE = ["virsh", "undefine", "web01", "--remove-all-storage", "--nvram", "--managed-save", "--snapshots-metadata"]


def test_restore_replaces_existing_vm_only_after_disks_are_ready(fast_worker, monkeypatch):
    make_backup(fast_worker)
    virsh = FakeVirsh(running=True)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream(calls=virsh.calls))
    operation = backups.start_restore("web01", "20260924-0300", "", True)
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "success", fresh["message"]
    convert = next(index for index, cmd in enumerate(virsh.calls) if cmd[:2] == ["qemu-img", "convert"])
    assert convert < virsh.calls.index(["virsh", "destroy", "web01"]) < virsh.calls.index(UNDEFINE)
    assert virsh.calls.index(UNDEFINE) < next(index for index, cmd in enumerate(virsh.calls) if cmd[:2] == ["virsh", "define"])
    assert (fast_worker["images"] / "web01.qcow2").exists() and not list(fast_worker["images"].glob("*.restore-*"))
    assert "<name>web01</name>" in virsh.defined_xml
    # при замене машина остаётся «той же»: UUID и MAC из копии сохраняются
    assert "<uuid>6c1b3c1e-0000-0000-0000-000000000001</uuid>" in virsh.defined_xml and "52:54:00:aa:bb:cc" in virsh.defined_xml


def test_replace_restore_keeps_old_vm_when_copy_fails(fast_worker, monkeypatch):
    make_backup(fast_worker)
    virsh = FakeVirsh(running=True)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream(code=1))
    operation = backups.start_restore("web01", "20260924-0300", "web01", True)
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "error" and "Не удалось восстановить диск vda" in fresh["message"] and "qemu-img" not in fresh["message"]
    assert "qemu-img завершился с кодом 1" in fresh["log_tail"]
    assert ["virsh", "destroy", "web01"] not in virsh.calls and UNDEFINE not in virsh.calls and virsh.running
    assert not list(fast_worker["images"].iterdir())


def test_restore_refuses_when_space_is_tight_even_if_old_vm_would_free_it(fast_worker, monkeypatch):
    make_backup(fast_worker)
    monkeypatch.setattr(backups, "free_bytes", lambda path: 1024)
    with pytest.raises(backups.BackupError) as exc:
        backups.start_restore("web01", "20260924-0300", "web01", True)
    assert exc.value.code == "space" and "рядом со старой машиной" in str(exc.value)
    with pytest.raises(backups.BackupError) as exc:
        backups.start_restore("web01", "20260924-0300", "web02", False)
    assert exc.value.code == "space" and "рядом со старой машиной" not in str(exc.value)


def test_restore_ejects_iso_before_undefine(fast_worker, monkeypatch):
    make_backup(fast_worker)
    virsh = FakeVirsh(running=False)
    base = virsh.__call__

    def with_iso(cmd, timeout=12, **kw):
        if cmd[:2] == ["virsh", "domblklist"]:
            virsh.calls.append(list(cmd))
            return fake_result(DOMBLKLIST_DETAILS.replace("sda      -", "sda      /var/lib/virtuality/iso/debian.iso"))
        return base(cmd, timeout, **kw)

    monkeypatch.setattr(core, "run_cmd", with_iso)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    backups.start_restore("web01", "20260924-0300", "web01", True)
    eject = ["virsh", "change-media", "web01", "sda", "--eject", "--config"]
    assert eject in virsh.calls
    assert virsh.calls.index(eject) < virsh.calls.index(UNDEFINE)


def test_restore_rejects_bad_name_and_incomplete_backup(fast_worker):
    make_backup(fast_worker)
    with pytest.raises(backups.BackupError) as exc:
        backups.start_restore("web01", "20260924-0300", "-bad", False)
    assert exc.value.code == "name"
    make_backup(fast_worker, backup_id="20260901-0000", complete=False)
    with pytest.raises(backups.BackupError) as exc:
        backups.start_restore("web01", "20260901-0000", "web02", False)
    assert "неполная" in str(exc.value)


def test_restore_cleans_up_on_define_failure(fast_worker, monkeypatch):
    make_backup(fast_worker)
    virsh = FakeVirsh(running=False)
    base = virsh.__call__

    def failing_define(cmd, timeout=12, **kw):
        if cmd[:2] == ["virsh", "define"]:
            return fake_result(ok=False, stderr="error: XML error: bad thing")
        return base(cmd, timeout, **kw)

    monkeypatch.setattr(core, "run_cmd", failing_define)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    operation = backups.start_restore("web01", "20260924-0300", "web02", False)
    fresh = core.read_operation(operation["id"])
    assert fresh["status"] == "error" and "bad thing" in fresh["message"]
    assert not (fast_worker["images"] / "web02.qcow2").exists() and not list(fast_worker["images"].glob("*.restore-*"))


def test_restore_disk_path_avoids_collisions(data_dirs):
    (data_dirs["images"] / "web02.qcow2").write_bytes(b"x")
    assert backups.restore_disk_path("web02", "vda", 0) == data_dirs["images"] / "web02-vda.qcow2"
    assert backups.restore_disk_path("web02", "vdb", 1) == data_dirs["images"] / "web02-vdb.qcow2"
    (data_dirs["images"] / "web02-vda.qcow2").write_bytes(b"x")
    assert backups.restore_disk_path("web02", "vda", 0) == data_dirs["images"] / "web02-vda-2.qcow2"


# ---------------------------------------------------------------- страницы
def test_backup_pages_require_login(client):
    for path in ("/backups", "/vm/web01/backups", "/backups/web01/20260924-0300/restore"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_backups_page_lists_groups(logged_in, data_dirs):
    make_backup(data_dirs, "web01", "20260924-0300", size=2_684_354_560, note="перед обновлением")
    make_backup(data_dirs, "gone-vm", "20260901-1200", size=1024 ** 3)
    html = logged_in.get("/backups").text
    assert "24 сентября 2026, 03:00" in html and "2.5 ГБ" in html and "перед обновлением" in html
    assert 'href="/backups/web01/20260924-0300/restore"' in html
    assert "машина удалена" in html and 'href="/vm/web01/backups"' in html
    assert "Резервные копии" in html


def test_backups_page_empty_state(logged_in):
    html = logged_in.get("/backups").text
    assert "Копий пока нет" in html


def test_vm_backups_page_and_tab_link(logged_in, data_dirs):
    make_backup(data_dirs)
    html = logged_in.get("/vm/web01/backups").text
    assert 'action="/vm/web01/backups/create"' in html and "Создать копию" in html
    assert "24 сентября 2026, 03:00" in html
    assert 'href="/vm/web01/backups"' in logged_in.get("/vm/web01").text
    assert logged_in.get("/vm/nope/backups", follow_redirects=False).headers["location"] == "/"


def test_vm_backup_create_route(logged_in, fast_worker, monkeypatch):
    monkeypatch.setattr(core, "run_cmd", FakeVirsh(running=False))
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    response = logged_in.post("/vm/web01/backups/create", data={"note": "перед обновлением"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/operations/")
    operation_id = response.headers["location"].rsplit("/", 1)[1]
    assert core.read_operation(operation_id)["status"] == "success"
    assert logged_in.get(f"/operations/{operation_id}").status_code == 200


def test_vm_backup_create_route_shows_error(logged_in, data_dirs, monkeypatch):
    monkeypatch.setattr(backups, "free_bytes", lambda path: 0)
    response = logged_in.post("/vm/web01/backups/create", data={"note": ""}, follow_redirects=False)
    assert response.status_code == 303
    assert "backup_error=" in response.headers["location"] and "Недостаточно" in unquote(response.headers["location"])
    assert "Недостаточно места" in logged_in.get(response.headers["location"]).text


def test_restore_page_and_submit(logged_in, fast_worker, monkeypatch):
    make_backup(fast_worker)
    monkeypatch.setattr(core, "run_cmd", FakeVirsh(running=False))
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    html = logged_in.get("/backups/web01/20260924-0300/restore").text
    assert 'name="replace"' in html and "Заменить существующую машину web01" in html
    assert "Старая машина удаляется только после распаковки" in html and "Сетевые адреса сохраняются" in html
    response = logged_in.post("/backups/web01/20260924-0300/restore", data={"name": "web01", "replace": ""})
    assert response.status_code == 400 and "уже есть на сервере" in response.text
    response = logged_in.post("/backups/web01/20260924-0300/restore", data={"name": "web02", "replace": ""}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/operations/")
    response = logged_in.post("/backups/web01/20260924-0300/restore", data={"name": "-bad"})
    assert response.status_code == 400
    assert logged_in.get("/backups/web01/20990101-0000/restore", follow_redirects=False).headers["location"].startswith("/backups?backup_error=")


def test_delete_backup_route(logged_in, data_dirs):
    path = make_backup(data_dirs)
    response = logged_in.post("/backups/web01/20260924-0300/delete", data={"next": "/vm/web01/backups"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/vm/web01/backups?backup_message=")
    assert not path.exists() and not path.parent.exists()
    response = logged_in.post("/backups/web01/20260924-0300/delete", data={"next": "http://evil"}, follow_redirects=False)
    assert response.headers["location"].startswith("/backups?backup_error=")


def test_delete_backup_path_traversal(logged_in, data_dirs, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "20260924-0300").mkdir()
    # %2F раскрывается до маршрутизации: лишние сегменты не подходят ни под один маршрут
    assert logged_in.post("/backups/..%2Fvictim/20260924-0300/delete", follow_redirects=False).status_code == 404
    assert logged_in.post("/backups/web01/..%2F..%2Fvictim/delete", follow_redirects=False).status_code == 404
    # а сама проверка путей — в delete_backup, независимо от маршрутизации
    with pytest.raises(backups.BackupError):
        backups.delete_backup("../victim", "20260924-0300")
    with pytest.raises(backups.BackupError):
        backups.delete_backup("web01", "../x")
    assert (victim / "20260924-0300").exists()


def test_delete_backup_refused_while_copy_is_in_use(fast_worker, monkeypatch):
    monkeypatch.setattr(core, "run_operation", lambda operation, worker: None)  # задачи остаются в очереди
    monkeypatch.setattr(core, "run_cmd", FakeVirsh(running=False))
    queued = backups.create_backup("web01")
    with pytest.raises(backups.BackupError) as exc:
        backups.delete_backup("web01", queued["backup_id"])
    assert "идёт работа" in str(exc.value)
    core.finish_operation(queued, False, "прервано")
    backups.delete_backup("web01", queued["backup_id"])
    assert not (fast_worker["backups"] / "web01").exists()

    path = make_backup(fast_worker)
    restore = backups.start_restore("web01", "20260924-0300", "web02", False)  # под другим именем: задача записана на web02
    second = backups.start_restore("web01", "20260924-0300", "web03", False)  # две распаковки одной копии сразу
    with pytest.raises(backups.BackupError) as exc:
        backups.delete_backup("web01", "20260924-0300")
    assert "идёт работа" in str(exc.value)
    core.finish_operation(second, False, "прервано")  # первая ещё идёт — копию по-прежнему нельзя удалить
    with pytest.raises(backups.BackupError):
        backups.delete_backup("web01", "20260924-0300")
    core.finish_operation(restore, False, "прервано")
    backups.delete_backup("web01", "20260924-0300")
    assert not path.exists()


def test_late_restart_watch_is_cancelled_by_user_action(fast_worker, monkeypatch):
    import threading

    virsh = FakeVirsh(running=True, shuts_down=False)
    monkeypatch.setattr(core, "run_cmd", virsh)
    monkeypatch.setattr(backups, "stream_cmd", fake_stream())
    monkeypatch.setattr(backups, "LATE_SHUTDOWN_WATCH", 5.0)
    watchers = []
    monkeypatch.setattr(backups, "start_thread", lambda target, *args: watchers.append(threading.Thread(target=target, args=args)) or watchers[-1].start())
    operation = backups.create_backup("web01")
    for _ in range(200):  # наблюдатель запущен и ждёт
        if "web01" in backups.LATE_WATCHES:
            break
        __import__("time").sleep(0.01)
    backups.cancel_late_restart("web01")  # пользователь нажал «Выключить» в панели
    virsh.running = False
    watchers[0].join(timeout=5)
    assert not watchers[0].is_alive() and ["virsh", "start", "web01"] not in virsh.calls
    assert "Наблюдение снято" in core.read_operation(operation["id"])["log_tail"]
