#!/usr/bin/env python3
from pathlib import Path
import re
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
app_dir = app_path.resolve().parent if app_path.exists() else Path('/opt/virtuality/web')
template_path = app_dir / 'templates' / 'vm_detail.html'
css_path = app_dir / 'static' / 'app.css'
changed = []

if not template_path.exists():
    raise SystemExit(f'vm_detail.html not found: {template_path}')

tpl = template_path.read_text()
original = tpl

resource_messages = r'''
    {% if resource_message %}
      <div class="alert success">{{ resource_message }}</div>
    {% endif %}
    {% if resource_error %}
      <div class="alert danger">{{ resource_error }}</div>
    {% endif %}
'''

resource_card = r'''
      <article class="card">
        <div class="card-head">
          <h2>CPU / RAM / Архитектура</h2>
          <span class="pill">resources</span>
        </div>
        <div class="resource-mini-grid">
          <div><span>CPU</span><b>{{ resource_settings.vcpus }}</b></div>
          <div><span>RAM</span><b>{{ resource_settings.memory_mb }} MB</b></div>
          <div><span>Архитектура</span><b>{{ resource_settings.arch }}</b></div>
        </div>
        <form method="post" action="/vm/{{ vm.name }}/resources" class="form-grid">
          <label>
            <span>CPU, vCPU</span>
            <input type="number" name="vcpus" min="1" max="128" value="{{ resource_settings.vcpus }}" required {% if not resource_settings.is_shutoff %}disabled{% endif %}>
          </label>
          <label>
            <span>RAM, MB</span>
            <input type="number" name="memory_mb" min="512" max="262144" step="128" value="{{ resource_settings.memory_mb }}" required {% if not resource_settings.is_shutoff %}disabled{% endif %}>
          </label>
          <label>
            <span>Архитектура</span>
            <select name="guest_arch" {% if not resource_settings.is_shutoff %}disabled{% endif %}>
              <option value="keep" selected>Не менять — {{ resource_settings.arch }}</option>
              <option value="x86_64">x86_64 / amd64</option>
              <option value="aarch64">ARM64 / aarch64</option>
            </select>
          </label>
          <button class="primary wide" type="submit" {% if not resource_settings.is_shutoff %}disabled{% endif %}>Применить ресурсы</button>
        </form>
        {% if not resource_settings.is_shutoff %}
          <div class="alert danger">VM сейчас не выключена. CPU, RAM и архитектуру можно менять только после Shutdown/Power off.</div>
        {% endif %}
        <p class="muted small-note">CPU/RAM меняются в XML VM. Смена архитектуры требует совместимый диск и загрузчик; x86-диск не станет ARM-диском по щелчку, увы, физика всё ещё душнит.</p>
      </article>
'''

if 'resource_message' not in tpl:
    marker = '    {% if iso_message %}'
    if marker in tpl:
        tpl = tpl.replace(marker, resource_messages + '\n' + marker, 1)
        changed.append('resource messages added')

if 'CPU / RAM / Архитектура' not in tpl:
    marker = '    <section class="grid two vm-boot-iso-grid">'
    if marker in tpl:
        tpl = tpl.replace(marker, '    <section class="grid three vm-resource-boot-iso-grid">', 1)
        insert_at = tpl.find('\n      <article class="card">', tpl.find('vm-resource-boot-iso-grid'))
        if insert_at != -1:
            tpl = tpl[:insert_at] + '\n' + resource_card + tpl[insert_at:]
            changed.append('resource card inserted before ISO card')
    else:
        # fallback: insert before first two-column grid in VM detail
        marker = '\n\n    <section class="grid two">'
        if marker not in tpl:
            raise SystemExit('vm detail grid marker not found')
        settings = '\n\n    <section class="grid three vm-resource-boot-iso-grid">\n' + resource_card + '    </section>\n'
        tpl = tpl.replace(marker, settings + marker, 1)
        changed.append('resource grid inserted fallback')
else:
    changed.append('resource card already present')

# If grid is already present from older patch, upgrade two columns to three.
tpl = tpl.replace('class="grid two vm-boot-iso-grid"', 'class="grid three vm-resource-boot-iso-grid"')

template_path.write_text(tpl)

if css_path.exists():
    css = css_path.read_text()
    original_css = css
    if '.three {' not in css:
        css = css.replace('.two { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 12px; }', '.two { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 12px; }\n.three { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 12px; }')
        changed.append('three-column grid CSS added')
    if '.resource-mini-grid' not in css:
        css += '''

.resource-mini-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; margin-bottom: 10px; }
.resource-mini-grid div { display: grid; gap: 3px; padding: 8px; border: 1px solid var(--line); border-radius: 7px; background: var(--panel-2); }
.resource-mini-grid span { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
.resource-mini-grid b { font-size: 14px; overflow-wrap: anywhere; }
.boot-order-list { display: grid; gap: 8px; }
.boot-order-item { display: flex; align-items: center; gap: 10px; padding: 9px; border: 1px solid var(--line); border-radius: 7px; background: var(--panel-2); cursor: grab; }
.boot-order-item:active { cursor: grabbing; }
.boot-order-item.dragging { opacity: .55; border-color: var(--accent); }
.boot-order-item small { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
.drag-handle { color: var(--muted); font-weight: 900; }
@media (max-width: 1180px) { .three { grid-template-columns: 1fr; } .resource-mini-grid { grid-template-columns: 1fr; } }
'''
        changed.append('resource and boot-order CSS added')
    if css != original_css:
        css_path.write_text(css)

print('VM detail resource layout patch applied:')
for item in changed or ['already applied']:
    print(f'- {item}')
