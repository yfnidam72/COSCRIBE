"""Model catalog: what Coscribe can use, whether it's installed, and how to fetch / remove it.

Three storage kinds:
- "hf"     faster-whisper models in the Hugging Face cache (~/.cache/huggingface/hub)
- "dir"    CTranslate2 NLLB models under <repo>/models/<name>
- "ollama" models served by Ollama (TranslateGemma, Atlas-Chat)
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

import engine
import translators

Progress = Callable[[float, str], None]

CATALOG: list[dict] = [
    # ---------------------------------------------------------------- transcription
    {"id": "large-v3", "group": "transcription", "name": "Whisper large-v3", "kind": "hf",
     "repo": "Systran/faster-whisper-large-v3", "size": 3.09, "tag": "recommended"},
    {"id": "large-v3-turbo", "group": "transcription", "name": "Whisper large-v3 turbo", "kind": "hf",
     "repo": "dropbox-dash/faster-whisper-large-v3-turbo", "size": 1.62, "tag": "faster"},
    {"id": "medium", "group": "transcription", "name": "Whisper medium", "kind": "hf",
     "repo": "Systran/faster-whisper-medium", "size": 1.53, "tag": "lighter"},
    {"id": "small", "group": "transcription", "name": "Whisper small", "kind": "hf",
     "repo": "Systran/faster-whisper-small", "size": 0.49, "tag": "cpu"},
    # ---------------------------------------------------------------- translation
    {"id": "translategemma", "group": "translation", "name": "TranslateGemma 12B", "kind": "ollama",
     "ollama": translators.GEMMA_MODEL, "size": 8.1, "tag": "best"},
    {"id": "nllb-600m", "group": "translation", "name": "NLLB-200 600M", "kind": "dir",
     "repo": engine.NLLB_REPO, "dir": "nllb-600m", "size": 0.62, "tag": "fast"},
    {"id": "nllb-3.3b", "group": "translation", "name": "NLLB-200 3.3B", "kind": "dir",
     "repo": engine.NLLB_BIG_REPO, "dir": "nllb-3.3b", "size": 3.36, "tag": "amazigh"},
    {"id": "atlas-chat", "group": "translation", "name": "Atlas-Chat 9B", "kind": "ollama",
     "ollama": translators.ATLAS_MODEL, "size": 5.76, "tag": "darija"},
]
BY_ID = {m["id"]: m for m in CATALOG}


def _hf_dir(repo: str) -> Path:
    home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return home / "hub" / ("models--" + repo.replace("/", "--"))


def _dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _ollama_models() -> set[str] | None:
    try:
        tags = json.loads(translators._ollama("/api/tags", timeout=3).read())
        return {m["name"] for m in tags.get("models", [])}
    except Exception:  # noqa: BLE001
        return None  # Ollama not running / not installed


def ollama_installed() -> bool:
    return bool(shutil.which("ollama")) or (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe").exists()


def status() -> list[dict]:
    served = _ollama_models()
    out = []
    for m in CATALOG:
        s = {k: m[k] for k in ("id", "group", "name", "kind", "size", "tag")}
        if m["kind"] == "hf":
            snap = _hf_dir(m["repo"]) / "snapshots"
            s["installed"] = snap.exists() and any(snap.glob("*/model.bin"))
        elif m["kind"] == "dir":
            s["installed"] = (engine.MODELS_DIR / m["dir"] / "model.bin").exists()
        else:
            s["installed"] = bool(served and m["ollama"] in served)
            s["needsOllama"] = not ollama_installed()
        out.append(s)
    return out


def _watch_size(path: Path, total_gb: float, progress: Progress, label: str, stop: threading.Event,
                start: float = 0.02, span: float = 0.96) -> None:
    """Hugging Face downloads report nothing we can hook, so progress = bytes on disk."""
    base = _dir_size(path) if path.exists() else 0
    want = max(1, total_gb * 1e9 - base)
    while not stop.wait(1.0):
        got = (_dir_size(path) if path.exists() else 0) - base
        try:
            progress(start + span * min(0.99, got / want), label)
        except engine.Cancelled:
            return


def download(model_id: str, progress: Progress) -> None:
    m = BY_ID[model_id]
    label = f"Downloading {m['name']} (~{m['size']:.1f} GB)"
    progress(0.01, label)
    if m["kind"] in ("hf", "dir"):
        from huggingface_hub import snapshot_download
        target = _hf_dir(m["repo"]) if m["kind"] == "hf" else engine.MODELS_DIR / m["dir"]
        stop = threading.Event()
        t = threading.Thread(target=_watch_size, args=(target, m["size"], progress, label, stop), daemon=True)
        t.start()
        try:
            if m["kind"] == "hf":
                snapshot_download(m["repo"])
            else:
                snapshot_download(m["repo"], local_dir=str(target))
        finally:
            stop.set()
        progress(0.99, "Verifying")
        return
    # Ollama-served models
    translators._ensure_ollama(progress, m["ollama"])


def remove(model_id: str) -> None:
    m = BY_ID[model_id]
    if m["kind"] == "hf":
        shutil.rmtree(_hf_dir(m["repo"]), ignore_errors=True)
    elif m["kind"] == "dir":
        shutil.rmtree(engine.MODELS_DIR / m["dir"], ignore_errors=True)
    else:
        req_body = {"model": m["ollama"]}
        import urllib.request
        req = urllib.request.Request(translators.OLLAMA + "/api/delete", data=json.dumps(req_body).encode(),
                                     headers={"Content-Type": "application/json"}, method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:  # noqa: BLE001
            pass
        if model_id == "atlas-chat":
            shutil.rmtree(translators.ATLAS_DIR, ignore_errors=True)
    time.sleep(0.2)
