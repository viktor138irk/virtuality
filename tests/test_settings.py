import nodectl
from test_setup import prepare


def ready(data_dirs, monkeypatch, ctl=True):
    prepare(data_dirs, monkeypatch)
    nodectl.mark_wizard_done()
    monkeypatch.setattr(nodectl, "ctl_available", lambda: ctl)
    nodectl.NODE_ENV = data_dirs["config"] / "web.env"
    nodectl.NODE_ENV.write_text("VIRTUALITY_WEB_PORT=8088\nVIRTUALITY_TLS=0\nVIRTUALITY_AUTO_UPDATE=1\nVIRTUALITY_UPDATE_CHANNEL=stable\n")


def test_settings_page_renders(logged_in, data_dirs, monkeypatch):
    ready(data_dirs, monkeypatch)
    html = logged_in.get("/settings").text
    for text in ("Доступ к панели", "Сеть машин", "Хранилище", "Часовой пояс", "Обновления", "Перезагрузить", "http://panel.test:8088"):
        assert text in html, text


def test_settings_page_without_ctl_disables_actions(logged_in, data_dirs, monkeypatch):
    ready(data_dirs, monkeypatch, ctl=False)
    html = logged_in.get("/settings").text
    assert "virtuality-ctl не найдена" in html and "disabled" in html


def test_settings_requires_login(client, data_dirs, monkeypatch):
    ready(data_dirs, monkeypatch)
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_access_change_applies_and_shows_new_url(logged_in, data_dirs, monkeypatch):
    ready(data_dirs, monkeypatch)
    calls = []
    monkeypatch.setattr(nodectl, "ctl_detached", lambda *args, **kw: calls.append(args) or {"ok": True, "stdout": "", "stderr": "", "code": 0, "cmd": ""})
    response = logged_in.post("/settings/access", data={"tls": "1", "port": "8088"})
    assert response.status_code == 200 and "https://panel.test:8443" in response.text
    assert calls == [("set", "VIRTUALITY_TLS=1", "VIRTUALITY_WEB_PORT=8088")]
    assert logged_in.post("/settings/access", data={"tls": "0", "port": "22"}).status_code == 400
    unchanged = logged_in.post("/settings/access", data={"tls": "0", "port": "8088"}, follow_redirects=False)
    assert unchanged.status_code == 303 and "message=" in unchanged.headers["location"]


def test_password_change(logged_in, data_dirs, monkeypatch):
    ready(data_dirs, monkeypatch)
    seen = {}
    monkeypatch.setattr(nodectl, "ctl", lambda *args, **kw: seen.update(args=args, stdin=kw.get("stdin")) or {"ok": True, "stdout": "", "stderr": "", "code": 0, "cmd": ""})
    assert logged_in.post("/settings/password", data={"password": "abc", "password2": "abd"}).status_code == 400
    assert logged_in.post("/settings/password", data={"password": "abc", "password2": "abc"}).status_code == 400
    response = logged_in.post("/settings/password", data={"password": "secret1", "password2": "secret1"}, follow_redirects=False)
    assert response.status_code == 303 and seen["args"] == ("passwd", "tester") and seen["stdin"] == "secret1\n"


def test_timezone_and_updates(logged_in, data_dirs, monkeypatch):
    ready(data_dirs, monkeypatch)
    calls = []
    monkeypatch.setattr(nodectl, "ctl", lambda *args, **kw: calls.append(args) or {"ok": True, "stdout": "", "stderr": "", "code": 0, "cmd": ""})
    monkeypatch.setattr(nodectl, "ctl_detached", lambda *args, **kw: calls.append(args) or {"ok": True, "stdout": "", "stderr": "", "code": 0, "cmd": ""})
    assert logged_in.post("/settings/timezone", data={"timezone": "Europe/Moscow"}, follow_redirects=False).status_code == 303
    assert logged_in.post("/settings/timezone", data={"timezone": "../etc"}).status_code == 400
    assert logged_in.post("/settings/updates", data={"channel": "main"}, follow_redirects=False).status_code == 303
    assert calls == [("timezone", "Europe/Moscow"), ("set", "VIRTUALITY_AUTO_UPDATE=0", "VIRTUALITY_UPDATE_CHANNEL=main")]


def test_network_bridge_and_revert(logged_in, data_dirs, monkeypatch):
    ready(data_dirs, monkeypatch)
    calls = []
    monkeypatch.setattr(nodectl, "enable_bridge", lambda iface, mode="dhcp": calls.append(("bridge", iface, mode)) or (True, "ok"))
    monkeypatch.setattr(nodectl, "revert_bridge", lambda: calls.append(("revert",)) or (True, "Возвращаем прежнюю сеть"))
    response = logged_in.post("/settings/network", data={"mode": "bridge"})
    assert response.status_code == 200 and "Перенастраиваем сеть" in response.text and 'data-next-url="/settings"' in response.text
    assert logged_in.post("/settings/network/revert", follow_redirects=False).headers["location"].startswith("/settings?message=")
    assert calls == [("bridge", "enp3s0", "dhcp"), ("revert",)]


def test_storage_power_and_downloads(logged_in, data_dirs, monkeypatch, tmp_path):
    ready(data_dirs, monkeypatch)
    monkeypatch.setattr(nodectl, "use_disk_for_storage", lambda path: (path == "/dev/sdb", "Диск подключён" if path == "/dev/sdb" else "Нельзя"))
    assert "message=" in logged_in.post("/settings/storage", data={"disk": "/dev/sdb"}, follow_redirects=False).headers["location"]
    assert "error=" in logged_in.post("/settings/storage", data={"disk": "/dev/sda"}, follow_redirects=False).headers["location"]
    monkeypatch.setattr(nodectl, "ctl_detached", lambda *args, **kw: {"ok": True, "stdout": "", "stderr": "", "code": 0, "cmd": ""})
    assert "Перезагружаем сервер" in logged_in.post("/settings/power", data={"action": "reboot"}).text
    assert logged_in.post("/settings/power", data={"action": "halt"}).status_code == 400
    archive = tmp_path / "config-1.tar.gz"
    archive.write_bytes(b"\x1f\x8b" + b"0" * 16)
    monkeypatch.setattr(nodectl, "ctl", lambda *args, **kw: {"ok": True, "stdout": str(archive), "stderr": "", "code": 0, "cmd": ""})
    response = logged_in.post("/settings/backup-config")
    assert response.status_code == 200 and response.headers["content-disposition"].endswith('"config-1.tar.gz"') and response.content.startswith(b"\x1f\x8b")
    assert logged_in.post("/settings/support-bundle").status_code == 200
