"""High-accuracy local translation through Ollama.

- Google TranslateGemma 12B: every language.
- MBZUAI-Paris Atlas-Chat 9B: used instead whenever the target is Moroccan Darija ("ary"),
  because it was trained on Darija and TranslateGemma drifts into MSA / adds words.
Everything runs on this computer. The fast NLLB engine lives in engine.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger("coscribe.translators")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
Progress = Callable[[float, str], None]

# Plain-language names + script/register rules the models need to get right.
LANG_INFO: dict[str, tuple[str, str]] = {
    "en": ("English", ""),
    "ar": ("Modern Standard Arabic", "Use correct Arabic spelling and Arabic punctuation (، ؛ ؟). No diacritics unless needed to avoid ambiguity."),
    "ary": ("Moroccan Arabic (Darija)", "Write natural Moroccan Darija as Moroccans actually speak it, in Arabic script (not MSA, not Latin letters/Arabizi). Keep common French/Spanish loanwords the way Moroccans use them."),
    "zgh": ("Standard Moroccan Tamazight", "Write in Tifinagh script (ⵜⵉⴼⵉⵏⴰⵖ)."),
    "fr": ("French", "Use French typographic spacing before ? ! : ;"),
    "es": ("Spanish", "Use ¿ and ¡ where required."),
    "de": ("German", ""), "it": ("Italian", ""), "pt": ("Portuguese", ""), "nl": ("Dutch", ""),
    "tr": ("Turkish", ""), "ru": ("Russian", ""), "zh": ("Simplified Chinese", "Use full-width Chinese punctuation."),
    "ja": ("Japanese", ""), "ko": ("Korean", ""), "hi": ("Hindi", ""), "ur": ("Urdu", ""),
    "fa": ("Persian", ""), "he": ("Hebrew", ""), "id": ("Indonesian", ""), "sw": ("Swahili", ""),
    "pl": ("Polish", ""), "uk": ("Ukrainian", ""),
}


def _lang(code: str) -> str:
    return LANG_INFO.get(code, (code, ""))[0]


# ============================================================== TranslateGemma (Ollama)
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
GEMMA_MODEL = os.environ.get("COSCRIBE_GEMMA", "translategemma:12b")
ATLAS_MODEL = os.environ.get("COSCRIBE_ATLAS", "atlas-chat:9b")
ATLAS_DIR = Path(__file__).resolve().parent.parent / "models" / "atlas-chat"

GEMMA_PROMPT = (
    "You are a professional {sn} ({sc}) to {tn} ({tc}) translator. Your goal is to accurately convey the "
    "meaning and nuances of the original {sn} text while adhering to {tn} grammar, vocabulary, and cultural "
    "sensitivities. Produce only the {tn} translation, without any additional explanations or commentary. "
    "Please translate the following {sn} text into {tn}:\n\n\n{text}"
)


def _ollama(path: str, payload: dict | None = None, timeout: int = 600):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _ensure_ollama(progress: Progress, model: str = GEMMA_MODEL) -> None:
    try:
        _ollama("/api/version", timeout=3).read()
    except Exception:  # noqa: BLE001
        exe = shutil.which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
        if not Path(exe).exists():
            raise RuntimeError("Ollama isn't installed, so the offline 'Great' engine is unavailable. "
                               "Install it from ollama.com or choose another engine.")
        progress(0.02, "Starting Ollama")
        subprocess.Popen([exe, "serve"], creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            time.sleep(0.5)
            try:
                _ollama("/api/version", timeout=2).read()
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            raise RuntimeError("Ollama didn't start.")
    tags = json.loads(_ollama("/api/tags", timeout=10).read())
    if any(m.get("name") == model for m in tags.get("models", [])):
        return
    if model == ATLAS_MODEL:
        _create_atlas(progress)
        return
    progress(0.02, "Downloading TranslateGemma (one time, ~8 GB)")
    with _ollama("/api/pull", {"model": GEMMA_MODEL}, timeout=7200) as resp:
        for line in resp:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise RuntimeError(f"Model download failed: {ev['error']}")
            if ev.get("total"):
                progress(0.02 + 0.13 * ev.get("completed", 0) / ev["total"], "Downloading TranslateGemma (one time, ~8 GB)")


ATLAS_REPO, ATLAS_FILE = "mradermacher/Atlas-Chat-9B-GGUF", "Atlas-Chat-9B.Q4_K_M.gguf"
ATLAS_TEMPLATE = Path(__file__).resolve().parent / "modelfiles" / "atlas-chat.Modelfile"


def _create_atlas(progress: Progress) -> None:
    """Atlas-Chat isn't in the Ollama library: fetch the GGUF once, then build it with our Modelfile."""
    ATLAS_DIR.mkdir(parents=True, exist_ok=True)
    if not (ATLAS_DIR / ATLAS_FILE).exists():
        progress(0.03, "Downloading the Darija model (one time, ~5.8 GB)")
        from huggingface_hub import hf_hub_download
        hf_hub_download(ATLAS_REPO, ATLAS_FILE, local_dir=str(ATLAS_DIR))
    modelfile = ATLAS_DIR / "Modelfile"
    modelfile.write_text(ATLAS_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    exe = shutil.which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    progress(0.08, "Setting up the Darija model (one time)")
    out = subprocess.run([exe, "create", ATLAS_MODEL, "-f", str(modelfile)], cwd=str(ATLAS_DIR),
                         capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError("Could not set up the Darija model: " + (out.stderr or out.stdout).strip()[-300:])


_META = re.compile(r"\s*[(\[（][^()\[\]（）]{0,80}[)\]）]")
_ALT = re.compile(r"(?<=\w)/\w+")


def clean_output(out: str, source: str) -> str:
    """Remove translator side-notes the model sometimes adds.

    e.g. "(الجملة غير مكتملة)" = "(sentence incomplete)", or "word/alternative" choices.
    Only stripped when the source itself has no brackets / slashes.
    """
    out = " ".join(out.split())
    if not re.search(r"[(\[]", source):
        out = _META.sub("", out)
    if "/" not in source:
        out = _ALT.sub("", out)
    out = re.sub(r"\s*(\.\.\.|…)\s*(?=[.。]?$)", "", out) if not re.search(r"(\.\.\.|…)\s*$", source) else out
    return out.strip()


def _unload(model: str) -> None:
    # Free the GPU for the next Whisper/export job.
    try:
        _ollama("/api/generate", {"model": model, "keep_alive": 0}, timeout=30).read()
    except Exception:  # noqa: BLE001
        pass


def _chat(model: str, prompt: str) -> str:
    body = {"model": model, "stream": False, "keep_alive": "2m",
            "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [{"role": "user", "content": prompt}]}
    return json.loads(_ollama("/api/chat", body, timeout=600).read())["message"]["content"].strip()


def gemma_translate(caps: list[dict], groups: list[list[int]], src: str, tgt: str, progress: Progress,
                    distribute: Callable[[list[dict], list[int], str], None]) -> None:
    if tgt == "ary":
        atlas_translate(caps, groups, src, progress, distribute)
        return
    _ensure_ollama(progress)
    sn, tn = _lang(src), _lang(tgt)
    sc, tc = ("ar-MA" if src == "ary" else src), tgt
    progress(0.15, "Loading TranslateGemma")
    try:
        for n, g in enumerate(groups):
            text = " ".join(caps[i]["text"] for i in g)
            out = _chat(GEMMA_MODEL, GEMMA_PROMPT.format(sn=sn, sc=sc, tn=tn, tc=tc, text=text))
            distribute(caps, g, clean_output(out, text))
            progress(0.15 + 0.85 * (n + 1) / len(groups), f"Translating offline with TranslateGemma ({n + 1}/{len(groups)})")
    finally:
        _unload(GEMMA_MODEL)


# Atlas-Chat follows Darija instructions best; the rules are spelled out because a general
# chat model otherwise explains, adds greetings, or slips into MSA / Arabizi.
ATLAS_PROMPT = (
    "ترجم هاد النص من {sn} للدارجة المغربية.\n"
    "- كتب بالدارجة المغربية كيف كيهضرو المغاربة، بالحروف العربية (ماشي بالفصحى وماشي بالحروف اللاتينية).\n"
    "- خلي المعنى كامل وبلا زيادة وبلا نقصان.\n"
    "- خلي الأسماء والماركات كيف هوما.\n"
    "- عطيني غير الترجمة، بلا شرح، بلا ملاحظات، بلا علامات تنصيص.\n\n"
    "النص:\n{text}"
)


_ROMAN = {"II": "2", "III": "3", "IV": "4", "VI": "6", "VII": "7", "VIII": "8", "IX": "9", "XI": "11", "XII": "12"}
_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def numbers_kept(source: str, out: str) -> bool:
    """True when every number / model numeral in the source survives in the translation.

    Atlas-Chat reliably Darija-fies meaning but can rewrite quantities ("18 months" ->
    "two and a half years"), which a viewer can't catch — so these are checked mechanically.
    """
    out_n = out.translate(_INDIC)
    for num in re.findall(r"\d+(?:[.,]\d+)?", source):
        if num not in out_n:
            return False
    for tok in re.findall(r"\b(?:II|III|IV|VI|VII|VIII|IX|XI|XII)\b", source):
        if tok not in out and _ROMAN[tok] not in out_n:
            return False
    return True


def atlas_translate(caps: list[dict], groups: list[list[int]], src: str, progress: Progress,
                    distribute: Callable[[list[dict], list[int], str], None]) -> None:
    _ensure_ollama(progress, ATLAS_MODEL)
    sn = {"en": "الإنجليزية", "fr": "الفرنسية", "ar": "العربية الفصحى", "es": "الإسبانية"}.get(src, _lang(src))
    progress(0.15, "Loading Atlas-Chat (Darija)")
    retry: list[list[int]] = []
    try:
        for n, g in enumerate(groups):
            text = " ".join(caps[i]["text"] for i in g)
            out = _chat(ATLAS_MODEL, ATLAS_PROMPT.format(sn=sn, text=text))
            out = re.sub(r'^\s*(الترجمة|Translation)\s*[:：]\s*', "", out).strip().strip('"«»')
            out = clean_output(out, text)
            if numbers_kept(text, out):
                distribute(caps, g, out)
            else:
                retry.append(g)
            progress(0.15 + 0.75 * (n + 1) / len(groups), f"Translating to Darija with Atlas-Chat ({n + 1}/{len(groups)})")
    finally:
        _unload(ATLAS_MODEL)
    if not retry:
        return
    # Sentences whose numbers changed are redone by TranslateGemma, which keeps them exact.
    log.info("atlas-chat changed numbers in %d sentence(s); redoing with TranslateGemma", len(retry))
    _ensure_ollama(progress)
    try:
        for n, g in enumerate(retry):
            text = " ".join(caps[i]["text"] for i in g)
            out = _chat(GEMMA_MODEL, GEMMA_PROMPT.format(sn=_lang(src), sc=src, tn=_lang("ary"), tc="ar-MA", text=text))
            distribute(caps, g, clean_output(out, text))
            progress(0.9 + 0.1 * (n + 1) / len(retry), "Double-checking numbers")
    finally:
        _unload(GEMMA_MODEL)
