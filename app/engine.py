"""Coscribe engine: media probing, transcription, translation, caption building, export.

Heavy models (Whisper, NLLB) are loaded inside one call and released afterwards so
the 8GB GPU never holds both at once.
"""
from __future__ import annotations

import gc
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from gpu import ensure_cuda_dlls

log = logging.getLogger("coscribe.engine")

APP_DIR = Path(__file__).resolve().parent
FONTS_DIR = APP_DIR / "fonts"
MODELS_DIR = APP_DIR.parent / "models"

WHISPER_REPO = os.environ.get("COSCRIBE_WHISPER", "Systran/faster-whisper-large-v3")
NLLB_REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
NLLB_BIG_REPO = "OpenNMT/nllb-200-3.3B-ct2-int8"

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

Progress = Callable[[float, str], None]

# ---------------------------------------------------------------- languages
# code -> (label, NLLB code). Codes are Whisper codes where Whisper supports them.
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", "eng_Latn"),
    "ar": ("Arabic", "arb_Arab"),
    "zgh": ("Amazigh (Tifinagh)", "tzm_Tfng"),
    "ary": ("Moroccan Darija", "ary_Arab"),
    "fr": ("French", "fra_Latn"),
    "es": ("Spanish", "spa_Latn"),
    "de": ("German", "deu_Latn"),
    "it": ("Italian", "ita_Latn"),
    "pt": ("Portuguese", "por_Latn"),
    "nl": ("Dutch", "nld_Latn"),
    "tr": ("Turkish", "tur_Latn"),
    "ru": ("Russian", "rus_Cyrl"),
    "zh": ("Chinese (Simplified)", "zho_Hans"),
    "ja": ("Japanese", "jpn_Jpan"),
    "ko": ("Korean", "kor_Hang"),
    "hi": ("Hindi", "hin_Deva"),
    "ur": ("Urdu", "urd_Arab"),
    "fa": ("Persian", "pes_Arab"),
    "he": ("Hebrew", "heb_Hebr"),
    "id": ("Indonesian", "ind_Latn"),
    "sw": ("Swahili", "swh_Latn"),
    "pl": ("Polish", "pol_Latn"),
    "uk": ("Ukrainian", "ukr_Cyrl"),
}
RTL = {"ar", "ary", "ur", "fa", "he"}
# The app's first-class languages (UI + captions); shown first in every language picker.
MAIN_LANGS = ("ar", "en", "fr", "zgh")
# Output quality not yet reliable enough to trust without review.
EXPERIMENTAL_LANGS = ("ary",)
TIFINAGH_FONT = "Noto Sans Tifinagh"


class Cancelled(Exception):
    """Raised from a progress callback when the user cancels a job."""
# Languages Whisper can transcribe (the Darija/Tamazight entries are translation-only).
WHISPER_LANGS = [c for c in LANGUAGES if c not in ("ary", "zgh")]


# ---------------------------------------------------------------- media
def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW,
                          encoding="utf-8", errors="replace", **kw)


def probe(path: Path) -> dict:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                "-show_format", str(path)])
    if out.returncode != 0:
        raise RuntimeError(f"Could not read this file as a video: {out.stderr.strip()[:300]}")
    data = json.loads(out.stdout)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    if video is None:
        raise RuntimeError("This file has no video track.")
    w, h = int(video["width"]), int(video["height"])
    rot = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if abs(rot) in (90, 270):
        w, h = h, w
    return {
        "duration": float(data["format"].get("duration") or video.get("duration") or 0),
        "width": w, "height": h,
        "vcodec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
        "has_audio": audio is not None,
        "acodec": audio.get("codec_name") if audio else None,
    }


def browser_playable(info: dict, path: Path) -> bool:
    return (info["vcodec"] in ("h264", "vp8", "vp9", "av1")
            and info.get("pix_fmt") in ("yuv420p", "yuvj420p", None)
            and path.suffix.lower() in (".mp4", ".m4v", ".webm", ".mov"))


def make_proxy(src: Path, dst: Path, duration: float, progress: Progress) -> None:
    """Lightweight H.264 copy for the in-app preview when the source codec won't play."""
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", "scale=-2:'min(720,ih)'",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", str(dst)]
    _ffmpeg_with_progress(cmd, duration, lambda p: progress(p, "Preparing preview"))


