"""Снимки (точки восстановления) машины: парсеры и маршруты /vm/{name}/snapshots."""
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import pytest

import conftest
import core
from features import snapshots

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------- parsers
def test_parse_snapshot_list_fixture():
    rows = snapshots.parse_snapshot_list((FIXTURES / "snapshot-list.txt").read_text())
    assert [row["name"] for row in rows] == ["before-upd", "clean"]  # новые сверху
    assert rows[0] == {"name": "before-upd", "created_at": "2026-09-25 10:12:03", "tz": "+0000", "state": "shutoff"}
    assert rows[1]["state"] == "running"


def test_parse_snapshot_list_without_timezone_and_garbage():
    text = " Name   Creation Time         State\n------\n snap1   2026-01-02 03:04:05   running\nerror: something\n\n"
    rows = snapshots.parse_snapshot_list(text)
    assert rows == [{"name": "snap1", "created_at": "2026-01-02 03:04:05", "tz": "", "state": "running"}]
    assert snapshots.parse_snapshot_list("") == []


def test_parse_snapshot_xml():
    meta = snapshots.parse_snapshot_xml(conftest.fake_snapshot_xml("clean"))
    assert meta["description"] == "Чистая система после установки"
    assert meta["state"] == "running" and meta["with_memory"] is True
    assert snapshots.parse_snapshot_xml(conftest.fake_snapshot_xml("before-upd"))["with_memory"] is False
    assert snapshots.parse_snapshot_xml("<broken") == {}


def test_inspect_domain_xml_qcow2_fixture():
    info = snapshots.inspect_domain_xml((FIXTURES / "dumpxml-web01.xml").read_text())
    assert info["disks"] == [{"target": "vda", "format": "qcow2"}]  # cdrom пропущен
    assert info["unsupported"] == [] and info["uefi"] is False


def test_inspect_domain_xml_raw_and_uefi():
    xml = """<domain type='kvm'><name>x</name>
      <os firmware='efi'><type arch='x86_64'>hvm</type><loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE.fd</loader><nvram>/var/lib/libvirt/qemu/nvram/x.fd</nvram></os>
      <devices>
        <disk type='file' device='disk'><driver name='qemu' type='raw'/><source file='/x.img'/><target dev='vda' bus='virtio'/></disk>
        <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/y.qcow2'/><target dev='vdb' bus='virtio'/></disk>
      </devices></domain>"""
    info = snapshots.inspect_domain_xml(xml)
    assert info["unsupported"] == [{"target": "vda", "format": "raw"}]
    assert info["uefi"] is True
    assert snapshots.inspect_domain_xml("<nope") == {"disks": [], "unsupported": [], "uefi": False}


def test_format_when():
    now = datetime(2026, 9, 25, 12, 0, 0)
    assert snapshots.format_when("2026-09-25 10:12:03", now) == "Сегодня, 10:12"
    assert snapshots.format_when("2026-09-24 09:00:41", now) == "Вчера, 09:00"
    assert snapshots.format_when("2026-03-08 18:30:00", now) == "8 марта 2026, 18:30"
    assert snapshots.format_when("garbage", now) == "garbage"


def test_explain_error_translates_known_libvirt_messages():
    text = snapshots.explain_error("error: unsupported configuration: internal snapshots of a VM with pflash based firmware are not supported")
    assert text.startswith("Для машин с UEFI-прошивкой") and "libvirt:" in text
    assert snapshots.explain_error("error: internal snapshot for disk vda unsupported for storage type raw").startswith("Внутренние снимки работают только с дисками qcow2")
    assert snapshots.explain_error("error: Timed out during operation: cannot acquire state change lock").startswith("Машина занята")
    assert snapshots.explain_error("error: something odd\nsecond line") == "something odd"
    assert "не объяснил" in snapshots.explain_error("")


def test_next_snapshot_name_and_description():
    now = datetime(2026, 9, 25, 10, 12)
    assert snapshots.next_snapshot_name([], now) == "snap-20260925-1012"
    assert snapshots.next_snapshot_name(["snap-20260925-1012"], now) == "snap-20260925-1012-2"
    assert snapshots.next_snapshot_name(["snap-20260925-1012", "snap-20260925-1012-2"], now) == "snap-20260925-1012-3"
    assert core.valid_label(snapshots.next_snapshot_name([], now))
    assert snapshots.clean_description("  --force\x00 перед\nобновлением  ") == "force перед обновлением"
    assert len(snapshots.clean_description("x" * 500)) == snapshots.DESCRIPTION_MAX


def test_snapshot_state_labels():
    assert snapshots.snapshot_state("running") == {"label": "Работала", "tone": "success"}
    assert snapshots.snapshot_state("shutoff")["label"] == "Была выключена"
    assert snapshots.snapshot_state("weird") == {"label": "weird", "tone": "neutral"}


