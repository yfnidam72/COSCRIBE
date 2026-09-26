"""Coscribe desktop launcher: runs the local server and opens it in a native window."""
from __future__ import annotations

import ctypes
import glob
import logging
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 47821
ICON = ROOT / "app" / "static" / "coscribe.ico"

(ROOT / "data").mkdir(exist_ok=True)
logging.basicConfig(
    filename=ROOT / "data" / "coscribe.log", level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
sys.path.insert(0, str(ROOT / "app"))


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "Coscribe", 0x40)


def ensure_ffmpeg() -> bool:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True
    # Shortcuts can start with a thinner PATH than a terminal; look where winget puts it.
    local = os.environ.get("LOCALAPPDATA", "")
    for pattern in (rf"{local}\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin",
                    rf"{local}\Microsoft\WinGet\Links", r"C:\ffmpeg\bin"):
        for d in glob.glob(pattern):
            if Path(d, "ffmpeg.exe").exists():
                os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
                return True
    return False


def port_busy() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main() -> None:
    # Own taskbar identity so the window groups under the Coscribe icon, not Python's.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Coscribe.App")
    except Exception:  # noqa: BLE001
        pass

    if port_busy():
        message("Coscribe is already open.")
        return
    if not ensure_ffmpeg():
        message("Coscribe needs FFmpeg.\n\nInstall it once with:\nwinget install Gyan.FFmpeg\n\nthen open Coscribe again.")
        return

    import uvicorn
    import webview

    import server

    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning",
                                       log_config=None))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window(
        "Coscribe", f"http://127.0.0.1:{PORT}/", width=1440, height=900,
        min_size=(1000, 660), background_color="#0c0c0f", text_select=True,
    )
    webview.start(gui="edgechromium", private_mode=False, icon=str(ICON),
                  storage_path=str(ROOT / "data" / "webview"))
    srv.should_exit = True


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        logging.exception("fatal")
        message(f"Coscribe hit a problem and has to close:\n\n{exc}\n\nDetails: {ROOT / 'data' / 'coscribe.log'}")
