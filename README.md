# Virtuality

**Virtuality** — лёгкая серверная платформа виртуализации в стиле Proxmox на базе **KVM**, **QEMU**, **libvirt**, **Cockpit** и собственной web-панели.

Цель проекта — собрать понятную, компактную и расширяемую систему для управления виртуальными машинами, ISO-образами, storage pools, сетевыми bridge-интерфейсами, диагностикой и будущими backup/cluster-функциями.

> Текущий статус: ранняя стадия разработки. Virtuality уже поднимает KVM/libvirt/Cockpit, создаёт storage pools, имеет web-панель, авторизацию Linux-пользователем, healthcheck и создание VM из интерфейса.

---

## Быстрая установка одной командой

Одна команда работает и под `root`, и под обычным пользователем с `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh | bash
```

Как выбирается пользователь для входа в web-панель:

```text
Если запуск под root      → login: root
Если запуск через sudo    → login: текущий sudo-пользователь
Если задан VIRTUALITY_USER → login: указанный пользователь
```

Принудительно выбрать пользователя:

```bash
curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh | VIRTUALITY_USER=viktor bash
```

Установка с авторизацией под `root`:

```bash
curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh | VIRTUALITY_USER=root bash
```

Если пароль выбранного Linux-пользователя не задан:

```bash
sudo passwd viktor
```

или:

```bash
passwd root
```

---

## Что уже есть

- универсальная one-command установка через `install.sh`;
- красивый пошаговый installer с live-status и логами;
- preflight-проверка системных требований;
- установка KVM/QEMU/libvirt;
- установка Cockpit и Cockpit Machines;
- рабочая директория `/opt/virtuality/source`;
- libvirt storage pools `virtuality-images` и `virtuality-iso`;
- firewall-правила для SSH, Cockpit, VNC и web-панели;
- настройка bridge `br0` отдельным безопасным скриптом через `netplan try`;
- healthcheck одной командой `vhealth`;
- консольный dashboard для физического монитора;
- web-панель Virtuality на FastAPI;
- авторизация web-панели через системного Linux-пользователя;
- список VM;
- управление VM: Start, Shutdown, Reboot, Power off, Autostart on/off, Delete VM with disks;
- создание VM из web-интерфейса;
- страница деталей VM: `dominfo`, VNC display, диски, сетевые интерфейсы.

---

## Системные требования

Минимально для тестового стенда:

```text
CPU: 2 ядра с Intel VT-x / AMD-V
RAM: 4 GB
/: минимум 8 GB свободно
/var/lib: минимум 20 GB свободно
OS: Ubuntu Server 24.04 LTS / Debian-like с apt
Network: один проводной интерфейс
```

Рекомендуемо для нормальной работы:

```text
CPU: 4+ ядра
RAM: 16+ GB
Storage: 100+ GB SSD/NVMe под /var/lib/virtuality
Network: 1 Gbit/s+
OS: Ubuntu Server 24.04 LTS
```

Подробнее: [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md)

---

## Порты

```text
Cockpit:       https://SERVER_IP:9090
Virtuality UI: http://SERVER_IP:8088
VNC:           5900-5999/tcp
SSH:           22/tcp
```

Изменить порт web-панели:

```bash
curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh | VIRTUALITY_WEB_PORT=8089 bash
```

---

## Директории

```text
/opt/virtuality/source          # исходники проекта из GitHub
/opt/virtuality/web             # установленная web-панель
/opt/virtuality/venv            # Python virtualenv web-панели
/opt/virtuality/virtuality.env  # базовый env ноды
/var/lib/virtuality/iso         # ISO-образы
/var/lib/virtuality/images      # qcow2-диски VM
/var/lib/virtuality/backups     # backups
/var/log/virtuality             # логи установки и диагностики
```

---

## Диагностика

```bash
sudo vhealth
```

Проверяются CPU virtualization, `/dev/kvm`, `virsh`, QEMU, libvirt, Cockpit, web-панель, bridge, netplan, firewall, storage pools, VM, директории, диск и память.

---

## Bridge br0

По умолчанию one-command установщик **не включает `br0` автоматически**, чтобы не уронить SSH-сессию.

Сначала нужно посмотреть сетевой интерфейс:

```bash
ip -br a
ip route
```

Затем запустить bridge setup, например для `enp2s0`:

```bash
cd /opt/virtuality/source
sudo bash scripts/setup_bridge_br0.sh enp2s0 static
```

Ожидаемый результат:

```text
enp2s0  UP
br0     UP  SERVER_IP/24
default via GATEWAY dev br0
```

---

## Web-панель

Переустановка web-панели:

```bash
cd /opt/virtuality/source
sudo bash scripts/install_web_panel.sh
```

Статус:

```bash
systemctl status virtuality-web --no-pager
```

Логи:

```bash
journalctl -u virtuality-web -f
```

Вход:

```text
URL:    http://SERVER_IP:8088
Login:  выбранный Linux-пользователь
Pass:   пароль этого Linux-пользователя
```

---

## Создание тестовой VM

Через web-панель:

```text
Virtuality UI → + Создать VM
```

Или командой:

```bash
cd /opt/virtuality/source
sudo bash scripts/create_test_vm.sh
```

Alpine ISO:

```bash
sudo wget -O /var/lib/virtuality/iso/alpine-standard.iso https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-standard-3.20.3-x86_64.iso
sudo virsh pool-refresh virtuality-iso
```

---

## Повторная установка / обновление

```bash
cd /opt/virtuality/source
sudo git pull
sudo bash install_virtuality_node.sh
sudo bash scripts/install_web_panel.sh
```

---

## Полезные команды

```bash
sudo vhealth
virsh list --all
virsh pool-list --all
ip -br a
ip route
sudo ufw status
systemctl status libvirtd --no-pager
systemctl status cockpit.socket --no-pager
systemctl status virtuality-web --no-pager
```

---

## Roadmap

- загрузка ISO из web-интерфейса;
- web-console/noVNC;
- управление storage pools;
- backup/snapshot manager;
- сетевой менеджер bridge/VLAN;
- роли и права пользователей;
- журнал событий;
- автообновление;
- кластеризация.

---

## Лицензия

Рекомендуемая лицензия: **MIT License**.
