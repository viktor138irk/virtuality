# Установочный образ Virtuality

Virtuality можно поставить двумя способами:

1. **Скрипт на готовый сервер** — `curl ... install.sh | bash` (см. README).
2. **Установочный ISO** — загрузочный образ «с нуля», как у Proxmox: записываете на флешку, загружаетесь, отвечаете на 3 экрана, после перезагрузки получаете готовую ноду.

Этот документ про второй способ.

---

## Что внутри образа

Образ собирается из официального, ничем не изменённого **Ubuntu Server 26.04 LTS live-server ISO** (amd64 или arm64; поддержка до 2031 года) и дополняется:

```text
/autoinstall.yaml                      # сценарий установщика Ubuntu (subiquity autoinstall)
/virtuality/source                     # исходники Virtuality (git-репозиторий нужной версии)
/virtuality/source/wheels              # Python-колёса web-панели для офлайн-установки
/virtuality/image.env                  # параметры сборки: порт панели, автообновление, пользователь
/virtuality/virtuality-firstboot.service
/boot/grub/grub.cfg                    # меню «Install Virtuality X.Y.Z»
```

Загрузочные записи BIOS и UEFI берутся из оригинального ISO без изменений.

## Как проходит установка

1. Загрузка с ISO → пункт **Install Virtuality**.
2. Установщик Ubuntu спрашивает только **сеть**, **диск** и **пользователя** (имя, пароль, hostname). Язык, раскладка, SSH-сервер и остальное заданы автоматически.
3. Установщик копирует Virtuality в `/opt/virtuality/source` и включает `virtuality-firstboot.service`.
4. После перезагрузки `virtuality-firstboot` (нужен интернет) ставит KVM/QEMU/libvirt, Cockpit, web-панель, `vhealth` и консольный dashboard. Обычно это 5–15 минут. Если сети нет или apt упал, попытка повторяется каждую минуту до успеха.
5. Готово:

```text
Web-панель: http://SERVER_IP:8088   (логин — пользователь, созданный в установщике)
Cockpit:    https://SERVER_IP:9090
```

Адрес панели также показывается на экране входа консоли. Ход первой настройки:

```bash
sudo journalctl -fu virtuality-firstboot
cat /var/log/virtuality/firstboot.log
```

---

## Сборка ISO

Нужна Linux-машина (root не нужен), ~10 GB свободного места и пакеты:

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

Скрипт сам скачает последний Ubuntu 26.04.x live-server ISO, проверит SHA256 и положит результат в `dist/`:

```text
dist/virtuality-0.10.0-ubuntu-26.04-amd64.iso
dist/virtuality-0.10.0-ubuntu-26.04-amd64.iso.sha256
```

Скачанный ISO Ubuntu кешируется в `.cache/iso`. Уже скачанный образ можно передать явно:

```bash
./image/build-iso.sh --ubuntu-iso ~/Downloads/ubuntu-26.04.1-live-server-amd64.iso
```

### Параметры

```text
--arch amd64|arm64         архитектура
--ubuntu 26.04|24.04       база образа (по умолчанию 26.04 LTS)
--ref GIT_REF              какую версию Virtuality положить в образ (по умолчанию HEAD)
--web-port PORT            порт web-панели (по умолчанию 8088)
--no-auto-update           не включать ночное автообновление с GitHub
--locale / --keyboard / --timezone
--no-wheels                не класть Python-колёса (тогда первая загрузка скачает их из PyPI)
--repo-url URL             откуда нода будет получать обновления
```

Через make параметры передаются так:

```bash
make iso ISO_ARGS="--web-port 8089 --no-auto-update --timezone Europe/Moscow"
```

### Полностью автоматическая установка

Для массовой раскатки можно собрать образ, который не задаёт вопросов:

```bash
make iso ISO_ARGS="--unattended --username admin --password-hash '$(openssl passwd -6)' --hostname node01"
```

> ⚠️ Такой образ загружается в установку сам (через 10 секунд в меню) и **без подтверждения стирает самый большой диск** машины. Файл получает суффикс `-unattended`. Не оставляйте такую флешку в серверах.

---

## Запись на флешку

Linux / macOS:

```bash
sudo dd if=dist/virtuality-0.10.0-ubuntu-26.04-amd64.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Windows: Rufus (режим DD) или balenaEtcher.

Проверка целостности:

```bash
cd dist && sha256sum -c virtuality-0.10.0-ubuntu-26.04-amd64.iso.sha256
```

---

## Сборка в GitHub Actions

Workflow `.github/workflows/image.yml` собирает ISO:

- автоматически при пуше тега `v*` (amd64 и arm64);
- вручную: Actions → **Build ISO image** → Run workflow.

Образы Ubuntu Server больше лимита GitHub Releases (2 GB на файл), поэтому ISO публикуются как **artifacts** workflow и хранятся 30 дней.

---

## Ограничения

- База образа — Ubuntu Server 26.04 LTS (или 24.04 LTS через `--ubuntu 24.04`).
- Для первой загрузки нужен интернет: пакеты KVM/libvirt/Cockpit ставятся из репозиториев Ubuntu. Python-зависимости панели уже лежат в образе.
- ARM64-образ рассчитан на серверы с UEFI. Raspberry Pi и Orange Pi 5 грузятся иначе — для них используйте их штатный образ Ubuntu Server и `install.sh`.
