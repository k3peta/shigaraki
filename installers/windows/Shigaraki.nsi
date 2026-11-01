; -*- coding: utf-8 -*-
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "installer-copy.nsh"
!define APP_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ShigarakiHaasPreview"
Name "${NAME}"
OutFile "${OUTPUT_EXE}"
InstallDir "$LOCALAPPDATA\Programs\ShigarakiHaasPreview"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=1041 "ProductName" "${NAME}"
VIAddVersionKey /LANG=1041 "FileDescription" "${SETUP_TITLE}"
VIAddVersionKey /LANG=1041 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1041 "LegalCopyright" "Shigaraki contributors"
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_WELCOMEPAGE_TITLE "${WELCOME_TITLE}"
!define MUI_WELCOMEPAGE_TEXT "${WELCOME_TEXT}"
!define MUI_FINISHPAGE_TITLE "${FINISH_TITLE}"
!define MUI_FINISHPAGE_TEXT "${FINISH_TEXT}"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "${PREPARE_LABEL}"
!define MUI_FINISHPAGE_RUN_FUNCTION PrepareModels
!define MUI_UNCONFIRMPAGE_TEXT_TOP "${UNINSTALL_TEXT}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Japanese"

!macro RefuseLockedFile path
    ${If} ${FileExists} "${path}"
        System::Call 'kernel32::CreateFileW(w "${path}", i 0x80, i 0, p 0, i 3, i 0, p 0) p.r0'
        ${If} $0 P= -1
            MessageBox MB_OK|MB_ICONINFORMATION "${CLOSE_FIRST}"
            Abort
        ${Else}
            System::Call 'kernel32::CloseHandle(p r0)'
        ${EndIf}
    ${EndIf}
!macroend

!macro RefuseWhileRunning
    System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\ShigarakiHaasPreview.Desktop.Running") p.r0'
    ${If} $0 P<> 0
        System::Call 'kernel32::CloseHandle(p r0)'
        MessageBox MB_OK|MB_ICONINFORMATION "${CLOSE_FIRST}"
        Abort
    ${EndIf}
    ; Older cmd-based releases do not hold our mutex. A loaded Python DLL must
    ; not be overwritten or removed either; do not terminate another process.
    !insertmacro RefuseLockedFile "$INSTDIR\runtime\python314.dll"
    !insertmacro RefuseLockedFile "$INSTDIR\信楽.exe"
    !insertmacro RefuseLockedFile "$INSTDIR\信楽 Haasテスト.exe"
!macroend

Function .onInit
    SetShellVarContext current
    SetRegView 64
    ; Fixed per-user destination: /D cannot redirect file deletion or installation.
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\ShigarakiHaasPreview"
    !insertmacro RefuseWhileRunning
FunctionEnd

Function PrepareModels
    Exec '"$INSTDIR\信楽.exe" --prepare'
FunctionEnd

Section "ShigarakiHaasPreview"
    SetOutPath "$INSTDIR"
    SetOverwrite on
    AllowSkipFiles off
    ClearErrors
    !include "payload-install.nsh"
    ${If} ${Errors}
        Abort
    ${EndIf}
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    ; Remove only the earlier launcher in this same fixed Haas installation.
    ; Keep it until the new payload has been copied successfully. Refuse instead
    ; of scheduling removal or terminating a running process.
    ${If} ${FileExists} "$INSTDIR\信楽 Haasテスト.exe"
        ClearErrors
        Delete "$INSTDIR\信楽 Haasテスト.exe"
        ${If} ${Errors}
            MessageBox MB_OK|MB_ICONINFORMATION "${CLOSE_FIRST}"
            Abort
        ${EndIf}
    ${EndIf}
    ; Shared Python/COM code checks shortcut ownership, preserving other apps.
    nsExec::ExecToStack /TIMEOUT=15000 '"$INSTDIR\runtime\pythonw.exe" "$INSTDIR\install.py" --shortcut "$INSTDIR"'
    Pop $0
    Pop $1
    ${If} $0 == "2"
        MessageBox MB_OK|MB_ICONINFORMATION "${SHORTCUT_COLLISION}"
    ${ElseIf} $0 != "0"
        MessageBox MB_OK|MB_ICONEXCLAMATION "${SHORTCUT_FAILED}"
    ${EndIf}
    WriteRegStr HKCU "${APP_KEY}" "DisplayName" "${NAME}"
    WriteRegStr HKCU "${APP_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${APP_KEY}" "Publisher" "Shigaraki contributors"
    WriteRegStr HKCU "${APP_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${APP_KEY}" "DisplayIcon" "$INSTDIR\信楽.exe,0"
    WriteRegStr HKCU "${APP_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegDWORD HKCU "${APP_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${APP_KEY}" "NoRepair" 1
    WriteRegDWORD HKCU "${APP_KEY}" "EstimatedSize" ${INSTALL_KB}
SectionEnd

Function un.onInit
    SetShellVarContext current
    SetRegView 64
    ; Never recursively remove an arbitrary directory if this exe has been moved.
    ${If} $INSTDIR != "$LOCALAPPDATA\Programs\ShigarakiHaasPreview"
        MessageBox MB_OK|MB_ICONSTOP "${UNINSTALL_LOCATION}"
        Abort
    ${EndIf}
    !insertmacro RefuseWhileRunning
FunctionEnd

!macro DeleteOwned path
    ${If} ${FileExists} "${path}"
        ClearErrors
        Delete "${path}"
        ${If} ${Errors}
            StrCpy $R9 1
        ${EndIf}
    ${EndIf}
!macroend

Section "Uninstall"
    SetOutPath "$TEMP"
    ; Resolve ownership while the embedded runtime is still present. If it is
    ; broken or blocked, leave links alone and continue exact-path app removal.
    nsExec::ExecToStack /TIMEOUT=15000 '"$INSTDIR\runtime\pythonw.exe" "$INSTDIR\install.py" --remove-shortcuts "$INSTDIR"'
    Pop $0
    Pop $1
    ${If} $0 != "0"
        MessageBox MB_OK|MB_ICONEXCLAMATION "${SHORTCUT_CLEANUP_FAILED}"
    ${EndIf}
    StrCpy $R9 0
    ; Generated exact paths only. User data outside Programs is intentionally retained.
    !include "payload-uninstall.nsh"
    ${If} $R9 == 1
        MessageBox MB_OK|MB_ICONEXCLAMATION "${UNINSTALL_FAILED}"
        SetErrorLevel 1
        Quit
    ${EndIf}
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
    DeleteRegKey HKCU "${APP_KEY}"
SectionEnd
