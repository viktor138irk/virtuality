# Архитектура Virtuality

Virtuality — одна нода: сервер с KVM/QEMU/libvirt, поверх которого работает панель на FastAPI. Панель не хранит собственную базу данных: источник правды о машинах — libvirt (`virsh`), о ноде — файлы в `/var/lib/virtuality/config`, о задачах — файлы в `/var/log/virtuality/operations`. Всё, что меняет сам сервер (службы, firewall, сеть, диски), делает один скрипт — `virtuality-ctl`, и панель вызывает его же.

```text
Браузер
  │  HTTP 8088 / HTTPS 8443
  ▼
virtuality-web.service — uvicorn, web/app.py (FastAPI, Jinja2)
  ├── web/features/*        мастер, настройки, снимки, копии, загрузки, операции с машиной
  ├── web/core.py           общие примитивы, хранилище задач
  ├── web/nodectl.py ───────► /usr/local/bin/virtuality-ctl (systemd, TLS, ufw, br0, диск, пароль)
  ├── web/network_core.py ──► virsh net-*, nft, ufw, iptables (сеть NAT и пробросы)
  └── virsh / virt-install / virt-clone / qemu-img ──► libvirtd ──► QEMU/KVM
```

---

## Код панели: `web/`

| Модуль | Назначение |
|---|---|
| `app.py` | Основное приложение: вход, обзор, машины (создание, действия, ресурсы, порядок загрузки, привод), ISO и образы дисков (загрузка, конвертация, распаковка), сеть и пробросы, обновления, задачи, журналы, экран машины (WebSocket-прокси к VNC), `/healthz`. В конце подключает роутеры из `features/`. |
| `core.py` | Общие примитивы: настройки из `.env`, сессии и авторизация (`require_auth`, `require_api_auth`, защита от cross-origin POST, ограничение попыток входа), `run_cmd`, `render` и окружение шаблонов, `valid_vm_name`, хранилище задач (`new_operation`, `run_operation`, `update_operation`, `finish_operation`, `active_operations_for`, `running_operations`, `interrupt_orphaned_operations`). Импорт `core` выставляет `LC_ALL=C.UTF-8`: вывод `virsh` разбирается панелью и не должен переводиться. |
| `auth.py` | Проверка пароля Linux-пользователя по `/etc/shadow`: через `crypt`/`spwd`, а на Python 3.13+ — через `libcrypt` (ctypes). Ограничение попыток входа. |
| `features/setup.py` | Мастер первичной настройки `/setup`: шаги install → welcome → account → access → network → storage → updates → finish, страница «Применяем настройки», ожидание перестройки сети в мост. |
| `features/settings.py` | Страница «Настройки»: доступ (HTTPS/порт), пароль, часовой пояс, обновления, сеть машин, хранилище, перезагрузка/выключение, архив настроек, отчёт для поддержки. |
| `features/snapshots.py` | Снимки: внутренние снимки libvirt (`virsh snapshot-*`), до 8 на машину, создание и возврат — фоновые задачи. |
| `features/backups.py` | Резервные копии: `vm.xml` + копии дисков + `meta.json` в `backups/<машина>/<ГГГГММДД-ЧЧММ>/`; восстановление под тем же или новым именем; запуск как скрипта для расписания. |
| `features/downloads.py` | Скачивание ISO и образов по ссылке и из каталога — фоновые задачи типа `download` с прогрессом, проверкой контрольной суммы и отменой. |
| `features/vmops.py` | Увеличение диска, клонирование (`virt-clone`), пауза, спящий режим (`virsh managedsave`), заметки (title/description машины), живая нагрузка (`virsh domstats`). |
| `nodectl.py` | Факты о ноде (сеть, диски, оборудование, часовые пояса, состояние первой загрузки, выбор мастера) и действия через `virtuality-ctl`. Действия, которые перезапускают панель, запускаются отложенно через `systemd-run`. |
| `network_core.py` | Сеть NAT `virtuality-nat`, резервации DHCP, пробросы портов и их применение (nftables, ufw, iptables). |
| `catalog.py` | Каталог облачных образов (Ubuntu 26.04/24.04, Debian 13, Alpine 3.23 для x86_64 и aarch64) и типы ОС для установки с ISO (`osinfo` для `virt-install`). |
| `cloudinit.py` | Seed cloud-init для машин из готовых образов: `user-data` (пользователь, хеш пароля SHA-512, SSH-ключи, рост диска, гостевой агент) и `meta-data`; подключается к `virt-install --cloud-init`. |
| `host_profile.py` | Профиль сервера (`x86_64`, `raspberry-arm64`, `orangepi5-arm64`, `generic-arm64`), проверки готовности; хранится в `config/host_profile.json`. |
| `update_core.py` | Проверка обновлений: канал, целевой ref, список пропущенных версий из `updates/versions.json`, запуск `scripts/apply_github_update.sh` через `systemd-run`. |
| `presenters.py` | Человеческие названия состояний, профили ресурсов машин, список типовых портов, форматирование размеров. |
| `restore_network.py` | Повторное применение пробросов после загрузки (`virtuality-network.service`). |
| `redirect_http.py` | Перенаправление HTTP → HTTPS на порту 8088, когда включён TLS (`virtuality-redirect.service`). |
| `templates/`, `static/` | Jinja2-шаблоны (`base.html` — каркас, `_ui.html` — компоненты), `app.css` (светлая и тёмная темы), `panel.js`, шрифт и иконки. Панель не ходит в интернет. |

