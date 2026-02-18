# DGX Desktop Remote — Connection Architecture

**Version**: 2.0  
**Status**: Implementation-Ready  
**Last Updated**: February 18, 2026  
**Platform**: PC = Windows 11 / DGX = Ubuntu Linux (NVIDIA DGX OS)

---

## 1. Overview

DGX Desktop Remote is a two-machine communication system. The PC runs a GUI client application. The DGX runs a headless background service. They communicate over a **direct 10 GbE Ethernet cable** using TCP/IP — no Wi-Fi, no router, no internet required.

This document covers:
- Physical hardware link and IP configuration
- Three-channel TCP connection model
- Wire protocol (byte-level framing)
- Server and client threading model
- Screen capture and input injection
- Firewall requirements on Windows
- Full implementation-ready Python code

---

## 2. Hardware & Network Layer

### 2.1 Physical Connection

```
┌─────────────────────┐                    ┌─────────────────────┐
│  PC (Windows 11)    │                    │  DGX (Ubuntu Linux) │
│                     │                    │                     │
│  NIC: 10GbE Adapter │◄──── Cat6A/DAC ───►│  NIC: 10GbE NIC     │
│  IP:  10.0.0.2/24   │   Direct Cable     │  IP:  10.0.0.1/24   │
│  Port: 12010        │   (No switch)      │  Ports: 22010-22012 │
└─────────────────────┘                    └─────────────────────┘
```

**Key facts**:
- Direct point-to-point cable. No router, no switch, no DHCP server.
- Both IPs are statically assigned in the 10.0.0.0/24 private subnet.
- 10.0.0.0/24 is non-routable — traffic cannot leave this subnet.
- Theoretical max bandwidth: 10 Gbps (1.25 GB/s)
- Real-world file throughput: ~600–900 MB/s (limited by disk I/O)
- RTT (ping) on 10 GbE direct cable: typically < 0.5 ms

### 2.2 Static IP Configuration

**On PC (Windows 11)** — Network Adapter Properties:
```
IP Address:      10.0.0.2
Subnet Mask:     255.255.255.0
Default Gateway: (leave blank)
DNS:             (leave blank)
```

**On DGX (Ubuntu)** — Netplan config:
```bash
# /etc/netplan/01-10gbe.yaml
network:
  version: 2
  ethernets:
    [interface-name]:               # find with: ip link show
      addresses:
        - 10.0.0.1/24
      dhcp4: false
      optional: true

# Apply changes:
sudo netplan apply
```

**Verify connectivity**:
```bash
# From DGX, ping PC:
ping 10.0.0.2 -c 4

# From PC (PowerShell), ping DGX:
ping 10.0.0.1
```

### 2.3 Port Allocation — Three Dedicated Channels

| Machine | Port | Protocol | Purpose |
|---------|------|----------|---------|
| DGX | 22010 | TCP | Control channel: RPC, file transfer, handshake |
| DGX | 22011 | TCP | Video channel: continuous JPEG frame stream |
| DGX | 22012 | TCP | Input channel: mouse and keyboard events |
| PC  | 12010 | TCP | Reverse channel: DGX → PC file push (future) |

**Why three separate ports?**

Mixing video frames, input events, and file transfers on a single TCP stream creates **head-of-line blocking** — a large file upload would stall mouse input delivery. With dedicated channels:
- Input events are never delayed by frame data
- File transfers never block the control channel
- Video stream can be paused independently

---

## 3. TCP Connection Architecture

### 3.1 Roles

- **DGX service** = always the server. Listens on all three ports at all times.
- **PC application** = always the client. Connects when user clicks "Connect".
- Once connected, both ends can initiate messages, but the PC always opens the TCP connection.

This is the same proven pattern from the Fathom Bridge.

### 3.2 Full Connection Lifecycle

