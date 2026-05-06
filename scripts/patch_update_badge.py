#!/usr/bin/env python3
from pathlib import Path
import sys

app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/virtuality/web/app.py')
if not app_path.exists():
    raise SystemExit(f'app.py not found: {app_path}')

text = app_path.read_text()
changed = []

if 'import update_core' not in text:
    text = text.replace('import network_core\n', 'import network_core\nimport update_core\n', 1)
    changed.append('import update_core added')

helper = r'''

def dashboard_update_notice() -> dict[str, Any]:
    try:
        info = update_core.check_updates(fetch=False)
        if info.get("has_update"):
            return {
                "has_update": True,
                "current_version": info.get("current_version", "unknown"),
                "latest_version": info.get("latest_version", "unknown"),
                "missing_count": len(info.get("missing_versions", [])),
                "commit_count": len(info.get("commits", [])),
            }
    except Exception:
        pass
    return {"has_update": False}
'''

if 'def dashboard_update_notice() -> dict[str, Any]:' not in text:
    marker = '\n\ndef update_operation(operation: dict[str, Any], **changes: Any) -> None:'
    if marker not in text:
        raise SystemExit('update_operation marker not found')
    text = text.replace(marker, helper + marker, 1)
    changed.append('dashboard update notice helper added')
else:
    changed.append('dashboard update notice helper already present')

old_fragment = '"operations": list_operations(5), "operation_css": operation_css})'
new_fragment = '"operations": list_operations(5), "operation_css": operation_css, "update_notice": dashboard_update_notice()})'
if old_fragment in text:
    text = text.replace(old_fragment, new_fragment, 1)
    changed.append('dashboard context gets update_notice')
elif '"update_notice": dashboard_update_notice()' in text:
    changed.append('dashboard context already has update_notice')
else:
    raise SystemExit('dashboard context marker not found')

app_path.write_text(text)
print('update badge patch applied:')
for item in changed:
    print(f'- {item}')
