# Установочный образ Virtuality

Загрузочный ISO ставит Virtuality на чистый сервер «с нуля», как Proxmox: записали на флешку, загрузились, ответили на три экрана — после перезагрузки панель уже открыта в браузере, а мастер настройки показывает, как ставятся остальные компоненты.

Образ собирается из официального, ничем не изменённого **Ubuntu Server 26.04 LTS live-server ISO** (amd64 или arm64) и дополняется:

```text
/autoinstall.yaml                          # сценарий установщика Ubuntu (subiquity autoinstall)
/virtuality/source                         # исходники Virtuality нужной версии (git-репозиторий)
/virtuality/source/wheels                  # Python-колёса панели и pip — для установки без интернета
/virtuality/image.env                      # параметры сборки: версия, порт панели, автообновление
/virtuality/virtuality-firstboot.service   # служба первой загрузки
/boot/grub/grub.cfg                        # меню «Установить Virtuality X.Y.Z» с фирменной темой
/boot/grub/themes/virtuality/              # тема GRUB
```

Загрузочные записи BIOS и UEFI берутся из оригинального ISO без изменений.

---

## Где взять готовый образ

Workflow `.github/workflows/image.yml` собирает ISO в GitHub Actions:

- автоматически при выпуске — пуш тега `v*` (amd64 и arm64);
- при pull request, который меняет `image/**`, `scripts/virtuality_firstboot.sh`, `web/requirements.txt` или сам workflow;
- вручную: Actions → **Build ISO image** → Run workflow → архитектура `amd64`, `arm64` или `both`.

После сборки workflow проверяет, что в образе есть загрузочная запись UEFI, `autoinstall.yaml` разбирается как YAML, а в `grub.cfg` есть пункт Virtuality. Образы Ubuntu Server больше лимита GitHub Releases (2 ГБ на файл), поэтому ISO публикуются как **artifacts** запуска (`virtuality-iso-amd64`, `virtuality-iso-arm64`) и хранятся 30 дней. В артефакте — `.iso` и `.sha256`.

---

## Сборка своими руками

Нужна Linux-машина (root не нужен), около 10 ГБ свободного места и пакеты:

```bash
sudo apt install -y xorriso git curl python3-pip
```

Сборка:

```bash
git clone https://github.com/viktor138irk/virtuality.git
cd virtuality
make iso                 # amd64
make iso ARCH=arm64      # ARM64-серверы с UEFI
```

`image/build-iso.sh` сам находит последний Ubuntu 26.04.x live-server ISO, скачивает его (около 3 ГБ), проверяет SHA256 и кладёт результат в `dist/`:

```text
dist/virtuality-0.11.0-ubuntu-26.04-amd64.iso
dist/virtuality-0.11.0-ubuntu-26.04-amd64.iso.sha256
```

Скачанный ISO Ubuntu кешируется в `.cache/iso` (или в `VIRTUALITY_ISO_CACHE`). Уже скачанный образ можно передать явно:

```bash
./image/build-iso.sh --ubuntu-iso ~/Downloads/ubuntu-26.04.1-live-server-amd64.iso
```

### Параметры `image/build-iso.sh`

```text
--arch amd64|arm64         архитектура (по умолчанию amd64)
--ubuntu 26.04|24.04       база образа (по умолчанию 26.04 LTS)
--ubuntu-iso PATH          использовать локальный live-server ISO вместо скачивания
--cache-dir DIR            где хранить скачанные ISO Ubuntu (по умолчанию .cache/iso)
--output DIR               куда класть результат (по умолчанию dist)
--ref GIT_REF              какую версию Virtuality положить в образ (по умолчанию HEAD)
--repo-url URL             откуда установленная нода будет получать обновления
--web-port PORT            порт панели на установленной ноде (по умолчанию 8088)
--no-auto-update           не включать ночное автообновление
--locale LOCALE            локаль установщика и системы (по умолчанию en_US.UTF-8)
--keyboard LAYOUT          раскладка (по умолчанию us)
--timezone TZ              часовой пояс (по умолчанию geoip — установщик определит по сети; мастер настройки может сменить)
--no-wheels                не класть Python-колёса (тогда первая загрузка скачает их из PyPI)
```