```
[DGX Service starts on boot or login]
    │
    ├── Listen: 10.0.0.1:22010  (control/RPC)
    ├── Listen: 10.0.0.1:22011  (video stream)
    └── Listen: 10.0.0.1:22012  (input events)

[PC User clicks "Connect"]
    │
    ├── TCP connect → 10.0.0.1:22010
    │   ├── Send hello handshake
    │   └── Receive DGX info (resolution, OS, capabilities)
    │
    ├── TCP connect → 10.0.0.1:22011
    │   └── Send start_stream → DGX begins sending JPEG frames
    │
    └── TCP connect → 10.0.0.1:22012
        └── Send start_input → ready to receive mouse/keyboard

[During connected session]
    ├── Video channel: DGX → PC  (60 JPEG frames/sec)
    ├── Input channel: PC → DGX  (mouse_move, key_press, etc.)
    └── Control channel: bidirectional (file ops, ping, resolution events)

[Disconnect — user closes app or cable unplugs]
    ├── PC sends: {"type": "goodbye"}  (if clean close)
    ├── PC closes all 3 sockets
    └── DGX detects disconnection, returns all ports to listen state
```

---

## 4. Python Implementation

### 4.1 Shared Utilities (both sides use these)

```python
# shared/protocol.py

import socket
import json

CHUNK_SIZE = 65536  # 64 KB — optimal for TCP on 10 GbE


def send_json(conn: socket.socket, obj: dict) -> None:
    """Serialize dict to JSON and send with newline delimiter."""
    conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def recv_line(conn: socket.socket, max_bytes: int = 65536) -> bytes:
    """
    Read bytes from socket until newline found.
    Used to receive JSON header lines.
    Raises ConnectionError if client disconnects.
    """
    buf = b""
    while b"\n" not in buf:
        if len(buf) >= max_bytes:
            raise ValueError(f"recv_line exceeded {max_bytes} bytes without newline")
        chunk = conn.recv(1024)
        if not chunk:
            raise ConnectionError("Remote end disconnected")
        buf += chunk
    return buf.split(b"\n")[0]


def recv_exact(conn: socket.socket, n: int) -> bytes:
    """
    Read exactly n bytes from socket.
    Used to receive binary file/frame payloads after JSON header.
    """
    if n == 0:
        return b""
    data = bytearray()
    while len(data) < n:
        chunk = conn.recv(min(CHUNK_SIZE, n - len(data)))
        if not chunk:
            raise ConnectionError("Disconnected mid-stream")
        data.extend(chunk)
    return bytes(data)
```

### 4.2 DGX Server — Multi-Port Listener

```python
# dgx-service/src/server.py

import socket
import threading
import logging
from typing import Callable

log = logging.getLogger("dgx.server")

DGX_IP     = "10.0.0.1"
RPC_PORT   = 22010
VIDEO_PORT = 22011
INPUT_PORT = 22012


class PortListener:
    """
    Listens on one TCP port, spawns a new thread per accepted connection.
    Each accepted connection runs handler(conn, addr).
    """

    def __init__(self, host: str, port: int, handler: Callable):
        self.host    = host
        self.port    = port
        self.handler = handler
        self._sock   = None
        self._thread = None

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Disable Nagle — lower latency for small input event messages
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(2)
        log.info(f"Listening on {self.host}:{self.port}")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._sock:
            self._sock.close()

    def _loop(self):
        while True:
            try:
                conn, addr = self._sock.accept()
                log.info(f"[:{self.port}] connection from {addr[0]}:{addr[1]}")
                threading.Thread(
                    target=self.handler, args=(conn, addr), daemon=True
                ).start()
            except OSError:
                break


class DGXService:
    """Starts all three listener ports and coordinates the service."""

    def __init__(self, rpc_handler, video_handler, input_handler):
        self.rpc_listener   = PortListener(DGX_IP, RPC_PORT,   rpc_handler)
        self.video_listener = PortListener(DGX_IP, VIDEO_PORT, video_handler)
        self.input_listener = PortListener(DGX_IP, INPUT_PORT, input_handler)

    def start(self):
        self.rpc_listener.start()
        self.video_listener.start()
        self.input_listener.start()
        log.info("DGX service started on all ports")

    def stop(self):
        self.rpc_listener.stop()
        self.video_listener.stop()
        self.input_listener.stop()
```

