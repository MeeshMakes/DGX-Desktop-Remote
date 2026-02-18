"""
pc-application/src/network/port_negotiator.py

Automatic port negotiation between PC and DGX.

Protocol:
  1. PC scans its own local ports to find 3 free ones in the range 22010-22059.
  2. PC connects to the DGX on a fixed DISCOVERY port (22000).
  3. PC sends:  {"type": "negotiate", "candidates": [p1, p2, p3, ...]}
  4. DGX checks which candidates it can bind, picks the first 3 it can use,
     and responds: {"ok": true, "rpc": N, "video": N, "input": N}
  5. Both sides lock in those three ports.

The discovery port (22000) is the ONLY hard-coded port in the system.
"""

import socket
import json
import time
import logging
from typing import Optional

log = logging.getLogger(__name__)

DISCOVERY_PORT   = 22000          # The one fixed "handshake" port on DGX
PORT_RANGE_START = 22010
PORT_RANGE_END   = 22059          # 50 candidates to scan
CONNECT_TIMEOUT  = 5.0


def _is_port_free_local(port: int) -> bool:
    """Return True if no process on this machine is listening on the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def scan_local_free_ports(count: int = 3) -> list[int]:
    """
    Return up to `count` port numbers in [22010-22059] that are currently
    not in-use on this PC.
    """
    free = []
    for p in range(PORT_RANGE_START, PORT_RANGE_END + 1):
        if _is_port_free_local(p):
            free.append(p)
            if len(free) >= count * 3:   # send plenty of candidates
                break
    return free


def negotiate_ports(
    dgx_ip: str,
    timeout: float = CONNECT_TIMEOUT,
) -> Optional[dict]:
    """
    Connect to DGX discovery port and agree on RPC, video, and input ports.

    Returns:
        {"rpc": int, "video": int, "input": int}  on success
        None on failure
    """
    candidates = scan_local_free_ports(count=3)
    if len(candidates) < 3:
        log.error("Could not find 3 free local ports in range %d-%d",
                  PORT_RANGE_START, PORT_RANGE_END)
        return None

    log.info("Port candidates (PC free): %s", candidates)

    try:
        sock = socket.create_connection((dgx_ip, DISCOVERY_PORT), timeout=timeout)
    except OSError as e:
        log.debug("Cannot reach DGX discovery port %d: %s", DISCOVERY_PORT, e)
        return None

    try:
        # Send candidate list
        msg = json.dumps({
            "type":       "negotiate",
            "candidates": candidates,
        }) + "\n"
        sock.sendall(msg.encode())

        # Read response
        buf = b""
        sock.settimeout(timeout)
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk

        resp = json.loads(buf.split(b"\n")[0].decode())
        if resp.get("ok"):
            result = {
                "rpc":   resp["rpc"],
                "video": resp["video"],
                "input": resp["input"],
            }
            log.info("Negotiated ports: RPC=%d  Video=%d  Input=%d",
                     result["rpc"], result["video"], result["input"])
            return result
        else:
            log.debug("DGX rejected negotiation: %s", resp.get("error"))
            return None
    except Exception as e:
        log.debug("Negotiation error: %s", e)
        return None
    finally:
        sock.close()