# ---------------------------------------------------------------- helpers for routes
@pytest.fixture()
def recorder(monkeypatch):
    """run_cmd, который записывает команды virsh и выполняет операции сразу (без потоков)."""
    calls: list[list[str]] = []

    def run(cmd, timeout=12, **kwargs):
        calls.append(list(cmd))
        return conftest.fake_run_cmd(cmd, timeout, **kwargs)

    monkeypatch.setattr(snapshots, "run_cmd", run)
    monkeypatch.setattr(snapshots, "run_operation", lambda operation, worker: worker(operation))
    return calls


def find(calls, *prefix):
    return [cmd for cmd in calls if cmd[: len(prefix)] == list(prefix)]


# ---------------------------------------------------------------- routes
def test_snapshots_page_requires_login(client):
    response = client.get("/vm/web01/snapshots", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_snapshots_page_renders_list(logged_in):
    html = logged_in.get("/vm/web01/snapshots").text
    assert "Перед обновлением ядра" in html and "Чистая система после установки" in html
    assert "Текущий" in html and "Была выключена" in html and "Работала" in html
    assert 'action="/vm/web01/snapshots/before-upd/revert"' in html
    assert 'action="/vm/web01/snapshots/clean/delete"' in html
    assert "2 из 8" in html
    assert "snap-" in html  # предложенное имя
    assert "shut off" not in html


def test_snapshots_page_unknown_vm_redirects_home(logged_in):
    response = logged_in.get("/vm/ghost/snapshots", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert logged_in.get("/vm/-bad/snapshots", follow_redirects=False).status_code == 400


def test_vm_page_links_to_snapshots_tab(logged_in):
    assert 'href="/vm/web01/snapshots"' in logged_in.get("/vm/web01").text


def test_create_snapshot_runs_virsh_and_reports_operation(logged_in, data_dirs, recorder):
    response = logged_in.post("/vm/web01/snapshots/create", data={"description": "Перед экспериментом"}, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/vm/web01/snapshots?op=")
    cmd = find(recorder, "virsh", "snapshot-create-as")[0]
    assert cmd[2:6] == ["--domain", "web01", "--name", cmd[5]] and cmd[5].startswith("snap-")
    assert cmd[6:] == ["--description", "Перед экспериментом", "--atomic"]
    operation = core.read_operation(location.split("op=", 1)[1])
    assert operation["status"] == "success" and operation["type"] == "snapshot_create" and operation["vm_name"] == "web01"
    assert "создан" in operation["message"]
    html = logged_in.get(location).text
    assert "создан" in html


def test_create_snapshot_without_description_omits_option(logged_in, data_dirs, recorder):
    logged_in.post("/vm/web01/snapshots/create", data={"description": "   "}, follow_redirects=False)
    cmd = find(recorder, "virsh", "snapshot-create-as")[0]
    assert "--description" not in cmd and cmd[-1] == "--atomic"


def test_create_snapshot_limit(logged_in, data_dirs, recorder, monkeypatch):
    rows = "\n".join(f" snap-{i:02d}   2026-09-{i + 1:02d} 10:00:00 +0000   shutoff" for i in range(8))
    original = snapshots.run_cmd

    def run(cmd, timeout=12, **kwargs):
        if cmd[:2] == ["virsh", "snapshot-list"]:
            return conftest.fake_result(" Name   Creation Time   State\n---\n" + rows)
        return original(cmd, timeout, **kwargs)

    monkeypatch.setattr(snapshots, "run_cmd", run)
    response = logged_in.post("/vm/web01/snapshots/create", data={"description": "x"}, follow_redirects=False)
    assert response.status_code == 303
    assert "максимум" in unquote(response.headers["location"])
    assert not find(recorder, "virsh", "snapshot-create-as")
    html = logged_in.get("/vm/web01/snapshots").text
    assert "8 из 8" in html and "максимум" in html


def test_create_snapshot_rejects_raw_disk(logged_in, data_dirs, recorder, monkeypatch):
    original = snapshots.run_cmd

    def run(cmd, timeout=12, **kwargs):
        if cmd[:2] == ["virsh", "dumpxml"]:
            return conftest.fake_result("<domain><devices><disk device='disk'><driver type='raw'/><target dev='vda'/></disk></devices></domain>")
        return original(cmd, timeout, **kwargs)

    monkeypatch.setattr(snapshots, "run_cmd", run)
    response = logged_in.post("/vm/web01/snapshots/create", data={"description": "x"}, follow_redirects=False)
    assert "qcow2" in unquote(response.headers["location"])
    assert not find(recorder, "virsh", "snapshot-create-as")


def test_create_snapshot_surfaces_libvirt_error(logged_in, data_dirs, recorder, monkeypatch):
    original = snapshots.run_cmd

    def run(cmd, timeout=12, **kwargs):
        if cmd[:2] == ["virsh", "snapshot-create-as"]:
            return conftest.fake_result(ok=False, stderr="error: unsupported configuration: internal snapshots of a VM with pflash based firmware are not supported")
        return original(cmd, timeout, **kwargs)

    monkeypatch.setattr(snapshots, "run_cmd", run)
    response = logged_in.post("/vm/web01/snapshots/create", data={}, follow_redirects=False)
    location = response.headers["location"]
    operation = core.read_operation(location.split("op=", 1)[1])
    assert operation["status"] == "error"
    assert operation["message"].startswith("Для машин с UEFI-прошивкой") and "pflash" in operation["message"]
    assert "pflash" in operation["log_tail"]
    html = logged_in.get(location).text
    assert "Для машин с UEFI-прошивкой" in html


def test_create_snapshot_waits_for_active_operation(logged_in, data_dirs, recorder):
    core.new_operation("snapshot_create", "Снимок машины web01", vm_name="web01", snapshot="snap-x")
    response = logged_in.post("/vm/web01/snapshots/create", data={}, follow_redirects=False)
    assert "предыдущей операции" in unquote(response.headers["location"])
    assert not find(recorder, "virsh", "snapshot-create-as")
    html = logged_in.get("/vm/web01/snapshots").text
    assert 'data-watch-operation=' in html and "/api/operations/" in html


def test_revert_snapshot(logged_in, data_dirs, recorder):
    response = logged_in.post("/vm/web01/snapshots/clean/revert", follow_redirects=False)
    assert response.status_code == 303
    assert find(recorder, "virsh", "snapshot-revert") == [["virsh", "snapshot-revert", "--domain", "web01", "--snapshotname", "clean"]]
    operation = core.read_operation(response.headers["location"].split("op=", 1)[1])
    assert operation["status"] == "success" and "возвращена" in operation["message"] and "работает" in operation["message"]


def test_revert_retries_with_force(logged_in, data_dirs, recorder, monkeypatch):
    original = snapshots.run_cmd

    def run(cmd, timeout=12, **kwargs):
        if cmd[:2] == ["virsh", "snapshot-revert"] and "--force" not in cmd:
            recorder.append(list(cmd))
            return conftest.fake_result(ok=False, stderr="error: revert requires force: snapshot was created with a different configuration")
        return original(cmd, timeout, **kwargs)

    monkeypatch.setattr(snapshots, "run_cmd", run)
    response = logged_in.post("/vm/web01/snapshots/clean/revert", follow_redirects=False)
    reverts = find(recorder, "virsh", "snapshot-revert")
    assert len(reverts) == 2 and reverts[1][-1] == "--force"
    assert core.read_operation(response.headers["location"].split("op=", 1)[1])["status"] == "success"


def test_revert_reports_error(logged_in, data_dirs, recorder, monkeypatch):
    original = snapshots.run_cmd

    def run(cmd, timeout=12, **kwargs):
        if cmd[:2] == ["virsh", "snapshot-revert"]:
            return conftest.fake_result(ok=False, stderr="error: Domain snapshot not found: no domain snapshot with matching name 'clean'")
        return original(cmd, timeout, **kwargs)

    monkeypatch.setattr(snapshots, "run_cmd", run)
    response = logged_in.post("/vm/web01/snapshots/clean/revert", follow_redirects=False)
    operation = core.read_operation(response.headers["location"].split("op=", 1)[1])
    assert operation["status"] == "error" and operation["message"].startswith("Такого снимка уже нет")


def test_delete_snapshot(logged_in, data_dirs, recorder):
    response = logged_in.post("/vm/web01/snapshots/before-upd/delete", follow_redirects=False)
    assert response.status_code == 303
    assert "удалён" in unquote(response.headers["location"])
    assert find(recorder, "virsh", "snapshot-delete") == [["virsh", "snapshot-delete", "--domain", "web01", "--snapshotname", "before-upd"]]


def test_delete_snapshot_error(logged_in, data_dirs, recorder, monkeypatch):
    original = snapshots.run_cmd

    def run(cmd, timeout=12, **kwargs):
        if cmd[:2] == ["virsh", "snapshot-delete"]:
            return conftest.fake_result(ok=False, stderr="error: Timed out during operation: cannot acquire state change lock")
        return original(cmd, timeout, **kwargs)

    monkeypatch.setattr(snapshots, "run_cmd", run)
    response = logged_in.post("/vm/web01/snapshots/before-upd/delete", follow_redirects=False)
    assert "snapshot_error=" in response.headers["location"] and "занята" in unquote(response.headers["location"])


def test_snapshot_routes_validate_names(logged_in, data_dirs, recorder):
    bad = logged_in.post("/vm/web01/snapshots/-bad/revert", follow_redirects=False)
    assert bad.status_code == 303 and "snapshot_error=" in bad.headers["location"]
    assert logged_in.post("/vm/web01/snapshots/..%2Fx/delete", follow_redirects=False).status_code in (400, 404)
    assert logged_in.post("/vm/-bad/snapshots/create", data={}, follow_redirects=False).status_code == 400
    assert not find(recorder, "virsh", "snapshot-delete") and not find(recorder, "virsh", "snapshot-revert")


def test_snapshot_routes_require_login(client):
    for path in ("/vm/web01/snapshots/create", "/vm/web01/snapshots/clean/revert", "/vm/web01/snapshots/clean/delete"):
        response = client.post(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