Правило для роутов модулей: POST-маршруты вида `/vm/{name}/<модуль>/<действие>` — минимум четыре сегмента, иначе их перехватит общий `@app.post("/vm/{name}/{action}")`.

### Фоновые задачи

Долгие операции (создание машины, конвертация образа, скачивание, снимок, копия, клон, спящий режим) выполняются в потоках и записываются в `/var/log/virtuality/operations/<id>.json` (статус, прогресс, сообщение, метаданные) и `<id>.log` (журнал). Раздел «Задачи» и страницы машин читают эти файлы; `active_operations_for(vm, kinds)` защищает машину от двух одновременных операций. При старте панели `interrupt_orphaned_operations()` закрывает задачи, оставшиеся в состоянии «выполняется» после перезапуска. `virtuality-ctl maintenance` удаляет задачи старше 30 дней и оставляет не больше 300.

---

## Управление нодой: `scripts/virtuality-ctl`

Один bash-скрипт, устанавливается в `/usr/local/bin`. Панель (через `nodectl.py`) и администратор в терминале делают одно и то же:

- **`reconfigure`** — читает `/var/lib/virtuality/config/web.env` и пересоздаёт systemd-юниты, logrotate, `/etc/default/libvirt-guests`, правила ufw; переводит VNC старых машин на `127.0.0.1`; перезапускает панель.
- **`set KEY=VALUE`** — проверяет и сохраняет настройку в `web.env`, затем `reconfigure` (для настроек обновления — только перезапись таймера).
- **TLS** — при `VIRTUALITY_TLS=1` создаёт самоподписанный сертификат EC P-256 на 10 лет в `config/tls/panel.{key,crt}` (SAN: имя сервера, localhost, адрес), uvicorn слушает `VIRTUALITY_TLS_PORT` (8443), а `virtuality-redirect.service` перенаправляет с HTTP-порта.
- **Мост `br0`** — `bridge <iface> [dhcp|static]` делает копию `/etc/netplan`, пишет `60-virtuality-br0.yaml`, проверяет `netplan generate`, взводит таймер `virtuality-netplan-revert` на 120 секунд и применяет; `bridge-confirm` снимает таймер, `bridge-revert` возвращает копию. Панель после перестройки сети сама вызывает подтверждение, когда снова получает ответ от сервера.
- **`storage-use /dev/sdX`** — только пустой диск без разделов: `mkfs.ext4`, перенос текущего содержимого `/var/lib/virtuality`, запись в `/etc/fstab` (`nofail`), монтирование.
- **`passwd`, `timezone`, `power`, `backup-config`, `restore-config`, `rollback`, `maintenance`, `support-bundle`, `uninstall`** — см. README.

Журнал действий: `/var/log/virtuality/virtuality-ctl.log`.

---

## Службы systemd

Создаются `virtuality-ctl reconfigure`:

