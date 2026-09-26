"""GPU runtime plumbing.

CTranslate2's Windows wheel does not bundle cuBLAS or cuDNN; it expects them on
the DLL search path. We install them as pip packages (nvidia-cublas-cu12,
nvidia-cudnn-cu12), which puts the DLLs under site-packages/nvidia/*/bin where
Windows will not find them on its own. Python 3.8+ ignores PATH for extension
module dependencies, so the directories have to be registered explicitly.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("pipeline.gpu")

_done = False


def ensure_cuda_dlls() -> None:
    """Idempotent. Must run before anything imports ctranslate2."""
    global _done
    if _done or sys.platform != "win32":
        _done = True
        return

    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    added = []
    for bin_dir in sorted(nvidia_root.glob("*/bin")):
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))
            added.append(bin_dir.parent.name)

    # add_dll_directory alone is not enough: CTranslate2 loads cuBLAS/cuDNN
    # lazily by bare name, and that lookup does not consult the added dirs.
    # Loading each DLL by absolute path puts it in the process module table,
    # so the later load-by-name resolves to the already-loaded copy.
    _preload(nvidia_root)

    if added:
        log.info("registered CUDA dll dirs: %s", ", ".join(added))
    else:
        log.warning(
            "no nvidia/*/bin directories under %s - GPU inference will fail. "
            "run: uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12",
            nvidia_root,
        )
    _done = True


def _preload(nvidia_root: Path) -> None:
    """Load every CUDA DLL by absolute path, retrying to satisfy inter-dependencies."""
    import ctypes

    pending = sorted(nvidia_root.glob("*/bin/*.dll"))
    loaded: set[Path] = set()
    # Each pass can only load DLLs whose dependencies are already in. A handful
    # of passes is plenty for cublas -> cudnn; the loop stops when it stalls.
    while pending:
        progressed = False
        still: list[Path] = []
        for dll in pending:
            try:
                ctypes.WinDLL(str(dll))
                loaded.add(dll)
                progressed = True
            except OSError:
                still.append(dll)
        pending = still
        if not progressed:
            break

    log.info("preloaded %d CUDA dll(s)", len(loaded))
    if pending:
        log.debug("could not preload: %s", ", ".join(d.name for d in pending))


def vram_used_mb() -> float | None:
    """Current GPU memory use via nvidia-smi, or None if unavailable."""
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return float(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        pass
    return None
