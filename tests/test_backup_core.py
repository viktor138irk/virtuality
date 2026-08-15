import time
from pathlib import Path

import backup_core


def test_backup_filename_validation():
    assert backup_core.valid_backup_filename("vm1_20260815_010101.qcow2")
    assert backup_core.valid_backup_filename("a.qcow2")
    assert not backup_core.valid_backup_filename("../evil.qcow2")
    assert not backup_core.valid_backup_filename("x.img")
    assert not backup_core.valid_backup_filename("")


def test_backup_path_traversal_guard():
    assert backup_core.backup_path("vm1", "vm1_x.qcow2") is not None
    assert backup_core.backup_path("vm1", "../../etc/passwd") is None
    assert backup_core.backup_path("../vm1", "x.qcow2") is None
    assert backup_core.backup_path("vm1", "nested/x.qcow2") is None


def test_new_backup_path_shape():
    path = backup_core.new_backup_path("vm1")
    assert path is not None
    assert path.name.startswith("vm1_")
    assert path.suffix == ".qcow2"
    assert backup_core.new_backup_path("bad name") is None


def test_rotation_keeps_newest():
    vm_dir = backup_core.backup_dir_for("vm1")
    vm_dir.mkdir(parents=True)
    for index in range(5):
        item = vm_dir / f"vm1_{index}.qcow2"
        item.write_text("x")
        stamp = time.time() - (5 - index) * 60
        import os
        os.utime(item, (stamp, stamp))
    removed = backup_core.rotate_backups("vm1", keep=2)
    assert len(removed) == 3
    remaining = sorted(p.name for p in vm_dir.glob("*.qcow2"))
    assert remaining == ["vm1_3.qcow2", "vm1_4.qcow2"]


def test_rotation_ignores_bad_input():
    assert backup_core.rotate_backups("no-such-vm", keep=3) == []
    assert backup_core.rotate_backups("vm1", keep=0) == []


def test_schedule_roundtrip():
    config = backup_core.save_schedule(True, ["vm2", "vm1", "bad name", "vm1"], 99)
    assert config == {"enabled": True, "vms": ["vm1", "vm2"], "keep": 30}
    assert backup_core.load_schedule() == config
    config = backup_core.save_schedule(False, [], 0)
    assert backup_core.load_schedule() == {"enabled": False, "vms": [], "keep": 1}


def test_schedule_defaults_when_missing():
    assert backup_core.load_schedule() == {"enabled": False, "vms": [], "keep": backup_core.DEFAULT_KEEP}


def test_backup_script_contents():
    target = Path("/var/lib/virtuality/backups/vm1/vm1_x.qcow2")
    script = backup_core.build_backup_script("vm1", "/var/lib/virtuality/images/vm1.qcow2", target)
    assert "virsh suspend vm1" in script
    assert "qemu-img convert -p -c -O qcow2" in script
    assert "virsh resume vm1" in script
    assert f"{target}.part" in script  # атомарная запись через .part
    restore = backup_core.build_restore_script("/var/lib/virtuality/images/vm1.qcow2", target)
    assert "qemu-img convert -p -O qcow2" in restore
    assert ".restore" in restore


def test_first_disk_of_parsing(monkeypatch):
    sample = """ Type   Device   Target   Source
------------------------------------------------
 file   disk     vda      /var/lib/virtuality/images/vm1.qcow2
 file   cdrom    sda      /var/lib/virtuality/iso/ubuntu.iso
"""

    class FakeResult:
        returncode = 0
        stdout = sample
        stderr = ""

    monkeypatch.setattr(backup_core, "_run", lambda cmd, timeout=60: FakeResult())
    assert backup_core.first_disk_of("vm1") == "/var/lib/virtuality/images/vm1.qcow2"
