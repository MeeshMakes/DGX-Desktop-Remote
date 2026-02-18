"""
pc-application/src/display/coordinate_mapper.py
Maps coordinates between DGX native resolution and PC display.
"""


class CoordinateMapper:
    """
    Authoritative coordinate translation between:
      (A) DGX native resolution  (dgx_w × dgx_h)
      (B) Canvas render rect     (after aspect-ratio letterboxing)
      (C) Screen coordinates     (Virtual Display Mode only)

    DGX resolution is the source of truth — it is updated live
    when the DGX sends a resolution_changed push event.
    """

    def __init__(self, dgx_w: int = 1920, dgx_h: int = 1080):
        self.dgx_w = dgx_w
        self.dgx_h = dgx_h

    def update(self, w: int, h: int):
        self.dgx_w = w
        self.dgx_h = h

    # ── Relative ↔ DGX ────────────────────────────────────────────────

    def relative_to_dgx(self, rx: float, ry: float) -> tuple:
        """Relative [0–1] → DGX pixel. rx/ry already clamped."""
        x = int(rx * (self.dgx_w - 1))
        y = int(ry * (self.dgx_h - 1))
        return x, y

    # ── Letterbox-aware canvas position ───────────────────────────────

    def canvas_pos_to_dgx(self, pos_x: float, pos_y: float,
                           label_w: int, label_h: int,
                           pixmap_w: int, pixmap_h: int) -> tuple:
        """
        Convert a mouse position inside a QLabel (label_w × label_h)
        where a scaled pixmap (pixmap_w × pixmap_h) is centred, to
        DGX pixel coordinates.
        """
        if pixmap_w <= 0 or pixmap_h <= 0:
            return 0, 0
        off_x = (label_w - pixmap_w) / 2
        off_y = (label_h - pixmap_h) / 2
        rel_x = (pos_x - off_x) / pixmap_w
        rel_y = (pos_y - off_y) / pixmap_h
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        return self.relative_to_dgx(rel_x, rel_y)

    # ── Virtual Display Mode ───────────────────────────────────────────

    def screen_to_dgx(self, sx: int, sy: int,
                      virt_x: int, virt_y: int) -> tuple:
        """
        Absolute Windows screen coordinate → DGX pixel.
        virt_x, virt_y = top-left of the virtual monitor in screen space.
        """
        rel_x = (sx - virt_x) / self.dgx_w
        rel_y = (sy - virt_y) / self.dgx_h
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        return self.relative_to_dgx(rel_x, rel_y)
