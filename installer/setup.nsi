Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"

!define PRODUCT_NAME "弦字幕 Plus（LyraSub Plus）"
!define PRODUCT_VERSION "v261015A"
!define PRODUCT_PUBLISHER "天琴工作室（Lyra Studio）"
!define PRODUCT_EXE "LyraSub.exe"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\release\LyraSub-Plus-setup.exe"
InstallDir "$LOCALAPPDATA\LyraSub"
InstallDirRegKey HKCU "Software\LyraStudio\LyraSub" "InstallDir"
ShowInstDetails show
ShowUninstDetails show

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "安装弦字幕 Plus（LyraSub Plus）" SEC_MAIN
  SetOutPath "$INSTDIR"
  File /r "..\dist\LyraSub\*.*"

  ; Plus deliberately reuses the standard edition's install directory and
  ; registry location. Remove only old shortcuts; user models/configuration
  ; stay under the same current-user data locations.
  Delete "$SMPROGRAMS\LyraSub\弦字幕（LyraSub）.lnk"
  Delete "$DESKTOP\弦字幕（LyraSub）.lnk"

  WriteRegStr HKCU "Software\LyraStudio\LyraSub" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\LyraStudio\LyraSub" "Version" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LyraSub" "DisplayName" "${PRODUCT_NAME} ${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LyraSub" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LyraSub" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LyraSub" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LyraSub" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LyraSub" "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\LyraSub"
  CreateShortcut "$SMPROGRAMS\LyraSub\弦字幕 Plus（LyraSub Plus）.lnk" "$INSTDIR\${PRODUCT_EXE}"
  CreateShortcut "$DESKTOP\弦字幕 Plus（LyraSub Plus）.lnk" "$INSTDIR\${PRODUCT_EXE}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\LyraSub\弦字幕 Plus（LyraSub Plus）.lnk"
  Delete "$SMPROGRAMS\LyraSub\弦字幕（LyraSub）.lnk"
  RMDir "$SMPROGRAMS\LyraSub"
  Delete "$DESKTOP\弦字幕 Plus（LyraSub Plus）.lnk"
  Delete "$DESKTOP\弦字幕（LyraSub）.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\LyraSub"
  DeleteRegKey HKCU "Software\LyraStudio\LyraSub"
  RMDir /r "$INSTDIR"
  ; Local ASR and alignment models live in the user's application-data directory.
  ; They are deliberately retained on uninstall so a reinstall/patch does not re-download them.
SectionEnd
