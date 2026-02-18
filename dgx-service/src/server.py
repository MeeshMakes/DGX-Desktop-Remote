"""
dgx-service/src/server.py
Core DGX service: three TCP listener threads (RPC / Video / Input).
"""

import hashlib
import json
import logging
import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Import local modules
from screen_capture    import ScreenCapture
from input_handler     import InputHandler
from resolution_monitor import ResolutionMonitor
from rpc_handler       import RPCHandler

# ─── port negotiation ─────────────────────────────────────────────────
DISCOVERY_PORT   = 22000          # fixed handshake port — always open
PORT_RANGE_START = 22010
PORT_RANGE_END   = 22059


def _is_port_free(port: int) -> bool:
    """Return True if no process on DGX is listening on this port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False

# ─── file transfer root ───────────────────────────────────────────────
TRANSFER_ROOT = Path.home() / "Desktop" / "PC-Transfer"
for _d in ("inbox", "outbox", "staging", "archive"):
    (TRANSFER_ROOT / _d).mkdir(parents=True, exist_ok=True)

CHUNK = 65536


def _send_json(conn: socket.socket, obj: dict):
    data = (json.dumps(obj) + "\n").encode()
    conn.sendall(data)


def _recv_line(conn: socket.socket, maxlen: int = 131072) -> str:
    buf = bytearray()
    while True:
        b = conn.recv(1)
        if not b or b == b"\n":
            break
        buf += b
        if len(buf) > maxlen:
            raise ValueError("Line too long")
    return buf.decode()


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    pos = 0
    while pos < n:
        read = conn.recv_into(view[pos:], n - pos)
        if not read:
            raise ConnectionResetError("Connection closed mid-transfer")
        pos += read
    return bytes(buf)


# ──────────────────────────────────────────────────────────────────────
# Session — one PC client
# ──────────────────────────────────────────────────────────────────────

class ClientSession:
    def __init__(self, service: "DGXService", rpc_conn: socket.socket):
        self._svc       = service
        self._rpc_conn  = rpc_conn
        self._vid_conn: Optional[socket.socket] = None
        self._inp_conn: Optional[socket.socket] = None
        self._running   = False
        self._lock      = threading.Lock()

    def set_video_conn(self, conn: socket.socket):
        """Accept the video channel socket and drain the start_stream handshake."""
        self._vid_conn = conn
        # Drain the PC's opening start_stream message (no response needed)
        try:
            conn.settimeout(3)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            conn.settimeout(None)
        except Exception:
            pass

    def set_input_conn(self, conn: socket.socket):
        """Accept the input channel socket, drain start_input, then start loop."""
        self._inp_conn = conn
        # Drain the PC's opening start_input message (no response needed)
        try:
            conn.settimeout(3)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            conn.settimeout(None)
        except Exception:
            pass
        threading.Thread(target=self._input_loop, daemon=True).start()

    def run(self):
        """Block on RPC control channel until client disconnects."""
        self._running = True
        self._last_cursor_shape = ""

        # ── Hello handshake ──────────────────────────────────────────
        # PC sends {"type": "hello", ...} immediately after connecting.
        # We must respond before entering the main loop.
        try:
            hello_line = _recv_line(self._rpc_conn)
            hello = json.loads(hello_line) if hello_line else {}
        except Exception as e:
            log.warning("Handshake recv failed: %s", e)
            self._cleanup()
            return

        if hello.get("type") != "hello":
            log.warning("Expected hello, got: %s", hello.get("type"))
            _send_json(self._rpc_conn, {"ok": False, "error": "expected hello"})
            self._cleanup()
            return

        _res = self._svc.resolution_monitor.current
        res = _res if (_res and _res[0] > 0) else (1920, 1080)
        _send_json(self._rpc_conn, {
            "ok":      True,
            "type":    "hello",
            "agent":   "DGX",
            "version": "1.0",
            "width":   res[0],
            "height":  res[1],
            "fps":     self._svc.capture.fps,
            "hostname": __import__("socket").gethostname(),
        })
        log.info("Handshake complete with PC (agent=%s)", hello.get("agent", "?"))
        # ─────────────────────────────────────────────────────────────

        self._svc.capture.start(self._on_frame)
        # Start cursor push thread
        threading.Thread(target=self._cursor_push_loop, daemon=True).start()
        try:
            while self._running:
                line = _recv_line(self._rpc_conn)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = msg.get("type", "")

                # File receive (upload from PC to DGX)
                if t == "file_send":
                    resp = self._handle_file_receive(msg)
                # File send (download from DGX to PC)
                elif t == "file_get":
                    resp = self._handle_file_send(msg)
                else:
                    resp = self._svc.rpc.dispatch(msg)

                _send_json(self._rpc_conn, resp)
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            log.info("Client disconnected: %s", e)
        finally:
            self._cleanup()

    def _cursor_push_loop(self):
        """
        Polls the X11 cursor shape every 150ms and pushes a cursor_shape
        message to the PC when it changes.  Uses python-xlib if available,
        falls back to xdotool subprocess.
        Push messages are sent on the RPC socket between request/response
        pairs — they are NOT responses to a request, so we use _rpc_push_lock.
        """
        self._rpc_push_lock = threading.Lock()

        def _get_cursor_name() -> str:
            """Return an X11 cursor name string, e.g. 'text', 'pointer', 'default'."""
            # Try Xlib first (zero-fork, fast)
            try:
                from Xlib import display as _xdisplay, X as _X, Xutil
                from Xlib.ext import xfixes
                dpy = _xdisplay.Display()
                if dpy.has_extension("XFIXES"):
                    ci = dpy.xfixes_get_cursor_image(dpy.screen().root)
                    # cursor_image has a .name field on newer python-xlib
                    name = getattr(ci, "name", "") or ""
                    dpy.close()
                    return name.lower() or "default"
                dpy.close()
            except Exception:
                pass
            # Fall back to parsing xprop / xdotool
            try:
                out = __import__("subprocess").check_output(
                    ["xdotool", "getmouselocation", "--shell"],
                    timeout=0.2, stderr=__import__("subprocess").DEVNULL
                ).decode()
                # xdotool doesn't give cursor name directly; skip
            except Exception:
                pass
            return "default"

        while self._running and self._rpc_conn:
            try:
                shape = _get_cursor_name()
                if shape and shape != self._last_cursor_shape:
                    self._last_cursor_shape = shape
                    msg = (
                        __import__("json").dumps(
                            {"type": "cursor_shape", "shape": shape}
                        ) + "\n"
                    ).encode()
                    with self._rpc_push_lock:
                        try:
                            self._rpc_conn.sendall(msg)
                        except OSError:
                            break
            except Exception as e:
                log.debug("Cursor push error: %s", e)
            time.sleep(0.15)

    def _cleanup(self):
        self._running = False
        self._svc.capture.stop()
        for c in (self._rpc_conn, self._vid_conn, self._inp_conn):
            if c:
                try:
                    c.close()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Video push (called from capture thread)
    # ------------------------------------------------------------------

    def _on_frame(self, jpeg: bytes, w: int, h: int):
        if not self._vid_conn:
            return
        # Wire format: 4-byte big-endian length, then JPEG payload
        try:
            header = struct.pack(">I", len(jpeg))
            with self._lock:
                self._vid_conn.sendall(header + jpeg)
        except OSError:
            self._running = False

    # ------------------------------------------------------------------
    # Input channel loop
    # ------------------------------------------------------------------

    def _input_loop(self):
        ih = self._svc.input_handler
        while self._running and self._inp_conn:
            try:
                line = _recv_line(self._inp_conn)
                if not line:
                    break
                msg = json.loads(line)
            except Exception:
                break

            t = msg.get("type", "")
            if t == "mouse_move":
                ih.mouse_move(msg["x"], msg["y"])
            elif t == "mouse_press":
                ih.mouse_press(msg.get("button", "left"))
            elif t == "mouse_release":
                ih.mouse_release(msg.get("button", "left"))
            elif t == "mouse_scroll":
                ih.mouse_scroll(msg.get("dx", 0), msg.get("dy", 0))
            elif t == "key_press":
                ih.key_press(msg.get("key", ""))
            elif t == "key_release":
                ih.key_release(msg.get("key", ""))

    # ------------------------------------------------------------------
    # File receive (upload PC → DGX)
    # ------------------------------------------------------------------

    def _handle_file_receive(self, msg: dict) -> dict:
        folder   = msg.get("folder", "inbox")
        size     = msg.get("size", 0)
        expected = msg.get("sha256", "")
        meta     = msg.get("metadata", {})
        name     = (meta.get("name") or msg.get("filename") or "received_file")
        # Sanitize filename
        name = Path(name).name

        if folder not in ("inbox", "outbox", "staging", "archive"):
            return {"ok": False, "error": "Invalid folder"}

        dest = TRANSFER_ROOT / folder / name
        sha  = hashlib.sha256()
        try:
            _send_json(self._rpc_conn, {"ok": True, "type": "ready"})
            with open(dest, "wb") as fh:
                remaining = size
                while remaining > 0:
                    chunk = _recv_exact(self._rpc_conn, min(CHUNK, remaining))
                    fh.write(chunk)
                    sha.update(chunk)
                    remaining -= len(chunk)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        ok = (sha.hexdigest() == expected) if expected else True
        # Apply permissions if provided
        perms = meta.get("permissions")
        if perms:
            try:
                dest.chmod(int(perms, 8))
            except Exception:
                pass
        return {"ok": ok, "sha256": sha.hexdigest()}

    # ------------------------------------------------------------------
    # File send (download DGX → PC)
    # ------------------------------------------------------------------

    def _handle_file_send(self, msg: dict) -> dict:
        folder   = msg.get("folder", "outbox")
        filename = msg.get("filename", "")
        if not filename:
            return {"ok": False, "error": "No filename"}
        src = TRANSFER_ROOT / folder / Path(filename).name
        if not src.exists():
            return {"ok": False, "error": "File not found"}

        size = src.stat().st_size
        sha  = hashlib.sha256()
        try:
            _send_json(self._rpc_conn, {"ok": True, "type": "file_data", "size": size})
            with open(src, "rb") as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    self._rpc_conn.sendall(chunk)
                    sha.update(chunk)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        return {"ok": True, "sha256": sha.hexdigest()}


# ──────────────────────────────────────────────────────────────────────
# DGXService — main entry object
# ──────────────────────────────────────────────────────────────────────

class DGXService:

    def __init__(
        self,
        host:        str  = "0.0.0.0",
        rpc_port:    int  = 22010,
        video_port:  int  = 22011,
        input_port:  int  = 22012,
        fps:         int  = 60,
        quality:     int  = 85,
    ):
        self.host        = host
        self.rpc_port    = rpc_port
        self.video_port  = video_port
        self.input_port  = input_port

        self.capture           = ScreenCapture(fps=fps, quality=quality)
        self.input_handler     = InputHandler()
        self.resolution_monitor = ResolutionMonitor()
        self.rpc               = RPCHandler(self)

        self._running          = False
        self._session: Optional[ClientSession] = None
        self._pending_vid: Optional[socket.socket] = None
        self._pending_inp: Optional[socket.socket] = None
        self._session_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public start / stop
    # ------------------------------------------------------------------

    def start(self):
        self._running = True
        self.resolution_monitor.start(self._on_resolution_change)

        # Discovery/negotiation listener (always on DISCOVERY_PORT)
        t = threading.Thread(
            target=self._accept_negotiation, daemon=True, name="Listener-discovery"
        )
        t.start()

        for tag, port, handler in [
            ("rpc",   self.rpc_port,   self._accept_rpc),
            ("video", self.video_port, self._accept_video),
            ("input", self.input_port, self._accept_input),
        ]:
            t = threading.Thread(
                target=handler, args=(port,), daemon=True, name=f"Listener-{tag}"
            )
            t.start()
        log.info(
            "DGX service started — Discovery:%d  RPC:%d  Video:%d  Input:%d",
            DISCOVERY_PORT, self.rpc_port, self.video_port, self.input_port,
        )

    def stop(self):
        self._running = False
        self.resolution_monitor.stop()
        self.capture.stop()
        log.info("DGX service stopped")

    # ------------------------------------------------------------------
    # Accept loops
    # ------------------------------------------------------------------

    def _accept_negotiation(self):
        """
        Always-on listener on DISCOVERY_PORT.
        When a PC connects, negotiate which RPC/video/input ports to use,
        then restart the data listeners on those ports.
        """
        srv = self._make_server(DISCOVERY_PORT)
        log.info("Discovery listener ready on port %d", DISCOVERY_PORT)
        with srv:
            while self._running:
                try:
                    conn, addr = srv.accept()
                    log.info("Negotiation request from %s", addr)
                    threading.Thread(
                        target=self._handle_negotiation,
                        args=(conn, addr),
                        daemon=True,
                    ).start()
                except OSError:
                    break

    def _handle_negotiation(self, conn: socket.socket, addr):
        """
        Respond to a port-negotiation request.

        We always advertise the fixed ports this service is *already* listening
        on (set up in start()).  We never spawn new listener threads here —
        doing so caused port exhaustion: every retry allocated a fresh triplet
        that was never released.

        If a session is currently active we reject the request so the PC
        backs off rather than stacking up zombie connections.
        """
        try:
            conn.settimeout(8)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            msg = json.loads(buf.split(b"\n")[0].decode())
            if msg.get("type") != "negotiate":
                _send_json(conn, {"ok": False, "error": "expected negotiate"})
                return

            # Reject if a session is already running
            with self._session_lock:
                if self._session and self._session._running:
                    _send_json(conn, {"ok": False, "error": "session already active"})
                    log.info("Rejected negotiation from %s — session already active", addr)
                    return

            # Tell the PC to use the ports we're already listening on.
            # No new listeners are spawned — no port exhaustion.
            _send_json(conn, {
                "ok":    True,
                "rpc":   self.rpc_port,
                "video": self.video_port,
                "input": self.input_port,
            })
            log.info("Negotiated ports — RPC:%d  Video:%d  Input:%d",
                     self.rpc_port, self.video_port, self.input_port)

        except Exception as e:
            log.exception("Error during negotiation: %s", e)
        finally:
            conn.close()

    def _accept_rpc(self, port: int):
        srv = self._make_server(port)
        with srv:
            while self._running:
                try:
                    conn, addr = srv.accept()
                    log.info("RPC connection from %s", addr)
                    self._start_session(conn)
                except OSError:
                    break

    def _accept_video(self, port: int):
        srv = self._make_server(port)
        with srv:
            while self._running:
                try:
                    conn, addr = srv.accept()
                    log.info("Video connection from %s", addr)
                    with self._session_lock:
                        if self._session:
                            self._session.set_video_conn(conn)
                        else:
                            if self._pending_vid:
                                try: self._pending_vid.close()
                                except: pass
                            self._pending_vid = conn
                except OSError:
                    break

    def _accept_input(self, port: int):
        srv = self._make_server(port)
        with srv:
            while self._running:
                try:
                    conn, addr = srv.accept()
                    log.info("Input connection from %s", addr)
                    with self._session_lock:
                        if self._session:
                            self._session.set_input_conn(conn)
                        else:
                            if self._pending_inp:
                                try: self._pending_inp.close()
                                except: pass
                            self._pending_inp = conn
                except OSError:
                    break

    def _start_session(self, rpc_conn: socket.socket):
        sess = ClientSession(self, rpc_conn)
        with self._session_lock:
            self._session = sess
            if self._pending_vid:
                sess.set_video_conn(self._pending_vid)
                self._pending_vid = None
            if self._pending_inp:
                sess.set_input_conn(self._pending_inp)
                self._pending_inp = None
        threading.Thread(target=sess.run, daemon=True, name="ClientSession").start()

    @staticmethod
    def _make_server(port: int) -> socket.socket:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(2)
        return srv

    def _on_resolution_change(self, w: int, h: int):
        log.info("Resolution changed to %dx%d", w, h)
        if self._session and self._session._running:
            try:
                _send_json(self._session._rpc_conn, {
                    "type": "resolution_changed",
                    "width": w, "height": h,
                })
            except OSError:
                pass
