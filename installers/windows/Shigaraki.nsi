; -*- coding: utf-8 -*-
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "installer-copy.nsh"
!define APP_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ShigarakiHaasPreview"
Name "信楽 Haasテスト"
OutFile "${OUTPUT_EXE}"
InstallDir "$LOCALAPPDATA\Programs\ShigarakiHaasPreview"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=1041 "ProductName" "信楽 Haasテスト"
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

!macro RefuseWhileRunning
    System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\ShigarakiHaasPreview.Desktop.Running") p.r0'
    ${If} $0 P<> 0
        System::Call 'kernel32::CloseHandle(p r0)'
        MessageBox MB_OK|MB_ICONINFORMATION "${CLOSE_FIRST}"
        Abort
    ${EndIf}
    ; Older cmd-based releases do not hold our mutex. A loaded Python DLL must
    ; not be overwritten or removed either; do not terminate another process.
    ${If} ${FileExists} "$INSTDIR\runtime\python314.dll"
        System::Call 'kernel32::CreateFileW(w "$INSTDIR\runtime\python314.dll", i 0x80, i 0, p 0, i 3, i 0, p 0) p.r0'
        ${If} $0 P= -1
            MessageBox MB_OK|MB_ICONINFORMATION "${CLOSE_FIRST}"
            Abort
        ${Else}
            System::Call 'kernel32::CloseHandle(p r0)'
        ${EndIf}
    ${EndIf}
!macroend

Function .onInit
    SetShellVarContext current
    SetRegView 64
    ; Fixed per-user destination: /D cannot redirect file deletion or installation.
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\ShigarakiHaasPreview"
    !insertmacro RefuseWhileRunning
FunctionEnd

Function PrepareModels
    Exec '"$INSTDIR\信楽 Haasテスト.exe" --prepare'
FunctionEnd

Section "ShigarakiHaasPreview"
    SetOutPath "$INSTDIR"
    SetOverwrite on
    !include "payload-install.nsh"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    CreateShortcut "$SMPROGRAMS\信楽 Haasテスト.lnk" "$INSTDIR\信楽 Haasテスト.exe" "" "$INSTDIR\信楽 Haasテスト.exe" 0
    CreateShortcut "$DESKTOP\信楽 Haasテスト.lnk" "$INSTDIR\信楽 Haasテスト.exe" "" "$INSTDIR\信楽 Haasテスト.exe" 0
    WriteRegStr HKCU "${APP_KEY}" "DisplayName" "信楽 Haasテスト"
    WriteRegStr HKCU "${APP_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${APP_KEY}" "Publisher" "Shigaraki contributors"
    WriteRegStr HKCU "${APP_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${APP_KEY}" "DisplayIcon" "$INSTDIR\信楽 Haasテスト.exe,0"
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
    Delete "$SMPROGRAMS\信楽 Haasテスト.lnk"
    Delete "$DESKTOP\信楽 Haasテスト.lnk"
    DeleteRegKey HKCU "${APP_KEY}"
SectionEnd
