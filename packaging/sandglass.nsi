Unicode True
RequestExecutionLevel user
ManifestDPIAware true
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!ifndef APPVERSION
  !define APPVERSION "0.1.0"
!endif
!ifndef SOURCEDIR
  !define SOURCEDIR "..\dist\Sandglass"
!endif
!ifndef ARTIFACTDIR
  !define ARTIFACTDIR "..\dist"
!endif
!ifndef ARTIFACTNAME
  !define ARTIFACTNAME "Sandglass-${APPVERSION}-windows-x64-unsigned-setup"
!endif
!ifdef EXPORT_UNINST
  !ifndef UNINSTOUT
    !error "EXPORT_UNINST requires UNINSTOUT"
  !endif
  !uninstfinalize 'cmd /C copy /Y "%1" "${UNINSTOUT}" >nul'
!endif
!ifdef IMPORT_UNINST
  !ifndef SIGNEDUNINST
    !error "IMPORT_UNINST requires SIGNEDUNINST"
  !endif
!endif

Name "Sandglass"
OutFile "${ARTIFACTDIR}\${ARTIFACTNAME}.exe"
InstallDir "$LOCALAPPDATA\Programs\Sandglass"
InstallDirRegKey HKCU "Software\Sandglass" "InstallDir"
Icon "..\sandglass\web\assets\orb.ico"
UninstallIcon "..\sandglass\web\assets\orb.ico"
BrandingText "Sandglass"

VIProductVersion "${APPVERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "Sandglass"
VIAddVersionKey /LANG=1033 "FileDescription" "Sandglass installer"
VIAddVersionKey /LANG=1033 "FileVersion" "${APPVERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${APPVERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright (c) 2026 Ayun"

!define MUI_ABORTWARNING
!define MUI_ICON "..\sandglass\web\assets\orb.ico"
!define MUI_UNICON "..\sandglass\web\assets\orb.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\Sandglass.exe"
!insertmacro MUI_PAGE_FINISH
!ifndef IMPORT_UNINST
  !insertmacro MUI_UNPAGE_CONFIRM
  !insertmacro MUI_UNPAGE_INSTFILES
!endif

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "TradChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Sandglass currently requires 64-bit Windows."
    Abort
  ${EndIf}
FunctionEnd

; Push "free", "held" or "unknown" for the observer's single-instance mutex.
; Unknown is not free: if Windows cannot distinguish absence from an error, the
; installer must not conclude the files are safe to overwrite.
Function CheckSandglassMutex
  ; ? e makes the System plugin capture GetLastError the instant the call
  ; returns. A separate System::Call to GetLastError does not work: the plugin
  ; makes its own Win32 calls in between and the thread's last error is gone by
  ; then. That branch had never run -- the desktop mutex was always held when
  ; this was reached, so the not-found path was first exercised tonight, and it
  ; answered "unknown" for a mutex that was plainly free.
  System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\Sandglass.Observer.SingleInstance") p .r3 ? e'
  Pop $4
  ${If} $3 != 0
    System::Call 'kernel32::CloseHandle(p r3)'
    Push "held"
  ${Else}
    ${If} $4 == 2
      Push "free"
    ${Else}
      Push "unknown"
    ${EndIf}
  ${EndIf}
FunctionEnd

