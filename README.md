# DGX Desktop Remote

Stream your DGX GPU workstation desktop to your Windows PC over a direct 10 GbE link — low-latency, high-FPS, with seamless file transfer.

---

## Architecture

```
Windows PC (10.0.0.2)          DGX (10.0.0.1)
┌──────────────────────┐       ┌──────────────────────┐
│  pc-application/     │◄─────►│  dgx-service/        │
│  PyQt6 GUI           │ 10GbE │  headless Python      │
│  • MainWindow        │       │  • ScreenCapture      │
│  • VideoCanvas       │       │  • InputHandler       │
│  • TransferPanel     │       │  • RPCHandler         │
│  • ManagerWindow     │       │  • ResolutionMonitor  │
└──────────────────────┘       └──────────────────────┘
         Port 22010  →  RPC / Control
         Port 22011  ←  JPEG video stream
         Port 22012  →  Mouse / keyboard input
```

## Quick Start

### On the DGX (Ubuntu)

```bash
# 1. Clone / copy this repo to the DGX
# 2. Install & start the service
cd dgx-service/install
sudo bash install.sh
sudo systemctl start dgx-desktop-remote

# Or run manually:
cd dgx-service
pip install -r requirements.txt
python src/dgx_service.py
```

### On the Windows PC

```powershell
# 1. Install dependencies
cd pc-application
pip install -r requirements.txt

# 2. Launch the app
python src/main.py
```

The setup wizard will guide you through entering the IP addresses (defaults: PC `10.0.0.2`, DGX `10.0.0.1`).

---

## File Transfer

Files are exchanged via the **Transfer Panel** (📁 toolbar button or drag-and-drop onto the canvas).

| Folder   | Purpose                        |
|----------|--------------------------------|
| `inbox`  | Files sent from PC to DGX      |
| `outbox` | Files staged for PC download   |
| `staging`| Work in progress               |
| `archive`| Completed / archived transfers |

All transfers use SHA-256 verification. Text files are automatically CRLF-stripped before upload.

---

## Configuration

Settings are stored in `~/.dgx-desktop-remote/config.json` (never committed to git).

Edit via the **Manager** dialog (⚙ toolbar button) or directly in the JSON file.

---

## Ports

| Port  | Protocol | Direction  | Purpose              |
|-------|----------|------------|----------------------|
| 22010 | TCP      | PC → DGX   | RPC / control        |
| 22011 | TCP      | DGX → PC   | JPEG video stream    |
| 22012 | TCP      | PC → DGX   | Mouse / keyboard     |

---

## Requirements

**PC (Windows):**
- Python 3.10+
- PyQt6

**DGX (Ubuntu):**
- Python 3.10+
- `xdotool`
- `mss`, `Pillow`
- PyQt6 (optional, for manager GUI)

---

## Project Structure

```
DGX-Desktop-Remote/
├── pc-application/
│   ├── requirements.txt
│   └── src/
│       ├── main.py              # Entry point
│       ├── config.py            # Settings dataclass
│       ├── theme.py             # Dark stylesheet
│       ├── main_window.py       # Main GUI
│       ├── manager_window.py    # Settings dialog
│       ├── setup_wizard.py      # First-run wizard
│       ├── system_tray.py       # System tray
│       ├── widgets.py           # Reusable widgets
│       ├── network/
│       │   └── connection.py    # TCP connection manager
│       ├── display/
│       │   ├── video_canvas.py  # JPEG display widget
│       │   └── coordinate_mapper.py
│       └── transfer/
│           ├── file_analyzer.py
│           ├── file_converter.py
│           ├── transfer_worker.py
│           └── transfer_panel.py
├── dgx-service/
│   ├── requirements.txt
│   ├── src/
│   │   ├── dgx_service.py       # Entry point
│   │   ├── server.py            # TCP listeners + session
│   │   ├── rpc_handler.py       # RPC dispatcher
│   │   ├── screen_capture.py    # mss JPEG pump
│   │   ├── input_handler.py     # xdotool injection
│   │   ├── resolution_monitor.py
│   │   └── manager_gui.py       # DGX-side tray manager
│   └── install/
│       ├── install.sh
│       └── dgx-desktop-remote.service
├── shared/
│   └── protocol.py              # Wire protocol primitives
└── create_shortcuts.py
```
