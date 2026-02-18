"""
pc-application/src/config.py
Persistent configuration — stored in ~/.dgx-desktop-remote/config.json
Never committed to Git.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict


CONFIG_DIR  = Path.home() / ".dgx-desktop-remote"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    # Network
    pc_ip:          str  = ""
    dgx_ip:         str  = ""
    rpc_port:       int  = 22010
    video_port:     int  = 22011
    input_port:     int  = 22012
    pc_listen_port: int  = 12010

    # Display
    display_mode:   str  = "window"    # "window" | "virtual_display"
    target_fps:     int  = 60
    jpeg_quality:   int  = 85
    virt_side:      str  = "right"     # "right"|"left"|"top"|"bottom"

    # Window state
    win_x:          int  = -1          # -1 = let Qt decide
    win_y:          int  = -1
    win_w:          int  = 1280
    win_h:          int  = 760
    pinned:         bool = False

    # Behavior
    auto_connect:     bool = False
    start_minimized:  bool = False
    show_fps:         bool = True
    show_ping:        bool = True
    confirm_file_del: bool = True
    cursor_mode:      str  = "bridge"   # "bridge" | "hidden" | "arrow"

    # Auto-reconnect watchdog
    auto_reconnect:       bool = True   # keep trying even after connection drop
    reconnect_interval:   int  = 5      # seconds between retry attempts

    # Last successfully negotiated ports (saved after each successful connect)
    last_rpc_port:    int  = 22010
    last_video_port:  int  = 22011
    last_input_port:  int  = 22012

    def is_configured(self) -> bool:
        return CONFIG_FILE.exists() and bool(self.pc_ip) and bool(self.dgx_ip)

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with CONFIG_FILE.open("w") as f:
            json.dump(asdict(self), f, indent=4)

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            with CONFIG_FILE.open() as f:
                data = json.load(f)
            valid = {k: v for k, v in data.items()
                     if k in cls.__dataclass_fields__}
            return cls(**valid)
        except Exception:
            return cls()
