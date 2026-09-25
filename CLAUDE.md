# Virtuality — заметки для Claude Code

Virtuality — простая замена Proxmox для дома и малого офиса: KVM/QEMU/libvirt + собственная
веб-панель на FastAPI, установочный ISO на базе чистого Ubuntu Server 26.04 LTS и мастер
первичной настройки в браузере. Пользователь — обычный человек, не админ.

**Общайся с владельцем проекта по-русски.** Весь текст интерфейса — на простом русском, без жаргона
(термины вроде qcow2, libvirt, nvram — только в раскрывающихся блоках «Для специалистов»).

## Устройство репозитория

- `web/app.py` — основное приложение FastAPI (машины, ISO, образы дисков, сеть, журналы, обновления, консоль).
- `web/core.py` — общие примитивы: `run_cmd`, хранилище операций (`new_operation`, `run_operation`,
  `finish_operation`, `active_operations_for`, `interrupt_orphaned_operations`), `render`, `require_auth`,
  `valid_vm_name`, шаблоны Jinja. Импорт `core` выставляет `LC_ALL=C.UTF-8` (virsh иначе переводится).
- `web/features/*.py` — модули с `router = APIRouter()`, подключаются в конце `app.py`:
  `setup` (мастер `/setup`), `settings` (`/settings`), `snapshots`, `backups`, `downloads` (скачивание
  по ссылке и каталог), `vmops` (клон, увеличение диска, пауза/спящий режим, заметки, нагрузка).
  POST-маршруты модулей — минимум 4 сегмента (`/vm/{name}/<модуль>/<действие>`), иначе их перехватит
  `@app.post("/vm/{name}/{action}")`.
- `web/nodectl.py` — факты об узле и действия через `scripts/virtuality-ctl` (единая точка для
  systemd-юнитов, TLS, firewall, моста br0 с автооткатом, диска под хранилище, пароля, часового пояса).
- `web/network_core.py` — NAT-сеть `virtuality-nat` (192.168.100.0/24), резервации DHCP (фиксированный
  MAC по имени машины), проброс портов (nftables + ufw + iptables-fallback с меткой `virtuality-forward`).
- `web/catalog.py` — каталог облачных образов и типы ОС; `web/cloudinit.py` — seed cloud-init.
- `web/update_core.py` + `scripts/auto_update_check.sh`, `scripts/apply_github_update.sh`,
  `scripts/update_target.sh` — обновления; канал `stable` = последний тег `vX.Y.Z` (пока тегов нет — main),
  канал `main` = ветка main.
- `scripts/virtuality_firstboot.sh` — первая загрузка установленной системы: panel → network →
  virtualization → tools → finish, прогресс в `/var/lib/virtuality/config/setup.json`.
- `scripts/install_web_panel.sh` — установка/обновление панели (web.prev для отката, офлайн-wheels).
- `install.sh` (установка одной командой), `install_virtuality_node.sh` (пакеты KVM/libvirt).
- `image/build-iso.sh`, `image/autoinstall.yaml`, `image/grub/` — сборка установочного ISO.
- `updates/versions.json` — журнал изменений для центра обновлений; `VERSION` — текущая версия.

## Команды

```bash
make check                      # pyflakes + shellcheck + pytest
python3 -m pytest -q            # тесты (fake virsh в tests/conftest.py)
python3 tests/dev_server.py 8765                            # демо-панель, вход: tester / любой пароль
VIRTUALITY_DEV_SETUP=installing python3 tests/dev_server.py # мастер на шаге установки
VIRTUALITY_DEV_SETUP=done|finished ...                      # мастер после установки / настроенный сервер
make iso                        # ISO в dist/ (нужны xorriso, git, curl, python3-pip; ~3 ГБ загрузки)
./image/build-iso.sh --help     # все параметры сборки (в т.ч. --unattended)
```

Скриншоты: Playwright и Chromium доступны в облачной среде, локально — `npx playwright`.

## Правила

- Код как вокруг: плотность комментариев, имена, идиомы модуля. Не переформатировать чужой код.
- Каждое изменение поведения — с тестом. Перед коммитом: `make check`.
- Коммиты по-русски в стиле «<что> было сделано».
- `main` — рабочая ветка узлов: ноды на канале `main` подтягивают её каждую ночь. **Не сливать в main
  без явного согласия владельца.** Релиз для канала `stable` — тег `vX.Y.Z` на main (он же запускает
  сборку ISO в GitHub Actions).
- Секреты и пароли не логировать; пароль cloud-init хранится только как SHA-512 crypt.
- Не возвращать удалённое: Cockpit, консольный dashboard, эмуляцию ARM64 на x86, PXE, смену
  архитектуры машины, скрипты bootstrap/clean_install/setup_bridge_br0/create_test_vm.
