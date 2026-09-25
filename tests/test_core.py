import json
from pathlib import Path

import pytest

import network_core
import restore_network
import update_core


def test_parse_port_range():
    assert network_core.parse_port_range("80") == (80, 80)
    assert network_core.parse_port_range("10000-10010") == (10000, 10010)
    assert network_core.parse_port_range("10000:10010") == (10000, 10010)
    for bad in ("", "0", "70000", "20-10", "abc", "80;rm"):
        with pytest.raises(network_core.NetworkError):
            network_core.parse_port_range(bad)


def test_add_port_forward_rejects_overlap(data_dirs):
    network_core.add_port_forward("web01", "192.168.100.51", "8000-8010", "8000-8010", "tcp")
    with pytest.raises(network_core.NetworkError):
        network_core.add_port_forward("web01", "192.168.100.52", "8005", "80", "tcp")
    network_core.add_port_forward("web01", "192.168.100.52", "8005", "80", "udp")


def test_add_port_forward_rejects_mismatched_ranges(data_dirs):
    with pytest.raises(network_core.NetworkError):
        network_core.add_port_forward("web01", "192.168.100.51", "8000-8010", "80", "tcp")


def test_render_nft_rules_range(data_dirs):
    network_core.add_port_forward("web01", "192.168.100.51", "10000-10002", "20000-20002", "udp")
    rules = network_core.render_nft_rules()
    assert 'iifname "eth0" udp dport 10000-10002 dnat to 192.168.100.51:20000-20002' in rules
    assert "masquerade" in rules


def test_restore_network_noop_without_config(data_dirs, capsys):
    assert restore_network.main() == 0
    assert "nothing to restore" in capsys.readouterr().out


def test_restore_network_applies_rules(data_dirs):
    data_dirs["network"].joinpath("port_forwards.json").write_text(json.dumps([
        {"id": "1", "vm_name": "web01", "guest_ip": "192.168.100.51", "external_port_start": 2222, "external_port_end": 2222, "guest_port_start": 22, "guest_port_end": 22, "protocol": "tcp"},
    ]))
    assert restore_network.main() == 0
    assert "dport 2222 dnat to 192.168.100.51:22" in (data_dirs["nft"] / "virtuality.nft").read_text()


def test_version_tuple_ordering():
    assert update_core.version_tuple("0.9.6") < update_core.version_tuple("0.10.0")
    assert update_core.version_tuple("v1.0") == update_core.version_tuple("1.0.0")


def test_missing_versions():
    manifest = {"versions": [{"version": v} for v in ("0.9.5", "0.9.6", "0.10.0", "0.10.1")]}
    missed = update_core.missing_versions("0.9.6", "0.10.1", manifest)
    assert [item["version"] for item in missed] == ["0.10.0", "0.10.1"]


def test_versions_manifest_matches_version_file():
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text().strip()
    manifest = json.loads((root / "updates" / "versions.json").read_text())
    assert version in [item["version"] for item in manifest["versions"]]


def test_presenters_parse_virsh_tables():
    import presenters

    disks = presenters.parse_domblklist(" Type   Device   Target   Source\n------------------------------------\n file   disk     vda      /var/lib/virtuality/images/web01.qcow2\n file   cdrom    sda      -\n")
    assert [d["name"] for d in disks] == ["web01.qcow2", "Пусто"]
    assert disks[1]["is_cdrom"]
    nics = presenters.parse_domiflist(" Interface   Type      Source           Model    MAC\n---\n vnet0       network   virtuality-nat   virtio   52:54:00:aa:bb:cc\n")
    assert nics[0]["mac"] == "52:54:00:aa:bb:cc"
    info = presenters.parse_dominfo("State: shut off\nCPU(s): 4\nMax memory: 8388608 KiB\nUsed memory: 8388608 KiB\nAutostart: disable\n")
    assert (info["vcpus"], info["memory_mb"], info["autostart"]) == (4, 8192, False)
    assert presenters.vm_state("shut off")["label"] == "Выключена"
    assert presenters.format_mb(1536) == "1.5 ГБ"
    assert presenters.plural(22, "ядро", "ядра", "ядер") == "ядра"


def test_update_not_offered_when_local_is_newer(tmp_path, monkeypatch, data_dirs):
    import subprocess

    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    git("init", "-q", "-b", "main", cwd=remote)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base", cwd=remote)
    git("clone", "-q", str(remote), str(local), cwd=tmp_path)
    (local / "VERSION").write_text("0.10.0\n")
    git("add", "VERSION", cwd=local)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "newer local build", cwd=local)
    monkeypatch.setattr(update_core, "SOURCE_DIR", local)
    assert update_core.check_updates(fetch=True)["has_update"] is False

    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "upstream", cwd=remote)
    git("reset", "-q", "--hard", "origin/main", cwd=local)
    assert update_core.check_updates(fetch=True)["has_update"] is True