def make_thumb(src: Path, dst: Path, duration: float) -> None:
    """Library card image: a frame ~10% in (skips black intros), 480px wide."""
    at = max(0.0, min(duration * 0.1, 8.0)) if duration else 0.0
    _run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
          "-vf", "scale=480:-2", "-q:v", "4", str(dst)])


def extract_audio(src: Path, dst: Path) -> None:
    out = _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(dst)])
    if out.returncode != 0:
        raise RuntimeError("Could not extract audio: " + out.stderr.strip()[-300:])


def _ffmpeg_with_progress(cmd: list[str], duration: float, cb: Callable[[float], None],
                          cwd: str | None = None) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
                            creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    import threading
    err: list[str] = []
    t = threading.Thread(target=lambda: err.extend(proc.stderr.readlines()), daemon=True)
    t.start()
    try:
        for line in proc.stdout:
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    frac = min(0.99, int(line.split("=")[1]) / 1e6 / duration)
                except ValueError:
                    continue
                cb(frac)
    except BaseException:
        proc.kill()  # cancelled (or crashed) mid-render: don't leave ffmpeg running
        proc.wait()
        raise
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + "".join(err[-8:]).strip()[-600:])


# ---------------------------------------------------------------- transcription
def _load_whisper(repo: str = WHISPER_REPO):
    ensure_cuda_dlls()
    import numpy as np
    from faster_whisper import WhisperModel
    def make(device: str, compute: str):
        # Fully offline once cached; only reach the internet for the one-time download.
        try:
            return WhisperModel(repo, device=device, compute_type=compute, local_files_only=True)
        except Exception:  # noqa: BLE001
            return WhisperModel(repo, device=device, compute_type=compute)

    try:
        model = make("cuda", "float16")
        # The first encode is what touches cuBLAS; force it so failures fall back here.
        list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en")[0])
        return model, "GPU"
    except Exception as exc:  # noqa: BLE001
        log.warning("GPU whisper failed (%s) - using CPU", exc)
        return make("cpu", "int8"), "CPU"


