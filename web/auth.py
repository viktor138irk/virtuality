"""Linux password verification for the Virtuality panel.

The stdlib `crypt` and `spwd` modules are deprecated in Python 3.11+ and
removed in 3.13 (Debian 13, Ubuntu 25.04+). This module uses them when they
exist and otherwise falls back to libcrypt via ctypes and /etc/shadow.
"""
import ctypes
import ctypes.util
import secrets
import threading
import time
import warnings
from pathlib import Path

SHADOW_FILE = Path("/etc/shadow")
LOCKED_HASHES = {"", "!", "*", "!!"}

_crypt_lock = threading.Lock()
_libcrypt = None

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import crypt as _stdlib_crypt  # type: ignore[import-not-found]
except ImportError:
    _stdlib_crypt = None

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import spwd as _stdlib_spwd  # type: ignore[import-not-found]
except ImportError:
    _stdlib_spwd = None


def _load_libcrypt():
    global _libcrypt
    if _libcrypt is None:
        name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
        lib = ctypes.CDLL(name)
        lib.crypt.restype = ctypes.c_char_p
        lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        _libcrypt = lib
    return _libcrypt


def crypt_hash(password: str, salt: str) -> str | None:
    # crypt(3) returns a pointer to a static buffer, so calls are serialized.
    with _crypt_lock:
        if _stdlib_crypt is not None:
            return _stdlib_crypt.crypt(password, salt)
        try:
            result = _load_libcrypt().crypt(password.encode(), salt.encode())
        except OSError:
            return None
        return result.decode() if result else None


def shadow_hash(username: str) -> str | None:
    if _stdlib_spwd is not None:
        try:
            return _stdlib_spwd.getspnam(username).sp_pwdp
        except (KeyError, PermissionError):
            return None
    try:
        for line in SHADOW_FILE.read_text().splitlines():
            parts = line.split(":")
            if len(parts) > 1 and parts[0] == username:
                return parts[1]
    except OSError:
        return None
    return None


def verify_password(username: str, password: str) -> bool:
    if not username or not password:
        return False
    stored_hash = shadow_hash(username)
    if stored_hash is None or stored_hash in LOCKED_HASHES or stored_hash.startswith("!"):
        return False
    computed = crypt_hash(password, stored_hash)
    if not computed:
        return False
    return secrets.compare_digest(computed, stored_hash)


class LoginThrottle:
    """In-memory brute-force protection keyed by client address."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300, lockout_seconds: int = 300):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        with self._lock:
            until = self._locked_until.get(key, 0.0)
            remaining = until - time.monotonic()
            if remaining <= 0:
                self._locked_until.pop(key, None)
                return 0
            return int(remaining) + 1

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [ts for ts in self._failures.get(key, []) if now - ts < self.window_seconds]
            recent.append(now)
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_seconds
                recent = []
            self._failures[key] = recent

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)
