#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="$ROOT/.build-cache"
WINEPREFIX="${WINEPREFIX:-$HOME/.wine-lyrasub}"
PYTHON_VERSION="3.12.3"
PYTHON_INSTALLER="$CACHE_DIR/python-${PYTHON_VERSION}-amd64.exe"
WIN_PYTHON='C:\LyraPython312\python.exe'

mkdir -p "$CACHE_DIR"
export WINEPREFIX
export WINEDEBUG=-all

if [[ ! -f "$PYTHON_INSTALLER" ]]; then
  curl --fail --location --retry 3 \
    "https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-amd64.exe" \
    --output "$PYTHON_INSTALLER"
fi

if ! wine "$WIN_PYTHON" --version >/dev/null 2>&1; then
  wine "$PYTHON_INSTALLER" /quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 Include_test=0 \
    TargetDir='C:\LyraPython312'
fi

wine "$WIN_PYTHON" -m pip install --upgrade pip setuptools wheel
wine "$WIN_PYTHON" -m pip install \
  "PySide6>=6.8,<6.10" \
  "faster-whisper>=1.1,<2.0" \
  "av>=18,<19" \
  "cryptography>=43,<46" \
  "platformdirs>=4.3,<5.0" \
  "requests>=2.32,<3.0" \
  "opencc-python-reimplemented>=0.1.7,<0.2.0" \
  "pyinstaller>=6.12,<7.0" \
  "numpy==1.26.4"

echo "Windows build Python ready: $WIN_PYTHON"