### 4.3 PC Client — Three-Channel Connection Manager

```python
# pc-application/src/network/connection.py

import socket
import json
import threading
import logging
from typing import Optional, Callable
from shared.protocol import send_json, recv_line, recv_exact, CHUNK_SIZE

log = logging.getLogger("pc.connection")

PC_IP      = "10.0.0.2"
DGX_IP     = "10.0.0.1"
RPC_PORT   = 22010
VIDEO_PORT = 22011
INPUT_PORT = 22012

CONNECT_TIMEOUT = 5.0    # seconds to wait during initial connection
RPC_TIMEOUT     = 3.0    # seconds to wait for an RPC response


class DGXConnection:
    """
    Manages the three TCP channels to DGX.
    Thread-safe. Emits callbacks for connection events.
    """

    def __init__(self,
                 on_frame: Optional[Callable[[bytes], None]] = None,
                 on_disconnect: Optional[Callable] = None):
        self._rpc_sock:   Optional[socket.socket] = None
        self._video_sock: Optional[socket.socket] = None
        self._input_sock: Optional[socket.socket] = None
        self._rpc_lock = threading.Lock()
        self._connected  = False
        self._on_frame      = on_frame
        self._on_disconnect = on_disconnect

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Connect / Disconnect
    # ------------------------------------------------------------------

    def connect(self) -> dict:
        """
        Open all three channels, complete handshake, start video thread.
        Returns DGX info dict on success. Raises on any failure.
        """
        try:
            # 1. Control channel + handshake
            self._rpc_sock = self._make_socket(DGX_IP, RPC_PORT)
            send_json(self._rpc_sock, {
                "type": "hello",
                "agent": "PC",
                "version": "1.0",
                "capabilities": ["file_transfer", "screen_view", "input_control"]
            })
            raw  = recv_line(self._rpc_sock)
            info = json.loads(raw)
            if not info.get("ok"):
                raise ConnectionError(f"Handshake rejected: {info.get('error')}")

            # 2. Video channel
            self._video_sock = self._make_socket(DGX_IP, VIDEO_PORT)
            send_json(self._video_sock, {"type": "start_stream", "fps": 60, "encoding": "jpeg", "quality": 85})

            # 3. Input channel
            self._input_sock = self._make_socket(DGX_IP, INPUT_PORT)
            send_json(self._input_sock, {"type": "start_input"})

            self._connected = True

            # Start background video reader
            if self._on_frame:
                threading.Thread(target=self._video_loop, daemon=True).start()

            log.info(f"Connected to DGX. Display: {info.get('display')}")
            return info

        except Exception:
            self.disconnect()
            raise

    def disconnect(self):
        """Close all channels cleanly."""
        self._connected = False
        for sock in (self._rpc_sock, self._video_sock, self._input_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._rpc_sock = self._video_sock = self._input_sock = None
        if self._on_disconnect:
            self._on_disconnect()

    # ------------------------------------------------------------------
    # Input Events (PC → DGX) — fire and forget
    # ------------------------------------------------------------------

    def send_mouse_move(self, x: int, y: int):
        self._send_input({"type": "mouse_move", "x": x, "y": y})

    def send_mouse_press(self, button: str, x: int, y: int):
        self._send_input({"type": "mouse_press", "button": button, "x": x, "y": y})

    def send_mouse_release(self, button: str, x: int, y: int):
        self._send_input({"type": "mouse_release", "button": button, "x": x, "y": y})

    def send_mouse_scroll(self, dy: int, x: int, y: int):
        self._send_input({"type": "mouse_scroll", "dy": dy, "x": x, "y": y})

    def send_key_press(self, key: str, modifiers: list = None):
        self._send_input({"type": "key_press", "key": key, "modifiers": modifiers or []})

    def send_key_release(self, key: str, modifiers: list = None):
        self._send_input({"type": "key_release", "key": key, "modifiers": modifiers or []})

    def _send_input(self, event: dict):
        if not self._input_sock:
            return
        try:
            send_json(self._input_sock, event)
        except Exception as e:
            log.warning(f"Input send error: {e}")
            self._connected = False

    # ------------------------------------------------------------------
    # RPC (control channel — bidirectional, thread-safe)
    # ------------------------------------------------------------------

    def rpc(self, request: dict, timeout: float = RPC_TIMEOUT) -> dict:
        """Send an RPC request and wait for the response. Thread-safe."""
        with self._rpc_lock:
            if not self._rpc_sock:
                return {"ok": False, "error": "not_connected"}
            try:
                self._rpc_sock.settimeout(timeout)
                send_json(self._rpc_sock, request)
                raw = recv_line(self._rpc_sock, max_bytes=512_000)
                return json.loads(raw)
            except socket.timeout:
                return {"ok": False, "error": "timeout"}
            except Exception as e:
                log.error(f"RPC error: {e}")
                self._connected = False
                return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # File Transfer
    # ------------------------------------------------------------------

    def send_file(self,
                  local_path: str,
                  remote_folder: str = "inbox",
                  progress_cb: Optional[Callable[[int, int], None]] = None) -> dict:
        """
        Upload a single file to DGX.
        progress_cb(bytes_sent, total_bytes) called periodically.
        Returns {"ok": True, "checksum": "..."} on success.
        """
        import hashlib
        from pathlib import Path

        p = Path(local_path)
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": "file_not_found"}

        size = p.stat().st_size
        hasher = hashlib.sha256()

        with self._rpc_lock:
            try:
                self._rpc_sock.settimeout(600.0)  # 10 min for huge files
                send_json(self._rpc_sock, {
                    "type": "put_file",
                    "filename": p.name,
                    "size": size,
                    "destination": remote_folder,
                    "checksum_method": "sha256"
                })

                sent = 0
                with p.open("rb") as f:
                    while chunk := f.read(CHUNK_SIZE):
                        self._rpc_sock.sendall(chunk)
                        hasher.update(chunk)
                        sent += len(chunk)
                        if progress_cb:
                            progress_cb(sent, size)

                raw = recv_line(self._rpc_sock)
                result = json.loads(raw)

                # Verify checksum matches
                local_checksum = hasher.hexdigest()
                if result.get("checksum") and result["checksum"] != local_checksum:
                    return {"ok": False, "error": "checksum_mismatch"}

                return result

            except Exception as e:
                self._connected = False
                return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Video Loop (background thread)
    # ------------------------------------------------------------------

    def _video_loop(self):
        """Continuously receive JPEG frames, call on_frame callback."""
        import time
        while self._connected and self._video_sock:
            try:
                raw    = recv_line(self._video_sock)
                header = json.loads(raw)
                if header.get("type") != "frame":
                    continue
                size = int(header["size"])
                data = recv_exact(self._video_sock, size)
                self._on_frame(data)
            except (ConnectionError, OSError):
                break
            except Exception as e:
                log.debug(f"Video loop error: {e}")

        self._connected = False
        if self._on_disconnect:
            self._on_disconnect()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_socket(host: str, port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect((host, port))
        sock.settimeout(None)
        return sock
```

