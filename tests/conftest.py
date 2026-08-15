import sys
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(WEB_DIR))


@pytest.fixture(autouse=True)
def isolate_dirs(tmp_path, monkeypatch):
    """Уводим все записи на диск в tmp, чтобы тесты работали без root и не трогали систему."""
    import app
    import backup_core
    import cloud_images

    monkeypatch.setattr(app, "OPERATIONS_DIR", tmp_path / "ops")
    monkeypatch.setattr(app, "ISO_DIR", tmp_path / "iso")
    monkeypatch.setattr(app, "IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(app, "DISK_IMAGES_DIR", tmp_path / "disk-images")
    monkeypatch.setattr(app, "VM_TEMPLATES_FILE", tmp_path / "config" / "vm_templates.json")
    monkeypatch.setattr(backup_core, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup_core, "SCHEDULE_FILE", tmp_path / "config" / "backup_schedule.json")
    monkeypatch.setattr(backup_core, "LOG_FILE", tmp_path / "log" / "backup.log")
    monkeypatch.setattr(cloud_images, "CLOUD_IMAGES_DIR", tmp_path / "cloud-images")
    yield


@pytest.fixture()
def auth_client():
    """TestClient с валидной сессией и CSRF-токеном."""
    from fastapi.testclient import TestClient
    import app

    client = TestClient(app.app)
    token = app.serializer.dumps({"user": app.AUTH_USER, "csrf": "test-csrf-token"})
    client.cookies.set("virtuality_session", token)
    return client
