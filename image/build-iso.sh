#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality ISO builder
# Remasters the official Ubuntu Server 24.04 live ISO into a
# Virtuality installer: Ubuntu autoinstall + bundled Virtuality
# source and Python wheels + first boot setup service.
#
# Usage: image/build-iso.sh [options]    (see --help)
# Needs: xorriso, git, curl, python3 with pip, sha256sum.
# Does not need root.
# ==========================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_DIR="${REPO_ROOT}/image"

ARCH="amd64"
UBUNTU_SERIES="24.04"
UBUNTU_ISO=""
CACHE_DIR="${VIRTUALITY_ISO_CACHE:-${REPO_ROOT}/.cache/iso}"
OUTPUT_DIR="${REPO_ROOT}/dist"
GIT_REF="HEAD"
REPO_URL="https://github.com/viktor138irk/virtuality.git"
WEB_PORT="8088"
AUTO_UPDATE="1"
UNATTENDED="0"
USERNAME=""
PASSWORD_HASH=""
HOSTNAME_VALUE="virtuality"
LOCALE="en_US.UTF-8"
KEYBOARD="us"
TIMEZONE="Etc/UTC"
WITH_WHEELS="1"
TARGET_PYTHON="3.12"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --arch amd64|arm64         Target architecture (default: ${ARCH})
  --ubuntu-iso PATH          Use a local Ubuntu ${UBUNTU_SERIES} live-server ISO instead of downloading
  --cache-dir DIR            Where downloaded Ubuntu ISOs are kept (default: ${CACHE_DIR})
  --output DIR               Output directory (default: ${OUTPUT_DIR})
  --ref GIT_REF              Git ref of Virtuality to bundle (default: ${GIT_REF})
  --repo-url URL             Update remote configured on installed nodes (default: ${REPO_URL})
  --web-port PORT            Web panel port on installed nodes (default: ${WEB_PORT})
  --no-auto-update           Disable the nightly GitHub auto-update on installed nodes
  --locale LOCALE            Installer/system locale (default: ${LOCALE})
  --keyboard LAYOUT          Keyboard layout (default: ${KEYBOARD})
  --timezone TZ              System timezone (default: ${TIMEZONE})
  --no-wheels                Do not bundle Python wheels (first boot downloads them from PyPI)

Unattended mode (no questions at all, ERASES THE LARGEST DISK):
  --unattended               Fully automatic install
  --username NAME            Admin user (panel login), required with --unattended
  --password-hash HASH       crypt(3) hash, e.g. from: openssl passwd -6
  --hostname NAME            Hostname (default: ${HOSTNAME_VALUE})

Without --unattended the installer asks for network, disk and user
like a regular Ubuntu Server install; everything else is automated.
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "==> $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    --ubuntu-iso) UBUNTU_ISO="$2"; shift 2 ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    --output) OUTPUT_DIR="$2"; shift 2 ;;
    --ref) GIT_REF="$2"; shift 2 ;;
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --web-port) WEB_PORT="$2"; shift 2 ;;
    --no-auto-update) AUTO_UPDATE="0"; shift ;;
    --locale) LOCALE="$2"; shift 2 ;;
    --keyboard) KEYBOARD="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --no-wheels) WITH_WHEELS="0"; shift ;;
    --unattended) UNATTENDED="1"; shift ;;
    --username) USERNAME="$2"; shift 2 ;;
    --password-hash) PASSWORD_HASH="$2"; shift 2 ;;
    --hostname) HOSTNAME_VALUE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

case "$ARCH" in
  amd64) PY_PLATFORM_ARCH="x86_64"; MIRROR="https://releases.ubuntu.com/${UBUNTU_SERIES}" ;;
  arm64) PY_PLATFORM_ARCH="aarch64"; MIRROR="https://cdimage.ubuntu.com/releases/${UBUNTU_SERIES}/release" ;;
  *) die "unsupported arch: $ARCH (amd64 or arm64)" ;;