| Юнит | Что делает |
|---|---|
| `virtuality-web.service` | Панель: `uvicorn app:app` от root из `/opt/virtuality/web` с venv `/opt/virtuality/venv`; `Restart=always`. |
| `virtuality-redirect.service` | Только при TLS: HTTP → HTTPS. |
| `virtuality-network.service` | При загрузке заново применяет пробросы портов (`restore_network.py`). |
| `virtuality-auto-update.timer` / `.service` | Ночная проверка обновлений (`scripts/auto_update_check.sh`), 04:00 ± 30 минут; включён при `VIRTUALITY_AUTO_UPDATE=1`. |
| `virtuality-maintenance.timer` / `.service` | Ежедневное обслуживание: старые задачи, временные файлы, недокачанные загрузки, `journalctl --vacuum-size=300M`, запись занятости диска в `config/health.json`. |
| `libvirt-guests.service` | При выключении сервера машины выключаются корректно (`ON_SHUTDOWN=shutdown`, до 180 секунд), при загрузке не запускаются сами — только те, у кого включён автозапуск. |

Отдельно, из образа: `virtuality-firstboot.service` — первая загрузка (см. [IMAGE.md](IMAGE.md)); временные юниты `virtuality-netplan-revert` (автооткат моста), `virtuality-apply-*` (отложенное применение настроек) и `virtuality-update-*` (обновление вне cgroup панели, чтобы перезапуск панели не убил сам процесс обновления).

---

## Безопасность панели

- Вход — по имени и паролю системного пользователя (`VIRTUALITY_AUTH_USER`); проверяется по `/etc/shadow`, поэтому панель работает от root. После 5 неверных паролей адрес блокируется на 5 минут; сессия (подписанная cookie, ключ в `config/session_secret`) живёт 12 часов.
- POST-запросы принимаются только с того же origin; за reverse proxy нужно передавать исходный `Host` (и `Upgrade`/`Connection` для экрана машины).
- Экран машины: VNC у всех машин слушает `127.0.0.1`, панель проксирует его в WebSocket по подписанному токену и только при действующей сессии. Порты 5900–5999 в ufw закрыты.
- Пароль cloud-init хранится только как хеш SHA-512; секреты не попадают в журналы и отчёт для поддержки.
- Firewall: `install_virtuality_node.sh` включает ufw и разрешает SSH; `virtuality-ctl` открывает только порт панели (и HTTPS-порт), список открытых портов хранит в `config/ports.state`.

---

## Сеть машин

### NAT: `virtuality-nat`

libvirt-сеть с мостом `virbr100`, подсеть `192.168.100.0/24`, шлюз `192.168.100.1`, DHCP `192.168.100.50–200`. Описание сохраняется в `/var/lib/virtuality/network/virtuality-nat.xml`. Создаётся мастером, страницей «Сеть и доступ» или автоматически при создании первой машины в режиме NAT. Включается `net.ipv4.ip_forward` (`/etc/sysctl.d/99-virtuality-forward.conf`) и ослабляется rp_filter (`98-virtuality-rpfilter.conf`).

**Постоянные адреса.** Для каждой машины MAC-адрес вычисляется из её имени (`stable_mac`), и в сети создаётся резервация DHCP (`virsh net-update add ip-dhcp-host`) — машина всегда получает один и тот же адрес, панель знает его ещё до первого запуска. При удалении машины резервация освобождается.

**Пробросы портов** хранятся в `/var/lib/virtuality/network/port_forwards.json` и применяются тремя способами сразу, чтобы работать на любом дистрибутиве:

1. nftables — таблица `ip virtuality` в `/etc/virtuality/nftables/virtuality.nft` (DNAT, forward, masquerade);
2. ufw — правила `route allow`, список сохранённых правил в `network/ufw_rules.json`;
3. iptables — запасной вариант, все правила помечены комментарием `virtuality-forward`, чтобы их можно было найти и удалить.

Адрес машины определяется через гостевой агент, `virsh domifaddr`, аренды DHCP по MAC машины, либо задаётся вручную. «Проверить» рядом с правилом делает TCP-подключение к машине и показывает команды для диагностики.

### Домашняя сеть: мост `br0`

Сервер объединяет свой проводной интерфейс в мост `br0` (netplan), машины подключаются к нему и получают адреса от роутера. Wi-Fi в мост объединить нельзя — тогда доступен только NAT. Как устроен автооткат — выше, в разделе про `virtuality-ctl`.

---

## Хранилище

