Unicode True
RequestExecutionLevel user
ManifestDPIAware true
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
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

Var UpdateMode
Var UpdateParentPid
Var UpdateBackup
Var UpdateStage
Var UpdateRestartExe
Var UpdatePhase
Var UpdateFailureLog

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
!define MUI_PAGE_CUSTOMFUNCTION_PRE UpdateSkipPage
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE UpdateSkipPage
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!define MUI_PAGE_CUSTOMFUNCTION_PRE UpdateSkipPage
!insertmacro MUI_PAGE_DIRECTORY
!define MUI_PAGE_CUSTOMFUNCTION_SHOW UpdateInstFilesShow
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\Sandglass.exe"
!define MUI_PAGE_CUSTOMFUNCTION_PRE UpdateSkipPage
!insertmacro MUI_PAGE_FINISH
!ifndef IMPORT_UNINST
  !insertmacro MUI_UNPAGE_CONFIRM
  !insertmacro MUI_UNPAGE_INSTFILES
!endif

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "TradChinese"

Function .onInit
  StrCpy $UpdateMode 0
  ${GetParameters} $R9
  ClearErrors
  ${GetOptions} "$R9" "/UPDATE" $R8
  ${IfNot} ${Errors}
    StrCpy $UpdateMode 1
  ${EndIf}
  ClearErrors
  ${GetOptions} "$R9" "/PARENTPID=" $UpdateParentPid
  ClearErrors
  ${GetOptions} "$R9" "/RESTARTEXE=" $UpdateRestartExe
  ${If} $UpdateMode == 1
    ; Canonicalize the PID before it becomes part of the staging path. Besides
    ; rejecting a broken protocol, this prevents command-line path injection
    ; into the only recursive cleanup used before installation.
    IntOp $R7 $UpdateParentPid + 0
    ${If} $R7 <= 0
      SetErrorLevel 20
      Abort
    ${EndIf}
    StrCpy $UpdateParentPid $R7
    StrCpy $UpdateFailureLog "$TEMP\Sandglass-update-$UpdateParentPid.failure.txt"
    Delete "$UpdateFailureLog"
  ${EndIf}
  ${IfNot} ${RunningX64}
    ${If} $UpdateMode == 1
      SetErrorLevel 21
    ${Else}
      MessageBox MB_ICONSTOP "Sandglass currently requires 64-bit Windows."
    ${EndIf}
    Abort
  ${EndIf}
FunctionEnd

Function UpdateSkipPage
  ${If} $UpdateMode == 1
    Abort
  ${EndIf}
FunctionEnd

Function UpdateInstFilesShow
  ${If} $UpdateMode == 1
    ; The update is unattended after the user confirmed it in Sandglass. A
    ; Cancel button here would leave the product stopped and the old directory
    ; potentially renamed, so make the progress page non-cancellable.
    GetDlgItem $0 $HWNDPARENT 2
    EnableWindow $0 0
    ; The InstFiles page is the whole update UI. Close it automatically when
    ; the section finishes instead of leaving a completed progress window next
    ; to the relaunched application.
    SetAutoClose true
    System::Call 'user32::GetSystemMenu(p $HWNDPARENT, i 0) p .r1'
    ${If} $1 != 0
      System::Call 'user32::EnableMenuItem(p r1, i 0xF060, i 0x1)'
    ${EndIf}
  ${EndIf}
FunctionEnd

Function WaitForUpdateParent
  ${If} $UpdateMode != 1
    Return
  ${EndIf}
  ${If} $UpdateParentPid == ""
    StrCpy $UpdatePhase "parent-pid"
    Call UpdateFailure
  ${EndIf}
  ; A PID alone is not a synchronization primitive. Open the actual process
  ; handle, then wait for the process that launched this updater to exit.
  System::Call 'kernel32::OpenProcess(i 0x00100000, i 0, i $UpdateParentPid) p .r0 ? e'
  Pop $2
  ${If} $0 == 0
    ; ERROR_INVALID_PARAMETER means the parent already exited between launch
    ; and OpenProcess. That is the state the wait was meant to establish.
    ${If} $2 == 87
      Return
    ${EndIf}
    StrCpy $UpdatePhase "open-parent"
    Call UpdateFailure
  ${EndIf}
  System::Call 'kernel32::WaitForSingleObject(p r0, i 0xFFFFFFFF) i .r1'
  System::Call 'kernel32::CloseHandle(p r0)'
  ${If} $1 != 0
    StrCpy $UpdatePhase "wait-parent"
    Call UpdateFailure
  ${EndIf}
FunctionEnd

