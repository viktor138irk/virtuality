import json

import app
import nodectl
from features import setup


def prepare(data_dirs, monkeypatch, stage="done"):
    config = data_dirs["config"]
    monkeypatch.setattr(nodectl, "CONFIG_DIR", config)
    monkeypatch.setattr(nodectl, "SETUP_STATE", config / "setup.json")
    monkeypatch.setattr(nodectl, "SETUP_DONE", config / "setup_done")
    monkeypatch.setattr(nodectl, "WIZARD_FILE", config / "wizard.json")
    monkeypatch.setattr(nodectl, "FIRSTBOOT_LOG", config / "firstboot.log")
    monkeypatch.setattr(nodectl, "ctl_available", lambda: False)
    (config / "setup.json").write_text(json.dumps({"stage": stage, "message": "Устанавливаем…", "steps": [{"id": "panel", "title": "Панель", "status": "done"}, {"id": "virt", "title": "KVM", "status": "running" if stage != "done" else "done"}]}))
    monkeypatch.setattr(nodectl, "network_facts", lambda: {"interface": "enp3s0", "gateway": "192.168.1.1", "address": "192.168.1.10", "prefix": 24, "mac": "aa:bb:cc:dd:ee:ff", "wireless": False, "private": True, "is_vps": False, "virt": "none", "bridge_present": False, "on_bridge": False, "recommended": "bridge", "revert_armed": False, "bridge_possible": True})
    monkeypatch.setattr(nodectl, "spare_disks", lambda: [])
    monkeypatch.setattr(nodectl, "timezones", lambda: {"current": "UTC", "popular": ["Europe/Moscow", "UTC"], "all": ["Europe/Moscow", "UTC"]})


def test_dashboard_redirects_to_wizard_until_done(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch)
    response = logged_in.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/setup"
    nodectl.mark_wizard_done()
    assert logged_in.get("/", follow_redirects=False).status_code == 200


def test_dashboard_without_installer_state_has_no_wizard(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch)
    nodectl.SETUP_STATE.unlink()
    assert logged_in.get("/", follow_redirects=False).status_code == 200


def test_install_step_shown_while_installing(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch, stage="installing")
    assert logged_in.get("/setup", follow_redirects=False).headers["location"] == "/setup/install"
    html = logged_in.get("/setup/install").text
    assert "Настраиваем сервер" in html and "Устанавливаем…" in html
    # other steps are locked until the installation finishes
    assert logged_in.get("/setup/welcome", follow_redirects=False).headers["location"] == "/setup/install"
    state = logged_in.get("/api/setup/state").json()
    assert state["ok"] and state["state"]["installed"] is False


def test_wizard_steps_render_and_store_choices(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch)
    monkeypatch.setattr(nodectl, "set_timezone", lambda tz: (True, tz))
    for step in setup.STEP_IDS[1:]:
        assert logged_in.get(f"/setup/{step}").status_code == 200, step
    assert logged_in.post("/setup/welcome", data={"timezone": "Europe/Moscow"}, follow_redirects=False).headers["location"] == "/setup/account"
    assert logged_in.post("/setup/account", data={"password": "", "password2": ""}, follow_redirects=False).headers["location"] == "/setup/access"
    assert logged_in.post("/setup/account", data={"password": "abcdef", "password2": "other"}).status_code == 400
    assert logged_in.post("/setup/access", data={"tls": "0", "port": "8090"}, follow_redirects=False).headers["location"] == "/setup/network"
    assert logged_in.post("/setup/access", data={"tls": "1", "port": "22"}).status_code == 400
    monkeypatch.setattr(app.network_core, "create_nat_network", lambda: {})
    assert logged_in.post("/setup/network", data={"mode": "nat"}, follow_redirects=False).headers["location"] == "/setup/storage"
    assert logged_in.post("/setup/storage", data={"disk": "keep"}, follow_redirects=False).headers["location"] == "/setup/updates"
    assert logged_in.post("/setup/updates", data={"auto_update": "0", "channel": "stable"}, follow_redirects=False).headers["location"] == "/setup/finish"
    choices = nodectl.wizard_choices()
    assert choices["timezone"] == "Europe/Moscow" and choices["port"] == "8090" and choices["tls"] == "0" and choices["network"] == "nat" and choices["auto_update"] == "0"
    finish = logged_in.get("/setup/finish").text
    assert "http://" in finish and "Europe/Moscow" in finish and "Вручную" in finish


def test_finish_marks_done_without_ctl(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch)
    response = logged_in.post("/setup/finish", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/?setup_message=")
    assert nodectl.wizard_done()


def test_finish_applies_settings_through_ctl(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch)
    calls = []
    monkeypatch.setattr(nodectl, "ctl_available", lambda: True)
    monkeypatch.setattr(nodectl, "ctl_detached", lambda *args, **kw: calls.append(args) or {"ok": True, "stdout": "", "stderr": "", "code": 0, "cmd": ""})
    nodectl.save_wizard_choices(tls="1", port="8088", auto_update="1", channel="stable")
    response = logged_in.post("/setup/finish", follow_redirects=False)
    assert response.status_code == 200 and "https://panel.test:8443" in response.text
    assert calls and calls[0][0] == "set" and "VIRTUALITY_TLS=1" in calls[0]


def test_bridge_mode_starts_network_change(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch)
    calls = []
    monkeypatch.setattr(nodectl, "enable_bridge", lambda iface, mode="dhcp": calls.append((iface, mode)) or (True, "ok"))
    response = logged_in.post("/setup/network", data={"mode": "bridge"})
    assert response.status_code == 200 and "Перенастраиваем сеть" in response.text
    assert calls == [("enp3s0", "dhcp")]
    monkeypatch.setattr(nodectl, "confirm_bridge", lambda: (True, "Сеть подтверждена"))
    assert logged_in.post("/setup/network/confirm").json()["ok"] is True
    assert nodectl.wizard_choices()["bridge_pending"] is False


def test_restart_wizard_from_settings(logged_in, data_dirs, monkeypatch):
    prepare(data_dirs, monkeypatch)
    nodectl.mark_wizard_done()
    assert logged_in.post("/setup/restart", follow_redirects=False).headers["location"] == "/setup/welcome"
    assert not nodectl.wizard_done()
