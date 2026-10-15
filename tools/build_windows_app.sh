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
  --collect-all opencc \
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
  numpy yaml typing_extensions platformdirs packaging opencc numpy.libs av.libs; do
  if [[ -e "$RUNTIME_SITE/$package" ]]; then
    cp -a "$RUNTIME_SITE/$package" "$RUNTIME_TARGET/"
  fi
done

# Fail the build if OpenCC is missing from the frozen analysis or from the
# runtime package tree. Validate the actual copied bundle, not only site-packages.
if grep -q "missing module named opencc" "$ROOT/build-win/LyraSub/warn-LyraSub.txt"; then
  echo "ERROR: PyInstaller reports the OpenCC module as missing." >&2
  exit 1
fi
wine "$WIN_PYTHON" -c "import sys; sys.path.insert(0, r'${WIN_DIST}\\LyraSub\\_internal'); from opencc import OpenCC; result = OpenCC('t2s').convert('繁體中文測試'); assert result == '繁体中文测试', result; print('Bundled OpenCC verification passed:', result)"

cat > "$ROOT/dist/LyraSub/release-manifest.json" <<'JSON'
{
  "product": "LyraSub",
  "display_name": "弦字幕 Plus（LyraSub Plus）",
  "version": "v261015A",
  "build_revision": 2,
  "baseline": false,
  "release_note": "LyraSub Plus：新增 WAV 音频导入与本地识别支持；修复 Windows 安装包遗漏 OpenCC 简繁转换组件。",
  "update_channel": "stable",
  "models_location": "%LOCALAPPDATA%\\LyraSub\\models"
}
JSON

echo "Windows application directory built at: $ROOT/dist/LyraSub"