---

## 5. Wire Protocol Reference

### 5.1 Message Framing

All messages use **newline-delimited JSON (NDJSON)**:
```
{"type": "some_request", "key": "value"}\n
```

Messages with binary payloads (file upload, video frame):
```
{"type": "put_file", "filename": "data.bin", "size": 1048576}\n
[exactly 1048576 bytes of binary data — no delimiter]
{"ok": true, "received": 1048576, "checksum": "abc123..."}\n
```

### 5.2 Full RPC Catalog

#### Control Channel (22010)

| `type` | Direction | Payload | Response |
|--------|-----------|---------|----------|
| `hello` | PC→DGX | `{agent, version, capabilities}` | DGX info + display |
| `goodbye` | PC→DGX | — | none (close) |
| `put_file` | PC→DGX | `{filename, size, destination}` + binary | `{ok, received, checksum}` |
| `get_file` | PC→DGX | `{filename, folder}` | `{ok, size}` + binary + `{ok, checksum}` |
| `list_files` | PC→DGX | `{folder}` | `{ok, files: [...], counts: {...}}` |
| `delete_file` | PC→DGX | `{folder, name}` | `{ok}` |
| `move_file` | PC→DGX | `{src_folder, src_name, dst_folder, dst_name}` | `{ok, new_path}` |
| `mkdir` | PC→DGX | `{folder, name}` | `{ok, path}` |
| `get_system_info` | PC→DGX | — | `{ok, hostname, os, gpus, disk_free_gb}` |
| `resolution_changed` | DGX→PC | `{old, new, refresh_hz}` | — (push event) |
| `ping` | either | `{ts}` | `pong` with same ts |

