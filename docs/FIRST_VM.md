# Первая тестовая виртуальная машина

Этот документ фиксирует проверку первого рабочего сценария Virtuality: сеть `br0`, libvirt storage pools и запуск тестовой VM.

## Требования

Перед созданием VM должны быть готовы:

- KVM/QEMU;
- libvirt;
- Cockpit;
- storage pools `virtuality-images` и `virtuality-iso`;
- bridge `br0`;
- default route через `br0`.

Проверка:

```bash
sudo vhealth
ip -br a
ip route
virsh pool-list --all
```

Нормальная сеть:

```text
enp2s0  UP
br0     UP  10.0.0.200/24
default via 10.0.0.1 dev br0
```

## Создание тестовой VM

```bash
cd ~/virtuality
git pull
sudo bash scripts/create_test_vm.sh
```

По умолчанию создаётся VM:

```text
Name:   test-alpine
RAM:    1024 MB
CPU:    1 vCPU
Disk:   8 GB qcow2
ISO:    Alpine Linux
Network: br0
Graphics: VNC
```

Можно задать имя:

```bash
sudo bash scripts/create_test_vm.sh test-alpine-2
```

## Проверка

```bash
virsh list --all
sudo virsh vncdisplay test-alpine
sudo vhealth
```

Если `vncdisplay` показывает `:0`, значит порт VNC — `5900`.

Если показывает `:1`, значит порт VNC — `5901`.

## Доступ через Cockpit

Открыть:

```text
https://SERVER_IP:9090
```

Дальше:

```text
Virtual machines → test-alpine → Console
```

## Что считается успешным результатом

Этап считается пройденным, если:

- VM появилась в `virsh list --all`;
- VM запущена;
- консоль открывается в Cockpit или через VNC;
- VM подключена к bridge `br0`;
- `sudo vhealth` не показывает критических ошибок по Virtuality.

## Следующий этап

После первой тестовой VM можно переходить к разработке собственной панели управления Virtuality:

- список VM;
- статусы VM;
- запуск/остановка;
- просмотр storage pools;
- создание VM через UI;
- базовый журнал событий.
