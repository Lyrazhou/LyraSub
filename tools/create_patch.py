"""Build a compact LyraSub NSIS patch from two application directories.

The resulting filename is always ``LyraSub-patch.exe``. It validates the installed
base version, stops LyraSub, copies only changed files, stores overwritten files
under ``.update-backup`` and updates the installed version registry value.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path

EXCLUDED_PREFIXES = {".update-backup"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_files(root: Path) -> dict[Path, str]:
    files: dict[Path, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PREFIXES for part in relative.parts):
            continue
        files[relative] = sha256(path)
    return files


def nsis_escape(value: str) -> str:
    return value.replace("$", "$$").replace('"', '$\\"')


def to_windows(path: Path) -> str:
    return str(path).replace("/", "\\")


def relative_windows(path: Path) -> str:
    return to_windows(path)


def render_file_operation(
    relative: Path, staged_path: Path, target_version: str, operation_index: int
) -> str:
    parent = relative.parent
    target_directory = "$INSTDIR" if str(parent) == "." else f"$INSTDIR\\{relative_windows(parent)}"
    backup_directory = (
        f"$INSTDIR\\.update-backup\\{target_version}"
        if str(parent) == "."
        else f"$INSTDIR\\.update-backup\\{target_version}\\{relative_windows(parent)}"
    )
    target_file = f"$INSTDIR\\{relative_windows(relative)}"
    staged = nsis_escape(to_windows(staged_path.resolve()))
    done_label = f"backup_done_{operation_index}"
    failed_label = f"backup_failed_{operation_index}"
    return "\n".join(
        [
            f'  CreateDirectory "{backup_directory}"',
            f'  IfFileExists "{target_file}" 0 {done_label}',
            f'  CopyFiles /SILENT "{target_file}" "{backup_directory}"',
            f"  IfErrors {failed_label} {done_label}",
            f"  {failed_label}:",
            '    MessageBox MB_ICONSTOP "无法备份现有文件，补丁已取消。" /SD IDOK',
            "    Abort",
            f"  {done_label}:",
            f'  CreateDirectory "{target_directory}"',
            f'  SetOutPath "{target_directory}"',
            f'  File "/oname={relative.name}" "{staged}"',
        ]
    )


def render_nsi(
    from_version: str,
    to_version: str,
    operations: list[str],
    output: Path,
) -> str:
    return f'''Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"

!define PRODUCT_NAME "弦字幕（LyraSub）"
!define FROM_VERSION "{nsis_escape(from_version)}"
!define TO_VERSION "{nsis_escape(to_version)}"

Name "${{PRODUCT_NAME}} 补丁 ${{TO_VERSION}}"
OutFile "{nsis_escape(to_windows(output.resolve()))}"
InstallDir "$LOCALAPPDATA\\LyraSub"
InstallDirRegKey HKCU "Software\\LyraStudio\\LyraSub" "InstallDir"
ShowInstDetails show

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ReadRegStr $0 HKCU "Software\\LyraStudio\\LyraSub" "InstallDir"
  ${{If}} $0 == ""
    MessageBox MB_ICONSTOP "未检测到弦字幕基准版。请先运行 LyraSub-setup.exe。" /SD IDOK
    Abort
  ${{EndIf}}
  StrCpy $INSTDIR $0
  ReadRegStr $1 HKCU "Software\\LyraStudio\\LyraSub" "Version"
  ${{If}} $1 != "${{FROM_VERSION}}"
    MessageBox MB_ICONSTOP "此补丁适用于 ${{FROM_VERSION}}，当前安装版本为：$1。为保护项目，补丁不会继续。" /SD IDOK
    Abort
  ${{EndIf}}
  IfFileExists "$INSTDIR\\LyraSub.exe" +3 0
    MessageBox MB_ICONSTOP "安装目录不完整，补丁不会继续。" /SD IDOK
    Abort
FunctionEnd

Section "更新弦字幕"
  nsExec::ExecToLog '"$SYSDIR\\taskkill.exe" /f /im LyraSub.exe'
  Sleep 600
  CreateDirectory "$INSTDIR\\.update-backup\\${{TO_VERSION}}"
  FileOpen $2 "$INSTDIR\\.update-backup\\${{TO_VERSION}}\\patch-info.txt" w
  FileWrite $2 "From: ${{FROM_VERSION}}$\\r$\\nTo: ${{TO_VERSION}}$\\r$\\n"
  FileClose $2
{chr(10).join(operations)}
  WriteRegStr HKCU "Software\\LyraStudio\\LyraSub" "Version" "${{TO_VERSION}}"
  MessageBox MB_ICONINFORMATION "弦字幕已更新至 ${{TO_VERSION}}。已替换的文件备份在 .update-backup 目录中。" /SD IDOK
SectionEnd
'''


def build_patch(
    base: Path, target: Path, from_version: str, to_version: str, output: Path
) -> list[Path]:
    base_files = collect_files(base)
    target_files = collect_files(target)
    changed = sorted(
        relative for relative, digest in target_files.items() if base_files.get(relative) != digest
    )
    if not changed:
        raise ValueError("目标目录与基准目录没有差异，无法生成空补丁。")

    work_root = output.parent / ".patch-build"
    stage = work_root / "payload"
    if work_root.exists():
        shutil.rmtree(work_root)
    stage.mkdir(parents=True)
    operations: list[str] = []
    for operation_index, relative in enumerate(changed, start=1):
        staged = stage / relative
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target / relative, staged)
        operations.append(render_file_operation(relative, staged, to_version, operation_index))

    script = work_root / "LyraSub-patch.nsi"
    script.write_text(render_nsi(from_version, to_version, operations, output), encoding="utf-8")
    subprocess.run(["makensis", str(script)], check=True)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 LyraSub 差分补丁。")
    parser.add_argument("--base", required=True, type=Path, help="已发布基准应用目录")
    parser.add_argument("--target", required=True, type=Path, help="新版本应用目录")
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("release") / "LyraSub-patch.exe",
        help="固定补丁输出路径",
    )
    arguments = parser.parse_args()
    changed = build_patch(
        arguments.base.resolve(),
        arguments.target.resolve(),
        arguments.from_version,
        arguments.to_version,
        arguments.output.resolve(),
    )
    print(f"已生成 {arguments.output.resolve()}，包含 {len(changed)} 个变更文件。")
    for relative in changed:
        print(relative)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
