"""
shared/protocol.py
Low-level socket primitives used by both PC and DGX sides.
"""

import socket
import json

CHUNK_SIZE = 65536   # 64 KB — optimal for TCP on 10 GbE


def send_json(conn: socket.socket, obj: dict) -> None:
    """Serialize dict → JSON + newline and send atomically."""
    conn.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))


def recv_line(conn: socket.socket, max_bytes: int = 65536) -> bytes:
    """
    Read bytes from socket until '\n' found.
    Raises ConnectionError on disconnect, ValueError on oversized line.
    """
    buf = b""
    while b"\n" not in buf:
        if len(buf) >= max_bytes:
            raise ValueError(f"recv_line exceeded {max_bytes} bytes without newline")
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("Remote end disconnected")
        buf += chunk
    return buf.split(b"\n")[0]


def recv_exact(conn: socket.socket, n: int) -> bytes:
    """
    Read exactly n bytes from socket.
    Used for binary payloads following a JSON header.
    """
    if n == 0:
        return b""
    data = bytearray()
    while len(data) < n:
        chunk = conn.recv(min(CHUNK_SIZE, n - len(data)))
        if not chunk:
            raise ConnectionError("Disconnected mid-stream after receiving "
                                  f"{len(data)}/{n} bytes")
        data.extend(chunk)
    return bytes(data)
