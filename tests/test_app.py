from urllib.parse import unquote

import pytest

import app

PAGES = ["/", "/host", "/iso", "/disk-images", "/network", "/operations", "/logs", "/update", "/vm/create", "/vm/web01", "/vm/web01/console"]


def test_healthz_is_public(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["version"] == (app.BASE_DIR.parent / "VERSION").read_text().strip()


def test_security_headers(client):
    response = client.get("/login")
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", PAGES)
def test_pages_require_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("path", PAGES)
def test_pages_render(logged_in, path):
    response = logged_in.get(path)
    assert response.status_code == 200, response.text[:500]


def test_wrong_user_is_rejected(client, monkeypatch):
    monkeypatch.setattr(app.auth, "verify_password", lambda user, password: True)
    response = client.post("/login", data={"username": "intruder", "password": "x"}, follow_redirects=False)
    assert response.status_code == 401


def test_login_throttle(client, monkeypatch):
    monkeypatch.setattr(app.auth, "verify_password", lambda user, password: False)
    for _ in range(5):
        assert client.post("/login", data={"username": "tester", "password": "bad"}).status_code == 401
    response = client.post("/login", data={"username": "tester", "password": "bad"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_session_cookie_flags(client, monkeypatch):
    monkeypatch.setattr(app.auth, "verify_password", lambda user, password: True)
    response = client.post("/login", data={"username": "tester", "password": "x"}, follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_forged_cookie_is_rejected(client):
    client.cookies.set("virtuality_session", "forged.token.value")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_cross_origin_post_blocked(logged_in):
    response = logged_in.post("/vm/web01/start", headers={"Origin": "http://evil.example"}, follow_redirects=False)
    assert response.status_code == 403


def test_same_origin_post_allowed(logged_in):
    response = logged_in.post("/vm/web01/start", headers={"Origin": "http://panel.test"}, follow_redirects=False)
    assert response.status_code == 303


def test_vm_action_rejects_bad_name(logged_in):
    assert logged_in.post("/vm/-bad/start", follow_redirects=False).status_code == 400


def test_vm_action_unknown(logged_in):
    assert logged_in.post("/vm/web01/format-disk", follow_redirects=False).status_code == 400


def test_vm_details_include_autostart(data_dirs):
    details = app.vm_details("web01")
    assert details["autostart_enabled"] is True
    assert details["autostart_label"] == "enabled"


def test_vm_detail_shows_autostart_enabled(logged_in):
    assert "autostart: enabled" in logged_in.get("/vm/web01").text


def test_redirect_message_is_url_encoded(logged_in, monkeypatch):
    monkeypatch.setattr(app, "apply_vm_boot_order", lambda name, order: (True, "Готово: диск & ISO?"))
    response = logged_in.post("/vm/web01/boot-order", data={"boot_order": "disk"}, follow_redirects=False)
    location = response.headers["location"]
    assert " " not in location and "&" not in location.split("?", 1)[1]
    assert unquote(location.split("boot_message=", 1)[1]) == "Готово: диск & ISO?"


def test_vm_create_iso_builds_virt_install(logged_in, data_dirs, monkeypatch):
    iso = data_dirs["iso"] / "debian.iso"
    iso.write_bytes(b"iso")
    captured = {}
    monkeypatch.setattr(app, "start_background_operation", lambda operation, cmd: captured.update(operation=operation, cmd=cmd))
    monkeypatch.setattr(app.network_core, "create_nat_network", lambda: {})
    form = {"name": "new-vm", "memory": 2048, "vcpus": 2, "disk_size": 20, "iso_path": str(iso), "source_type": "iso", "guest_arch": "x86_64", "boot_order": "auto", "network_mode": "nat"}
    response = logged_in.post("/vm/create", data=form, follow_redirects=False)
    assert response.status_code == 303, response.text[:500]
    cmd = captured["cmd"]
    assert cmd[0] == "virt-install"
    assert "--cdrom" in cmd and str(iso) in cmd
    assert "network=virtuality-nat,model=virtio" in cmd
    assert captured["operation"]["boot_order"] == "cdrom_disk"


def test_vm_create_validation_error(logged_in):
    form = {"name": "x", "memory": 2048, "vcpus": 2, "disk_size": 20, "source_type": "iso", "network_mode": "nat"}
    response = logged_in.post("/vm/create", data=form)
    assert response.status_code == 400


def test_iso_upload_and_delete(logged_in, data_dirs, monkeypatch):
    response = logged_in.post("/iso/upload", files={"iso_file": ("My Distro 1.0.iso", b"data", "application/octet-stream")}, follow_redirects=False)
    assert response.status_code == 303
    assert (data_dirs["iso"] / "My-Distro-1.0.iso").read_bytes() == b"data"
    logged_in.post("/iso/My-Distro-1.0.iso/delete", follow_redirects=False)
    assert not (data_dirs["iso"] / "My-Distro-1.0.iso").exists()


def test_iso_upload_rejects_other_types(logged_in):
    response = logged_in.post("/iso/upload", files={"iso_file": ("evil.sh", b"#!/bin/sh", "text/plain")})
    assert response.status_code == 400


def test_iso_delete_path_traversal(logged_in, data_dirs, tmp_path):
    victim = tmp_path / "victim.iso"
    victim.write_bytes(b"keep")
    logged_in.post("/iso/..%2Fvictim.iso/delete", follow_redirects=False)
    assert victim.exists()


def test_port_forward_add(logged_in, data_dirs):
    form = {"vm_name": "web01", "guest_ip": "192.168.100.51", "external_port": "2222", "guest_port": "22", "protocol": "tcp"}
    response = logged_in.post("/network/forward/add", data=form, follow_redirects=False)
    assert response.status_code == 303
    rules = (data_dirs["nft"] / "virtuality.nft").read_text()
    assert "tcp dport 2222 dnat to 192.168.100.51:22" in rules


def test_api_requires_auth(client):
    for path in ("/api/health", "/api/operations", "/live/status", "/api/logs"):
        assert client.get(path).status_code == 401


def test_live_status(logged_in):
    data = logged_in.get("/live/status").json()
    assert data["ok"] is True
    assert {vm["name"] for vm in data["vms"]} == {"web01", "db01"}
