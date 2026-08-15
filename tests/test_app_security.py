import app


def test_login_page_renders(auth_client):
    response = auth_client.get("/login", follow_redirects=False)
    # авторизованный пользователь уводится на дашборд
    assert response.status_code == 303


def test_unauthenticated_post_redirects_to_login():
    from fastapi.testclient import TestClient

    client = TestClient(app.app)
    response = client.post("/host/refresh", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_without_csrf_rejected(auth_client):
    response = auth_client.post("/iso/refresh", follow_redirects=False)
    assert response.status_code == 403


def test_post_with_wrong_csrf_rejected(auth_client):
    response = auth_client.post("/iso/refresh", data={"csrf_token": "wrong"}, follow_redirects=False)
    assert response.status_code == 403


def test_post_with_valid_csrf_accepted(auth_client):
    response = auth_client.post("/iso/refresh", data={"csrf_token": "test-csrf-token"}, follow_redirects=False)
    assert response.status_code == 303


def test_dashboard_renders_with_csrf_inputs(auth_client):
    response = auth_client.get("/")
    assert response.status_code == 200
    assert 'name="csrf_token"' in response.text
    assert "cpu-spark" in response.text


def test_backups_page_renders(auth_client):
    response = auth_client.get("/backups")
    assert response.status_code == 200


def test_cloud_images_page_renders(auth_client):
    response = auth_client.get("/cloud-images")
    assert response.status_code == 200


def test_login_rate_limit():
    ip = "203.0.113.7"
    app.reset_login_failures(ip)
    assert app.login_block_remaining(ip) == 0
    for _ in range(app.LOGIN_MAX_FAILURES):
        app.register_login_failure(ip)
    assert app.login_block_remaining(ip) > 0
    app.reset_login_failures(ip)
    assert app.login_block_remaining(ip) == 0


def test_session_token_roundtrip():
    token = app.serializer.dumps({"user": app.AUTH_USER, "csrf": "abc"})
    assert app.user_from_session_token(token) == app.AUTH_USER
    assert app.user_from_session_token("garbage") is None
    assert app.user_from_session_token(None) is None


def test_template_vm_cannot_start(auth_client, monkeypatch):
    monkeypatch.setattr(app, "vm_exists", lambda name: True)
    app.set_vm_template("tpl-vm", True)
    response = auth_client.post("/vm/tpl-vm/start", data={"csrf_token": "test-csrf-token"}, follow_redirects=False)
    assert response.status_code == 303
    assert "clone_error" in response.headers["location"]


def test_metrics_endpoint(auth_client):
    response = auth_client.get("/live/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"]
    assert "series" in payload
