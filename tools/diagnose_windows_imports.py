"""Probe imported runtime modules in the Wine Windows build environment."""

from __future__ import annotations

import importlib

MODULES = [
    "PySide6",
    "requests",
    "cryptography",
    "numpy",
    "av",
    "ctranslate2",
    "onnxruntime",
    "faster_whisper",
    "PyInstaller",
]

for module_name in MODULES:
    print(f"IMPORT {module_name}", flush=True)
    importlib.import_module(module_name)
    print(f"OK {module_name}", flush=True)
