"""
pc-application/src/network/connection.py
Three-channel TCP connection manager to DGX.
Thread-safe. Emits callbacks for frame arrival and disconnection.
"""

import socket
import json
import threading
import hashlib
import logging
import time
from typing import Optional, Callable

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[3]))
from shared.protocol import send_json, recv_line, recv_exact, CHUNK_SIZE

log = logging.getLogger("pc.connection")

CONNECT_TIMEOUT = 5.0
RPC_TIMEOUT     = 8.0


class DGXConnection:
    """
    Manages three TCP channels to DGX:
      • Control / RPC  (22010)
      • Video stream   (22011)
      • Input events   (22012)
    """

    def __init__(self,
                 on_frame:      Optional[Callable[[bytes], None]] = None,
                 on_disconnect: Optional[Callable]                = None,
                 on_ping_update: Optional[Callable[[float], None]] = None,
                 on_cursor: Optional[Callable[[str], None]] = None):
        self._rpc_sock:   Optional[socket.socket] = None
        self._video_sock: Optional[socket.socket] = None
        self._input_sock: Optional[socket.socket] = None
        self._rpc_lock    = threading.Lock()
        self._connected   = False
        self._dgx_ip      = ""

        self._on_frame       = on_frame
        self._on_disconnect  = on_disconnect
        self._on_ping_update = on_ping_update
        self._on_cursor      = on_cursor

        # Mouse-move coalescing: only the latest position is sent per cycle
        self._mouse_x:    int   = -1
        self._mouse_y:    int   = -1
        self._mouse_dirty = False
        self._mouse_lock  = threading.Lock()

        # Stats
        self.ping_ms:     float = 0.0
        self.fps_actual:  float = 0.0
        self.bytes_recv:  int   = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Connect / Disconnect
    # ------------------------------------------------------------------

    def connect(self, dgx_ip: str, rpc_port: int = 22010,
                video_port: int = 22011, input_port: int = 22012) -> dict:
        """
        Open all three channels, complete handshake, start video thread.
        Returns DGX info dict. Raises on failure.
        """
        self._dgx_ip = dgx_ip
        try:
            # 1. Control channel + handshake
            self._rpc_sock = self._make_socket(dgx_ip, rpc_port)
            send_json(self._rpc_sock, {
                "type":         "hello",
                "agent":        "PC",
                "version":      "1.0",
                "capabilities": ["file_transfer", "screen_view", "input_control"]
            })
            raw  = recv_line(self._rpc_sock)
            info = json.loads(raw)
            if not info.get("ok"):
                raise ConnectionError(f"Handshake rejected: {info.get('error')}")

            # 2. Video channel
            self._video_sock = self._make_socket(dgx_ip, video_port)
            send_json(self._video_sock, {
                "type":     "start_stream",
                "fps":      60,
                "encoding": "jpeg",
                "quality":  85
            })

            # 3. Input channel
            self._input_sock = self._make_socket(dgx_ip, input_port)
            send_json(self._input_sock, {"type": "start_input"})

            self._connected = True

            # Start background threads
            if self._on_frame:
                threading.Thread(target=self._video_loop,
                                  name="VideoReceiver", daemon=True).start()
            threading.Thread(target=self._rpc_push_loop,
                              name="RPCPushListener", daemon=True).start()
            threading.Thread(target=self._ping_loop,
                              name="PingMonitor", daemon=True).start()
            threading.Thread(target=self._mouse_flush_loop,
                              name="MouseFlusher", daemon=True).start()

            log.info(f"Connected to DGX @ {dgx_ip}")
            return info

        except Exception:
            self.disconnect()
            raise

    def disconnect(self):
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
    # Input Events — fire and forget on dedicated socket
    # ------------------------------------------------------------------

    def send_mouse_move(self, x: int, y: int):
        """Queue a mouse move — coalesced and flushed by _mouse_flush_loop."""
        with self._mouse_lock:
            self._mouse_x = x
            self._mouse_y = y
            self._mouse_dirty = True

    def send_mouse_press(self, button: str, x: int, y: int):
        self._send_input({"type": "mouse_press", "button": button, "x": x, "y": y})

    def send_mouse_release(self, button: str, x: int, y: int):
        self._send_input({"type": "mouse_release", "button": button, "x": x, "y": y})

    def send_mouse_scroll(self, dy: int, x: int, y: int):
        self._send_input({"type": "mouse_scroll", "dy": dy, "x": x, "y": y})

    def send_key_press(self, key: str, modifiers: list = None):
        self._send_input({"type": "key_press", "key": key,
                           "modifiers": modifiers or []})

    def send_key_release(self, key: str, modifiers: list = None):
        self._send_input({"type": "key_release", "key": key,
                           "modifiers": modifiers or []})

    def _send_input(self, event: dict):
        if not self._input_sock or not self._connected:
            return
        try:
            send_json(self._input_sock, event)
        except Exception:
            self._connected = False

    # ------------------------------------------------------------------
    # RPC — request/response on control channel (thread-safe)
    # ------------------------------------------------------------------

    def rpc(self, request: dict, timeout: float = RPC_TIMEOUT) -> dict:
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
            finally:
                if self._rpc_sock:
                    self._rpc_sock.settimeout(None)

    # ------------------------------------------------------------------
    # File Transfer
    # ------------------------------------------------------------------

    def send_file(self, local_path: str, remote_folder: str = "inbox",
                  progress_cb: Optional[Callable[[int, int], None]] = None,
                  metadata: dict = None) -> dict:
        from pathlib import Path as P
        p    = P(local_path)
        size = p.stat().st_size
        hasher = hashlib.sha256()

        with self._rpc_lock:
            try:
                self._rpc_sock.settimeout(600.0)
                payload = {
                    "type":            "put_file",
                    "filename":        p.name,
                    "size":            size,
                    "destination":     remote_folder,
                    "checksum_method": "sha256"
                }
                if metadata:
                    payload["metadata"] = metadata
                send_json(self._rpc_sock, payload)

                sent = 0
                with p.open("rb") as f:
                    while chunk := f.read(CHUNK_SIZE):
                        self._rpc_sock.sendall(chunk)
                        hasher.update(chunk)
                        sent += len(chunk)
                        if progress_cb:
                            progress_cb(sent, size)

                raw    = recv_line(self._rpc_sock)
                result = json.loads(raw)
                if result.get("checksum") and result["checksum"] != hasher.hexdigest():
                    return {"ok": False, "error": "checksum_mismatch"}
                return result

            except Exception as e:
                self._connected = False
                return {"ok": False, "error": str(e)}
            finally:
                if self._rpc_sock:
                    self._rpc_sock.settimeout(None)

    def get_file(self, filename: str, folder: str,
                 local_dest: str,
                 progress_cb: Optional[Callable[[int, int], None]] = None) -> dict:
        """Download a file from DGX to local_dest path."""
        with self._rpc_lock:
            try:
                self._rpc_sock.settimeout(600.0)
                send_json(self._rpc_sock, {
                    "type": "get_file", "filename": filename, "folder": folder
                })
                raw    = recv_line(self._rpc_sock)
                header = json.loads(raw)
                if not header.get("ok"):
                    return header

                size   = int(header["size"])
                hasher = hashlib.sha256()
                recv   = 0

                from pathlib import Path as P
                with P(local_dest).open("wb") as f:
                    while recv < size:
                        chunk = self._rpc_sock.recv(
                            min(CHUNK_SIZE, size - recv))
                        if not chunk:
                            raise ConnectionError("Disconnected mid-download")
                        f.write(chunk)
                        hasher.update(chunk)
                        recv += len(chunk)
                        if progress_cb:
                            progress_cb(recv, size)

                # Read checksum response
                raw    = recv_line(self._rpc_sock)
                result = json.loads(raw)
                if result.get("checksum") and result["checksum"] != hasher.hexdigest():
                    return {"ok": False, "error": "checksum_mismatch"}
                return {"ok": True, "filename": filename, "size": size}

            except Exception as e:
                self._connected = False
                return {"ok": False, "error": str(e)}
            finally:
                if self._rpc_sock:
                    self._rpc_sock.settimeout(None)

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _video_loop(self):
        """Continuously read JPEG frames and call on_frame callback.
        Wire format: 4-byte big-endian length (uint32) followed by JPEG bytes.
        """
        import struct
        _fps_times = []
        while self._connected and self._video_sock:
            try:
                # Read 4-byte length header
                header_bytes = b""
                while len(header_bytes) < 4:
                    chunk = self._video_sock.recv(4 - len(header_bytes))
                    if not chunk:
                        raise ConnectionResetError("Video socket closed")
                    header_bytes += chunk

                size = struct.unpack(">I", header_bytes)[0]
                if size == 0 or size > 20_000_000:   # sanity: 0 or >20 MB
                    continue

                # Read exactly `size` bytes of JPEG
                data = recv_exact(self._video_sock, size)
                self.bytes_recv += size

                # FPS tracking (1-second window)
                now = time.monotonic()
                _fps_times.append(now)
                _fps_times = [t for t in _fps_times if now - t <= 1.0]
                self.fps_actual = len(_fps_times)

                if self._on_frame:
                    self._on_frame(data)
            except (ConnectionError, OSError, struct.error):
                break
            except Exception as e:
                log.debug(f"Video loop error: {e}")
        self._connected = False
        if self._on_disconnect:
            self._on_disconnect()

    def _rpc_push_loop(self):
        """
        Listens for unsolicited push messages from DGX on a dedicated
        push socket (same RPC socket, but reads only when the rpc_lock
        is not held).  Handles: cursor_shape, resolution_changed.
        We use a separate socket-level read with a short select loop so
        we don’t block rpc().
        """
        import select, json
        # We need our own socket handle for pushes to avoid contention
        # with the request/response rpc() method.  The simplest safe
        # approach: use a non-blocking peek loop with select.
        while self._connected and self._rpc_sock:
            try:
                # Wait up to 0.5s for data without holding the lock
                ready, _, _ = select.select([self._rpc_sock], [], [], 0.5)
                if not ready:
                    continue
                # Only read if no RPC call is in flight
                if self._rpc_lock.acquire(blocking=False):
                    try:
                        self._rpc_sock.settimeout(0.1)
                        raw = recv_line(self._rpc_sock)
                        if not raw:
                            continue
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    finally:
                        if self._rpc_sock:
                            self._rpc_sock.settimeout(None)
                        self._rpc_lock.release()

                    t = msg.get("type", "")
                    if t == "cursor_shape" and self._on_cursor:
                        self._on_cursor(msg.get("shape", "arrow"))
                    elif t == "resolution_changed":
                        log.info("DGX resolution changed: %s", msg)
                    elif t == "pong":
                        pass  # swallow stale pongs
                    else:
                        log.debug("Unhandled push: %s", t)
            except Exception as e:
                log.debug("RPC push loop error: %s", e)
                break

    def _mouse_flush_loop(self):
        """
        Dedicated thread: sends the latest queued mouse position as fast
        as the socket allows.  Runs at ~500 Hz (0.002 s sleep) — well above
        the DGX display rate so no moves are perceptibly dropped.  Coalescing
        means high-frequency PC polling (165 Hz) never floods the TCP buffer.
        """
        import time as _time
        while self._connected and self._input_sock:
            with self._mouse_lock:
                dirty = self._mouse_dirty
                x, y  = self._mouse_x, self._mouse_y
                if dirty:
                    self._mouse_dirty = False
            if dirty:
                self._send_input({"type": "mouse_move", "x": x, "y": y})
            _time.sleep(0.002)   # 500 Hz ceiling — adjust if needed

    def _ping_loop(self):
        """Send a ping every 2 seconds, update ping_ms."""
        while self._connected:
            t0 = time.monotonic()
            result = self.rpc({"type": "ping", "ts": t0}, timeout=3.0)
            if result.get("type") == "pong":
                self.ping_ms = (time.monotonic() - t0) * 1000
                if self._on_ping_update:
                    self._on_ping_update(self.ping_ms)
            time.sleep(2.0)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_socket(host: str, port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect((host, port))
        sock.settimeout(None)
        return sock
