import cloud_images


def test_catalog_entries_consistent():
    keys = [entry["key"] for entry in cloud_images.CATALOG]
    assert len(keys) == len(set(keys))
    for entry in cloud_images.CATALOG:
        assert entry["arch"] in ("x86_64", "aarch64")
        assert entry["url"].startswith("https://")
        assert cloud_images.safe_image_filename(entry["filename"])


def test_catalog_entry_lookup():
    assert cloud_images.catalog_entry("ubuntu-24.04-x86_64") is not None
    assert cloud_images.catalog_entry("nope") is None


def test_catalog_for_arch_puts_native_first():
    entries = cloud_images.catalog_for_arch("aarch64")
    natives = [e["native"] for e in entries]
    assert natives == sorted(natives, reverse=True)
    assert entries[0]["arch"] == "aarch64"
    entries_x86 = cloud_images.catalog_for_arch("x86_64")
    assert entries_x86[0]["arch"] == "x86_64"


def test_safe_image_filename():
    assert cloud_images.safe_image_filename("ubuntu-24.04-x86_64.qcow2")
    assert cloud_images.safe_image_filename("img.img")
    assert cloud_images.safe_image_filename("../../etc/passwd") is None
    assert cloud_images.safe_image_filename("../escape.qcow2") == "escape.qcow2"  # basename-санитизация
    assert cloud_images.safe_image_filename("evil.iso") is None


def test_image_path_traversal_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_images, "CLOUD_IMAGES_DIR", tmp_path)
    assert cloud_images.image_path_by_name("ok.qcow2") == tmp_path / "ok.qcow2"
    # Каталоги отбрасываются до basename — результат всегда внутри CLOUD_IMAGES_DIR
    assert cloud_images.image_path_by_name("../escape.qcow2") == tmp_path / "escape.qcow2"
    assert cloud_images.image_path_by_name("bad name.qcow2") is None


def test_valid_cloud_username():
    assert cloud_images.valid_cloud_username("admin")
    assert cloud_images.valid_cloud_username("_svc-user")
    assert not cloud_images.valid_cloud_username("Admin")
    assert not cloud_images.valid_cloud_username("1abc")
    assert not cloud_images.valid_cloud_username("")


def test_valid_ssh_key():
    assert cloud_images.valid_ssh_key("")  # ключ необязателен
    assert cloud_images.valid_ssh_key("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample user@host")
    assert cloud_images.valid_ssh_key("ssh-rsa AAAAB3NzaC1yc2E=")
    assert not cloud_images.valid_ssh_key("not a key")
    assert not cloud_images.valid_ssh_key("ssh-ed25519")


def test_build_user_data_with_password():
    data = cloud_images.build_user_data("vm1", "admin", 'p"ss', "")
    assert data.startswith("#cloud-config")
    assert "hostname: vm1" in data
    assert "name: admin" in data
    assert "ssh_pwauth: true" in data
    assert '"p\\"ss"' in data  # пароль экранирован как JSON/YAML-строка
    assert "ssh_authorized_keys" not in data


def test_build_user_data_with_key_only():
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample user@host"
    data = cloud_images.build_user_data("vm1", "admin", "", key)
    assert "ssh_pwauth: false" in data
    assert key in data
    assert "chpasswd" not in data


def test_build_meta_data():
    meta = cloud_images.build_meta_data("vm1")
    assert "instance-id: virtuality-vm1" in meta
    assert "local-hostname: vm1" in meta
