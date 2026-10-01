"""Download the fast model with LyraSub's direct downloader in the Windows build runtime."""

from __future__ import annotations

import shutil
from pathlib import Path

from lyrasub.model_manager import ModelManager


root = Path("C:/LyraDirectModelProbe")
shutil.rmtree(root, ignore_errors=True)
manager = ModelManager(root=root)


def report(message: str, progress: int | None) -> None:
    first_line = message.splitlines()[0]
    if progress in {0, 100}:
        print(f"PROGRESS {progress}: {first_line}")


path = manager.download_model("fast", "https://hf-mirror.com", status=report)
print(f"MODEL_PATH {path}")
print(f"MODEL_OK {manager.is_model_downloaded('fast')}")
print(f"MODEL_SIZE {manager.downloaded_size_bytes('fast')}")
