#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
templates_dir = app_path.parent / 'templates'
if not templates_dir.exists():
    raise SystemExit(f'templates dir not found: {templates_dir}')

skip = {'login.html', 'console.html', '_sidebar.html'}
changed = []
skipped = []

for path in sorted(templates_dir.glob('*.html')):
    if path.name in skip:
        skipped.append(f'{path.name}: skipped system/template')
        continue
    text = path.read_text()
    if '{% include "_sidebar.html" %}' in text or "{% include '_sidebar.html' %}" in text:
        skipped.append(f'{path.name}: already has sidebar')
        continue
    if '<div class="v-layout">' in text:
        skipped.append(f'{path.name}: already v-layout')
        continue
    if '<div class="shell">' not in text:
        skipped.append(f'{path.name}: no shell')
        continue

    text = text.replace(
        '<body>\n  <div class="shell">',
        '<body>\n  <div class="v-layout">\n    {% include "_sidebar.html" %}\n    <main class="v-main">\n      <div class="shell v-shell-embedded">',
        1,
    )

    # Close shell/main/layout before scripts when page has JS block.
    marker = '\n\n  <script>'
    if marker in text:
        idx = text.rfind('\n  </div>', 0, text.find(marker))
        if idx != -1:
            text = text[:idx] + '\n      </div>\n    </main>\n  </div>' + text[idx + len('\n  </div>'):]
        else:
            skipped.append(f'{path.name}: close marker before script not found')
            continue
    else:
        marker2 = '\n</body>'
        idx = text.rfind('\n  </div>', 0, text.find(marker2) if marker2 in text else len(text))
        if idx != -1:
            text = text[:idx] + '\n      </div>\n    </main>\n  </div>' + text[idx + len('\n  </div>'):]
        else:
            skipped.append(f'{path.name}: close marker before body not found')
            continue

    path.write_text(text)
    changed.append(path.name)

print('sidebar layout patch applied:')
for name in changed:
    print(f'- {name}: sidebar wrapped')
for item in skipped:
    print(f'- {item}')