Section "Sandglass" SecMain
  SetShellVarContext current

  ; The observer is a second detached copy of Sandglass.exe. It holds the files
  ; about to be replaced, and File /r over a locked target leaves whichever
  ; pieces could be written next to the ones that could not -- an install the
  ; user believes happened. Ask the previous copy to stop, then prove the
  ; program file is actually free before writing anything.
  ReadRegStr $R0 HKCU "Software\Sandglass" "InstallDir"
  ${If} $R0 != ""
    ${AndIf} ${FileExists} "$R0\Sandglass.exe"
    ; --stop now waits for the observer to release the program files and reports
    ; whether it did. It used to signal and return 0 regardless, so this waited a
    ; fixed two seconds instead -- for an exit bounded by a fifteen-second vendor
    ; request per provider -- and then queried the desktop mutex, which says
    ; nothing about the detached observer the comment above names as the holder.
    ExecWait '"$R0\Sandglass.exe" --stop' $0
    ${If} $0 != 0
      ${If} ${Silent}
        SetErrorLevel 2
      ${Else}
        MessageBox MB_ICONSTOP "Sandglass could not confirm that its background observer stopped. Quit Sandglass from the tray icon and run this installer again."
      ${EndIf}
      Abort
    ${EndIf}

    ; Ask Windows anyway. --stop reporting success is the action; this is the
    ; gate, and a gate that trusts the action it guards is not a gate.
    Call CheckSandglassMutex
    Pop $R1
    ${If} $R1 == "held"
      ${If} ${Silent}
        SetErrorLevel 2
      ${Else}
        MessageBox MB_ICONSTOP "Sandglass's background observer is still running. Quit Sandglass from the tray icon and run this installer again."
      ${EndIf}
      Abort
    ${ElseIf} $R1 != "free"
      ${If} ${Silent}
        SetErrorLevel 3
      ${Else}
        MessageBox MB_ICONSTOP "Windows could not verify whether Sandglass's background observer is running. The installer will not replace it."
      ${EndIf}
      Abort
    ${EndIf}

    ; A PyInstaller executable may be renameable while its process is still
    ; alive, so file rename is not proof that the desktop has exited. The
    ; desktop mutex is the runtime authority for that fact. Query it directly
    ; and fail closed if Windows cannot distinguish absence from an error.
    System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\Sandglass.Desktop.SingleInstance") p .r1 ? e'
    Pop $2
    ${If} $1 != 0
      System::Call 'kernel32::CloseHandle(p r1)'
      ${If} ${Silent}
        SetErrorLevel 2
      ${Else}
        MessageBox MB_ICONSTOP "Sandglass is still running. Close it from the tray icon and run this installer again."
      ${EndIf}
      Abort
    ${Else}
      ${If} $2 != 2
        ${If} ${Silent}
          SetErrorLevel 3
        ${Else}
          MessageBox MB_ICONSTOP "Windows could not verify whether Sandglass is still running. The installer will not replace it."
        ${EndIf}
        Abort
      ${EndIf}
    ${EndIf}

    ClearErrors
    Rename "$R0\Sandglass.exe" "$R0\Sandglass.exe.replacing"
    ${If} ${Errors}
      ${If} ${Silent}
        ; A self-update reaches here: the panel asked for it and has already
        ; quit. /S does not suppress MessageBox, so a modal here would be a
        ; detached installer waiting on a dialog nobody is looking for. This
        ; aborts before File /r, so the old copy is untouched -- start it again
        ; rather than leaving the user with no program at all.
        Exec '"$R0\Sandglass.exe"'
        SetErrorLevel 2
      ${Else}
        MessageBox MB_ICONSTOP "Sandglass is still running. Close it from the tray icon and run this installer again."
      ${EndIf}
      Abort
    ${EndIf}
    Rename "$R0\Sandglass.exe.replacing" "$R0\Sandglass.exe"
  ${EndIf}

  SetOutPath "$INSTDIR"
  File /r "${SOURCEDIR}\*"

  CreateDirectory "$SMPROGRAMS\Sandglass"
  CreateShortcut "$SMPROGRAMS\Sandglass\Sandglass.lnk" "$INSTDIR\Sandglass.exe" "" "$INSTDIR\Sandglass.exe"
!ifdef IMPORT_UNINST
  File /oname=Uninstall.exe "${SIGNEDUNINST}"
!else
  WriteUninstaller "$INSTDIR\Uninstall.exe"
!endif
  WriteRegStr HKCU "Software\Sandglass" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayName" "Sandglass"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayIcon" "$INSTDIR\Sandglass.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoRepair" 1

  ; MUI_FINISHPAGE_RUN is a checkbox on a page /S never draws. Without this
  ; the in-app update installs correctly and ends with no Sandglass running:
  ; the user clicks update, the panel disappears, and nothing comes back.
  ${If} ${Silent}
    Exec '"$INSTDIR\Sandglass.exe"'
  ${EndIf}
