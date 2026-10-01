"""Fetch a video from a link (YouTube and the other sites yt-dlp supports)."""
from __future__ import annotations

import logging
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

import engine

log = logging.getLogger("coscribe")
ROOT = engine.APP_DIR.parent
DENO_DIR = ROOT / "tools" / "deno"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
# Capped at 1080p: captions are burned in at source size, and 4K only makes every step slower.
FORMAT = ("bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/"
          "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b")


def looks_like_url(text: str) -> bool:
    return bool(re.match(r"^https?://\S+\.\S+", text.strip()))


def _runtimes() -> dict:
    # YouTube needs a JavaScript runtime to unlock every format; the installer puts Deno in tools\deno.
    out: dict = {}
    for exe in DENO_DIR.glob("**/deno.exe"):
        out["deno"] = {"path": str(exe)}
        break
    else:
        if shutil.which("deno"):
            out["deno"] = {}
    if shutil.which("node"):
        out["node"] = {}
    return out or {"deno": {}}


def ensure_runtime(progress) -> None:
    """First link import on a PC without Deno or Node: fetch Deno (~45 MB) once. Best effort."""
    if any(DENO_DIR.glob("**/deno.exe")) or shutil.which("deno") or shutil.which("node"):
        return
    tmp = DENO_DIR.with_suffix(".zip.part")
    try:
        DENO_DIR.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(DENO_URL, headers={"User-Agent": "Coscribe"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                progress(done / total if total else 0, "Setting up the YouTube helper (one time)")
        staging = DENO_DIR.with_name("deno.new")
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(tmp) as zf:
            zf.extractall(staging)
        staging.rename(DENO_DIR)  # only a complete unpack becomes visible
    except engine.Cancelled:
        raise
    except Exception:  # noqa: BLE001 - yt-dlp still works without it, with fewer formats
        log.warning("Deno download failed", exc_info=True)
    finally:
        tmp.unlink(missing_ok=True)


def _base_opts() -> dict:
    return {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "js_runtimes": _runtimes(), "socket_timeout": 30, "retries": 5}


def info(url: str) -> dict:
    """Title and length, without downloading. Raises ValueError with a readable message."""
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as y:
            i = y.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise ValueError(_clean(str(exc))) from exc
    if i.get("_type") == "playlist":
        raise ValueError("That link is a playlist. Paste the link of a single video.")
    if i.get("is_live"):
        raise ValueError("Live streams can't be imported. Try again once the stream has ended.")
    return {"title": i.get("title") or "Video", "duration": float(i.get("duration") or 0),
            "url": i.get("webpage_url") or url}


def fetch(url: str, folder: Path, progress) -> Path:
    """Download into folder/source.<ext>; progress(frac, msg) gets 0..1."""
    import yt_dlp

    def hook(d: dict) -> None:
        if d["status"] != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        # Video and audio arrive as two files; give the bigger video stream most of the bar.
        part = 0.0 if d.get("info_dict", {}).get("vcodec", "none") != "none" else 0.85
        span = 0.85 if part == 0.0 else 0.15
        frac = part + span * (done / total if total else 0)
        mb = done / 1e6
        progress(min(frac, 0.99), f"Downloading video · {mb:.0f} MB" + (f" of {total / 1e6:.0f} MB" if total else ""))

    ensure_runtime(lambda f, m: progress(0, m))
    opts = _base_opts() | {
        "format": FORMAT, "merge_output_format": "mp4",
        "outtmpl": str(folder / "source.%(ext)s"), "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(_clean(str(exc))) from exc
    files = [f for f in folder.glob("source.*") if f.suffix not in (".part", ".ytdl") and ".f" not in f.stem[6:]]
    if not files:
        raise RuntimeError("The download finished but no video file was found.")
    return max(files, key=lambda f: f.stat().st_size)


def _clean(msg: str) -> str:
    msg = re.sub(r"^ERROR:\s*", "", msg.strip())
    msg = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", msg)
    low = msg.lower()
    if "unsupported url" in low or "http error 404" in low or "no video formats" in low:
        return "Coscribe can't read videos from that link."
    if "private video" in low or "sign in" in low or "members-only" in low:
        return "This video is private or needs a sign-in, so it can't be downloaded."
    if "unavailable" in low:
        return "This video is unavailable."
    if "getaddrinfo" in low or "unable to download webpage" in low or "timed out" in low:
        return "Couldn't reach the site. Check your internet connection."
    return msg.split("\n")[0][:300]
