"""Probe copied runtime modules exactly as the installed application will load them."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


if len(sys.argv) != 2:
    raise SystemExit("usage: probe_frozen_runtime.py <LyraSub _internal directory>")

runtime_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(runtime_root))
print(f"runtime root: {runtime_root}")
for name in ("numpy", "ctranslate2", "onnxruntime", "av", "faster_whisper"):
    try:
        module = importlib.import_module(name)
        print(f"OK {name}: {getattr(module, '__file__', '<built-in>')}")
    except Exception as exc:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        raise