def transcribe(audio: Path, duration: float, language: str | None, progress: Progress,
               repo: str = WHISPER_REPO) -> tuple[str, list[dict]]:
    progress(0.02, "Loading speech model (the first time downloads it)")
    model, dev = _load_whisper(repo)
    try:
        # Accuracy-first decoding: full beam search with temperature fallback, generous VAD
        # padding so word edges aren't clipped, and hallucination suppression in silences.
        segments, info = model.transcribe(
            str(audio), language=language or None, word_timestamps=True,
            beam_size=5, best_of=5, patience=1.5,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 400},
            condition_on_previous_text=True, hallucination_silence_threshold=2.0,
        )
        lang = info.language
        progress(0.05, f"Listening ({LANGUAGES.get(lang, (lang,))[0]} detected, {dev})")
        words: list[dict] = []
        for seg in segments:
            for w in seg.words or []:
                t = w.word.strip()
                if t:
                    words.append({"w": t, "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
            if duration > 0:
                progress(0.05 + 0.93 * min(1.0, seg.end / duration), f"Transcribing on {dev}")
        return lang, words
    finally:
        del model
        gc.collect()


# ---------------------------------------------------------------- captions
LENGTH_PRESETS = {  # max chars, max seconds
    "short": (22, 2.2),
    "normal": (44, 4.5),
    "long": (80, 6.5),
}
_END = re.compile(r"[.!?؟…。]$")
_PAUSE = re.compile(r"[,;:،]$")


def build_captions(words: list[dict], length: str = "normal") -> list[dict]:
    max_chars, max_dur = LENGTH_PRESETS.get(length, LENGTH_PRESETS["normal"])
    caps: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            caps.append({"start": cur[0]["s"], "end": cur[-1]["e"],
                         "text": " ".join(x["w"] for x in cur), "tr": ""})
            cur.clear()

    for w in words:
        if cur:
            text_len = len(" ".join(x["w"] for x in cur)) + 1 + len(w["w"])
            gap = w["s"] - cur[-1]["e"]
            # A sentence-final word may overflow a little rather than dangle alone.
            tail_ok = _END.search(w["w"]) and text_len <= max_chars * 1.25 and gap < 0.4
            if gap > 0.8 or ((text_len > max_chars or w["e"] - cur[0]["s"] > max_dur) and not tail_ok):
                flush()
        cur.append(w)
        n = len(" ".join(x["w"] for x in cur))
        if _END.search(w["w"]) and n > max_chars * 0.3:
            flush()
        elif _PAUSE.search(w["w"]) and n > max_chars * 0.6:
            flush()
    flush()

    # Let captions linger a little into silence so they are readable.
    for i, c in enumerate(caps):
        nxt = caps[i + 1]["start"] if i + 1 < len(caps) else c["end"] + 1.0
        c["end"] = round(min(nxt, max(c["end"], c["start"] + 0.7) + 0.25), 3)
        c["id"] = i
    return caps


# ---------------------------------------------------------------- translation
def _nllb_path(progress: Progress) -> Path:
    local = MODELS_DIR / "nllb-600m"
    if (local / "model.bin").exists():
        return local
    progress(0.02, "Downloading translation model (one time, ~620 MB)")
    from huggingface_hub import snapshot_download
    snapshot_download(NLLB_REPO, local_dir=str(local))
    return local


ENGINES = ("gemma", "nllb")


def translate(caps: list[dict], src: str, tgt: str, progress: Progress, engine: str = "gemma") -> None:
    if src not in LANGUAGES or tgt not in LANGUAGES:
        raise ValueError("Unsupported language")
    import translators
    if tgt == "zgh":
        # Only NLLB 3.3B writes usable Tifinagh locally (TranslateGemma and NLLB-600M don't),
        # so Amazigh always goes there whatever quality was picked.
        _nllb_translate(caps, src, tgt, progress, big=True)
    elif engine == "gemma":
        translators.gemma_translate(caps, _sentence_groups(caps), src, tgt, progress, _distribute)
    else:
        _nllb_translate(caps, src, tgt, progress)


def _nllb_big_path(progress: Progress) -> Path:
    local = MODELS_DIR / "nllb-3.3b"
    if (local / "model.bin").exists():
        return local
    progress(0.02, "Downloading the Amazigh model (one time, ~3.3 GB)")
    from huggingface_hub import snapshot_download
    snapshot_download(NLLB_BIG_REPO, local_dir=str(local))
    return local


def _nllb_translate(caps: list[dict], src: str, tgt: str, progress: Progress, big: bool = False) -> None:
    """Translates in sentence groups for context, then spreads words back over captions."""
    spm_dir = _nllb_path(progress)  # all NLLB sizes share this SentencePiece vocabulary
    path = _nllb_big_path(progress) if big else spm_dir
    progress(0.1, "Loading translation model")
    ensure_cuda_dlls()
    import ctranslate2
    import sentencepiece as spm

    sp = spm.SentencePieceProcessor(model_file=str(spm_dir / "sentencepiece.bpe.model"))
    try:
        tr = ctranslate2.Translator(str(path), device="cuda", compute_type="int8_float16")
        tr.translate_batch([["eng_Latn", "▁hi", "</s>"]], target_prefix=[["fra_Latn"]])
    except Exception as exc:  # noqa: BLE001
        log.warning("GPU translator failed (%s) - using CPU", exc)
        tr = ctranslate2.Translator(str(path), device="cpu", compute_type="int8")

    s_code, t_code = LANGUAGES[src][1], LANGUAGES[tgt][1]
    groups = _sentence_groups(caps)
    try:
        batch = 16
        for b in range(0, len(groups), batch):
            chunk = groups[b:b + batch]
            texts = [" ".join(caps[i]["text"] for i in g) for g in chunk]
            src_tok = [[s_code] + sp.encode(t, out_type=str) + ["</s>"] for t in texts]
            res = tr.translate_batch(src_tok, target_prefix=[[t_code]] * len(src_tok),
                                     beam_size=5 if big else 4, max_decoding_length=256,
                                     repetition_penalty=1.2 if big else 1.1,
                                     no_repeat_ngram_size=3 if big else 0)
            for g, r in zip(chunk, res):
                out = sp.decode([t for t in r.hypotheses[0] if t != t_code])
                _distribute(caps, g, out.strip())
            progress(0.15 + 0.85 * min(1.0, (b + batch) / max(1, len(groups))),
                     "Translating to Amazigh" if tgt == "zgh" else "Translating (fast)")
    finally:
        del tr
        gc.collect()


def _sentence_groups(caps: list[dict], max_caps: int = 10, max_chars: int = 450) -> list[list[int]]:
    """Group captions into whole sentences so the translator never sees a cut-off sentence.

    Only a very long run-on sentence (or a long pause) forces an early break.
    """
    groups: list[list[int]] = []
    cur: list[int] = []
    chars = 0
    for i, c in enumerate(caps):
        cur.append(i)
        chars += len(c["text"])
        gap = caps[i + 1]["start"] - c["end"] if i + 1 < len(caps) else 99
        sentence_end = bool(_END.search(c["text"].strip()))
        if sentence_end or gap > 2.0 or len(cur) >= max_caps or chars > max_chars:
            groups.append(cur)
            cur, chars = [], 0
    if cur:
        groups.append(cur)
    return groups


_BREAK = re.compile(r"[,.;:!?،؛؟…。，、]$")


def _distribute(caps: list[dict], idx: list[int], text: str) -> None:
    """Split one translated sentence across its captions.

    Split points start proportional to each caption's share of the source text, then
    snap to a nearby punctuation mark (within 2 words) so a caption ends on a natural pause.
    """
    if len(idx) == 1:
        caps[idx[0]]["tr"] = text
        return
    words = text.split()
    n = len(words)
    weights = [max(1, len(caps[i]["text"])) for i in idx]
    total = sum(weights)
    cuts, acc, prev = [], 0, 0
    for k in range(len(idx) - 1):
        acc += weights[k]
        target = round(n * acc / total)
        best = target
        for d in (0, 1, -1, 2, -2):
            j = target + d
            if prev < j < n and _BREAK.search(words[j - 1]):
                best = j
                break
        # Each remaining caption keeps at least one word when possible.
        best = max(prev + 1, min(best, n - (len(idx) - 1 - k)))
        best = min(best, n)
        cuts.append(best)
        prev = best
    bounds = [0] + cuts + [n]
    for k, i in enumerate(idx):
        caps[i]["tr"] = " ".join(words[bounds[k]:bounds[k + 1]])


# ---------------------------------------------------------------- subtitles
def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def to_vtt(caps: list[dict], field: str) -> str:
    out = ["WEBVTT", ""]
    for c in caps:
        txt = (c.get(field) or "").strip()
        if txt:
            out.append(f"{_ts_srt(c['start']).replace(',', '.')} --> {_ts_srt(c['end']).replace(',', '.')}\n{txt}\n")
    return "\n".join(out)


def to_srt(caps: list[dict], field: str) -> str:
    out = []
    n = 1
    for c in caps:
        txt = (c.get(field) or "").strip()
        if not txt:
            continue
        out.append(f"{n}\n{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}\n{txt}\n")
        n += 1
    return "\n".join(out)


_metrics_cache: dict[str, float] = {}


def font_ratio(family: str) -> float:
    """(winAscent+winDescent)/unitsPerEm: libass sizes fonts by that height, CSS by em."""
    if family in _metrics_cache:
        return _metrics_cache[family]
    ratio = 1.2
    try:
        from fontTools.ttLib import TTFont
        for f in FONTS_DIR.glob("*.ttf"):
            tt = TTFont(str(f), lazy=True)
            if tt["name"].getBestFamilyName() == family:
                os2 = tt["OS/2"]
                ratio = (os2.usWinAscent + os2.usWinDescent) / tt["head"].unitsPerEm
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("font metrics failed for %s: %s", family, exc)
    _metrics_cache[family] = ratio
    return ratio


def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = 255 - round(max(0.0, min(1.0, opacity)) * 255)
    return f"&H{a:02X}{b}{g}{r}".upper()


def _ass_ts(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def to_ass(caps: list[dict], style: dict, w: int, h: int) -> str:
    """Mirrors the preview renderer in static/app.js (sizes are % of video height)."""
    fam = style.get("font", "Cairo")
    em = style.get("size", 6.0) / 100 * h
    size = em * font_ratio(fam)
    bold = -1 if style.get("bold", True) else 0
    mode = style.get("bg", "box")
    bg_col = style.get("bgColor", "#000000")
    bg_op = style.get("bgOpacity", 70) / 100
    pad = style.get("pad", 0.35) * em
    outline_w = style.get("outline", 0.08) * em
    pos = style.get("position", "bottom")
    align = {"bottom": 2, "middle": 5, "top": 8}[pos]
    margin_v = round(style.get("offset", 8) / 100 * h)
    margin_h = round(style.get("sideMargin", 8) / 100 * w)

    if mode == "box":
        border_style, outline, shadow, out_col, back_col = 3, pad, 0, _ass_color(bg_col, bg_op), _ass_color(bg_col, bg_op)
    elif mode == "outline":
        border_style, outline, shadow, out_col, back_col = 1, outline_w, 0, _ass_color(bg_col, 1), _ass_color(bg_col, 0)
    elif mode == "shadow":
        border_style, outline, shadow, out_col, back_col = 1, 0, max(1.0, em * 0.06), _ass_color(bg_col, 0), _ass_color(bg_col, bg_op)
    else:
        border_style, outline, shadow, out_col, back_col = 1, 0, 0, _ass_color(bg_col, 0), _ass_color(bg_col, 0)
    if mode == "box":
        # In box mode MarginV is measured to the text, so keep the box itself inside the frame.
        margin_v = max(margin_v, round(pad))

    primary = _ass_color(style.get("color", "#FFFFFF"))
    show = style.get("show", "translation")
    sec_ratio = style.get("secondarySize", 70) / 100
    sec_col = _ass_color(style.get("secondaryColor", "#FFD84D"))
    upper = style.get("uppercase", False)

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{fam},{size:.1f},{primary},{primary},{out_col},{back_col},{bold},0,0,0,100,100,0,0,{border_style},{outline:.1f},{shadow:.1f},{align},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    tif = "{\\fn%s}" % TIFINAGH_FONT
    lines = [head]
    for c in caps:
        orig, trans = c.get("text", "").strip(), (c.get("tr") or "").strip()
        if upper:
            orig, trans = orig.upper(), trans.upper()
        if show == "original" or (show == "translation" and not trans):
            body = _esc(orig)
        elif show == "translation":
            body = _esc(trans)
        else:  # both: translation on top, original smaller underneath
            first, second = (trans or orig), (orig if trans else "")
            body = _esc(first)
            if second:
                body += "\\N{\\fs%.1f\\c%s}" % (size * sec_ratio, sec_col) + _esc(second)
        body = re.sub(r"([\u2D30-\u2D7F][\u2D30-\u2D7F\s.,!?]*)", lambda m: tif + m.group(1) + "{\\fn}", body)
        if body:
            lines.append(f"Dialogue: 0,{_ass_ts(c['start'])},{_ass_ts(c['end'])},Main,,0,0,0,,{body}\n")
    return "".join(lines)


def export_video(src: Path, ass: Path, dst: Path, info: dict, progress: Progress,
                 size: tuple[int, int] | None = None) -> None:
    # ffmpeg runs from the caption folder (below), so every other path must be absolute.
    src, dst, ass = src.resolve(), dst.resolve(), ass.resolve()
    fonts_rel = os.path.relpath(FONTS_DIR, ass.parent).replace("\\", "/")
    # The `ass` filter (unlike `subtitles`) exposes shaping; complex = HarfBuzz, which
    # Arabic needs - the simple shaper drops final letter forms as empty boxes.
    vf = f"ass={ass.name}:fontsdir={fonts_rel}:shaping=complex"
    if size:
        vf = f"scale={size[0]}:{size[1]}:flags=lanczos," + vf
    enc = _encoder()
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, *enc, "-pix_fmt", "yuv420p"]
    if info.get("has_audio"):
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(dst)]
    # Run from the caption folder so the subtitles filter gets relative paths:
    # absolute Windows paths need fragile "C\:" escaping inside filtergraphs.
    _ffmpeg_with_progress(cmd, info["duration"], lambda p: progress(p, "Rendering video"),
                          cwd=str(ass.parent))


_enc_cache: list[str] | None = None


def _encoder() -> list[str]:
    global _enc_cache
    if _enc_cache is None:
        test = _run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
                     "-c:v", "h264_nvenc", "-f", "null", "-"])
        _enc_cache = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
                      if test.returncode == 0 else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"])
    return _enc_cache
