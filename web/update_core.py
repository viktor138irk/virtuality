import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

SOURCE_DIR = Path(os.environ.get('VIRTUALITY_SOURCE_DIR', '/opt/virtuality/source'))
STATE_DIR = Path('/var/lib/virtuality/update')
STATE_FILE = STATE_DIR / 'state.json'
LOG_FILE = Path('/var/log/virtuality/update.log')
DEFAULT_BRANCH = os.environ.get('VIRTUALITY_UPDATE_BRANCH', 'main')
REMOTE = os.environ.get('VIRTUALITY_UPDATE_REMOTE', 'origin')


class UpdateError(Exception):
    pass


def run_cmd(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, check=False)
        return {'ok': result.returncode == 0, 'code': result.returncode, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip(), 'cmd': ' '.join(cmd)}
    except Exception as exc:
        return {'ok': False, 'code': -1, 'stdout': '', 'stderr': str(exc), 'cmd': ' '.join(cmd)}


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    ensure_state_dir()
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def state() -> dict[str, Any]:
    ensure_state_dir()
    data = read_json(STATE_FILE, {})
    data.setdefault('status', 'idle')
    data.setdefault('message', 'Готово к проверке обновлений')
    data.setdefault('updated_at', '')
    data.setdefault('started_at', '')
    data.setdefault('finished_at', '')
    return data


def source_ready() -> bool:
    return (SOURCE_DIR / '.git').exists()


def current_commit() -> str:
    result = run_cmd(['git', 'rev-parse', 'HEAD'], cwd=SOURCE_DIR, timeout=10)
    return result['stdout'] if result['ok'] else ''


def latest_commit() -> str:
    result = run_cmd(['git', 'rev-parse', f'{REMOTE}/{DEFAULT_BRANCH}'], cwd=SOURCE_DIR, timeout=10)
    return result['stdout'] if result['ok'] else ''


def file_from_git(ref: str, path: str) -> str:
    result = run_cmd(['git', 'show', f'{ref}:{path}'], cwd=SOURCE_DIR, timeout=10)
    return result['stdout'] if result['ok'] else ''


def local_file_text(path: str) -> str:
    target = SOURCE_DIR / path
    try:
        return target.read_text().strip()
    except Exception:
        return ''


def current_version() -> str:
    return local_file_text('VERSION') or '0.0.0'


def latest_version() -> str:
    value = file_from_git(f'{REMOTE}/{DEFAULT_BRANCH}', 'VERSION').strip()
    return value or current_version()


def version_tuple(version: str) -> tuple[int, ...]:
    clean = version.strip().lower().lstrip('v')
    parts = []
    for item in clean.split('.'):
        digits = ''.join(ch for ch in item if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts + [0] * (4 - len(parts)))


def load_manifest(ref: str | None = None) -> dict[str, Any]:
    text = ''
    if ref:
        text = file_from_git(ref, 'updates/versions.json')
    if not text:
        path = SOURCE_DIR / 'updates' / 'versions.json'
        try:
            text = path.read_text()
        except Exception:
            text = ''
    if not text:
        return {'versions': []}
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get('versions'), list):
            return data
    except Exception:
        pass
    return {'versions': []}


def missing_versions(local_version: str, remote_version: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    local_key = version_tuple(local_version)
    remote_key = version_tuple(remote_version)
    versions = []
    for item in manifest.get('versions', []):
        if not isinstance(item, dict):
            continue
        version = str(item.get('version', '')).strip()
        if not version:
            continue
        key = version_tuple(version)
        if local_key < key <= remote_key:
            versions.append(item)
    return sorted(versions, key=lambda item: version_tuple(str(item.get('version', '0.0.0'))))


def git_log_between(base: str, head: str, limit: int = 50) -> list[dict[str, str]]:
    if not base or not head or base == head:
        return []
    fmt = '%H%x1f%h%x1f%s%x1f%ci'
    result = run_cmd(['git', 'log', '--pretty=format:' + fmt, f'{base}..{head}', f'-n{limit}'], cwd=SOURCE_DIR, timeout=20)
    rows = []
    if not result['ok']:
        return rows
    for line in result['stdout'].splitlines():
        parts = line.split('\x1f')
        if len(parts) >= 4:
            rows.append({'sha': parts[0], 'short': parts[1], 'subject': parts[2], 'date': parts[3]})
    return rows


def check_updates(fetch: bool = True) -> dict[str, Any]:
    ensure_state_dir()
    if not source_ready():
        raise UpdateError(f'Не найден git-репозиторий: {SOURCE_DIR}')

    fetch_result = {'ok': True, 'stdout': '', 'stderr': '', 'cmd': 'skip'}
    if fetch:
        fetch_result = run_cmd(['git', 'fetch', '--quiet', REMOTE, DEFAULT_BRANCH], cwd=SOURCE_DIR, timeout=60)

    local_commit = current_commit()
    remote_commit = latest_commit()
    local_version = current_version()
    remote_version = latest_version()
    manifest = load_manifest(f'{REMOTE}/{DEFAULT_BRANCH}')
    missed = missing_versions(local_version, remote_version, manifest)
    commits = git_log_between(local_commit, remote_commit)
    has_update = bool(local_commit and remote_commit and local_commit != remote_commit)

    data = {
        'ok': True,
        'source_dir': str(SOURCE_DIR),
        'remote': REMOTE,
        'branch': DEFAULT_BRANCH,
        'fetch_ok': fetch_result['ok'],
        'fetch_error': fetch_result['stderr'] or fetch_result['stdout'],
        'current_commit': local_commit,
        'latest_commit': remote_commit,
        'current_version': local_version,
        'latest_version': remote_version,
        'has_update': has_update,
        'missing_versions': missed,
        'commits': commits,
        'checked_at': utc_now(),
        'state': state(),
        'log_tail': update_log_tail(),
    }
    write_json(STATE_DIR / 'last_check.json', data)
    return data


def update_log_tail(max_lines: int = 160) -> str:
    try:
        return '\n'.join(LOG_FILE.read_text(errors='replace').splitlines()[-max_lines:])
    except Exception:
        return ''


def start_update() -> dict[str, Any]:
    ensure_state_dir()
    if not source_ready():
        raise UpdateError(f'Не найден git-репозиторий: {SOURCE_DIR}')
    script = SOURCE_DIR / 'scripts' / 'apply_github_update.sh'
    if not script.exists():
        raise UpdateError(f'Не найден скрипт обновления: {script}')

    data = {
        'status': 'running',
        'message': 'Обновление запущено',
        'started_at': utc_now(),
        'updated_at': utc_now(),
        'finished_at': '',
    }
    write_json(STATE_FILE, data)
    subprocess.Popen(['bash', str(script)], cwd=str(SOURCE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return data
