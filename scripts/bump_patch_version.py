#!/usr/bin/env python3
from pathlib import Path

version_file = Path('VERSION')
current = version_file.read_text().strip() if version_file.exists() else '0.0.0'
parts = current.split('.')
while len(parts) < 3:
    parts.append('0')
major, minor, patch = [int(''.join(ch for ch in part if ch.isdigit()) or '0') for part in parts[:3]]
patch += 1
new_version = f'{major}.{minor}.{patch}'
version_file.write_text(new_version + '\n')
print(new_version)
