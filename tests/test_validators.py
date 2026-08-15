import app


def test_valid_vm_name():
    assert app.valid_vm_name("ubuntu-test")
    assert app.valid_vm_name("vm1.local_x")
    assert not app.valid_vm_name("")
    assert not app.valid_vm_name("a")  # минимум 2 символа
    assert not app.valid_vm_name("-starts-with-dash")
    assert not app.valid_vm_name("has space")
    assert not app.valid_vm_name("x" * 64)


def test_valid_snapshot_name():
    assert app.valid_snapshot_name("before-update")
    assert app.valid_snapshot_name("s1")
    assert app.valid_snapshot_name("a")  # один символ допустим
    assert not app.valid_snapshot_name("")
    assert not app.valid_snapshot_name("bad name")
    assert not app.valid_snapshot_name(".hidden")


def test_safe_iso_filename():
    assert app.safe_iso_filename("ubuntu-24.04.iso") == "ubuntu-24.04.iso"
    assert app.safe_iso_filename("my image.iso") == "my-image.iso"
    assert app.safe_iso_filename("../../etc/passwd") is None
    assert app.safe_iso_filename("evil.qcow2") is None
    assert app.safe_iso_filename("") is None


def test_iso_path_traversal_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ISO_DIR", tmp_path)
    assert app.iso_path_by_name("ok.iso") == tmp_path / "ok.iso"
    # Пути с каталогами санитизируются до basename и остаются внутри ISO_DIR
    assert app.iso_path_by_name("../escape.iso") == tmp_path / "escape.iso"
    assert app.iso_path_by_name("nested/evil.iso") == tmp_path / "evil.iso"
    assert app.iso_path_by_name("не-ascii.iso") != tmp_path / "не-ascii.iso"  # кириллица заменяется
    assert app.iso_path_by_name("no-extension") is None


def test_progress_parsers():
    assert app.wget_progress(10, " 512000K .......... 42% 10.5M 2m30s") == 42
    assert app.wget_progress(50, "no percent here") == 50
    assert app.wget_progress(10, "100%") == 99  # качаем — не показываем 100 до mv
    assert app.qemu_convert_progress(0, "    (42.00/100%)") == 42
    assert app.qemu_convert_progress(77, "    (42.00/100%)") == 77  # прогресс не откатывается
    assert app.progress_from_line(10, "Allocating disk...") == 30
    assert app.progress_from_line(50, "Creating domain...") == 75


def test_snapshot_list_parser(monkeypatch):
    sample = """ Name             Creation Time               State
--------------------------------------------------------
 before-update    2026-08-15 12:10:33 +0300   running
 clean            2026-08-14 09:01:02 +0300   shutoff
"""
    monkeypatch.setattr(app, "run_cmd", lambda cmd, timeout=12: {"ok": True, "stdout": sample, "stderr": "", "code": 0, "cmd": ""})
    snaps = app.list_vm_snapshots("vm1")
    assert [s["name"] for s in snaps] == ["before-update", "clean"]
    assert snaps[0]["state"] == "running"
    assert snaps[0]["created"] == "2026-08-15 12:10:33 +0300"


def test_snapshot_action_rejects_bad_name():
    ok, message = app.snapshot_action("vm1", "create", "bad name")
    assert not ok
    assert "Имя снапшота" in message


def test_vm_templates_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VM_TEMPLATES_FILE", tmp_path / "vm_templates.json")
    assert app.load_vm_templates() == []
    app.set_vm_template("vm1", True)
    app.set_vm_template("vm2", True)
    assert app.is_vm_template("vm1")
    assert sorted(app.load_vm_templates()) == ["vm1", "vm2"]
    app.set_vm_template("vm1", False)
    assert not app.is_vm_template("vm1")


def test_ttl_cache(monkeypatch):
    calls = []

    def producer():
        calls.append(1)
        return len(calls)

    app.cache_invalidate()
    assert app.cached("k1", 60, producer) == 1
    assert app.cached("k1", 60, producer) == 1
    assert len(calls) == 1
    app.cache_invalidate("k1")
    assert app.cached("k1", 60, producer) == 2