esac
[[ "$WEB_PORT" =~ ^[0-9]+$ ]] && (( WEB_PORT >= 1 && WEB_PORT <= 65535 )) || die "invalid --web-port: $WEB_PORT"
if [[ "$UNATTENDED" == "1" ]]; then
  [[ "$USERNAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "--unattended needs a valid --username"
  [[ "$PASSWORD_HASH" == \$* ]] || die "--unattended needs --password-hash (generate: openssl passwd -6)"
  [[ "$HOSTNAME_VALUE" =~ ^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$ ]] || die "invalid --hostname"
fi
for cmd in xorriso git curl python3 sha256sum md5sum; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd not found (Ubuntu: sudo apt install xorriso git curl python3-pip)"
done

VERSION="$(git -C "$REPO_ROOT" show "${GIT_REF}:VERSION" | tr -d '[:space:]')"
[[ -n "$VERSION" ]] || die "cannot read VERSION at ${GIT_REF}"
SUFFIX=""
[[ "$UNATTENDED" == "1" ]] && SUFFIX="-unattended"
OUTPUT_ISO="${OUTPUT_DIR}/virtuality-${VERSION}-ubuntu-${UBUNTU_SERIES}-${ARCH}${SUFFIX}.iso"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/virtuality-iso.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

# ---------------------------------------------------------- Ubuntu ISO
if [[ -z "$UBUNTU_ISO" ]]; then
  say "Resolving the latest Ubuntu ${UBUNTU_SERIES} live-server ISO for ${ARCH}"
  curl -fsSL --retry 3 "${MIRROR}/SHA256SUMS" -o "${WORK_DIR}/SHA256SUMS"
  iso_name="$(grep -oE "ubuntu-${UBUNTU_SERIES}(\.[0-9]+)*-live-server-${ARCH}\.iso" "${WORK_DIR}/SHA256SUMS" | sort -uV | tail -n1)"
  [[ -n "$iso_name" ]] || die "live-server ISO for ${ARCH} not found in ${MIRROR}/SHA256SUMS"
  expected_sha="$(awk -v n="*${iso_name}" '$2 == n || $2 == substr(n, 2) {print $1}' "${WORK_DIR}/SHA256SUMS")"
  UBUNTU_ISO="${CACHE_DIR}/${iso_name}"
  if [[ -f "$UBUNTU_ISO" ]] && echo "${expected_sha}  ${UBUNTU_ISO}" | sha256sum -c --status; then
    say "Using cached ${iso_name}"
  else
    say "Downloading ${iso_name}"
    curl -fL --retry 5 -C - -o "${UBUNTU_ISO}.part" "${MIRROR}/${iso_name}"
    mv "${UBUNTU_ISO}.part" "$UBUNTU_ISO"
    echo "${expected_sha}  ${UBUNTU_ISO}" | sha256sum -c --status || die "checksum mismatch for ${iso_name}"
  fi
  say "Checksum OK: ${expected_sha}"
fi
[[ -f "$UBUNTU_ISO" ]] || die "Ubuntu ISO not found: $UBUNTU_ISO"

# ---------------------------------------------------------- payload
PAYLOAD="${WORK_DIR}/virtuality"
mkdir -p "$PAYLOAD"
say "Bundling Virtuality ${VERSION} (${GIT_REF})"
git clone --quiet --no-hardlinks "$REPO_ROOT" "${PAYLOAD}/source"
git -C "${PAYLOAD}/source" checkout --quiet --detach "$(git -C "$REPO_ROOT" rev-parse "${GIT_REF}^{commit}")"
git -C "${PAYLOAD}/source" remote set-url origin "$REPO_URL"

if [[ "$WITH_WHEELS" == "1" ]]; then
  say "Downloading Python ${TARGET_PYTHON} wheels for ${PY_PLATFORM_ARCH}"
  platform_args=(--platform "manylinux2014_${PY_PLATFORM_ARCH}")
  for glibc_minor in 17 28 31 34 35 39; do
    platform_args+=(--platform "manylinux_2_${glibc_minor}_${PY_PLATFORM_ARCH}")
  done
  python3 -m pip download --quiet --disable-pip-version-check \
    -r "${PAYLOAD}/source/web/requirements.txt" -d "${PAYLOAD}/source/wheels" \
    --only-binary=:all: --implementation cp --python-version "$TARGET_PYTHON" \
    --abi "cp${TARGET_PYTHON/./}" --abi abi3 --abi none "${platform_args[@]}"
fi

cat > "${PAYLOAD}/image.env" <<EOF
# Written by image/build-iso.sh, read by scripts/virtuality_firstboot.sh
VIRTUALITY_IMAGE_VERSION=${VERSION}
VIRTUALITY_IMAGE_BUILT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
VIRTUALITY_WEB_PORT=${WEB_PORT}
VIRTUALITY_AUTO_UPDATE=${AUTO_UPDATE}
EOF
[[ "$UNATTENDED" == "1" ]] && echo "VIRTUALITY_USER=${USERNAME}" >> "${PAYLOAD}/image.env"
cp "${IMAGE_DIR}/files/virtuality-firstboot.service" "${PAYLOAD}/virtuality-firstboot.service"

# ---------------------------------------------------------- autoinstall
if [[ "$UNATTENDED" == "1" ]]; then
  interactive_sections="    []"
  identity="  identity:
    hostname: ${HOSTNAME_VALUE}
    username: ${USERNAME}
    realname: Virtuality Admin
    password: '${PASSWORD_HASH}'"
else
  interactive_sections="    - network
    - storage
    - identity"
  identity=""
fi
python3 - "${IMAGE_DIR}/autoinstall.yaml" "${WORK_DIR}/autoinstall.yaml" <<PYEOF
import sys
text = open(sys.argv[1]).read()
values = {
    "@@INTERACTIVE_SECTIONS@@": """${interactive_sections}""",
    "@@IDENTITY@@": """${identity}""",
    "@@LOCALE@@": "${LOCALE}",
    "@@KEYBOARD@@": "${KEYBOARD}",
    "@@TIMEZONE@@": "${TIMEZONE}",
}
for key, value in values.items():
    text = text.replace(key, value)
open(sys.argv[2], "w").write(text)
PYEOF
python3 -c 'import sys, yaml; data = yaml.safe_load(open(sys.argv[1])); assert data["autoinstall"]["version"] == 1' "${WORK_DIR}/autoinstall.yaml" 2>/dev/null \
  || python3 -c 'import sys, json; print("PyYAML not available, skipping YAML validation")'

# ---------------------------------------------------------- boot menu
xorriso -osirrox on -indev "$UBUNTU_ISO" \
  -extract /boot/grub/grub.cfg "${WORK_DIR}/grub.cfg" \
  -extract /md5sum.txt "${WORK_DIR}/md5sum.txt" >/dev/null 2>&1 || die "cannot read grub.cfg from $UBUNTU_ISO"
chmod u+w "${WORK_DIR}/grub.cfg" "${WORK_DIR}/md5sum.txt"
python3 - "${WORK_DIR}/grub.cfg" "$UNATTENDED" "$VERSION" <<'PYEOF'
import re
import sys

path, unattended, version = sys.argv[1], sys.argv[2] == "1", sys.argv[3]
text = open(path).read()
match = re.search(r'menuentry "[^"]*" \{\n(.*?)\n\}', text, re.S)
if not match:
    raise SystemExit("no menuentry found in grub.cfg")
body = match.group(1)
if "linux" not in body or "---" not in body:
    raise SystemExit("unexpected grub.cfg layout: " + body)
kernel_args = "autoinstall ---" if unattended else "---"
title = "Install Virtuality %s%s" % (version, " (UNATTENDED: erases disk)" if unattended else "")
entry = 'menuentry "%s" {\n%s\n}\n' % (title, body.replace("---", kernel_args, 1))
text = text[: match.start()] + entry + text[match.start():]
text = re.sub(r"^set timeout=\d+", "set default=0\nset timeout=10", text, count=1, flags=re.M)
text = text.replace('menuentry "Try or Install Ubuntu Server"', 'menuentry "Ubuntu Server (without Virtuality)"')
open(path, "w").write(text)
PYEOF
new_md5="$(md5sum "${WORK_DIR}/grub.cfg" | cut -d' ' -f1)"
sed -i "s|^[0-9a-f]\{32\}  \./boot/grub/grub.cfg$|${new_md5}  ./boot/grub/grub.cfg|" "${WORK_DIR}/md5sum.txt"

# ---------------------------------------------------------- repack
say "Writing ${OUTPUT_ISO}"
rm -f "$OUTPUT_ISO"
chmod -R a+rX,go-w "$PAYLOAD"
if ! xorriso -indev "$UBUNTU_ISO" -outdev "$OUTPUT_ISO" \
  -uid 0 -gid 0 \
  -map "$PAYLOAD" /virtuality \
  -map "${WORK_DIR}/autoinstall.yaml" /autoinstall.yaml \
  -map "${WORK_DIR}/grub.cfg" /boot/grub/grub.cfg \
  -map "${WORK_DIR}/md5sum.txt" /md5sum.txt \
  -boot_image any replay > "${WORK_DIR}/xorriso.log" 2>&1; then
  tail -n 30 "${WORK_DIR}/xorriso.log" >&2
  die "xorriso failed"
fi
[[ -s "$OUTPUT_ISO" ]] || die "xorriso did not produce $OUTPUT_ISO"

(cd "$OUTPUT_DIR" && sha256sum "$(basename "$OUTPUT_ISO")" > "$(basename "$OUTPUT_ISO").sha256")
say "Done: ${OUTPUT_ISO} ($(du -h "$OUTPUT_ISO" | cut -f1))"
say "SHA256: $(cut -d' ' -f1 "${OUTPUT_ISO}.sha256")"
