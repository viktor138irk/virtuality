# Системные требования Virtuality

Virtuality — это серверная система управления виртуализацией на базе KVM/QEMU/libvirt/Cockpit и собственной web-панели.

## Минимальные требования

Подходят только для тестового стенда и одной-двух лёгких VM.

```text
CPU: 2 ядра с аппаратной виртуализацией Intel VT-x / AMD-V
RAM: 4 GB
Диск /: минимум 8 GB свободно
Диск под /var/lib/virtuality: минимум 20 GB свободно
OS: Ubuntu Server 24.04 LTS / Debian-like с apt
Network: один проводной интерфейс
```

## Рекомендуемые требования

Для нормальной работы нескольких VM.

```text
CPU: 4+ ядра с Intel VT-x / AMD-V
RAM: 16+ GB
Диск: 100+ GB SSD/NVMe под /var/lib/virtuality
Network: проводной интерфейс 1 Gbit/s+
OS: Ubuntu Server 24.04 LTS
```

## Проверки установщика

`bootstrap.sh` и `install_virtuality_node.sh` проверяют:

- наличие root-прав;
- наличие `apt`;
- свободное место на `/`;
- свободное место для `/var/lib`;
- объём RAM;
- количество CPU cores;
- наличие флага аппаратной виртуализации `vmx` или `svm`.

## Значения по умолчанию

```text
VIRTUALITY_MIN_ROOT_FREE_MB=8192
VIRTUALITY_MIN_VAR_FREE_MB=20480
VIRTUALITY_MIN_RAM_MB=4096
VIRTUALITY_MIN_CPU_CORES=2
```

## Переопределение требований

Для тестового стенда можно временно уменьшить требования:

```bash
curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/bootstrap.sh | sudo VIRTUALITY_MIN_VAR_FREE_MB=8192 bash
```

Полностью отключить preflight-проверки можно так:

```bash
curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/bootstrap.sh | sudo VIRTUALITY_SKIP_REQUIREMENTS=1 bash
```

Использовать отключение проверок стоит только для тестов. Для реального сервера это плохая идея: VM быстро съедают диск.

## Как быстро проверить место

```bash
df -h
df -i
du -xh / --max-depth=1 2>/dev/null | sort -h
```

## Очистка перед установкой

```bash
sudo apt clean
sudo apt autoclean
sudo apt autoremove -y
sudo rm -rf /var/lib/apt/lists/*
sudo rm -rf /tmp/* /var/tmp/*
sudo journalctl --vacuum-time=3d
```

## Где Virtuality хранит данные

```text
/opt/virtuality                 # системные конфиги и установленная web-панель
/home/<user>/virtuality          # GitHub-репозиторий проекта
/var/lib/virtuality/iso          # ISO-образы
/var/lib/virtuality/images       # qcow2-диски VM
/var/lib/virtuality/backups      # будущие backup VM
/var/log/virtuality              # логи установки и диагностики
```