#### Video Channel (22011)

| `type` | Direction | Notes |
|--------|-----------|-------|
| `start_stream` | PC→DGX | `{fps: 60, encoding: "jpeg", quality: 85}` |
| `stop_stream` | PC→DGX | Pause stream |
| `request_keyframe` | PC→DGX | Force full JPEG (not diff) |
| `frame` | DGX→PC | `{frame_id, size, timestamp_ms}` + binary JPEG |

#### Input Channel (22012)

| `type` | Direction | Fields |
|--------|-----------|--------|
| `start_input` | PC→DGX | handshake |
| `mouse_move` | PC→DGX | `{x, y}` |
| `mouse_press` | PC→DGX | `{button, x, y}` — button = "left"/"right"/"middle" |
| `mouse_release` | PC→DGX | `{button, x, y}` |
| `mouse_scroll` | PC→DGX | `{dy, x, y}` — dy = ±3 |
| `key_press` | PC→DGX | `{key, modifiers: []}` — Qt key names |
| `key_release` | PC→DGX | `{key, modifiers: []}` |

---

## 6. DGX RPC Handler

```python
# dgx-service/src/rpc_handler.py

import json, socket, os, hashlib, subprocess, platform, shutil
from pathlib import Path
from shared.protocol import send_json, recv_line, recv_exact, CHUNK_SIZE
import logging

log = logging.getLogger("dgx.rpc")

BASE    = Path.home() / "Desktop" / "PC-Transfer"
FOLDERS = {k: BASE / k for k in ["staging", "inbox", "outbox", "archive"]}

for p in FOLDERS.values():
    p.mkdir(parents=True, exist_ok=True)


def handle_rpc_connection(conn: socket.socket, addr):
    log.info(f"RPC from {addr[0]}")
    try:
        raw = recv_line(conn)
        msg = json.loads(raw)

        if msg.get("type") != "hello":
            send_json(conn, {"ok": False, "error": "expected_hello"})
            return

        send_json(conn, _build_hello())

        while True:
            raw = recv_line(conn)
            if not raw:
                break
            msg = json.loads(raw)
            t = msg.get("type", "")

            if   t == "goodbye":         break
            elif t == "put_file":        _put_file(conn, msg)
            elif t == "get_file":        _get_file(conn, msg)
            elif t == "list_files":      _list_files(conn, msg)
            elif t == "delete_file":     _delete_file(conn, msg)
            elif t == "move_file":       _move_file(conn, msg)
            elif t == "mkdir":           _mkdir(conn, msg)
            elif t == "get_system_info": _system_info(conn, msg)
            elif t == "ping":            send_json(conn, {"type": "pong", "ts": msg.get("ts")})
            else:                        send_json(conn, {"ok": False, "error": f"unknown:{t}"})

    except (ConnectionError, json.JSONDecodeError, OSError) as e:
        log.info(f"RPC session ended: {e}")
    finally:
        conn.close()


def _put_file(conn, msg):
    name    = Path(msg["filename"]).name   # Sanitize: strip any path components
    size    = int(msg["size"])
    dest    = FOLDERS.get(msg.get("destination", "inbox"), FOLDERS["inbox"])
    dest.mkdir(parents=True, exist_ok=True)
    path    = dest / name
    hasher  = hashlib.sha256()
    received = 0

    try:
        with path.open("wb") as f:
            while received < size:
                chunk = conn.recv(min(CHUNK_SIZE, size - received))
                if not chunk:
                    raise ConnectionError("Client dropped during upload")
                f.write(chunk)
                hasher.update(chunk)
                received += len(chunk)

        send_json(conn, {"ok": True, "received": received, "checksum": hasher.hexdigest()})

    except Exception as e:
        send_json(conn, {"ok": False, "error": str(e), "received": received})
        if path.exists() and received < size:
            path.unlink()  # Remove partial file


def _get_file(conn, msg):
    folder = FOLDERS.get(msg.get("folder", "staging"), FOLDERS["staging"])
    name   = Path(msg.get("filename", "")).name
    path   = folder / name

    if not path.is_file():
        send_json(conn, {"ok": False, "error": "not_found"})
        return

    size   = path.stat().st_size
    hasher = hashlib.sha256()
    send_json(conn, {"ok": True, "filename": name, "size": size})

    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            conn.sendall(chunk)
            hasher.update(chunk)

    send_json(conn, {"ok": True, "checksum": hasher.hexdigest()})


def _list_files(conn, msg):
    folder = FOLDERS.get(msg.get("folder", "staging"), FOLDERS["staging"])
    files  = []

    if folder.exists():
        for p in sorted(folder.iterdir()):
            st = p.stat()
            files.append({
                "name":     p.name,
                "size":     st.st_size,
                "modified": round(st.st_mtime),
                "is_dir":   p.is_dir(),
                "perms":    oct(st.st_mode & 0o777)
            })

    counts = {
        k: len(list(p.iterdir())) if p.exists() else 0
        for k, p in FOLDERS.items()
    }

    send_json(conn, {"ok": True, "files": files, "counts": counts})


def _delete_file(conn, msg):
    folder = FOLDERS.get(msg.get("folder", "staging"), FOLDERS["staging"])
    name   = Path(msg.get("name", "")).name
    path   = folder / name
    try:
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            import shutil as sh; sh.rmtree(path)
        send_json(conn, {"ok": True})
    except Exception as e:
        send_json(conn, {"ok": False, "error": str(e)})


def _move_file(conn, msg):
    src = FOLDERS.get(msg.get("src_folder","staging"), FOLDERS["staging"]) / Path(msg.get("src_name","")).name
    dst = FOLDERS.get(msg.get("dst_folder","archive"), FOLDERS["archive"]) / Path(msg.get("dst_name", msg.get("src_name",""))).name
    try:
        src.rename(dst)
        send_json(conn, {"ok": True, "new_path": str(dst)})
    except Exception as e:
        send_json(conn, {"ok": False, "error": str(e)})


def _mkdir(conn, msg):
    folder = FOLDERS.get(msg.get("folder","staging"), FOLDERS["staging"])
    name   = Path(msg.get("name","")).name
    path   = folder / name
    try:
        path.mkdir(parents=True, exist_ok=True)
        send_json(conn, {"ok": True, "path": str(path)})
    except Exception as e:
        send_json(conn, {"ok": False, "error": str(e)})


def _system_info(conn, msg):
    info = {
        "ok": True,
        "hostname": platform.node(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "disk_free_gb": round(shutil.disk_usage(str(BASE)).free / 1e9, 1),
        "gpus": []
    }
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"], timeout=3
        ).decode().strip()
        for line in out.splitlines():
            parts = [v.strip() for v in line.split(",")]
            if len(parts) == 3:
                info["gpus"].append({
                    "name": parts[0],
                    "memory_total_mb": int(parts[1]),
                    "memory_free_mb": int(parts[2])
                })
    except Exception:
        pass
    send_json(conn, info)


def _build_hello() -> dict:
    import re
    w, h, hz = 1920, 1080, 60
    try:
        xr = subprocess.check_output(["xrandr"], timeout=2).decode()
        m  = re.search(r'(\d+)x(\d+)\s+([\d.]+)\*', xr)
        if m:
            w, h, hz = int(m.group(1)), int(m.group(2)), int(float(m.group(3)))
    except Exception:
        pass

    disp_server = "Wayland" if os.environ.get("WAYLAND_DISPLAY") else "X11"

    return {
        "ok": True, "agent": "DGX", "version": "1.0",
        "hostname": platform.node(),
        "display": {"width": w, "height": h, "refresh_hz": hz, "display_server": disp_server}
    }
```

