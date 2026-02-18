"""
dgx-service/src/dgx_service.py
Entry point for the DGX headless service.
Usage:
    python dgx_service.py [--no-gui] [--fps 60] [--quality 85]
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time

# ── logging setup ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dgx_service")

# ── local imports ─────────────────────────────────────────────────────
_SRC = os.path.dirname(os.path.abspath(__file__))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from server import DGXService


def parse_args():
    ap = argparse.ArgumentParser(description="DGX Desktop Remote Service")
    ap.add_argument("--host",    default="0.0.0.0",  help="Bind address")
    ap.add_argument("--rpc",     type=int, default=22010, help="RPC port")
    ap.add_argument("--video",   type=int, default=22011, help="Video port")
    ap.add_argument("--input",   type=int, default=22012, help="Input port")
    ap.add_argument("--fps",     type=int, default=60,    help="Target FPS")
    ap.add_argument("--quality", type=int, default=85,    help="JPEG quality (40-100)")
    ap.add_argument("--no-gui",  action="store_true",     help="Run headless (no tray)")
    return ap.parse_args()


def main():
    args = parse_args()

    svc = DGXService(
        host       = args.host,
        rpc_port   = args.rpc,
        video_port = args.video,
        input_port = args.input,
        fps        = args.fps,
        quality    = args.quality,
    )

    # Graceful shutdown on Ctrl-C / SIGTERM
    def _shutdown(signum, frame):
        log.info("Shutting down (signal %d) …", signum)
        svc.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Starting DGX Desktop Remote service …")
    svc.start()

    if args.no_gui or not os.environ.get("DISPLAY"):
        # Headless mode — just keep the main thread alive
        log.info("Running in headless mode (no GUI). Press Ctrl-C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        # Launch PyQt6 manager GUI + system tray on main thread
        from manager_gui import run_manager_gui
        run_manager_gui(svc)

    svc.stop()
    log.info("Service stopped cleanly.")


if __name__ == "__main__":
    main()
