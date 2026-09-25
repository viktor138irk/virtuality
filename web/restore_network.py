#!/usr/bin/env python3
"""Re-apply Virtuality NAT port forwards after boot.

nftables/iptables rules created by the panel live only in the kernel, so they
are lost on reboot. virtuality-network.service runs this once at startup.
"""
import sys

import network_core


def main() -> int:
    if not network_core.PORT_FORWARDS_FILE.exists():
        print("No port forwards configured, nothing to restore")
        return 0
    try:
        result = network_core.apply_port_forwards()
    except network_core.NetworkError as exc:
        print(f"Failed to restore port forwards: {exc}", file=sys.stderr)
        return 1
    count = len(network_core.load_port_forwards())
    print(f"Restored {count} port forward(s), nft ok={result['nft_apply']['ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