Function UpdateFailure
  ; Update mode has no attended user to dismiss a modal dialog. Restore the
  ; complete old directory when it was moved aside, then start that old copy.
  ${If} $UpdateMode == 1
    SetOutPath "$TEMP"
    FileOpen $4 "$UpdateFailureLog" w
    FileWrite $4 "$UpdatePhase$\r$\n"
    FileClose $4
    ${If} $UpdateStage != ""
      RMDir /r "$UpdateStage"
    ${EndIf}
    ${If} $UpdateBackup != ""
      ${If} ${FileExists} "$UpdateBackup\Sandglass.exe"
        Rename "$UpdateBackup" "$INSTDIR"
      ${EndIf}
      ${If} ${FileExists} "$INSTDIR\Sandglass.exe"
        Exec '"$INSTDIR\Sandglass.exe"'
      ${ElseIf} ${FileExists} "$UpdateBackup\Sandglass.exe"
        Exec '"$UpdateBackup\Sandglass.exe"'
      ${EndIf}
    ${ElseIf} $UpdateRestartExe != ""
      ${If} ${FileExists} "$UpdateRestartExe"
        ; Portable-to-installed update failure: restart the original portable
        ; executable. Never mistake a partly extracted target for the old app.
        Exec '"$UpdateRestartExe"'
      ${EndIf}
    ${EndIf}
    SetErrorLevel 20
    Quit
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

  Call WaitForUpdateParent

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
      StrCpy $UpdatePhase "stop-observer"
      ${If} $UpdateMode == 1
        Call UpdateFailure
      ${ElseIf} ${Silent}
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
      StrCpy $UpdatePhase "observer-mutex-held"
      ${If} $UpdateMode == 1
        Call UpdateFailure
      ${ElseIf} ${Silent}
        SetErrorLevel 2
      ${Else}
        MessageBox MB_ICONSTOP "Sandglass's background observer is still running. Quit Sandglass from the tray icon and run this installer again."
      ${EndIf}
      Abort
    ${ElseIf} $R1 != "free"
      StrCpy $UpdatePhase "observer-mutex-unknown"
      ${If} $UpdateMode == 1
        Call UpdateFailure
      ${ElseIf} ${Silent}
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
      StrCpy $UpdatePhase "desktop-mutex-held"
      ${If} $UpdateMode == 1
        Call UpdateFailure
      ${ElseIf} ${Silent}
        SetErrorLevel 2
      ${Else}
        MessageBox MB_ICONSTOP "Sandglass is still running. Close it from the tray icon and run this installer again."
      ${EndIf}
      Abort
    ${Else}
      ${If} $2 != 2
        StrCpy $UpdatePhase "desktop-mutex-unknown"
        ${If} $UpdateMode == 1
          Call UpdateFailure
        ${ElseIf} ${Silent}
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
      StrCpy $UpdatePhase "executable-locked"
      ${If} $UpdateMode == 1
        Call UpdateFailure
      ${ElseIf} ${Silent}
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

  ${If} $UpdateMode == 1
    ; Extract away from both the running source and the install target. This
    ; makes a portable-to-installed update safe and keeps a failed extraction
    ; from leaving a half-new installed directory.
    StrCpy $UpdateStage "$TEMP\Sandglass-update-$UpdateParentPid"
    ${If} ${FileExists} "$UpdateStage\*.*"
      StrCpy $UpdatePhase "staging-exists"
      Call UpdateFailure
    ${EndIf}
    CreateDirectory "$UpdateStage"
    SetOutPath "$UpdateStage"
  ${Else}
    SetOutPath "$INSTDIR"
  ${EndIf}
  File /r "${SOURCEDIR}\*"

  ${If} ${Errors}
    StrCpy $UpdatePhase "extract-stage"
    Call UpdateFailure
  ${EndIf}

  ${If} $UpdateMode == 1
    ; Only an actual prior Sandglass install is moved aside. An empty registry
    ; value is the normal portable-to-installed path and is never a delete or
    ; rename target.
    ${If} $R0 != ""
      ${AndIf} ${FileExists} "$R0\Sandglass.exe"
      StrCpy $UpdateBackup "$INSTDIR.update-backup"
      ${If} ${FileExists} "$UpdateBackup\Sandglass.exe"
        StrCpy $UpdatePhase "backup-exists"
        Call UpdateFailure
      ${EndIf}
      ClearErrors
      Rename "$INSTDIR" "$UpdateBackup"
      ${If} ${Errors}
        StrCpy $UpdatePhase "backup-old"
        Call UpdateFailure
      ${EndIf}
    ${Else}
      ; Rename can only create a new install directory. Refuse to merge into an
      ; unrelated non-empty directory chosen by stale external state.
      RMDir "$INSTDIR"
    ${EndIf}
    ; SetOutPath above made UpdateStage the installer's current directory.
    ; Windows cannot rename a process's current directory, so leave it before
    ; atomically activating the fully extracted tree.
    SetOutPath "$TEMP"
    ClearErrors
    Rename "$UpdateStage" "$INSTDIR"
    ${If} ${Errors}
      StrCpy $UpdatePhase "activate-new"
      Call UpdateFailure
    ${EndIf}
    StrCpy $UpdateStage ""
  ${EndIf}

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

  ${If} $UpdateMode == 1
    ${If} $UpdateBackup != ""
      RMDir /r "$UpdateBackup"
    ${EndIf}
    Delete "$UpdateFailureLog"
  ${EndIf}

  ; MUI_FINISHPAGE_RUN is a checkbox on a page /S never draws. Without this
  ; the in-app update installs correctly and ends with no Sandglass running:
  ; the user clicks update, the panel disappears, and nothing comes back.
  ${If} $UpdateMode == 1
    ; /UPDATE intentionally keeps the InstFiles progress window visible, so it
    ; is not ${Silent}; relaunch explicitly when that page has completed.
    ; Hide first so the visible order is progress complete, installer gone,
    ; then the new Sandglass window -- not two overlapping windows.
    ShowWindow $HWNDPARENT 0
    Exec '"$INSTDIR\Sandglass.exe"'
  ${ElseIf} ${Silent}
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
