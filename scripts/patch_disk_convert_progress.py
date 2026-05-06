#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    print(f'WARN: app.py not found: {app_path}')
    raise SystemExit(0)

text = app_path.read_text()
changed = []
warnings = []

old_progress = '''def progress_from_line(current: int, line: str) -> int:
    low = line.lower()
    if "allocating" in low or "creating storage" in low:
        return max(current, 30)
    if "starting install" in low or "installing" in low:
        return max(current, 50)
    if "creating domain" in low:
        return max(current, 75)
    if "domain creation completed" in low or "installation continues" in low:
        return max(current, 90)
    return current
'''
new_progress = '''def progress_from_line(current: int, line: str) -> int:
    low = line.lower()
    convert_match = re.search(r"\\((\\d+(?:\\.\\d+)?)\\s*/\\s*100%\\)", line)
    if convert_match:
        return max(1, min(99, int(float(convert_match.group(1)))))
    percent_match = re.search(r"(\\d+(?:\\.\\d+)?)%", line)
    if "qemu-img" in low and percent_match:
        return max(1, min(99, int(float(percent_match.group(1)))))
    if "allocating" in low or "creating storage" in low:
        return max(current, 30)
    if "starting install" in low or "installing" in low:
        return max(current, 50)
    if "creating domain" in low:
        return max(current, 75)
    if "domain creation completed" in low or "installation continues" in low:
        return max(current, 90)
    return current
'''
if old_progress in text:
    text = text.replace(old_progress, new_progress, 1)
    changed.append('qemu-img percent parser added')
elif 'convert_match = re.search' in text:
    changed.append('qemu-img percent parser already present')
else:
    warnings.append('progress_from_line marker not found, progress parser skipped')

helpers = r'''

def qemu_img_format_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".qcow2":
        return "qcow2"
    return "raw"


def qcow2_name_for_upload(safe_name: str) -> str:
    base = Path(safe_name).stem
    return f"{base}.qcow2"


def start_disk_image_convert_operation(source_path: Path, target_path: Path, source_format: str) -> dict[str, Any]:
    operation_id = str(uuid.uuid4())
    cmd = ["qemu-img", "convert", "-p", "-f", source_format, "-O", "qcow2", str(source_path), str(target_path)]
    operation = {
        "id": operation_id,
        "type": "disk_image_convert",
        "title": f"Конвертация образа {source_path.name}",
        "status": "queued",
        "progress": 0,
        "message": "Ожидание конвертации образа",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "created_by": AUTH_USER,
        "source_path": str(source_path),
        "target_path": str(target_path),
        "source_format": source_format,
        "cmd": " ".join(cmd),
    }
    start_background_operation(operation, cmd)
    return operation
'''
if 'def start_disk_image_convert_operation(' not in text:
    marker = '\n\ndef valid_vm_name(name: str) -> bool:'
    if marker in text:
        text = text.replace(marker, helpers + marker, 1)
        changed.append('disk conversion operation helpers added')
    else:
        warnings.append('valid_vm_name marker not found, conversion helpers skipped')
else:
    changed.append('disk conversion operation helpers already present')

old_upload = r'''@app.post("/disk-images/upload", response_class=HTMLResponse)
def disk_image_upload(request: Request, image_file: UploadFile = File(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    safe_name = safe_disk_image_filename(image_file.filename or "")
    if not safe_name:
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": "Можно загружать только .img, .raw или .qcow2 файлы с безопасным именем."}, status_code=400)
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = DISK_IMAGES_DIR / safe_name
    if target.exists():
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": f"Образ уже существует: {safe_name}"}, status_code=400)
    tmp_target = DISK_IMAGES_DIR / f".{safe_name}.uploading"
    try:
        with tmp_target.open("wb") as out:
            shutil.copyfileobj(image_file.file, out)
        tmp_target.rename(target)
    except Exception as exc:
        tmp_target.unlink(missing_ok=True)
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": f"Ошибка загрузки образа: {exc}"}, status_code=500)
    return RedirectResponse(url="/disk-images", status_code=303)
'''
new_upload = r'''@app.post("/disk-images/upload")
def disk_image_upload(request: Request, image_file: UploadFile = File(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    safe_name = safe_disk_image_filename(image_file.filename or "")
    wants_json = "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "XMLHttpRequest"

    def disk_error(message: str, status_code: int = 400):
        if wants_json:
            return JSONResponse({"ok": False, "error": message}, status_code=status_code)
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": message}, status_code=status_code)

    if not safe_name:
        return disk_error("Можно загружать только .img, .raw или .qcow2 файлы с безопасным именем.")
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = DISK_IMAGES_DIR / safe_name
    final_qcow2 = DISK_IMAGES_DIR / qcow2_name_for_upload(safe_name)
    if target.exists() or final_qcow2.exists():
        return disk_error(f"Образ уже существует: {safe_name}")
    tmp_target = DISK_IMAGES_DIR / f".{safe_name}.uploading"
    try:
        with tmp_target.open("wb") as out:
            shutil.copyfileobj(image_file.file, out)
        tmp_target.rename(target)
        if target.suffix.lower() == ".qcow2":
            payload = {"ok": True, "mode": "ready", "name": target.name, "redirect": "/disk-images"}
        else:
            operation = start_disk_image_convert_operation(target, final_qcow2, qemu_img_format_for_path(target))
            payload = {"ok": True, "mode": "converting", "name": target.name, "target": final_qcow2.name, "operation_id": operation["id"], "redirect": "/disk-images"}
    except Exception as exc:
        tmp_target.unlink(missing_ok=True)
        return disk_error(f"Ошибка загрузки образа: {exc}", status_code=500)

    if wants_json:
        return JSONResponse(payload)
    return RedirectResponse(url="/disk-images", status_code=303)
'''
if old_upload in text:
    text = text.replace(old_upload, new_upload, 1)
    changed.append('disk upload returns JSON and starts conversion operation')
elif 'mode": "converting"' in text and ('start_disk_image_convert_operation' in text or 'start_disk_convert_operation' in text):
    changed.append('disk upload conversion already present')
else:
    warnings.append('disk_image_upload marker not found, upload conversion patch skipped')

app_path.write_text(text)
print('disk convert progress patch completed:')
for item in changed:
    print(f'- {item}')
for item in warnings:
    print(f'WARN: {item}')
raise SystemExit(0)
