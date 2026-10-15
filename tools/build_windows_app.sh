#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINEPREFIX="${WINEPREFIX:-$HOME/.wine-lyrasub}"
WIN_PYTHON='C:\LyraPython312\python.exe'
WIN_ROOT="Z:${ROOT//\//\\}"
WIN_DIST="${WIN_ROOT}\\dist"
WIN_BUILD="${WIN_ROOT}\\build-win"
WIN_SPEC="${WIN_ROOT}\\build-spec"

export WINEPREFIX
export WINEDEBUG=-all
rm -rf "$ROOT/dist" "$ROOT/build-win" "$ROOT/build-spec"
mkdir -p "$ROOT/dist"

wine "$WIN_PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name LyraSub \
  --paths "${WIN_ROOT}\\src" \
  --distpath "$WIN_DIST" \
  --workpath "$WIN_BUILD" \
  --specpath "$WIN_SPEC" \
  --exclude-module faster_whisper \
  --exclude-module ctranslate2 \
  --exclude-module onnxruntime \
  --exclude-module av \
  --exclude-module tokenizers \
  --exclude-module huggingface_hub \
  --exclude-module numpy \
  "${WIN_ROOT}\\run_lyrasub.py"

# Keep the patch small by retaining the original manual ASR package layout, while
# adding the native runtime DLL folders that the first baseline omitted. Without
# numpy.libs / av.libs, Windows cannot load the package extension modules.
RUNTIME_SITE="$WINEPREFIX/drive_c/LyraPython312/Lib/site-packages"
RUNTIME_TARGET="$ROOT/dist/LyraSub/_internal"
for package in \
  faster_whisper ctranslate2 onnxruntime av tokenizers huggingface_hub \
  hf_xet filelock fsspec tqdm httpx httpcore anyio h11 click \
  numpy yaml typing_extensions platformdirs packaging numpy.libs av.libs; do
  if [[ -e "$RUNTIME_SITE/$package" ]]; then
    cp -a "$RUNTIME_SITE/$package" "$RUNTIME_TARGET/"
  fi
done

cat > "$ROOT/dist/LyraSub/release-manifest.json" <<'JSON'
{
  "product": "LyraSub",
  "display_name": "弦字幕 Plus（LyraSub Plus）",
  "version": "v261015A",
  "build_revision": 1,
  "baseline": false,
  "release_note": "LyraSub Plus：新增 WAV 音频导入、工程保存与本地识别支持。",
  "update_channel": "stable",
  "models_location": "%LOCALAPPDATA%\\LyraSub\\models"
}
JSON

echo "Windows application directory built at: $ROOT/dist/LyraSub"
