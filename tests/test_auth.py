import pytest

import auth

# SHA-512 crypt hash of "s3cret" with salt "virtualitysalt".
SHA512_HASH = "$6$virtualitysalt$"


@pytest.fixture()
def password_hash():
    computed = auth.crypt_hash("s3cret", SHA512_HASH)
    assert computed and computed.startswith(SHA512_HASH)
    return computed


def write_shadow(tmp_path, monkeypatch, entries):
    shadow = tmp_path / "shadow"
    shadow.write_text("".join(f"{user}:{pw}:19000:0:99999:7:::\n" for user, pw in entries))
    monkeypatch.setattr(auth, "SHADOW_FILE", shadow)
    monkeypatch.setattr(auth, "_stdlib_spwd", None)


def test_libcrypt_fallback_matches(password_hash, monkeypatch):
    monkeypatch.setattr(auth, "_stdlib_crypt", None)
    assert auth.crypt_hash("s3cret", password_hash) == password_hash
    assert auth.crypt_hash("wrong", password_hash) != password_hash


def test_verify_password_with_shadow_file(password_hash, tmp_path, monkeypatch):
    write_shadow(tmp_path, monkeypatch, [("root", "*"), ("admin", password_hash), ("locked", "!" + password_hash)])
    monkeypatch.setattr(auth, "_stdlib_crypt", None)
    assert auth.verify_password("admin", "s3cret")
    assert not auth.verify_password("admin", "wrong")
    assert not auth.verify_password("admin", "")
    assert not auth.verify_password("root", "anything")
    assert not auth.verify_password("locked", "s3cret")
    assert not auth.verify_password("missing", "s3cret")


def test_throttle_locks_and_resets():
    throttle = auth.LoginThrottle(max_failures=3, window_seconds=60, lockout_seconds=60)
    for _ in range(2):
        throttle.record_failure("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") == 0
    throttle.record_failure("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") > 0
    assert throttle.retry_after("5.6.7.8") == 0
    throttle.record_success("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") == 0
