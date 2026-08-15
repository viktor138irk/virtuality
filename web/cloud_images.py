#!/usr/bin/env python3
"""Каталог cloud-образов и генерация cloud-init seed для быстрых VM."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

CLOUD_IMAGES_DIR = Path("/var/lib/virtuality/cloud-images")

# Официальные cloud-образы. Для каждого дистрибутива — обе архитектуры,
# чтобы Raspberry/Orange Pi качали arm64 без ручного выбора URL.
CATALOG: list[dict[str, str]] = [
    {
        "key": "ubuntu-24.04-x86_64",
        "label": "Ubuntu Server 24.04 LTS",
        "arch": "x86_64",
        "filename": "ubuntu-24.04-x86_64.qcow2",
        "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "default_user": "ubuntu",
    },
    {
        "key": "ubuntu-24.04-aarch64",
        "label": "Ubuntu Server 24.04 LTS",
        "arch": "aarch64",
        "filename": "ubuntu-24.04-aarch64.qcow2",
        "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-arm64.img",
        "default_user": "ubuntu",
    },
    {
        "key": "ubuntu-22.04-x86_64",
        "label": "Ubuntu Server 22.04 LTS",
        "arch": "x86_64",
        "filename": "ubuntu-22.04-x86_64.qcow2",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        "default_user": "ubuntu",
    },
    {
        "key": "ubuntu-22.04-aarch64",
        "label": "Ubuntu Server 22.04 LTS",
        "arch": "aarch64",
        "filename": "ubuntu-22.04-aarch64.qcow2",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-arm64.img",
        "default_user": "ubuntu",
    },
    {
        "key": "debian-12-x86_64",
        "label": "Debian 12 Bookworm",
        "arch": "x86_64",
        "filename": "debian-12-x86_64.qcow2",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
        "default_user": "debian",
    },
    {
        "key": "debian-12-aarch64",
        "label": "Debian 12 Bookworm",
        "arch": "aarch64",
        "filename": "debian-12-aarch64.qcow2",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-arm64.qcow2",
        "default_user": "debian",
    },
]


def catalog_entry(key: str) -> dict[str, str] | None:
    for entry in CATALOG:
        if entry["key"] == key:
            return entry
    return None


def catalog_for_arch(host_arch: str) -> list[dict[str, Any]]:
    """Каталог с отметкой native — образы родной архитектуры хоста показываются первыми."""
    native = "aarch64" if host_arch in ("aarch64", "arm64") else "x86_64"
    entries = [dict(entry, native=entry["arch"] == native, downloaded=(CLOUD_IMAGES_DIR / entry["filename"]).exists()) for entry in CATALOG]
    return sorted(entries, key=lambda item: (not item["native"], item["label"]))


def safe_image_filename(filename: str) -> str | None:
    name = Path(filename or "").name.strip()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,180}\.(qcow2|img)", name):
        return None
    return name


def image_path_by_name(name: str) -> Path | None:
    safe_name = safe_image_filename(name)
    if not safe_name:
        return None
    path = (CLOUD_IMAGES_DIR / safe_name).resolve()
    if CLOUD_IMAGES_DIR.resolve() not in path.parents:
        return None
    return path


def list_images() -> list[dict[str, str]]:
    CLOUD_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for item in sorted(CLOUD_IMAGES_DIR.glob("*")):
        if item.suffix.lower() not in (".qcow2", ".img") or item.name.endswith(".part"):
            continue
        try:
            stat = item.stat()
            size_mb = stat.st_size / 1024 / 1024
            updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size_mb = 0
            updated = "unknown"
        known = next((entry for entry in CATALOG if entry["filename"] == item.name), None)
        files.append({
            "name": item.name,
            "path": str(item),
            "size": f"{size_mb:.0f} MB",
            "updated": updated,
            "label": known["label"] if known else item.stem,
            "arch": known["arch"] if known else ("aarch64" if "arm64" in item.name or "aarch64" in item.name else "x86_64"),
            "default_user": known["default_user"] if known else "user",
        })
    return files


def valid_cloud_username(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value or ""))


def valid_ssh_key(value: str) -> bool:
    if not value:
        return True
    return bool(re.fullmatch(r"(ssh-(rsa|ed25519|dss)|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/=]+( [^\r\n]{0,256})?", value.strip()))


def build_user_data(hostname: str, username: str, password: str, ssh_key: str) -> str:
    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        "manage_etc_hosts: true",
        f"ssh_pwauth: {'true' if password else 'false'}",
        "users:",
        f"  - name: {username}",
        "    groups: [sudo]",
        "    shell: /bin/bash",
        '    sudo: "ALL=(ALL) NOPASSWD:ALL"',
        "    lock_passwd: false",
    ]
    if ssh_key:
        lines.append("    ssh_authorized_keys:")
        lines.append(f"      - {ssh_key.strip()}")
    if password:
        lines += [
            "chpasswd:",
            "  expire: false",
            "  users:",
            f"    - name: {username}",
            f"      password: {json.dumps(password, ensure_ascii=False)}",
            "      type: text",
        ]
    lines.append("package_update: false")
    return "\n".join(lines) + "\n"


def build_meta_data(name: str) -> str:
    return f"instance-id: virtuality-{name}\nlocal-hostname: {name}\n"