---

## 7. DGX Screen Capture

```python
# dgx-service/src/screen_capture.py

import socket, json, time, logging
from io import BytesIO
from shared.protocol import send_json, recv_line

log      = logging.getLogger("dgx.video")
FPS_CAP  = 60
JPEG_Q   = 85   # 1-100. 85 = good quality, ~150-300 KB/frame at 1080p


def _capture_jpeg() -> bytes:
    """
    Capture the full X11 display as JPEG bytes.
    Uses 'mss' library (pip install mss) — pure Python, no subprocess.
    Falls back to scrot if mss unavailable.
    """
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            monitor = sct.monitors[1]          # monitor[0] = all; [1] = primary
            shot    = sct.grab(monitor)
            img     = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            buf     = BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_Q, optimize=True)
            return buf.getvalue()

    except ImportError:
        # Fallback: scrot (apt install scrot)
        import subprocess
        result = subprocess.run(
            ["scrot", "--quality", str(JPEG_Q), "-"],
            capture_output=True, timeout=0.15
        )
        return result.stdout

    except Exception as e:
        log.error(f"Capture error: {e}")
        return b""


def handle_video_connection(conn: socket.socket, addr):
    """Stream JPEG frames to connected PC at requested FPS."""
    log.info(f"Video stream started for {addr[0]}")
    frame_id = 0

    try:
        raw = recv_line(conn)
        msg = json.loads(raw)
        if msg.get("type") != "start_stream":
            return

        fps          = max(1, min(int(msg.get("fps", 60)), FPS_CAP))
        frame_budget = 1.0 / fps

        while True:
            t0   = time.monotonic()
            data = _capture_jpeg()

            if data:
                send_json(conn, {
                    "type":         "frame",
                    "frame_id":     frame_id,
                    "size":         len(data),
                    "timestamp_ms": int(time.time() * 1000)
                })
                conn.sendall(data)
                frame_id += 1

            sleep = frame_budget - (time.monotonic() - t0)
            if sleep > 0:
                time.sleep(sleep)

    except (BrokenPipeError, ConnectionError, OSError):
        log.info("Video client disconnected")
    finally:
        conn.close()
```

