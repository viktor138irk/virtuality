#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

if 'import zipfile' not in text:
    text = text.replace('import uuid\n', 'import uuid\nimport lzma\nimport tarfile\nimport zipfile\n', 1)
    changed.append('archive imports added')
elif 'import lzma' not in text:
    text = text.replace('import tarfile\n', 'import lzma\nimport tarfile\n', 1) if 'import tarfile\n' in text else text.replace('import zipfile\n', 'import lzma\nimport zipfile\n', 1)
    changed.append('lzma import added')
else:
    changed.append('archive imports already present')

helpers = r'''

def safe_disk_upload_filename(filename: str) -> str | None:
    original = Path(filename or "").name.strip()
    lower = original.lower()
    if lower.endswith(".tar.gz"):
        suffix = ".tar.gz"
        stem = original[:-7]
    elif lower.endswith(".img.xz"):
        suffix = ".img.xz"
        stem = original[:-7]
    elif lower.endswith(".tgz"):
        suffix = ".tgz"
        stem = original[:-4]
    else:
        suffix = Path(original).suffix.lower()
        stem = Path(original).stem
    if suffix not in (".img", ".raw", ".qcow2", ".img.xz", ".zip", ".tar.gz", ".tgz"):
        return None
    stem = re.sub(r"\s+", "-", stem.strip())
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem)
    stem = stem.strip("._-")[:120]
    if not stem:
        stem = "virtuality-disk"
    return f"{stem}{suffix}"


def disk_upload_is_archive(name: str) -> bool:
    lower = str(name or "").lower()
    return lower.endswith((".zip", ".tar.gz", ".tgz"))


def disk_upload_is_xz_image(name: str) -> bool:
    return str(name or "").lower().endswith(".img.xz")


def disk_upload_is_image(name: str) -> bool:
    lower = str(name or "").lower()
    return lower.endswith((".img", ".raw", ".qcow2", ".img.xz"))


def archive_member_is_safe(name: str) -> bool:
    if not name:
        return False
    p = Path(name)
    if p.is_absolute():
        return False
    return ".." not in p.parts


def archive_member_basename(name: str) -> str | None:
    return safe_disk_image_filename(Path(name).name)


def unique_disk_image_path(name: str) -> Path:
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe = safe_disk_image_filename(name)
    if not safe:
        raise ValueError("Некорректное имя образа диска")
    target = DISK_IMAGES_DIR / safe
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 1000):
        candidate = DISK_IMAGES_DIR / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("Не удалось подобрать свободное имя файла")


def find_archive_disk_members(archive_path: Path) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    lower = archive_path.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir() or not archive_member_is_safe(info.filename):
                    continue
                safe_name = archive_member_basename(info.filename)
                if safe_name:
                    members.append({"kind": "zip", "name": info.filename, "safe_name": safe_name, "size": int(info.file_size)})
    elif lower.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as archive:
            for info in archive.getmembers():
                if not info.isfile() or not archive_member_is_safe(info.name):
                    continue
                safe_name = archive_member_basename(info.name)
                if safe_name:
                    members.append({"kind": "tar", "name": info.name, "safe_name": safe_name, "size": int(info.size)})
    else:
        raise ValueError("Поддерживаются только .zip, .tar.gz и .tgz")
    return sorted(members, key=lambda item: item.get("size", 0), reverse=True)


def extract_disk_archive(archive_path: Path) -> list[Path]:
    members = find_archive_disk_members(archive_path)
    if not members:
        raise ValueError("В архиве не найдено .img, .raw или .qcow2 файлов")
    extracted: list[Path] = []
    lower = archive_path.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for member in members:
                target = unique_disk_image_path(member["safe_name"])
                with archive.open(member["name"], "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                extracted.append(target)
    else:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in members:
                file_obj = archive.extractfile(member["name"])
                if file_obj is None:
                    continue
                target = unique_disk_image_path(member["safe_name"])
                with file_obj as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                extracted.append(target)
    return extracted


def extract_xz_disk_image(compressed_path: Path, safe_name: str | None = None) -> Path:
    source_name = safe_name or compressed_path.name
    if not source_name.lower().endswith(".img.xz"):
        raise ValueError("Поддерживаются только сжатые образы .img.xz")
    raw_name = source_name[:-3]
    target = unique_disk_image_path(raw_name)
    try:
        with lzma.open(compressed_path, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def disk_convert_target_path(source_path: Path) -> Path:
    if source_path.suffix.lower() == ".qcow2":
        return source_path
    target = source_path.with_suffix(".qcow2")
    if not target.exists():
        return target
    for index in range(1, 1000):
        candidate = source_path.with_name(f"{source_path.stem}-{index}.qcow2")
        if not candidate.exists():
            return candidate
    raise ValueError("Не удалось подобрать имя qcow2 для конвертации")


def disk_convert_progress(line: str, current: int) -> int:
    match = re.search(r"\((\d+(?:\.\d+)?)/100%\)", line or "")
    if match:
        return max(current, int(float(match.group(1))))
    match = re.search(r"(\d+(?:\.\d+)?)%", line or "")
    if match:
        return max(current, int(float(match.group(1))))
    return current


def run_disk_convert_worker(operation_id: str, source_path: str, target_path: str) -> None:
    operation = read_operation(operation_id)
    if not operation:
        return
    source = Path(source_path)
    target = Path(target_path)
    update_operation(operation, status="running", progress=1, message=f"Конвертация {source.name} в qcow2", started_at=utc_now())
    cmd = ["qemu-img", "convert", "-p", "-f", disk_image_format(source), "-O", "qcow2", str(source), str(target)]
    append_operation_log(operation_id, "Запуск конвертации:")
    append_operation_log(operation_id, " ".join(cmd))
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        progress = 1
        if process.stdout:
            for line in process.stdout:
                append_operation_log(operation_id, line)
                progress = disk_convert_progress(line, progress)
                fresh = read_operation(operation_id) or operation
                update_operation(fresh, progress=progress, message=f"Конвертация {source.name}: {progress}%")
        exit_code = process.wait()
        fresh = read_operation(operation_id) or operation
        if exit_code == 0:
            append_operation_log(operation_id, f"Конвертация завершена: {target}")
            update_operation(fresh, status="success", progress=100, exit_code=exit_code, message=f"Готово: {target.name}", finished_at=utc_now(), target_path=str(target))
        else:
            append_operation_log(operation_id, f"qemu-img завершился с ошибкой. Exit code: {exit_code}")
            target.unlink(missing_ok=True)
            update_operation(fresh, status="error", progress=100, exit_code=exit_code, message=f"qemu-img завершился с ошибкой: {exit_code}", finished_at=utc_now())
    except Exception as exc:
        fresh = read_operation(operation_id) or operation
        append_operation_log(operation_id, f"Ошибка конвертации: {exc}")
        target.unlink(missing_ok=True)
        update_operation(fresh, status="error", progress=100, exit_code=-1, message=str(exc), finished_at=utc_now())


def start_disk_convert_operation(source_path: Path) -> dict[str, Any] | None:
    if source_path.suffix.lower() == ".qcow2":
        return None
    target_path = disk_convert_target_path(source_path)
    operation_id = str(uuid.uuid4())
    operation = {
        "id": operation_id,
        "type": "disk_convert",
        "title": f"Конвертация {source_path.name}",
        "status": "queued",
        "progress": 0,
        "message": "Конвертация поставлена в очередь",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "created_by": AUTH_USER,
        "source_path": str(source_path),
        "target_path": str(target_path),
    }
    write_operation(operation)
    append_operation_log(operation_id, "Операция поставлена в очередь.")
    threading.Thread(target=run_disk_convert_worker, args=(operation_id, str(source_path), str(target_path)), daemon=True).start()
    return operation


def disk_upload_response(request: Request, payload: dict[str, Any]):
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
        return JSONResponse(payload)
    return RedirectResponse(url="/disk-images", status_code=303)
'''

