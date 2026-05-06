#!/usr/bin/env python3
from pathlib import Path
import sys

app_dir = Path(sys.argv[1]).resolve().parent if len(sys.argv) > 1 else Path('/opt/virtuality/web')
templates_dir = app_dir / 'templates'
changed = []

guard_script = r'''

    function setUploadNavigationGuard(active) {
      window.virtualityUploadActive = Boolean(active);
      document.body.classList.toggle('upload-locked', window.virtualityUploadActive);
    }

    window.addEventListener('beforeunload', function (event) {
      if (!window.virtualityUploadActive) return;
      event.preventDefault();
      event.returnValue = 'Идёт загрузка файла. Переход остановит передачу.';
      return event.returnValue;
    });

    document.addEventListener('click', function (event) {
      if (!window.virtualityUploadActive) return;
      const target = event.target.closest('a, button, input[type="submit"]');
      if (!target) return;
      if (target.id === 'iso-upload-button' || target.id === 'disk-upload-button') return;
      event.preventDefault();
      event.stopPropagation();
      alert('Идёт загрузка файла. Дождись завершения или нажми «Отменить загрузку». Переход по меню сейчас заблокирован, чтобы файл не оборвался.');
    }, true);

    document.addEventListener('submit', function (event) {
      if (!window.virtualityUploadActive) return;
      if (event.target && (event.target.id === 'iso-upload-form' || event.target.id === 'disk-upload-form')) return;
      event.preventDefault();
      event.stopPropagation();
      alert('Идёт загрузка файла. Дождись завершения или нажми «Отменить загрузку».');
    }, true);
'''


def patch_template(path: Path, active_token: str, form_id: str) -> None:
    if not path.exists():
        return
    text = path.read_text()
    original = text

    if 'function setUploadNavigationGuard(active)' not in text:
        marker = '    function bytesText(bytes) {'
        if marker not in text:
            raise SystemExit(f'bytesText marker not found in {path}')
        text = text.replace(marker, guard_script + '\n' + marker, 1)

    text = text.replace('      uploadActive = active;\n', '      uploadActive = active;\n      setUploadNavigationGuard(active);\n')

    # During server-side post-processing after upload, file transfer is done, so page navigation is safe again.
    text = text.replace("          window.location.href = '/iso';", "          setUploadNavigationGuard(false);\n          window.location.href = '/iso';")
    text = text.replace("          window.location.href = '/disk-images';", "          setUploadNavigationGuard(false);\n          window.location.href = '/disk-images';")

    # Conversion polling is not an active browser upload anymore; allow navigation while backend operation continues.
    text = text.replace("      setUploadMode(false);\n      fileInput.disabled = true;", "      setUploadMode(false);\n      setUploadNavigationGuard(false);\n      fileInput.disabled = true;")

    if text != original:
        path.write_text(text)
        changed.append(str(path))

patch_template(templates_dir / 'iso.html', 'iso-upload-button', 'iso-upload-form')
patch_template(templates_dir / 'disk_images.html', 'disk-upload-button', 'disk-upload-form')

print('upload navigation guard patch applied:')
for item in changed or ['already applied']:
    print(f'- {item}')
