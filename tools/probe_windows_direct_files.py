"""Probe direct model-file URLs through requests in the Windows runtime."""

from __future__ import annotations

import sys

import requests


if len(sys.argv) != 2:
    raise SystemExit("usage: probe_windows_direct_files.py <endpoint>")

endpoint = sys.argv[1].rstrip("/")
base = f"{endpoint}/Systran/faster-whisper-base/resolve/main"
for filename in ("config.json", "model.bin"):
    url = f"{base}/{filename}"
    print(f"GET {url}")
    with requests.get(url, stream=True, timeout=(15, 30), allow_redirects=True) as response:
        print(f"STATUS {response.status_code} FINAL {response.url}")
        response.raise_for_status()
        first = next(response.iter_content(chunk_size=1024), b"")
        print(f"FIRST_CHUNK {len(first)} CONTENT_LENGTH {response.headers.get('content-length')}")