---

## 8. DGX Input Injection

```python
# dgx-service/src/input_handler.py

import socket, json, subprocess, logging
from shared.protocol import recv_line

log = logging.getLogger("dgx.input")

BUTTON_MAP   = {"left": "1", "middle": "2", "right": "3"}
MODIFIER_MAP = {"ctrl": "ctrl", "shift": "shift", "alt": "alt", "super": "super"}


def handle_input_connection(conn: socket.socket, addr):
    """Receive mouse/keyboard events from PC, inject into X11 via xdotool."""
    log.info(f"Input stream started for {addr[0]}")
    try:
        raw = recv_line(conn)
        msg = json.loads(raw)
        if msg.get("type") != "start_input":
            return

        while True:
            raw   = recv_line(conn)
            event = json.loads(raw)
            _dispatch(event)

    except (ConnectionError, OSError):
        log.info("Input client disconnected")
    finally:
        conn.close()


def _dispatch(event: dict):
    t = event.get("type")
    try:
        if   t == "mouse_move":    _run(["xdotool", "mousemove", str(event["x"]), str(event["y"])])
        elif t == "mouse_press":   _run(["xdotool", "mousedown", "--clearmodifiers", f"--button={BUTTON_MAP.get(event['button'],'1')}"])
        elif t == "mouse_release": _run(["xdotool", "mouseup",   "--clearmodifiers", f"--button={BUTTON_MAP.get(event['button'],'1')}"])
        elif t == "mouse_scroll":  _scroll(event.get("dy", 0))
        elif t == "key_press":     _key(event["key"], event.get("modifiers",[]), "keydown")
        elif t == "key_release":   _key(event["key"], event.get("modifiers",[]), "keyup")
    except Exception as e:
        log.debug(f"Input dispatch error {event}: {e}")


def _scroll(dy: int):
    btn = "4" if dy > 0 else "5"
    for _ in range(abs(dy)):
        _run(["xdotool", "click", btn])


def _key(key: str, mods: list, action: str):
    mod_str = "+".join(MODIFIER_MAP.get(m, m) for m in mods)
    keyspec = f"{mod_str}+{key}" if mod_str else key
    _run(["xdotool", action, keyspec])


def _run(cmd: list):
    subprocess.run(cmd, capture_output=True, timeout=0.05)
```