if 'def safe_disk_upload_filename(' not in text:
    marker = '\n\ndef valid_vm_name(name: str) -> bool:'
    if marker not in text:
        marker = '\n\ndef bridge_exists(name: str) -> bool:'
    if marker not in text:
        raise SystemExit('helper insert marker not found')
    text = text.replace(marker, helpers + marker, 1)
    changed.append('disk archive helpers added')
else:
    pattern = r"\n\ndef safe_disk_upload_filename\(filename: str\).*?\n\ndef disk_upload_response\(request: Request, payload: dict\[str, Any\]\):.*?\n    return RedirectResponse\(url=\"/disk-images\", status_code=303\)\n"
    text, count = re.subn(pattern, helpers, text, count=1, flags=re.S)
    changed.append('disk archive helpers replaced with img.xz support' if count else 'disk archive helpers already present')

old_route_start = text.find('@app.post("/disk-images/upload"')
if old_route_start == -1:
    raise SystemExit('disk image upload route not found')
next_route = text.find('\n\n@app.post("/disk-images/{name}/delete")', old_route_start)
if next_route == -1:
    raise SystemExit('disk image delete route marker not found')
new_route = r'''@app.post("/disk-images/upload", response_class=HTMLResponse)
def disk_image_upload(request: Request, image_file: UploadFile = File(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    safe_name = safe_disk_upload_filename(image_file.filename or "")
    if not safe_name:
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": "Можно загружать только .img, .raw, .qcow2, .img.xz, .zip, .tar.gz или .tgz файлы."}, status_code=400)
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    tmp_target = DISK_IMAGES_DIR / f".{safe_name}.uploading"
    saved_paths: list[Path] = []
    try:
        with tmp_target.open("wb") as out:
            shutil.copyfileobj(image_file.file, out, length=1024 * 1024)
        if disk_upload_is_archive(safe_name):
            saved_paths = extract_disk_archive(tmp_target)
            tmp_target.unlink(missing_ok=True)
        elif disk_upload_is_xz_image(safe_name):
            saved_paths = [extract_xz_disk_image(tmp_target, safe_name)]
            tmp_target.unlink(missing_ok=True)
        else:
            target = unique_disk_image_path(safe_name)
            tmp_target.rename(target)
            saved_paths = [target]
    except Exception as exc:
        tmp_target.unlink(missing_ok=True)
        for path in saved_paths:
            path.unlink(missing_ok=True)
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": f"Ошибка загрузки/распаковки образа: {exc}"}, status_code=500)

    operations = []
    for path in saved_paths:
        operation = start_disk_convert_operation(path)
        if operation:
            operations.append(operation)

    payload = {
        "ok": True,
        "mode": "converting" if operations else "ready",
        "operation_id": operations[0]["id"] if operations else None,
        "operation_ids": [op["id"] for op in operations],
        "files": [path.name for path in saved_paths],
        "message": f"Загружено файлов: {len(saved_paths)}. Конвертаций запущено: {len(operations)}.",
    }
    return disk_upload_response(request, payload)
'''
text = text[:old_route_start] + new_route + text[next_route:]
changed.append('disk image upload route supports img.xz and archives')

app_path.write_text(text)
print('disk archive import patch applied:')
for item in changed:
    print(f'- {item}')