SectionEnd

!ifndef IMPORT_UNINST
Function un.CheckSandglassMutex
  ; ? e makes the System plugin capture GetLastError the instant the call
  ; returns. A separate System::Call to GetLastError does not work: the plugin
  ; makes its own Win32 calls in between and the thread's last error is gone by
  ; then. That branch had never run -- the desktop mutex was always held when
  ; this was reached, so the not-found path was first exercised tonight, and it
  ; answered "unknown" for a mutex that was plainly free.
  System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\Sandglass.Observer.SingleInstance") p .r3 ? e'
  Pop $4
  ${If} $3 != 0
    System::Call 'kernel32::CloseHandle(p r3)'
    Push "held"
  ${Else}
    ${If} $4 == 2
      Push "free"
    ${Else}
      Push "unknown"
    ${EndIf}
  ${EndIf}
FunctionEnd

Section "Uninstall"
  SetShellVarContext current

  ; Stop observing before removing the program. The observer outlives the panel
  ; by design, so an uninstall that only deletes files leaves it running -- out
  ; of a half-deleted directory, still writing to the state directory this
  ; uninstaller deliberately preserves.
  ${If} ${FileExists} "$INSTDIR\Sandglass.exe"
    ; Same as the install path: --stop reports whether the observer let go,
    ; and the mutex is asked anyway. A fixed sleep cannot see an exit bounded
    ; by a fifteen-second vendor request per provider.
    ExecWait '"$INSTDIR\Sandglass.exe" --stop' $0
    ${If} $0 != 0
      SetErrorLevel 6
      MessageBox MB_ICONSTOP "Sandglass could not confirm that its background observer stopped. Quit Sandglass from the tray icon and run the uninstaller again."
      Abort
    ${EndIf}
    ; Distinct codes: an abort otherwise reports NSIS's generic 2 and the smoke
    ; cannot say which gate fired or why.
    Call un.CheckSandglassMutex
    Pop $R1
    ${If} $R1 == "held"
      SetErrorLevel 7
      MessageBox MB_ICONSTOP "Sandglass's background observer is still running. Quit Sandglass from the tray icon and run the uninstaller again."
      Abort
    ${ElseIf} $R1 != "free"
      SetErrorLevel 8
      MessageBox MB_ICONSTOP "Windows could not verify whether Sandglass's background observer is running. The uninstaller will not remove it."
      Abort
    ${EndIf}
    ; Stopping the observer is not enough on its own: a running panel supervises
    ; it and puts it back within the minute, which would land in the middle of
    ; this. The panel is Sandglass.exe, so renaming it proves nothing is left.
    ClearErrors
    Rename "$INSTDIR\Sandglass.exe" "$INSTDIR\Sandglass.exe.removing"
    ${If} ${Errors}
      MessageBox MB_ICONSTOP "Sandglass is still running. Close it from the tray icon and run the uninstaller again."
      Abort
    ${EndIf}
    Rename "$INSTDIR\Sandglass.exe.removing" "$INSTDIR\Sandglass.exe"
  ${EndIf}

  Delete "$SMPROGRAMS\Sandglass\Sandglass.lnk"
  RMDir "$SMPROGRAMS\Sandglass"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass"
  DeleteRegKey HKCU "Software\Sandglass"
  ; Remove only Sandglass's own opt-in login entry.  Leaving it behind would
  ; make Windows launch a deleted executable on every future sign-in.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass"
  ; Remove only known installed paths.  Do not recursively delete $INSTDIR:
  ; the user may have selected a pre-existing directory.  Sandglass state under
  ; %LOCALAPPDATA%\sandglass is also deliberately preserved.
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\Sandglass.exe"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\PRIVACY.md"
  Delete "$INSTDIR\SUPPORT.md"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.md"
  RMDir /r "$INSTDIR\THIRD_PARTY_LICENSES"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
!endif
