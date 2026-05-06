#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

safe_name_helper = r'''

def safe_upload_filename(filename: str, allowed_suffixes: tuple[str, ...], fallback_prefix: str) -> str | None:
    original = Path(filename or "").name.strip()
    suffix = Path(original).suffix.lower()
    if suffix not in allowed_suffixes:
        return None
    stem = Path(original).stem.strip()
    stem = re.sub(r"\s+", "-", stem)
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem)
    stem = stem.strip("._-")
    if not stem:
        stem = fallback_prefix
    stem = stem[:120]
    name = f"{stem}{suffix}"
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,180}\.[a-zA-Z0-9]{2,8}", name):
        name = f"{fallback_prefix}{suffix}"
    return name
'''

if 'def safe_upload_filename(' not in text:
    marker = '\n\ndef safe_iso_filename(filename: str) -> str | None:'
    if marker not in text:
        raise SystemExit('safe_iso_filename marker not found')
    text = text.replace(marker, safe_name_helper + marker, 1)
    changed.append('safe upload filename helper added')
else:
    changed.append('safe upload filename helper already present')

old_iso = r'''def safe_iso_filename(filename: str) -> str | None:
    name = Path(filename or "").name.strip().replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    if not name.lower().endswith(".iso"):
        return None
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,180}\.iso", name):
        return None
    return name
'''
new_iso = r'''def safe_iso_filename(filename: str) -> str | None:
    return safe_upload_filename(filename, (".iso",), "virtuality-iso")
'''
if old_iso in text:
    text = text.replace(old_iso, new_iso, 1)
    changed.append('ISO filename sanitizer relaxed')
elif 'return safe_upload_filename(filename, (".iso",), "virtuality-iso")' in text:
    changed.append('ISO filename sanitizer already relaxed')
else:
    changed.append('ISO filename sanitizer replacement skipped')

old_disk = r'''def safe_disk_image_filename(filename: str) -> str | None:
    name = Path(filename or "").name.strip().replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    if not name.lower().endswith((".img", ".raw", ".qcow2")):
        return None
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,180}\.(img|raw|qcow2)", name, re.IGNORECASE):
        return None
    return name
'''
new_disk = r'''def safe_disk_image_filename(filename: str) -> str | None:
    return safe_upload_filename(filename, (".img", ".raw", ".qcow2"), "virtuality-disk")
'''
if old_disk in text:
    text = text.replace(old_disk, new_disk, 1)
    changed.append('disk image filename sanitizer relaxed')
elif 'return safe_upload_filename(filename, (".img", ".raw", ".qcow2"), "virtuality-disk")' in text:
    changed.append('disk image filename sanitizer already relaxed')
else:
    changed.append('disk image filename sanitizer not found yet')

# Make upload errors more explicit when temp/filesystem fails after multipart parsing.
text = text.replace(
    '"error": f"Ошибка загрузки ISO: {exc}"}, status_code=500)',
    '"error": f"Ошибка загрузки ISO: {exc}. Проверь свободное место, права на /var/lib/virtuality/iso и временный каталог /var/lib/virtuality/tmp."}, status_code=500)'
)
text = text.replace(
    '"error": f"Ошибка загрузки образа: {exc}"}, status_code=500)',
    '"error": f"Ошибка загрузки образа: {exc}. Проверь свободное место, права на /var/lib/virtuality/disk-images и временный каталог /var/lib/virtuality/tmp."}, status_code=500)'
)
changed.append('upload error messages clarified')

app_path.write_text(text)
print('upload compatibility patch applied:')
for item in changed:
    print(f'- {item}')
