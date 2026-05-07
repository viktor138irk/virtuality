#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
template_path = app_path.with_name('templates') / 'network.html'
if not template_path.exists():
    raise SystemExit(f'network.html not found: {template_path}')

text = template_path.read_text()
old = '<input type="hidden" name="guest_ip" value="auto">'
new = '''<label>
              <span>IP VM</span>
              <input type="text" name="guest_ip" value="auto" placeholder="auto или 192.168.100.55" required>
              <small>Если IP не определяется автоматически, впиши внутренний IP VM вручную.</small>
            </label>'''

if old in text:
    text = text.replace(old, new, 1)
    template_path.write_text(text)
    print('network manual IP field added')
elif 'name="guest_ip" value="auto" placeholder="auto' in text:
    print('network manual IP field already present')
else:
    raise SystemExit('guest_ip marker not found in network.html')