```text
/var/lib/virtuality/
├── iso/            установочные образы; libvirt-пул virtuality-iso
├── images/         диски машин <имя>.qcow2; libvirt-пул virtuality-images
├── disk-images/    готовые образы систем (каталог, ссылки, загрузки с компьютера)
├── backups/        резервные копии машин (<машина>/<ГГГГММДД-ЧЧММ>/) и архивы настроек config-*.tar.gz
├── config/         web.env, setup.json, setup_done, wizard.json, host_profile.json,
│                   session_secret, ports.state, health.json, tls/, netplan-backup/
├── network/        port_forwards.json, ufw_rules.json, virtuality-nat.xml
├── update/         state.json, last_check.json, временные файлы обновления
└── tmp/            загрузки (.<имя>.part, .<имя>.uploading), seed cloud-init; TMPDIR панели
```

Мастер и «Настройки» умеют отдать под всё это отдельный пустой диск — он монтируется в `/var/lib/virtuality` целиком. Память «спящих» машин libvirt хранит в `/var/lib/libvirt/qemu/save`.

Код и служебные файлы:

```text
/opt/virtuality/source        git-репозиторий, из него ставится панель и берутся обновления
/opt/virtuality/web           установленная панель (+ .env с пользователем и ключом сессий)
/opt/virtuality/web.prev      предыдущая версия для virtuality-ctl rollback
/opt/virtuality/venv          virtualenv
/opt/virtuality/virtuality.env  пути ноды (пишет install_virtuality_node.sh)
/etc/virtuality/image.env     параметры образа (только после установки с ISO)
/etc/virtuality/nftables/     правила пробросов
/var/log/virtuality/          firstboot.log, update.log, virtuality-ctl.log, install_*.log, operations/
```

---

## Обновления

1. **Канал** (`VIRTUALITY_UPDATE_CHANNEL` в `web.env`): `stable` — последний тег `vX.Y.Z` (пока тегов нет — ветка `main`); `main` — ветка `main`. Логика одна и та же в `web/update_core.py` и `scripts/update_target.sh`.
2. **Проверка** (`/update/check`, ночной `scripts/auto_update_check.sh`): `git fetch --tags`, сравнение текущего коммита с целевым; обновление предлагается, только если целевой коммит — потомок текущего (нода никогда не откатывается сама). Список изменений берётся из `updates/versions.json` целевой версии.
3. **Применение** (`scripts/apply_github_update.sh`, запускается через `systemd-run`): `git checkout -B main <цель>` и `git clean`; если git недоступен — скачивание ZIP-архива с GitHub и `rsync`; затем `scripts/install_web_panel.sh` (панель копируется в `web.new`, старая переименовывается в `web.prev`, venv обновляется) и `systemctl restart virtuality-web`. Состояние — `/var/lib/virtuality/update/state.json`, журнал — `/var/log/virtuality/update.log`.
4. **Откат**: `virtuality-ctl rollback` меняет местами `web` и `web.prev`.

Установщики и скрипты вне `web/`:

```text
install.sh                          установка одной командой (проверки, пакеты, пользователь, клон, три установщика ниже)
install_virtuality_node.sh          пакеты KVM/QEMU/libvirt (x86_64 или ARM64), пулы, ufw, группы пользователя
scripts/install_web_panel.sh        панель, venv (офлайн из wheels/ при наличии), virtuality-ctl, юниты
scripts/install_healthcheck_command.sh   команда vhealth (scripts/virtuality_healthcheck.sh)
scripts/detect_host_profile.sh      профиль сервера для установщика
scripts/virtuality_firstboot.sh     первая загрузка после ISO
scripts/auto_update_check.sh, apply_github_update.sh, update_target.sh   обновления
scripts/bump_patch_version.py       поднять patch-версию в VERSION
image/build-iso.sh, autoinstall.yaml, files/, grub/   сборка ISO
```

---

## Тесты и CI

`tests/conftest.py` подменяет `run_cmd`, так что `virsh`, `systemctl`, `qemu-img` и остальные команды имитируются — тесты идут на любой машине без libvirt. `tests/dev_server.py` поднимает демо-панель с теми же подменами. `make check` = pyflakes + shellcheck + pytest; `.github/workflows/ci.yml` запускает это на Python 3.11–3.14, `.github/workflows/image.yml` собирает ISO.
