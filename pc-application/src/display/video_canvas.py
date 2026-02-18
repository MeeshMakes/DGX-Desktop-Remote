"""
pc-application/src/display/video_canvas.py
The DGX display surface: renders JPEG frames, handles all mouse/keyboard
input forwarding, drag-and-drop, cursor tunnel.
"""

import time
from collections import deque
from typing import Optional

from PyQt6.QtWidgets import QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QPoint, QSize, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap, QImage, QDragEnterEvent, QDropEvent, QKeyEvent, QCursor

from display.coordinate_mapper import CoordinateMapper


class VideoCanvas(QLabel):
    """
    Central display widget.
    - Renders incoming JPEG frames (thread-safe, uses QueuedConnection).
    - Hides cursor when inside (cursor tunnel mode).
    - Forwards all mouse + keyboard events to DGX via connection.
    - Accepts file drops, emits files_dropped signal.
    """

    files_dropped = pyqtSignal(list)   # list[str] — file paths
    _frame_ready = pyqtSignal(QPixmap)    # internal cross-thread signal

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #080810;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

        self.connection:  Optional[object] = None   # DGXConnection
        self.mapper:      Optional[CoordinateMapper] = None
        self.cursor_mode: str   = "bridge"  # "bridge" | "hidden" | "arrow"
        self._in_tunnel:  bool  = False
        self._dgx_w:      int   = 0          # DGX native width  (set at connect)
        self._dgx_h:      int   = 0          # DGX native height (set at connect)
        self._pixmap_w:   int   = 0
        self._pixmap_h:   int   = 0
        self._last_raw:   Optional[QPixmap] = None
        self.fps_actual:  float = 0.0
        self._fps_times:  deque = deque(maxlen=120)
        self._frame_ready.connect(self._set_pixmap)

    # ------------------------------------------------------------------
    # Frame update (called from network thread via queued invoke)
    # ------------------------------------------------------------------

    def update_frame(self, jpeg_data: bytes):
        """Called from network thread. Decodes JPEG and emits raw pixmap to GUI thread."""
        img = QImage.fromData(jpeg_data, "JPEG")
        if img.isNull():
            return
        now = time.monotonic()
        self._fps_times.append(now)
        cutoff = now - 1.0
        while self._fps_times and self._fps_times[0] < cutoff:
            self._fps_times.popleft()
        self.fps_actual = len(self._fps_times)
        # Emit raw pixmap — all scaling done on GUI thread
        self._frame_ready.emit(QPixmap.fromImage(img))

    @pyqtSlot(QPixmap)
    def _set_pixmap(self, pixmap: QPixmap):
        """GUI thread: store raw frame and fill the widget exactly."""
        self._last_raw = pixmap
        self._apply_scale()

    def _apply_scale(self):
        """
        Fill the widget with the last raw frame.
        The widget geometry is already locked to the DGX aspect ratio
        (enforced by hasHeightForWidth / MainWindow.resizeEvent), so we
        stretch the frame to fill it completely — no letterbox bars,
        no offset, and mouse coordinate mapping is always exact.
        """
        if self._last_raw is None or self._last_raw.isNull():
            return
        scaled = self._last_raw.scaled(
            self.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,    # widget IS the ratio
            Qt.TransformationMode.FastTransformation
        )
        self._pixmap_w = scaled.width()
        self._pixmap_h = scaled.height()
        self.setPixmap(scaled)

    # ------------------------------------------------------------------
    # DGX resolution — locks the widget to the correct aspect ratio
    # ------------------------------------------------------------------

    def set_dgx_resolution(self, w: int, h: int):
        """Called once on connect. Locks layout to DGX aspect ratio."""
        self._dgx_w = w
        self._dgx_h = h
        # Re-hint the layout engine so the window snaps immediately
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return self._dgx_w > 0 and self._dgx_h > 0

    def heightForWidth(self, width: int) -> int:
        if self._dgx_w > 0 and self._dgx_h > 0:
            return int(width * self._dgx_h / self._dgx_w)
        return super().heightForWidth(width)

    def clear_frame(self):
        self.setPixmap(QPixmap())
        self._last_raw = None
        self._dgx_w    = 0
        self._dgx_h    = 0
        self._pixmap_w = 0
        self._pixmap_h = 0
        self.fps_actual = 0.0
        self._fps_times.clear()
        self.updateGeometry()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def enterEvent(self, event):
        self._in_tunnel = True
        if self.cursor_mode == "bridge":
            # Start with arrow; DGX will push the real shape immediately
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif self.cursor_mode == "hidden":
            self.setCursor(Qt.CursorShape.BlankCursor)
        else:  # "arrow"
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocus()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._in_tunnel = False
        self.unsetCursor()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        """Re-scale the last received frame whenever the canvas is resized."""
        super().resizeEvent(event)
        self._apply_scale()

    # ------------------------------------------------------------------
    # Cursor bridging
    # ------------------------------------------------------------------

    def set_cursor_shape(self, x11_name: str):
        """Called from main thread when DGX pushes a cursor_shape event."""
        if self.cursor_mode != "bridge":
            return
        # Map X11 cursor names to Qt shapes
        _MAP = {
            "default":          Qt.CursorShape.ArrowCursor,
            "arrow":            Qt.CursorShape.ArrowCursor,
            "left_ptr":         Qt.CursorShape.ArrowCursor,
            "text":             Qt.CursorShape.IBeamCursor,
            "xterm":            Qt.CursorShape.IBeamCursor,
            "ibeam":            Qt.CursorShape.IBeamCursor,
            "wait":             Qt.CursorShape.WaitCursor,
            "watch":            Qt.CursorShape.WaitCursor,
            "crosshair":        Qt.CursorShape.CrossCursor,
            "cross":            Qt.CursorShape.CrossCursor,
            "pointer":          Qt.CursorShape.PointingHandCursor,
            "hand":             Qt.CursorShape.PointingHandCursor,
            "hand1":            Qt.CursorShape.PointingHandCursor,
            "hand2":            Qt.CursorShape.PointingHandCursor,
            "size_all":         Qt.CursorShape.SizeAllCursor,
            "fleur":            Qt.CursorShape.SizeAllCursor,
            "size_ver":         Qt.CursorShape.SizeVerCursor,
            "sb_v_double_arrow":Qt.CursorShape.SizeVerCursor,
            "size_hor":         Qt.CursorShape.SizeHorCursor,
            "sb_h_double_arrow":Qt.CursorShape.SizeHorCursor,
            "size_bdiag":       Qt.CursorShape.SizeBDiagCursor,
            "size_fdiag":       Qt.CursorShape.SizeFDiagCursor,
            "not-allowed":      Qt.CursorShape.ForbiddenCursor,
            "forbidden":        Qt.CursorShape.ForbiddenCursor,
            "x_cursor":         Qt.CursorShape.ForbiddenCursor,
            "split_v":          Qt.CursorShape.SplitVCursor,
            "split_h":          Qt.CursorShape.SplitHCursor,
            "open_hand":        Qt.CursorShape.OpenHandCursor,
            "grabbing":         Qt.CursorShape.ClosedHandCursor,
            "closedhand":       Qt.CursorShape.ClosedHandCursor,
            "whats_this":       Qt.CursorShape.WhatsThisCursor,
            "help":             Qt.CursorShape.WhatsThisCursor,
            "progress":         Qt.CursorShape.BusyCursor,
            "left_ptr_watch":   Qt.CursorShape.BusyCursor,
        }
        shape = _MAP.get(x11_name.lower(), Qt.CursorShape.ArrowCursor)
        self.setCursor(shape)

    def mouseMoveEvent(self, event):
        if self._connected():
            dx, dy = self._to_dgx(event.position().x(), event.position().y())
            self.connection.send_mouse_move(dx, dy)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        if self._connected():
            btn = _qt_btn(event.button())
            dx, dy = self._to_dgx(event.position().x(), event.position().y())
            self.connection.send_mouse_press(btn, dx, dy)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._connected():
            btn = _qt_btn(event.button())
            dx, dy = self._to_dgx(event.position().x(), event.position().y())
            self.connection.send_mouse_release(btn, dx, dy)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self._connected():
            dy = 1 if event.angleDelta().y() > 0 else -1
            dx, ddy = self._to_dgx(event.position().x(), event.position().y())
            self.connection.send_mouse_scroll(dy * 3, dx, ddy)
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # Keyboard events (forwarded by MainWindow.keyPressEvent)
    # ------------------------------------------------------------------

    def inject_key_press(self, key: str, mods: list):
        if self._connected():
            self.connection.send_key_press(key, mods)

    def inject_key_release(self, key: str, mods: list):
        if self._connected():
            self.connection.send_key_release(key, mods)

    # ------------------------------------------------------------------
    # Drag-and-Drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _connected(self) -> bool:
        return (self.connection is not None
                and self.connection.connected
                and self.mapper is not None)

    def _to_dgx(self, cx: float, cy: float) -> tuple:
        return self.mapper.canvas_pos_to_dgx(
            cx, cy,
            self.width(), self.height(),
            self._pixmap_w or self.width(),
            self._pixmap_h or self.height()
        )


def _qt_btn(btn) -> str:
    from PyQt6.QtCore import Qt
    return {
        Qt.MouseButton.LeftButton:   "left",
        Qt.MouseButton.RightButton:  "right",
        Qt.MouseButton.MiddleButton: "middle"
    }.get(btn, "left")