---

## 9. Firewall Configuration (Windows — Required)

Run in PowerShell **as Administrator**:

```powershell
# Allow inbound connections from DGX to PC port 12010
New-NetFirewallRule `
  -DisplayName "DGX-Desktop-Remote-Inbound" `
  -Direction   Inbound `
  -Protocol    TCP `
  -LocalPort   12010 `
  -RemoteAddress 10.0.0.1 `
  -Action      Allow

# Verify:
Get-NetFirewallRule -DisplayName "DGX-Desktop-Remote-Inbound" | Select-Object DisplayName, Enabled, Action
```

---

## 10. Dependencies

### PC (Windows)
```
PyQt6>=6.6.0      # GUI framework
pywin32>=306      # Windows API for input hooks (Virtual Display Mode)
```

### DGX (Ubuntu Linux)
```
# Python packages:
mss>=9.0.1        # Screen capture (pure Python, fast)
Pillow>=10.0.0    # JPEG encoding
PyQt6>=6.6.0      # Manager GUI

# System packages:
sudo apt install -y xdotool           # Input injection into X11
sudo apt install -y scrot             # Screen capture fallback
sudo apt install -y python3-pip
```

Install everything:
```bash
# DGX:
pip3 install mss Pillow PyQt6
sudo apt install -y xdotool scrot

# PC (in virtualenv):
pip install PyQt6 pywin32
```

---

## 11. Pre-Flight Checklist

```
Hardware
[ ] Ethernet cable physically connected PC NIC ↔ DGX NIC
[ ] Both NICs are 10GbE or faster

Network
[ ] PC: static IP 10.0.0.2 / 255.255.255.0 / no gateway / no DNS
[ ] DGX: static IP 10.0.0.1 / 255.255.255.0 via netplan
[ ] ping 10.0.0.1 from PC → 0% loss
[ ] ping 10.0.0.2 from DGX → 0% loss

Software — DGX
[ ] xdotool installed:  xdotool --version
[ ] mss installed:      python3 -c "import mss; print('ok')"
[ ] Pillow installed:   python3 -c "from PIL import Image; print('ok')"
[ ] PyQt6 installed:    python3 -c "from PyQt6.QtWidgets import QApplication; print('ok')"

Software — PC
[ ] PyQt6 installed:    python -c "from PyQt6.QtWidgets import QApplication; print('ok')"
[ ] pywin32 installed:  python -c "import win32api; print('ok')"

Firewall
[ ] Windows Firewall rule created (port 12010, remote 10.0.0.1, TCP, Allow, Inbound)

Final Test
[ ] DGX service running: python3 dgx_service.py
[ ] PC manager opens → click Connect → status turns green
[ ] DGX desktop appears in PC window
[ ] Move mouse inside window → DGX cursor follows
[ ] Type in PC window → text appears on DGX
```