Полностью автоматический режим (вопросов нет, **стирается самый большой диск**):

```text
--unattended               автоматическая установка
--username NAME            администратор (логин в панель), обязателен с --unattended
--password-hash HASH       хеш crypt(3), например от: openssl passwd -6
--hostname NAME            имя сервера (по умолчанию virtuality)
```

`./image/build-iso.sh --help` печатает тот же список. Через `make` параметры передаются так:

```bash
make iso ISO_ARGS="--web-port 8089 --no-auto-update --timezone Europe/Moscow"
```

### Что кладётся в образ

- **Исходники**: `git clone` репозитория на указанный `--ref`; у клона `origin` переключается на `--repo-url`, чтобы нода обновлялась оттуда.
- **Колёса**: `pip download` для Python целевого выпуска (3.14 для Ubuntu 26.04, 3.12 для 24.04) и архитектуры, только бинарные `manylinux`-колёса из `web/requirements.txt`. Сам `pip` тоже кладётся: в Ubuntu Server нет `python3-venv`, и установщик панели поднимает pip из этого колеса в virtualenv, созданный `--without-pip`. Благодаря этому панель ставится до появления сети.
- **`image.env`**: `VIRTUALITY_IMAGE_VERSION`, `VIRTUALITY_IMAGE_BUILT`, `VIRTUALITY_WEB_PORT`, `VIRTUALITY_AUTO_UPDATE` и, в автоматическом режиме, `VIRTUALITY_USER`. Установщик копирует файл в `/etc/virtuality/image.env`, его читает первая загрузка.

### Полностью автоматическая установка

Для массовой раскатки:

```bash
make iso ISO_ARGS="--unattended --username admin --password-hash '$(openssl passwd -6)' --hostname node01"
```

> ⚠️ Такой образ сам уходит в установку через 10 секунд в меню и **без подтверждения стирает самый большой диск** машины. Файл получает суффикс `-unattended`, пункты меню — пометку «автоматически, диск будет стёрт». Не оставляйте такую флешку в серверах.

---

## Что спрашивает установщик

Меню загрузки: **Установить Virtuality 0.11.0** (выбирается сам через 10 секунд), тот же пункт с новым ядром HWE, загрузка с другого диска, настройки UEFI, проверка памяти.

Сценарий `image/autoinstall.yaml` оставляет интерактивными только три экрана установщика Ubuntu:

1. **Сеть** — обычно достаточно подтвердить адрес по DHCP.
2. **Диск** — какой диск использовать. Разметка: LVM на весь диск.
3. **Пользователь** — имя сервера, имя пользователя и пароль. Этот пользователь становится администратором панели (и входит по SSH).

Язык, раскладка, часовой пояс (по умолчанию определяется по сети), SSH-сервер с входом по паролю и всё остальное заданы заранее. В конце установщик копирует исходники в `/opt/virtuality/source`, `image.env` в `/etc/virtuality/` и включает `virtuality-firstboot.service`. Затем — перезагрузка.

---

## Первая загрузка

`scripts/virtuality_firstboot.sh` запускается службой `virtuality-firstboot.service` один раз. Порядок шагов сделан так, чтобы панель появилась первой и показывала прогресс всего остального:

| Шаг | Что происходит | Что видно |
|---|---|---|
| `panel` | `scripts/install_web_panel.sh` с `VIRTUALITY_PANEL_ONLY=1`: панель ставится офлайн из колёс и запускается. | Через минуту после загрузки панель отвечает на `http://адрес:8088`; на экране сервера — подсказка `Virtuality настраивается`. |
| `network` | Ожидание маршрута по умолчанию и DNS. | Если через 20 секунд сети нет, мастер пишет «Нет доступа в интернет. Подключите кабель или настройте сеть — установка продолжится сама». |
| `virtualization` | `install_virtuality_node.sh`: пакеты KVM/QEMU/libvirt, пулы, firewall. Самый долгий шаг, 5–15 минут. | Мастер показывает, какой пакет скачивается, распаковывается или настраивается. |
| `tools` | `scripts/install_healthcheck_command.sh`: команда `vhealth`. | |
| `finish` | `virtuality-ctl reconfigure`: юниты, firewall, перезапуск панели; на экран сервера выводится адрес панели и логин. | Мастер переходит к шагу «Сервер». |

