"""cloud-init seed for machines created from ready-made (cloud) images.

Cloud images ship without a password: the first login is configured here —
user, password and/or SSH key — and virt-install attaches the seed as a small
CD-ROM (`--cloud-init user-data=…,meta-data=…`)."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import catalog
from core import STORAGE_DIR

USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SSH_KEY_RE = re.compile(r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp(256|384|521)|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com) [A-Za-z0-9+/=]+( .*)?$")
RESERVED_USERS = {"root", "daemon", "bin", "sys", "nobody", "systemd-network", "syslog", "messagebus"}


def default_user(image_name: str) -> str:
    entry = catalog.entry_for_filename(image_name)
    return str(entry.get("login") or "admin") if entry else "admin"


def valid_user(name: str) -> bool:
    return bool(USER_RE.fullmatch(name or "")) and name not in RESERVED_USERS


def parse_ssh_keys(text: str) -> list[str] | None:
    """Public keys, one per line. None when a line is not a public key."""
    keys = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not SSH_KEY_RE.match(line) or len(line) > 4096:
            return None
        keys.append(line)
    return keys


def hash_password(password: str) -> str:
    """SHA-512 crypt hash: the seed CD-ROM stays readable on the server, so the password is never stored as text."""
    try:
        result = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n", capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"openssl недоступен: {exc}") from exc
    digest = result.stdout.strip()
    if result.returncode != 0 or not digest.startswith("$6$"):
        raise RuntimeError(result.stderr.strip() or "openssl passwd завершился с ошибкой")
    return digest


def build_user_data(hostname: str, user: str, password_hash: str, ssh_keys: list[str]) -> str:
    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        "manage_etc_hosts: true",
        "users:",
        f"  - name: {user}",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    groups: [sudo, wheel, adm]",
        "    shell: /bin/bash",
        "    lock_passwd: false",
    ]
    if password_hash:
        lines.append(f"    passwd: '{password_hash}'")
    if ssh_keys:
        lines.append("    ssh_authorized_keys:")
        lines += [f"      - {key}" for key in ssh_keys]
    lines += [
        f"ssh_pwauth: {'true' if password_hash else 'false'}",
        "chpasswd:",
        "  expire: false",
        "disable_root: true",
        "growpart:",
        "  mode: auto",
        "  devices: ['/']",
        "resize_rootfs: true",
        "package_update: false",
    ]
    return "\n".join(lines) + "\n"


def build_meta_data(name: str) -> str:
    return f"instance-id: virtuality-{name}\nlocal-hostname: {name}\n"


def write_seed(name: str, user_data: str, meta_data: str) -> tuple[Path, Path]:
    """Files for virt-install --cloud-init; the folder is removed once the machine is created."""
    root = STORAGE_DIR / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"cloudinit-{name}-", dir=str(root)))
    user_file, meta_file = folder / "user-data", folder / "meta-data"
    user_file.write_text(user_data)
    meta_file.write_text(meta_data)
    user_file.chmod(0o600)
    return user_file, meta_file


def remove_seed(user_file: Path | str) -> None:
    folder = Path(user_file).parent
    if folder.name.startswith("cloudinit-"):
        shutil.rmtree(folder, ignore_errors=True)
