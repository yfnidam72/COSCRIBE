"""Coscribe local server: a tiny API + the single-page UI, all on 127.0.0.1."""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import engine

log = logging.getLogger("coscribe")
ROOT = engine.APP_DIR.parent
PROJECTS = ROOT / "data" / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)
OUTPUT = Path.home() / "Videos" / "Coscribe"
THEMES_FILE = ROOT / "data" / "themes.json"
SETTINGS_FILE = ROOT / "data" / "settings.json"
DEFAULT_SETTINGS = {"uiLang": "en", "appearance": "dark", "defaultTarget": "ar", "defaultEngine": "gemma",
                    "defaultTheme": None, "exportResolution": "source"}

app = FastAPI(title="Coscribe")
app.mount("/static", StaticFiles(directory=engine.APP_DIR / "static"), name="static")
app.mount("/fonts", StaticFiles(directory=engine.FONTS_DIR), name="fonts")

FONTS = [  # family, has Arabic glyphs
    ("Cairo", True), ("Tajawal", True), ("IBM Plex Sans Arabic", True), ("Readex Pro", True),
    ("Noto Kufi Arabic", True), ("Noto Naskh Arabic", True), ("Amiri", True), ("Lalezar", True),
    ("Inter", False), ("Montserrat", False), ("Poppins", False), ("Anton", False), ("Bebas Neue", False),
]


# ---------------------------------------------------------------- storage
def pdir(pid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", pid):
        raise HTTPException(404, "Unknown project")
    d = PROJECTS / pid
    if not (d / "project.json").exists():
        raise HTTPException(404, "Unknown project")
    return d


_lock = threading.Lock()


def load(pid: str) -> dict:
    return json.loads((pdir(pid) / "project.json").read_text(encoding="utf-8"))


def save(p: dict) -> None:
    with _lock:
        d = PROJECTS / p["id"]
        tmp = d / "project.json.tmp"
        p["updated"] = time.time()
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, d / "project.json")


def public(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "words"}
    if out.get("exported") and not Path(out["exported"]).exists():
        out["exported"] = None  # the file was moved or deleted outside the app
    return out


# ---------------------------------------------------------------- jobs
JOBS: dict[str, dict] = {}
_q: queue.Queue = queue.Queue()


def submit(pid: str, kind: str, fn) -> dict:
    for j in JOBS.values():
        if j["project"] == pid and j["state"] in ("queued", "running"):
            raise HTTPException(409, "This video is already busy - wait for the current step to finish.")
    job = {"id": uuid.uuid4().hex[:10], "project": pid, "kind": kind, "state": "queued",
           "progress": 0.0, "message": "Waiting…", "error": None, "cancel": False, "started": None}
    JOBS[job["id"]] = job
    _q.put((job, fn))
    return job


def _worker() -> None:
    # One worker: the GPU can only hold one model at a time anyway.
    while True:
        job, fn = _q.get()
        if job["cancel"]:
            job.update(state="cancelled", message="Cancelled")
            continue
        job["state"], job["started"] = "running", time.time()

        def progress(frac: float, msg: str, job=job) -> None:
            # Every long step reports progress often, so this is where a cancel lands.
            if job["cancel"]:
                raise engine.Cancelled()
            job["progress"], job["message"] = round(frac, 3), msg

        try:
            fn(progress)
            job.update(state="done", progress=1.0, message="Done")
        except engine.Cancelled:
            job.update(state="cancelled", message="Cancelled")
        except Exception as exc:  # noqa: BLE001
            log.error("job %s failed:\n%s", job["id"], traceback.format_exc())
            job.update(state="error", error=str(exc) or exc.__class__.__name__)


threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (engine.APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/meta")
def meta() -> dict:
    return {
        "languages": [{"code": c, "label": v[0], "rtl": c in engine.RTL, "asr": c in engine.WHISPER_LANGS}
                      for c, v in engine.LANGUAGES.items()],
        "fonts": [{"family": f, "arabic": a} for f, a in FONTS],
        "ratios": {f: round(engine.font_ratio(f), 4) for f, _ in FONTS},
        "output": str(OUTPUT),
        "engines": _engines(),
        "main": list(engine.MAIN_LANGS),
        "experimental": list(engine.EXPERIMENTAL_LANGS),
    }


# ---------------------------------------------------------------- settings + themes
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


@app.get("/api/settings")
def get_settings() -> dict:
    return DEFAULT_SETTINGS | _read_json(SETTINGS_FILE, {})


@app.put("/api/settings")
async def put_settings(req: Request) -> dict:
    body = await req.json()
    cur = get_settings()
    cur.update({k: v for k, v in body.items() if k in DEFAULT_SETTINGS})
    _write_json(SETTINGS_FILE, cur)
    return cur


@app.get("/api/themes")
def list_themes() -> list[dict]:
    return _read_json(THEMES_FILE, [])


@app.post("/api/themes")
async def add_theme(req: Request) -> dict:
    body = await req.json()
    name = str(body.get("name", "")).strip()[:40]
    if not name or not isinstance(body.get("style"), dict):
        raise HTTPException(400, "A theme needs a name and a style")
    themes = list_themes()
    theme = {"id": uuid.uuid4().hex[:8], "name": name, "style": body["style"], "created": time.time()}
    themes.insert(0, theme)
    _write_json(THEMES_FILE, themes)
    return theme


@app.put("/api/themes/{tid}")
async def update_theme(tid: str, req: Request) -> dict:
    body = await req.json()
    themes = list_themes()
    for t in themes:
        if t["id"] == tid:
            if "name" in body:
                t["name"] = str(body["name"]).strip()[:40] or t["name"]
            if isinstance(body.get("style"), dict):
                t["style"] = body["style"]
            _write_json(THEMES_FILE, themes)
            return t
    raise HTTPException(404, "Unknown theme")


@app.delete("/api/themes/{tid}")
def delete_theme(tid: str) -> dict:
    _write_json(THEMES_FILE, [t for t in list_themes() if t["id"] != tid])
    return {"ok": True}


def _engines() -> list[dict]:
    has_ollama = bool(shutil.which("ollama")) or (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe").exists()
    return [
        {"id": "gemma", "label": "TranslateGemma 12B", "available": has_ollama},
        {"id": "nllb", "label": "NLLB 600M", "available": True},
    ]


@app.get("/api/projects")
def list_projects() -> list[dict]:
    out = []
    busy = {j["project"]: j for j in JOBS.values() if j["state"] in ("queued", "running")}
    for f in PROJECTS.glob("*/project.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        exported = p.get("exported")
        out.append({k: p.get(k) for k in ("id", "name", "duration", "updated", "created", "width", "height",
                                          "language", "target")}
                   | {"captions": len(p.get("captions", [])),
                      "exported": bool(exported and Path(exported).exists()),
                      "job": ({k: busy[p["id"]][k] for k in ("id", "kind", "progress", "message")}
                              if p["id"] in busy else None)})
    return sorted(out, key=lambda x: x.get("updated") or 0, reverse=True)


@app.get("/thumb/{pid}")
def thumb(pid: str):
    d = pdir(pid)
    t = d / "thumb.jpg"
    if not t.exists():
        p = load(pid)
        engine.make_thumb(d / p["preview"], t, p.get("duration") or 0)
    if not t.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(t, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.post("/api/projects/{pid}/duplicate")
def duplicate(pid: str) -> dict:
    src_dir = pdir(pid)
    p = load(pid)
    new_id = uuid.uuid4().hex[:12]
    dst_dir = PROJECTS / new_id
    dst_dir.mkdir(parents=True)
    for f in src_dir.iterdir():
        if f.name in ("project.json", "project.json.tmp") or not f.is_file():
            continue
        try:
            os.link(f, dst_dir / f.name)  # large media shared on disk, not copied
        except OSError:
            shutil.copy2(f, dst_dir / f.name)
    p.update(id=new_id, name=f"{p['name']} (copy)", created=time.time(), exported=None)
    save(p)
    return public(p)


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str) -> dict:
    if jid not in JOBS:
        raise HTTPException(404, "Unknown job")
    JOBS[jid]["cancel"] = True
    return JOBS[jid]


@app.post("/api/projects")
async def create_project(file: UploadFile, language: str = "") -> dict:
    pid = uuid.uuid4().hex[:12]
    d = PROJECTS / pid
    d.mkdir(parents=True)
    ext = (Path(file.filename or "video.mp4").suffix or ".mp4").lower()
    src = d / f"source{ext}"
    with open(src, "wb") as fh:
        while chunk := await file.read(8 << 20):
            fh.write(chunk)
    try:
        info = engine.probe(src)
    except Exception as exc:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc

    engine.make_thumb(src, d / "thumb.jpg", info["duration"])
    p = {"id": pid, "name": Path(file.filename or "video").stem, "source": src.name,
         "preview": src.name, **info, "created": time.time(), "language": None,
         "target": None, "length": "normal", "captions": [], "words": [], "style": None,
         "exported": None, "engine": None}
    save(p)

    def run(progress):
        if not engine.browser_playable(info, src):
            engine.make_proxy(src, d / "preview.mp4", info["duration"], lambda f, m: progress(f * 0.3, m))
            cur = load(pid)
            cur["preview"] = "preview.mp4"
            save(cur)
        _transcribe(pid, language or None, progress)

    job = submit(pid, "transcribe", run)
    return {"project": public(p), "job": job}


def _transcribe(pid: str, language: str | None, progress) -> None:
    d = PROJECTS / pid
    p = load(pid)
    if not p["has_audio"]:
        raise RuntimeError("This video has no sound track, so there is nothing to transcribe.")
    audio = d / "audio.wav"
    if not audio.exists():
        progress(0.01, "Extracting audio")
        engine.extract_audio(d / p["source"], audio)
    lang, words = engine.transcribe(audio, p["duration"], language, progress)
    if not words:
        raise RuntimeError("No speech was found in this video.")
    p = load(pid)
    p.update(language=lang, words=words, captions=engine.build_captions(words, p.get("length", "normal")),
             target=None)
    save(p)


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    return public(load(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    shutil.rmtree(pdir(pid), ignore_errors=True)
    return {"ok": True}


@app.put("/api/projects/{pid}")
async def update_project(pid: str, req: Request) -> dict:
    body = await req.json()
    p = load(pid)
    for k in ("captions", "style", "name"):
        if k in body:
            p[k] = body[k]
    save(p)
    return {"ok": True}


@app.post("/api/projects/{pid}/transcribe")
async def retranscribe(pid: str, req: Request) -> dict:
    body = await req.json()
    pdir(pid)
    return submit(pid, "transcribe", lambda pr: _transcribe(pid, body.get("language") or None, pr))


@app.post("/api/projects/{pid}/rechunk")
async def rechunk(pid: str, req: Request) -> dict:
    body = await req.json()
    p = load(pid)
    length = body.get("length", "normal")
    if length not in engine.LENGTH_PRESETS:
        raise HTTPException(400, "Unknown length")
    if not p.get("words"):
        raise HTTPException(400, "Transcribe first")
    p.update(length=length, captions=engine.build_captions(p["words"], length), target=None)
    save(p)
    return public(p)


@app.post("/api/projects/{pid}/translate")
async def translate(pid: str, req: Request) -> dict:
    body = await req.json()
    target = body.get("target")
    p = load(pid)
    src = body.get("source") or p.get("language") or "en"
    if target not in engine.LANGUAGES:
        raise HTTPException(400, "Pick a language to translate into")
    if src not in engine.LANGUAGES:
        raise HTTPException(400, f"Translation from '{src}' is not supported yet")
    if not p.get("captions"):
        raise HTTPException(400, "Transcribe first")

    eng = body.get("engine") or "gemma"
    if eng not in engine.ENGINES:
        raise HTTPException(400, "Unknown translation engine")

    def run(progress):
        cur = load(pid)
        caps = cur["captions"]
        engine.translate(caps, src, target, progress, engine=eng)
        cur = load(pid)
        cur.update(captions=caps, target=target, engine=eng)
        save(cur)

    return submit(pid, "translate", run)


@app.post("/api/projects/{pid}/export")
async def export(pid: str, req: Request) -> dict:
    body = await req.json()
    p = load(pid)
    if body.get("style"):
        p["style"] = body["style"]
        save(p)
    d = PROJECTS / pid
    res = str(body.get("resolution", "source"))
    if res not in ("source", "1080", "720"):
        raise HTTPException(400, "Unknown resolution")
    subs = [f for f in body.get("subtitles", ["srt"]) if f in ("srt", "vtt")]

    def run(progress):
        cur = load(pid)
        out_h = cur["height"] if res == "source" else min(cur["height"], int(res))
        out_w = cur["width"] if out_h == cur["height"] else round(cur["width"] * out_h / cur["height"] / 2) * 2
        ass = d / "captions.ass"
        # Captions are laid out at the output size, so they stay crisp after scaling.
        ass.write_text(engine.to_ass(cur["captions"], cur["style"] or {}, out_w, out_h), encoding="utf-8-sig")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*]+', "_", cur["name"]).strip() or "video"
        suffix = f"_{cur['target']}" if cur.get("target") else ""
        dst = _unique(OUTPUT / f"{safe}{suffix}_coscribe.mp4")
        try:
            engine.export_video(d / cur["source"], ass, dst, cur, progress,
                                size=(out_w, out_h) if out_h != cur["height"] else None)
        except BaseException:
            dst.unlink(missing_ok=True)  # never leave a half-written video behind
            raise
        for field, tag in (("text", cur.get("language") or "orig"), ("tr", cur.get("target"))):
            if tag and any(c.get(field) for c in cur["captions"]):
                for fmt in subs:
                    writer = engine.to_srt if fmt == "srt" else engine.to_vtt
                    dst.with_name(f"{dst.stem}.{tag}.{fmt}").write_text(writer(cur["captions"], field),
                                                                        encoding="utf-8")
        cur = load(pid)
        cur["exported"] = str(dst)
        save(cur)

    return submit(pid, "export", run)


def _unique(path: Path) -> Path:
    n, cand = 2, path
    while cand.exists():
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return cand


@app.post("/api/projects/{pid}/reveal")
def reveal(pid: str) -> dict:
    p = load(pid)
    target = p.get("exported")
    if target and Path(target).exists():
        subprocess.Popen(["explorer", "/select,", target])
    else:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        os.startfile(OUTPUT)  # noqa: S606
    return {"ok": True}


@app.post("/api/reveal-output")
def reveal_output() -> dict:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.startfile(OUTPUT)  # noqa: S606
    return {"ok": True}


@app.post("/api/projects/{pid}/save-srt")
async def save_srt(pid: str, req: Request) -> dict:
    body = await req.json()
    field, fmt = body.get("field", "tr"), body.get("format", "srt")
    p = load(pid)
    if field not in ("text", "tr") or fmt not in ("srt", "vtt"):
        raise HTTPException(400, "field must be text or tr, format srt or vtt")
    if not any(c.get(field) for c in p["captions"]):
        raise HTTPException(400, "Nothing to save yet - translate first.")
    tag = p.get("target") if field == "tr" else p.get("language")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[<>:"/\|?*]+', "_", p["name"]).strip() or "video"
    dst = _unique(OUTPUT / f"{safe}.{tag or field}.{fmt}")
    dst.write_text((engine.to_srt if fmt == "srt" else engine.to_vtt)(p["captions"], field), encoding="utf-8")
    subprocess.Popen(["explorer", "/select,", str(dst)])
    return {"name": dst.name, "path": str(dst)}


@app.get("/api/projects/{pid}/srt")
def srt(pid: str, field: str = "tr") -> Response:
    p = load(pid)
    if field not in ("text", "tr"):
        raise HTTPException(400, "field must be text or tr")
    tag = p.get("target") if field == "tr" else p.get("language")
    name = f"{p['name']}.{tag or field}.srt"
    return Response(engine.to_srt(p["captions"], field), media_type="application/x-subrip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(name)}"})


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


@app.get("/api/jobs/{jid}")
def job(jid: str) -> dict:
    if jid not in JOBS:
        raise HTTPException(404, "Unknown job")
    return JOBS[jid]


@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: str) -> list[dict]:
    return [j for j in JOBS.values() if j["project"] == pid and j["state"] in ("queued", "running")]


# ---------------------------------------------------------------- media (with Range support)
@app.get("/media/{pid}")
def media(pid: str, request: Request):
    p = load(pid)
    path = PROJECTS / pid / p["preview"]
    size = path.stat().st_size
    ctype = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
             ".mov": "video/mp4"}.get(path.suffix.lower(), "video/mp4")
    rng = request.headers.get("range")
    if not rng:
        return FileResponse(path, media_type=ctype, headers={"Accept-Ranges": "bytes"})
    m = re.match(r"bytes=(\d*)-(\d*)", rng)
    start = int(m.group(1)) if m and m.group(1) else 0
    end = int(m.group(2)) if m and m.group(2) else size - 1
    end = min(end, size - 1, start + (8 << 20) - 1)
    if start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def body():
        with open(path, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=206, media_type=ctype, headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1)})