Прогресс пишется в `/var/lib/virtuality/config/setup.json` (`stage`, `message`, список шагов со статусами), мастер опрашивает его через `/api/setup/state`. Каждый шаг идемпотентен и отмечается флагом в `/var/lib/virtuality/.firstboot-steps/`; если что-то упало, systemd перезапускает скрипт через 60 секунд, и он продолжает с первого незавершённого шага — мастер в это время показывает ошибку и хвост журнала. По завершении создаётся `/var/lib/virtuality/.firstboot-done`, и служба больше не запускается.

Если образ собран с `--no-wheels`, офлайн-установка панели не удастся; скрипт дождётся сети и поставит зависимости из PyPI.

Смотреть ход установки с сервера:

```bash
sudo journalctl -fu virtuality-firstboot
cat /var/log/virtuality/firstboot.log
```

Логин в панель — пользователь из установщика, пароль — его пароль. Дальше вас ведёт мастер настройки (см. README).

---

## Тема загрузочного меню

`image/grub/theme.txt` — тема GRUB (gfxmenu), `background.png` — фон 1024×768, `select_*.png` — подсветка выбранного пункта. `build-iso.sh` дописывает в `grub.cfg` подключение темы (`gfxterm`, шрифт `unicode.pf2`, `gfxmode=1024x768`); если модуль или шрифт недоступны, GRUB показывает обычное текстовое меню. Пункты меню переводятся на русский, таймаут — 10 секунд. Контрольная сумма изменённого `grub.cfg` обновляется в `md5sum.txt`, чтобы проверка целостности ISO проходила.

Фон рендерится из `image/grub/background.html` (тот же стиль, что у страницы входа панели), как описано в `image/grub/README.md`.

---

## Проверка образа

Сверьте контрольную сумму скачанного или собранного образа:

```bash
sha256sum -c virtuality-0.11.0-ubuntu-26.04-amd64.iso.sha256
```

Убедиться, что образ загрузочный и содержит нужные файлы (то же делает CI):

```bash
xorriso -indev virtuality-0.11.0-ubuntu-26.04-amd64.iso -report_el_torito plain 2>/dev/null | grep UEFI
xorriso -osirrox on -indev virtuality-0.11.0-ubuntu-26.04-amd64.iso -extract /autoinstall.yaml autoinstall.yaml
```

---

## Запись на флешку

Нужна флешка от 4 ГБ. Все данные на ней будут стёрты.

- **Windows**: [Rufus](https://rufus.ie) (режим записи DD) или [balenaEtcher](https://etcher.balena.io).
- **macOS**: balenaEtcher.
- **Linux**: balenaEtcher или `dd`, где `/dev/sdX` — устройство флешки (проверьте через `lsblk`):

  ```bash
  sudo dd if=dist/virtuality-0.11.0-ubuntu-26.04-amd64.iso of=/dev/sdX bs=4M status=progress conv=fsync
  ```

Загрузитесь с флешки (меню загрузки — обычно F8, F11, F12 или Esc при включении), выберите **Установить Virtuality**.

---

## Ограничения

- База — Ubuntu Server 26.04 LTS или 24.04 LTS (`--ubuntu 24.04`).
- Для первой загрузки нужен интернет: пакеты KVM/libvirt ставятся из репозиториев Ubuntu. Панель и её Python-зависимости уже в образе.
- ARM64-образ рассчитан на серверы с UEFI. Raspberry Pi и Orange Pi 5 загружаются иначе — для них используйте штатный Ubuntu Server для платы и `install.sh`.
