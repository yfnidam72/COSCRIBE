# COSCRIBE

**The local video translation app** — by Youssef Nidam.

Drop a video. Coscribe listens, writes the captions, translates them, lets you style them, and burns them into the video. Everything runs on your own computer: no uploads, no accounts, no subscriptions.

![Themes collection](docs/screenshots/themes.png)

## Languages

| | Captions (speech → text) | Translation | Interface |
|---|---|---|---|
| **Arabic** | ✅ | ✅ | ✅ (right-to-left) |
| **English** | ✅ | ✅ | ✅ |
| **French** | ✅ | ✅ | ✅ |
| **Amazigh (Tifinagh)** | — | ✅ beta | — |
| Moroccan Darija | — | ⚠️ experimental | — |
| + 18 more (Spanish, German, Turkish, Chinese, …) | ✅ | ✅ | — |

- **Amazigh** uses NLLB-200 3.3B in Central Atlas Tamazight (`tzm_Tfng`), the base of standard Moroccan Amazigh, rendered with Noto Sans Tifinagh. It's the only local model that writes usable Tifinagh. Always proofread it.
- **Darija** uses Atlas-Chat 9B (MBZUAI-Paris). It sounds more natural than general models, but meaning can drift. Sentences whose numbers change are automatically redone with TranslateGemma.

## Features

- **Project library**: thumbnails, search, filters (in progress / translated / exported), sorting, rename, duplicate, delete. Drop a video anywhere in the app to start a project.
- **Accurate transcription**: Whisper large-v3 on the GPU, with accuracy-first decoding and word-level timing.
- **Two translation qualities**: *Best* (Google TranslateGemma 12B) and *Fast* (Meta NLLB-200 600M). Whole sentences are translated together, then split back across the captions at natural pauses.
- **Caption editor**: edit any line, split at the cursor, merge, delete, add captions at the playhead, set start/end timing (`[` `]`), find & replace, undo/redo.
- **Themes collection**: 16 built-in caption looks in 5 categories (Essentials, Social & Reels, Cinema, Arabic calligraphy, Bold). Save your own, and set a default theme for new projects.
- **Full styling**: 13 fonts (8 with Arabic), size, bold, uppercase, colors, box / outline / shadow, position, bilingual two-line captions.
- **What you see is what you export**: the preview uses the same font files and the same sizing maths as the renderer.
- **Export**: MP4 with burned-in captions at the original size, 1080p or 720p, plus `.srt` / WebVTT files. You can cancel a running job, and the app shows the time remaining.
- **Interface** in English, French and Arabic, dark / light / system appearance, keyboard shortcuts (`?`).

## Install (Windows)

Requirements: Windows 10/11 and an NVIDIA GPU with 8 GB of VRAM recommended (it also runs on the CPU, but slowly). Plan for ~25 GB of disk space for the models.

```powershell
git clone https://github.com/yfnidam72/COSCRIBE.git
cd COSCRIBE
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` installs uv + Python 3.11, the Python packages, FFmpeg and Ollama, downloads TranslateGemma, and creates Desktop and Start menu shortcuts. The other models download the first time they're needed:

| Model | Used for | Size |
|---|---|---|
| Whisper large-v3 (faster-whisper) | speech → text | ~3 GB |
| TranslateGemma 12B (Ollama) | translation, *Best* | ~8 GB |
| NLLB-200 600M (CTranslate2) | translation, *Fast* | ~0.6 GB |
| NLLB-200 3.3B (CTranslate2) | Amazigh | ~3.3 GB |
| Atlas-Chat 9B Q4_K_M (Ollama) | Moroccan Darija | ~5.8 GB |

## How it works

```
Coscribe.pyw        desktop window (pywebview / WebView2) + local server on 127.0.0.1:47821
app/server.py       FastAPI: projects, jobs (one GPU worker, cancellable), themes, settings, media
app/engine.py       probe, Whisper, caption building, NLLB, ASS/SRT/VTT, FFmpeg export
app/translators.py  TranslateGemma + Atlas-Chat via Ollama, output clean-up and number checks
app/static/         the interface (vanilla JS): library, editor, themes, i18n (en/fr/ar)
app/fonts/          caption fonts (SIL Open Font License)
```

Hard-won details:
- Arabic needs FFmpeg's `ass` filter with `shaping=complex` (HarfBuzz). The `subtitles` filter drops Arabic final letter forms.
- libass can't select bold from a variable font, so the fonts ship as static Regular/Bold files.
- libass sizes text by `(winAscent+winDescent)/unitsPerEm`, CSS by the em square. `engine.font_ratio` converts between them, so preview = export.
- On Windows, CTranslate2 needs cuBLAS/cuDNN preloaded by absolute path (`app/gpu.py`).
- NLLB has no `zgh_Tfng` token (it silently outputs `⁇`). `tzm_Tfng` is the working code for Tifinagh.

Run the server alone for development:

```powershell
.venv\Scripts\python -m uvicorn --app-dir app server:app --port 47822
```

Your data stays local: projects in `data/projects`, exports in `Videos\Coscribe`.

## Credits

Built by **Youssef Nidam** (MARIA · LA BASE, Casablanca), with Claude.
Models: OpenAI Whisper, Google TranslateGemma, Meta NLLB-200, MBZUAI-Paris Atlas-Chat. Fonts: Google Fonts (OFL).
