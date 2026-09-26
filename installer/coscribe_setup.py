"""CoscribeSetup.exe - a small web installer for Coscribe.

It downloads everything Coscribe needs into one per-user folder (no admin rights):
  uv (Python manager) -> Python 3.11 + packages -> the Coscribe app from GitHub
  -> a private FFmpeg -> optionally Ollama and the speech model
then creates shortcuts, registers an uninstaller, and launches the app.

Build:  pyinstaller --onefile --windowed --icon ../app/static/coscribe.ico coscribe_setup.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

APP = "Coscribe"
REPO = "yfnidam72/COSCRIBE"
UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
# gyan.dev "essentials" (has libass + HarfBuzz, which Arabic needs), from its GitHub mirror: far faster.
FFMPEG_FALLBACK = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / APP
NO_WINDOW = 0x08000000
KEEP = {"data", "models", ".venv", "tools"}  # survive an update / reinstall

BG, SURFACE, TEXT, MUTED, ACCENT = "#0c0c0f", "#17171c", "#ededf2", "#8a8a9c", "#7c6cff"


# ---------------------------------------------------------------- helpers
def download(url: str, dest: Path, report) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "CoscribeSetup"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            if total:
                report(got / total)
    return dest


def run(cmd: list[str], log, cwd: Path | None = None, env: dict | None = None) -> None:
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    for line in proc.stdout:
        line = line.strip()
        if line:
            log(line, detail=True)
    if proc.wait() != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd[:3])}…")


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def ollama_present() -> bool:
    return have("ollama") or (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe").exists()


def source_zip_url() -> str:
    """Newest release if there is one, otherwise the main branch."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"User-Agent": "CoscribeSetup"})
        tag = json.loads(urllib.request.urlopen(req, timeout=20).read())["tag_name"]
        return f"https://github.com/{REPO}/archive/refs/tags/{tag}.zip"
    except Exception:  # noqa: BLE001
        return f"https://github.com/{REPO}/archive/refs/heads/main.zip"


def ffmpeg_url() -> str:
    try:
        req = urllib.request.Request("https://api.github.com/repos/GyanD/codexffmpeg/releases/latest",
                                     headers={"User-Agent": "CoscribeSetup"})
        assets = json.loads(urllib.request.urlopen(req, timeout=20).read())["assets"]
        return next(a["browser_download_url"] for a in assets if a["name"].endswith("essentials_build.zip"))
    except Exception:  # noqa: BLE001
        return FFMPEG_FALLBACK


def ps(script: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   creationflags=NO_WINDOW, check=True)


