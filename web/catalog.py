"""Catalog of ready-to-run cloud images for the quick VM flow.

Each entry points to the vendor's official cloud image. Images boot in seconds
and are configured through cloud-init (user, password, SSH key) — no installer.
"""
from typing import Any

# osinfo ids must exist in osinfo-db shipped with Ubuntu 24.04+/Debian 13.
CLOUD_IMAGES: list[dict[str, Any]] = [
    {
        "id": "ubuntu-26.04",
        "title": "Ubuntu Server 26.04 LTS",
        "vendor": "Ubuntu",
        "hint": "Самая свежая LTS, поддержка до 2031 года. Рекомендуем.",
        "osinfo": "ubuntu24.04",
        "recommended": True,
        "login": "ubuntu",
        "arches": {
            "x86_64": {"url": "https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img", "sums": "https://cloud-images.ubuntu.com/resolute/current/SHA256SUMS", "algo": "sha256"},
            "aarch64": {"url": "https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-arm64.img", "sums": "https://cloud-images.ubuntu.com/resolute/current/SHA256SUMS", "algo": "sha256"},
        },
    },
    {
        "id": "ubuntu-24.04",
        "title": "Ubuntu Server 24.04 LTS",
        "vendor": "Ubuntu",
        "hint": "Проверенная LTS, поддержка до 2029 года.",
        "osinfo": "ubuntu24.04",
        "login": "ubuntu",
        "arches": {
            "x86_64": {"url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img", "sums": "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS", "algo": "sha256"},
            "aarch64": {"url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-arm64.img", "sums": "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS", "algo": "sha256"},
        },
    },
    {
        "id": "debian-13",
        "title": "Debian 13",
        "vendor": "Debian",
        "hint": "Лёгкий и стабильный. Хорош для домашних сервисов.",
        "osinfo": "debian13",
        "login": "debian",
        "arches": {
            "x86_64": {"url": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2", "sums": "https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS", "algo": "sha512"},
            "aarch64": {"url": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-arm64.qcow2", "sums": "https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS", "algo": "sha512"},
        },
    },
    {
        "id": "alpine-3.23",
        "title": "Alpine Linux 3.23",
        "vendor": "Alpine",
        "hint": "Очень маленький: 200 МБ на диске, стартует мгновенно.",
        "osinfo": "alpinelinux3.21",
        "login": "alpine",
        "arches": {
            "x86_64": {"url": "https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/cloud/nocloud_alpine-3.23.0-x86_64-uefi-cloudinit-r0.qcow2", "sums": "https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/cloud/nocloud_alpine-3.23.0-x86_64-uefi-cloudinit-r0.qcow2.sha512", "algo": "sha512", "uefi": True},
            "aarch64": {"url": "https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/cloud/nocloud_alpine-3.23.0-aarch64-uefi-cloudinit-r0.qcow2", "sums": "https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/cloud/nocloud_alpine-3.23.0-aarch64-uefi-cloudinit-r0.qcow2.sha512", "algo": "sha512", "uefi": True},
        },
    },
]

# OS families for the "install from ISO" flow. virt-install picks the right
# virtual hardware from osinfo: Windows gets SATA/e1000e (works without extra
# drivers), win11 additionally gets UEFI + TPM.
OS_TYPES: list[dict[str, Any]] = [
    {"id": "linux", "title": "Linux", "hint": "Ubuntu, Debian, Fedora и другие", "osinfo": "detect=on,require=off"},
    {"id": "windows11", "title": "Windows 11 / Server 2025", "hint": "Нужны UEFI и TPM — включим сами", "osinfo": "win11"},
    {"id": "windows10", "title": "Windows 10 / Server 2019–2022", "hint": "Классическая загрузка", "osinfo": "win10"},
    {"id": "other", "title": "Другая система", "hint": "FreeBSD, старые ОС, экзотика", "osinfo": "generic"},
]


def cloud_image(image_id: str) -> dict[str, Any] | None:
    return next((item for item in CLOUD_IMAGES if item["id"] == image_id), None)


def os_type(type_id: str) -> dict[str, Any]:
    return next((item for item in OS_TYPES if item["id"] == type_id), OS_TYPES[0])


def image_filename(entry: dict[str, Any], arch: str) -> str:
    url = entry["arches"][arch]["url"]
    return url.rsplit("/", 1)[-1]


def entry_for_filename(name: str) -> dict[str, Any] | None:
    """Catalog entry whose download has this file name (any architecture)."""
    for entry in CLOUD_IMAGES:
        for arch in entry["arches"].values():
            if arch["url"].rsplit("/", 1)[-1] == name:
                return entry
    return None
