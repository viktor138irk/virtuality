#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
app_dir = app_path.resolve().parent if app_path.exists() else Path('/opt/virtuality/web')
template_path = app_dir / 'templates' / 'vm_detail.html'
changed = []

if not template_path.exists():
    print(f'legacy boot order cleanup skipped: vm_detail.html not found: {template_path}')
    raise SystemExit(0)

tpl = template_path.read_text()
original = tpl

legacy_pattern = re.compile(
    r'\n\s*<section class="card">\s*\n'
    r'\s*<div class="card-head">\s*\n'
    r'\s*<h2>Порядок загрузки</h2>\s*\n'
    r'\s*<span class="pill">boot order</span>\s*\n'
    r'.*?'
    r'\s*</section>\s*\n',
    re.DOTALL,
)

tpl, count = legacy_pattern.subn('\n', tpl)
if count:
    changed.append(f'legacy select boot-order card removed: {count}')

# Safety cleanup for any accidental duplicated plain select card without the old pill text.
plain_select_pattern = re.compile(
    r'\n\s*<section class="card">\s*\n'
    r'\s*<div class="card-head">\s*\n'
    r'\s*<h2>Порядок загрузки</h2>.*?'
    r'<select name="boot_order" required>.*?'
    r'\s*</section>\s*\n',
    re.DOTALL,
)

tpl, count2 = plain_select_pattern.subn('\n', tpl)
if count2:
    changed.append(f'legacy plain select boot-order card removed: {count2}')

if tpl != original:
    template_path.write_text(tpl)
else:
    changed.append('legacy boot-order select card not found')

print('legacy boot order cleanup patch applied:')
for item in changed:
    print(f'- {item}')