# ---------------------------------------------------------------- install steps
class Installer:
    def __init__(self, ui: "SetupUI", target: Path, opts: dict):
        self.ui, self.dir, self.opts = ui, target, opts
        self.tmp = Path(tempfile.mkdtemp(prefix="coscribe-setup-"))

    def step(self, frac: float, msg: str) -> None:
        self.ui.set_progress(frac, msg)

    def sub(self, start: float, span: float, msg: str):
        return lambda f: self.ui.set_progress(start + span * f, msg)

    def go(self) -> None:
        d = self.dir
        d.mkdir(parents=True, exist_ok=True)
        tools = d / "tools"
        tools.mkdir(exist_ok=True)

        # 1. uv (self-contained Python + package manager)
        uv = tools / "uv" / "uv.exe"
        if not uv.exists():
            z = download(UV_URL, self.tmp / "uv.zip", self.sub(0.0, 0.06, "Downloading uv"))
            with zipfile.ZipFile(z) as zf:
                for name in zf.namelist():
                    if name.endswith((".exe",)):
                        (tools / "uv").mkdir(exist_ok=True)
                        (tools / "uv" / Path(name).name).write_bytes(zf.read(name))

        # 2. app source (replaces code, keeps data/models/venv/tools)
        self.step(0.07, "Downloading Coscribe")
        src_url = os.environ.get("COSCRIBE_SOURCE_ZIP") or source_zip_url()
        if src_url.startswith(("http://", "https://")):
            z = download(src_url, self.tmp / "src.zip", self.sub(0.07, 0.05, "Downloading Coscribe"))
        else:
            z = Path(src_url)
        with zipfile.ZipFile(z) as zf:
            top = zf.namelist()[0].split("/")[0]
            for item in d.iterdir():
                if item.name not in KEEP:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
            for info in zf.infolist():
                rel = info.filename[len(top) + 1:]
                if not rel:
                    continue
                out = d / rel
                if info.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(zf.read(info))

        # 3. Python 3.11 + packages (~1.5 GB incl. CUDA libraries)
        self.step(0.13, "Installing Python 3.11")
        env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(tools / "python"))
        run([str(uv), "python", "install", "3.11"], self.ui.log, env=env)
        if not (d / ".venv" / "Scripts" / "python.exe").exists():
            run([str(uv), "venv", "--python", "3.11", str(d / ".venv")], self.ui.log, env=env)
        self.step(0.2, "Installing Coscribe's components (this is the longest step)")
        run([str(uv), "pip", "install", "--python", str(d / ".venv" / "Scripts" / "python.exe"),
             "-r", str(d / "requirements.txt")], self.ui.log, env=env)

        # 4. FFmpeg (private copy, so nothing else on the PC is touched)
        if not any((tools / "ffmpeg").glob("**/bin/ffmpeg.exe")):
            z = download(ffmpeg_url(), self.tmp / "ffmpeg.zip", self.sub(0.55, 0.12, "Downloading FFmpeg"))
            self.step(0.67, "Unpacking FFmpeg")
            with zipfile.ZipFile(z) as zf:
                zf.extractall(tools / "ffmpeg")

        # 5. Ollama (optional; runs the translation models)
        if self.opts["ollama"] and not ollama_present():
            exe = download(OLLAMA_URL, self.tmp / "OllamaSetup.exe", self.sub(0.68, 0.14, "Downloading Ollama"))
            self.step(0.82, "Installing Ollama")
            run([str(exe), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"], self.ui.log)

        # 6. Speech model (optional now; otherwise from the Models page)
        if self.opts["speech"]:
            self.step(0.84, "Downloading the speech model (Whisper large-v3, 3.1 GB)")
            run([str(d / ".venv" / "Scripts" / "python.exe"), "-c",
                 "from huggingface_hub import snapshot_download as s; s('Systran/faster-whisper-large-v3')"],
                self.ui.log)

        # 7. Shortcuts + uninstaller entry
        self.step(0.97, "Creating shortcuts")
        self.shortcuts()
        self.register_uninstall()
        self.step(1.0, "Coscribe is installed.")

    def shortcuts(self) -> None:
        d = self.dir
        targets = [Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"]
        if self.opts["desktop"]:
            targets.append(Path.home() / "Desktop")
            desk = Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Desktop"))
            if desk.exists():
                targets.append(desk)
        for folder in targets:
            ps(f"""$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{folder}\\{APP}.lnk');
$s.TargetPath='{d}\\.venv\\Scripts\\pythonw.exe';$s.Arguments='"{d}\\Coscribe.pyw"';
$s.WorkingDirectory='{d}';$s.IconLocation='{d}\\app\\static\\coscribe.ico,0';
$s.Description='Coscribe - the local video translation app';$s.Save()""")

    def register_uninstall(self) -> None:
        d = self.dir
        uninstall = d / "uninstall.ps1"
        uninstall.write_text(f"""# Removes Coscribe. Your videos in Videos\\Coscribe are kept.
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{d}*Coscribe.pyw*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}
# Only remove shortcuts that point into this install (never another copy of Coscribe).
$ws = New-Object -ComObject WScript.Shell
foreach ($lnk in @("$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\\{APP}.lnk", "$env:USERPROFILE\\Desktop\\{APP}.lnk", "$env:USERPROFILE\\OneDrive\\Desktop\\{APP}.lnk")) {{
    if ((Test-Path $lnk) -and $ws.CreateShortcut($lnk).TargetPath -like '{d}\\*') {{ Remove-Item $lnk -Force }}
}}
Remove-Item 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{APP}' -Recurse -Force
Start-Sleep 1
Remove-Item '{d}' -Recurse -Force
Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show('Coscribe was removed. Your exported videos are still in Videos\\Coscribe.', 'Coscribe') | Out-Null
""", encoding="utf-8-sig")
        key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Coscribe"
        ps(f"""New-Item -Path '{key}' -Force | Out-Null
Set-ItemProperty '{key}' DisplayName 'Coscribe'
Set-ItemProperty '{key}' Publisher 'Youssef Nidam'
Set-ItemProperty '{key}' DisplayIcon '{d}\\app\\static\\coscribe.ico'
Set-ItemProperty '{key}' InstallLocation '{d}'
Set-ItemProperty '{key}' UninstallString 'powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "{uninstall}"'
Set-ItemProperty '{key}' NoModify 1
Set-ItemProperty '{key}' NoRepair 1""")

    def launch(self) -> None:
        subprocess.Popen([str(self.dir / ".venv" / "Scripts" / "pythonw.exe"), str(self.dir / "Coscribe.pyw")],
                         cwd=str(self.dir))


# ---------------------------------------------------------------- UI
class SetupUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Coscribe Setup")
        self.root.configure(bg=BG)
        self.root.geometry("620x520")
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap(resource("coscribe.ico"))
        except Exception:  # noqa: BLE001
            pass
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("C.Horizontal.TProgressbar", troughcolor=SURFACE, background=ACCENT, bordercolor=SURFACE,
                        lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
        self.installer: Installer | None = None
        self.build()

    def label(self, parent, text, size=10, color=TEXT, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=("Segoe UI", size, "bold" if bold else "normal"),
                        justify="left", anchor="w", **kw)

    def build(self) -> None:
        pad = tk.Frame(self.root, bg=BG, padx=28, pady=24)
        pad.pack(fill="both", expand=True)
        self.label(pad, "Coscribe", 22, bold=True).pack(anchor="w")
        self.label(pad, "The local video translation app · by Youssef Nidam", 10, MUTED).pack(anchor="w", pady=(0, 16))
        self.label(pad, "Transcribe, translate (Arabic · English · French · Amazigh), style and burn captions into your "
                        "videos — everything runs on your computer.", 10, TEXT, wraplength=560).pack(anchor="w")

        self.opts_frame = tk.Frame(pad, bg=BG)
        self.opts_frame.pack(fill="x", pady=16)
        row = tk.Frame(self.opts_frame, bg=BG)
        row.pack(fill="x", pady=(0, 10))
        self.label(row, "Install to", 10, MUTED).pack(anchor="w")
        self.path = tk.StringVar(value=str(DEFAULT_DIR))
        ent = tk.Entry(row, textvariable=self.path, bg=SURFACE, fg=TEXT, insertbackground=TEXT, relief="flat",
                       font=("Segoe UI", 10))
        ent.pack(side="left", fill="x", expand=True, ipady=6)
        tk.Button(row, text="Browse…", command=self.browse, bg=SURFACE, fg=TEXT, relief="flat",
                  activebackground="#24242c", activeforeground=TEXT, padx=10).pack(side="left", padx=(8, 0), ipady=3)

        self.v_ollama = tk.BooleanVar(value=not ollama_present())
        self.v_speech = tk.BooleanVar(value=True)
        self.v_desktop = tk.BooleanVar(value=True)
        for var, text in ((self.v_speech, "Download the speech model now (Whisper large-v3, 3.1 GB)"),
                          (self.v_ollama, "Install Ollama — needed for Best-quality translation (free, ~1 GB)"
                           if not ollama_present() else "Ollama is already installed"),
                          (self.v_desktop, "Create a Desktop shortcut")):
            cb = tk.Checkbutton(self.opts_frame, text=text, variable=var, bg=BG, fg=TEXT, selectcolor=SURFACE,
                                activebackground=BG, activeforeground=TEXT, font=("Segoe UI", 10), anchor="w")
            cb.pack(anchor="w")
            if var is self.v_ollama and ollama_present():
                cb.configure(state="disabled")
        self.label(self.opts_frame, "Needs Windows 10/11, ~6 GB for the app and speech model, plus up to 18 GB for "
                   "translation models you choose later. An NVIDIA GPU makes it much faster.", 9, MUTED,
                   wraplength=560).pack(anchor="w", pady=(10, 0))

        self.status = self.label(pad, "", 10)
        self.status.pack(anchor="w", pady=(10, 6))
        self.bar = ttk.Progressbar(pad, style="C.Horizontal.TProgressbar", maximum=1000)
        self.bar.pack(fill="x")
        self.detail = self.label(pad, "", 8, MUTED, wraplength=560)
        self.detail.pack(anchor="w", pady=(6, 0))

        btns = tk.Frame(pad, bg=BG)
        btns.pack(side="bottom", fill="x")
        self.btn = tk.Button(btns, text="Install", command=self.start, bg=ACCENT, fg="white", relief="flat",
                             activebackground="#9a8dff", activeforeground="white", font=("Segoe UI", 11, "bold"),
                             padx=26, pady=6)
        self.btn.pack(side="right")
        tk.Button(btns, text="Cancel", command=self.root.destroy, bg=BG, fg=MUTED, relief="flat",
                  activebackground=BG, padx=14).pack(side="right", padx=8)

    def browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.path.get())
        if d:
            self.path.set(str(Path(d) / APP) if Path(d).name.lower() != APP.lower() else d)

    # thread-safe UI updates
    def set_progress(self, frac: float, msg: str) -> None:
        self.root.after(0, lambda: (self.bar.configure(value=int(frac * 1000)), self.status.configure(text=msg)))

    def log(self, line: str, detail: bool = False) -> None:
        self.root.after(0, lambda: self.detail.configure(text=line[:160]))

    def start(self) -> None:
        self.btn.configure(state="disabled", text="Installing…")
        for w in self.opts_frame.winfo_children():
            try:
                w.configure(state="disabled")
            except tk.TclError:
                pass
        opts = {"ollama": self.v_ollama.get(), "speech": self.v_speech.get(), "desktop": self.v_desktop.get()}
        self.installer = Installer(self, Path(self.path.get()), opts)
        threading.Thread(target=self.work, daemon=True).start()

    def work(self) -> None:
        try:
            self.installer.go()
            self.root.after(0, self.finished)
        except Exception as exc:  # noqa: BLE001
            log_file = Path(tempfile.gettempdir()) / "coscribe-setup.log"
            log_file.write_text(traceback.format_exc(), encoding="utf-8")
            self.root.after(0, lambda: self.failed(exc, log_file))

    def finished(self) -> None:
        self.detail.configure(text=f"Installed in {self.installer.dir}. Your videos will be saved to Videos\\Coscribe.")
        self.btn.configure(state="normal", text="Open Coscribe", command=self.open_and_close)

    def failed(self, exc: Exception, log_file: Path) -> None:
        self.status.configure(text="Setup could not finish.", fg="#ff5c6c")
        self.detail.configure(text=f"{exc}\nDetails: {log_file}. Check your internet connection and run setup again — "
                                   "it continues where it stopped.")
        self.btn.configure(state="normal", text="Retry", command=self.start)

    def open_and_close(self) -> None:
        self.installer.launch()
        self.root.after(800, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()


def resource(name: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / name)


if __name__ == "__main__":
    SetupUI().run()
