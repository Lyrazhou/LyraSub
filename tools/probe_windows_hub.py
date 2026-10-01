"""Diagnostic probe for Hub endpoints from the same Windows Python used to build LyraSub."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import requests
from huggingface_hub import snapshot_download


if len(sys.argv) != 2:
    raise SystemExit("usage: probe_windows_hub.py <endpoint>")

endpoint = sys.argv[1].rstrip("/")
repo_id = "Systran/faster-whisper-base"
api_url = f"{endpoint}/api/models/{repo_id}"
print(f"API {api_url}")
response = requests.get(api_url, timeout=20)
print(f"API_STATUS {response.status_code}")
response.raise_for_status()

output = Path("C:/LyraHubProbe")
shutil.rmtree(output, ignore_errors=True)
print(f"SNAPSHOT {endpoint}")
result = snapshot_download(
    repo_id=repo_id,
    local_dir=output,
    allow_patterns=["config.json"],
    endpoint=endpoint,
    etag_timeout=20,
    max_workers=1,
)
print(f"SNAPSHOT_OK {result}")
print(f"CONFIG_EXISTS {(output / 'config.json').is_file()}")
