Unicode True
RequestExecutionLevel user
ManifestDPIAware true
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

!ifndef APPVERSION
!define APPVERSION "0.1.6"
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
Var UpdateMode
Var UpdateParentPid
Var UpdateBackup
Var UpdateStage
Var UpdateRestartExe
Var UpdateReadySignal
Var UpdateReadyEventName
Var UpdateReadyHandle
Var UpdateChildHandle
Var UpdateChildStopOk
Var UpdateSourceExe
Var UpdateHasInstalled
Var UpdateBackupCreated
Var UpdateActivated
Var UpdateNewRemoved
Var UpdatePhase
Var UpdateFailureLog
Var UpdateRegistryCaptured
Var UpdateOldInstallDir
Var UpdateOldInstallDirPresent
Var UpdateOldDisplayName
Var UpdateOldDisplayNamePresent
Var UpdateOldDisplayVersion
Var UpdateOldDisplayVersionPresent
Var UpdateOldDisplayIcon
Var UpdateOldDisplayIconPresent
Var UpdateOldUninstallString
Var UpdateOldUninstallStringPresent
Var UpdateOldNoModify
Var UpdateOldNoModifyPresent
Var UpdateOldNoRepair
Var UpdateOldNoRepairPresent
Var UpdateOldRunValue
Var UpdateOldRunPresent
Var UpdateOldRunType
Var UpdateRunChanged
Var UpdateShortcutBackup
Var UpdateShortcutCaptured
Var UpdateShortcutExisted
Var UpdateShortcutChanged
Var UpdateShortcutDirectoryExisted
Var UpdateDesktopShortcutBackup
Var UpdateDesktopShortcutCaptured
Var UpdateDesktopShortcutExisted
Var UpdateDesktopShortcutChanged
Var UpdatePreserveSource
Var UpdatePreserveTarget
Var UpdatePreserveOk

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
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

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
  ClearErrors
  ${GetOptions} "$R9" "/UPDATE_TOKEN=" $UpdateReadySignal
  ${If} $UpdateMode == 1
    ; Canonicalize the PID before it becomes part of the staging path. Besides
    ; rejecting a broken protocol, this prevents command-line path injection
    ; into the only recursive cleanup used before installation.
    IntOp $R7 $UpdateParentPid + 0
    ${If} $R7 <= 0
      SetErrorLevel 20
      Abort
    ${EndIf}
    ${If} $UpdateRestartExe == ""
      ; Portable handoff and rollback both require the exact executable that
      ; launched the updater. Do not proceed without a restart source.
      SetErrorLevel 22
      Abort
    ${EndIf}
    Push $UpdateReadySignal
    Call IsValidUpdateToken
    Pop $R8
    ${If} $R8 != 1
      ; A self-test only proves that Python can load.  The update must also
      ; receive a direct signal from the real desktop window before removing
      ; the rollback copy.
      SetErrorLevel 23
      Abort
    ${EndIf}
    StrCpy $UpdateReadyEventName "Local\Sandglass.UpdateReady.$UpdateReadySignal"
    ClearErrors
    ; Manual reset lets an independent smoke observer see the same readiness
    ; without consuming it before the installer wait. The event is still
    ; per-attempt: a random token names it, and any pre-existing name is refused.
    System::Call 'kernel32::CreateEventW(p 0, i 1, i 0, w "$UpdateReadyEventName") p .r0 ? e'
    Pop $R8
    ${If} $0 == 0
      SetErrorLevel 24
      Abort
    ${EndIf}
    ${If} $R8 == 183
      System::Call 'kernel32::CloseHandle(p r0)'
      SetErrorLevel 25
      Abort
    ${EndIf}
    StrCpy $UpdateReadyHandle $0
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

; Validate the update capability before it is used to name a kernel object.
; Only lower-case hexadecimal is accepted, so a malformed argument can never
; become a path, registry key, or an ambiguous event name.
Function IsValidUpdateToken
  Exch $R0
  StrCpy $R3 $R0
  StrLen $R1 $R3
  ${If} $R1 != 32
    StrCpy $R2 0
    Goto update_token_done
  ${EndIf}
  StrCpy $R1 0
  StrCpy $R2 1
  update_token_loop:
    StrCpy $R0 $R3 1 $R1
    ${If} $R0 == "0"
    ${OrIf} $R0 == "1"
    ${OrIf} $R0 == "2"
    ${OrIf} $R0 == "3"
    ${OrIf} $R0 == "4"
    ${OrIf} $R0 == "5"
    ${OrIf} $R0 == "6"
    ${OrIf} $R0 == "7"
    ${OrIf} $R0 == "8"
    ${OrIf} $R0 == "9"
    ${OrIf} $R0 == "a"
    ${OrIf} $R0 == "b"
    ${OrIf} $R0 == "c"
    ${OrIf} $R0 == "d"
    ${OrIf} $R0 == "e"
    ${OrIf} $R0 == "f"
    ${Else}
      StrCpy $R2 0
      Goto update_token_done
    ${EndIf}
    IntOp $R1 $R1 + 1
    ${If} $R1 < 32
      Goto update_token_loop
    ${EndIf}
  update_token_done:
  Exch $R2
  Pop $R1
  Push $R1
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
  ; A window that failed to begin shutting down must not leave a visible,
  ; non-cancellable installer waiting forever. No product path has been
  ; touched yet, so timeout is a clean handoff failure.
  System::Call 'kernel32::WaitForSingleObject(p r0, i 30000) i .r1'
  System::Call 'kernel32::CloseHandle(p r0)'
  ${If} $1 == 258
    StrCpy $UpdatePhase "parent-exit-timeout"
    Call UpdateFailure
  ${ElseIf} $1 != 0
    StrCpy $UpdatePhase "wait-parent"
    Call UpdateFailure
  ${EndIf}
FunctionEnd

; Launch the new desktop with CreateProcessW so the installer owns the exact
; child handle. Waiting on a PID or on a signal file can confuse an early
; child exit with readiness and can race another process reusing the PID.
Function LaunchUpdateDesktop
  StrCpy $R0 '"$INSTDIR\Sandglass.exe" "/UPDATE_TOKEN=$UpdateReadySignal"'
  ; Allocate fully initialized STARTUPINFOW and PROCESS_INFORMATION structs.
  ; System::Alloc alone does not promise zeroed bytes; CreateProcess reads the
  ; reserved fields, so every field is explicit here.
  System::Call "*(i 68, p 0, p 0, p 0, i 0, i 0, i 0, i 0, i 0, i 0, i 0, i 0, &i2 0, &i2 0, p 0, p 0, p 0, p 0) p .R1"
  System::Call "*(p 0, p 0, i 0, i 0) p .R2"
  ClearErrors
  ; NSIS register names are case-sensitive: `.r3` writes $3, while the
  ; failure branch reads $R3.
  System::Call 'kernel32::CreateProcessW(p 0, t R0, p 0, p 0, i 0, i 0x04000000, p 0, p 0, p R1, p R2) i .R3 ? e'
  Pop $R4
  System::Free $R1
  ${If} $R3 == 0
    System::Free $R2
    SetErrors
    Return
  ${EndIf}
  System::Call "*$R2(p .r0, p .r1, i .r5, i .r6)"
  System::Call 'kernel32::CloseHandle(p r1)'
  System::Free $R2
  StrCpy $UpdateChildHandle $0
FunctionEnd

Function StopFailedUpdateChild
  ; If the new process is still alive, terminate and wait on this exact handle
  ; before touching the activated tree. Then ask its observer to stop and prove
  ; that the shared mutex is free; otherwise rollback would race a file lock.
  StrCpy $UpdateChildStopOk 1
  ${If} $UpdateChildHandle != ""
    ; NSIS register names are case-sensitive: `.r0` writes $0 and `.r1`
    ; writes $1, while the wait branches below read $R0.
    System::Call 'kernel32::WaitForSingleObject(p $UpdateChildHandle, i 0) i .R0'
    ${If} $R0 == 258
      System::Call 'kernel32::TerminateProcess(p $UpdateChildHandle, i 20) i .R1'
      System::Call 'kernel32::WaitForSingleObject(p $UpdateChildHandle, i 15000) i .R0'
      ${If} $R0 != 0
        StrCpy $UpdateChildStopOk 0
      ${EndIf}
    ${ElseIf} $R0 != 0
      ; WAIT_FAILED or any unexpected result is not proof of process exit.
      StrCpy $UpdateChildStopOk 0
    ${EndIf}
    System::Call 'kernel32::CloseHandle(p $UpdateChildHandle)'
    StrCpy $UpdateChildHandle ""
  ${EndIf}
  ${If} $UpdateActivated == 1
    ; This request is best effort. The observer mutex below is the authority:
    ; a SAC-blocked executable cannot run --stop, but also cannot own it.
    ClearErrors
    ExecWait '"$INSTDIR\Sandglass.exe" --stop' $0
    StrCpy $R7 60
    update_mutex_wait:
      Call CheckSandglassMutex
      Pop $R6
      ${If} $R6 == "free"
        Return
      ${EndIf}
      Sleep 250
      IntOp $R7 $R7 - 1
      ${If} $R7 > 0
        Goto update_mutex_wait
      ${EndIf}
      StrCpy $UpdateChildStopOk 0
      StrCpy $UpdatePhase "$UpdatePhase-mutex"
  ${EndIf}
FunctionEnd

Function WaitForUpdateReady
  ; WaitForMultipleObjects distinguishes the one-shot UI event from an early
  ; process exit and from a timeout. The event is installer-created and named
  ; by a random per-attempt token, so readiness is never inferred from a file.
  System::Call "*(p $UpdateReadyHandle, p $UpdateChildHandle) p .R0"
  ; NSIS register names are case-sensitive: `.r1` writes $1, while every
  ; branch below reads $R1. The former left the freed STARTUPINFO pointer in
  ; $R1 and made every healthy wait look like an unexpected return.
  System::Call 'kernel32::WaitForMultipleObjects(i 2, p R0, i 0, i 120000) i .R1 ? e'
  Pop $R2
  System::Free $R0
  ${If} $R1 == 0
    ; If the event and child became signaled together, the lower array index
    ; wins. Require the exact desktop process to still be alive at readiness.
    System::Call 'kernel32::WaitForSingleObject(p $UpdateChildHandle, i 0) i .R2'
    ${If} $R2 == 258
      System::Call 'kernel32::CloseHandle(p $UpdateChildHandle)'
      StrCpy $UpdateChildHandle ""
      Return
    ${EndIf}
    StrCpy $UpdatePhase "ready-child-exit"
    Call UpdateFailure
  ${ElseIf} $R1 == 1
    StrCpy $UpdatePhase "ready-child-exit"
    Call UpdateFailure
  ${ElseIf} $R1 == 258
    StrCpy $UpdatePhase "ready-timeout"
    Call UpdateFailure
  ${Else}
    StrCpy $UpdatePhase "ready-wait-$R1-e$R2"
    Call UpdateFailure
  ${EndIf}
FunctionEnd

Function UpdateFailure
  ; Update mode has no attended user to dismiss a modal dialog. Restore the
  ; complete old directory when it was moved aside, then start that old copy.
  ${If} $UpdateMode == 1
    SetOutPath "$TEMP"
    Call StopFailedUpdateChild
    ${If} $UpdateChildStopOk != 1
      FileOpen $4 "$UpdateFailureLog" w
      FileWrite $4 "$UpdatePhase$\r$\n"
      FileClose $4
      SetErrorLevel 27
      Quit
    ${EndIf}
    ${If} $UpdateReadyHandle != ""
      System::Call 'kernel32::CloseHandle(p $UpdateReadyHandle)'
      StrCpy $UpdateReadyHandle ""
    ${EndIf}
    FileOpen $4 "$UpdateFailureLog" w
    FileWrite $4 "$UpdatePhase$\r$\n"
    FileClose $4
    ; Only undo a shortcut this update actually captured and changed. An early
    ; failure, a portable source, or an unrelated shortcut must remain intact.
    ${If} $UpdateShortcutCaptured == 1
      ${If} $UpdateShortcutChanged == 1
        Delete "$SMPROGRAMS\Sandglass\Sandglass.lnk"
      ${EndIf}
      ${If} $UpdateShortcutExisted == 1
        ${If} ${FileExists} "$UpdateShortcutBackup"
          ClearErrors
          Rename "$UpdateShortcutBackup" "$SMPROGRAMS\Sandglass\Sandglass.lnk"
          ${If} ${Errors}
            StrCpy $UpdatePhase "$UpdatePhase-rollback-shortcut"
          ${EndIf}
        ${EndIf}
      ${EndIf}
      ${If} $UpdateShortcutDirectoryExisted != 1
        ; Non-recursive: preserve it if anything unrelated appeared meanwhile.
        RMDir "$SMPROGRAMS\Sandglass"
      ${EndIf}
    ${EndIf}
    ; The desktop link is a separate user-visible object. Restore its exact
    ; pre-update bytes instead of assuming it matched the Start Menu link.
    ${If} $UpdateDesktopShortcutCaptured == 1
      ${If} $UpdateDesktopShortcutChanged == 1
        Delete "$DESKTOP\Sandglass.lnk"
      ${EndIf}
      ${If} $UpdateDesktopShortcutExisted == 1
        ${If} ${FileExists} "$UpdateDesktopShortcutBackup"
          ClearErrors
          Rename "$UpdateDesktopShortcutBackup" "$DESKTOP\Sandglass.lnk"
          ${If} ${Errors}
            StrCpy $UpdatePhase "$UpdatePhase-rollback-desktop-shortcut"
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${EndIf}
    ${If} $UpdateStage != ""
      RMDir /r "$UpdateStage"
    ${EndIf}
    ; Once the staged tree has been activated, the target is the new tree.
    ; Remove it before restoring the old directory; Rename cannot replace an
    ; existing directory and would otherwise leave the broken new runtime in
    ; place while reporting that rollback happened.
    ${If} $UpdateActivated == 1
      ${If} $UpdatePreserveOk == 1
      StrCpy $UpdateNewRemoved 0
      ClearErrors
      RMDir /r "$INSTDIR"
      ${If} ${Errors}
        ; Keep the backup intact and record the exact rollback failure. There
        ; is no safe way to claim the old runtime was restored when AV or a
        ; running child kept the new tree open.
        StrCpy $UpdatePhase "$UpdatePhase-rollback-remove-new"
      ${Else}
        StrCpy $UpdateNewRemoved 1
      ${EndIf}
      ${EndIf}
    ${EndIf}
    ${If} $UpdateBackupCreated == 1
      ${If} $UpdateActivated != 1
        ; The old tree was moved aside but activation never completed.
        ClearErrors
        Rename "$UpdateBackup" "$INSTDIR"
        ${If} ${Errors}
          StrCpy $UpdatePhase "$UpdatePhase-rollback-restore-old"
        ${EndIf}
      ${ElseIf} $UpdateNewRemoved == 1
        ${If} ${FileExists} "$UpdateBackup\Sandglass.exe"
          ClearErrors
          Rename "$UpdateBackup" "$INSTDIR"
          ${If} ${Errors}
            StrCpy $UpdatePhase "$UpdatePhase-rollback-restore-old"
          ${EndIf}
        ${EndIf}
      ${EndIf}
      ${If} $UpdateActivated == 1
        ${If} $UpdateNewRemoved == 1
          ${If} ${FileExists} "$INSTDIR\Sandglass.exe"
            Call RestoreUpdateRegistry
            Exec '"$INSTDIR\Sandglass.exe"'
          ${EndIf}
        ${EndIf}
      ${ElseIf} ${FileExists} "$INSTDIR\Sandglass.exe"
        ; Failure before activation leaves the original installation in place.
        Call RestoreUpdateRegistry
        Exec '"$INSTDIR\Sandglass.exe"'
      ${ElseIf} ${FileExists} "$UpdateBackup\Sandglass.exe"
        ClearErrors
        Call RestoreUpdateRegistry
        Exec '"$UpdateBackup\Sandglass.exe"'
      ${EndIf}
    ${ElseIf} $UpdateRestartExe != ""
      ; Portable-to-installed update failure: restart the original portable
      ; executable. Never inspect, rename, or remove its source directory.
      Call RestoreUpdateRegistry
      Exec '"$UpdateRestartExe"'
    ${EndIf}
    ; If rollback itself changed the phase, overwrite the first diagnostic so
    ; callers see the final, actionable state rather than a stale write phase.
    SetOutPath "$TEMP"
    FileOpen $4 "$UpdateFailureLog" w
    FileWrite $4 "$UpdatePhase$\r$\n"
    FileClose $4
    SetErrorLevel 20
    Quit
  ${EndIf}
FunctionEnd

; Save the product-owned values before an update can write any of them. The
; presence flags distinguish an absent value from an intentionally empty one.
; This is used only by /UPDATE, so ordinary installs keep their existing
; registry behavior.
Function SnapshotUpdateRegistry
  StrCpy $UpdateRegistryCaptured 1
  ClearErrors
  ReadRegStr $UpdateOldInstallDir HKCU "Software\Sandglass" "InstallDir"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldInstallDirPresent 1
  ${EndIf}
  ClearErrors
  ReadRegStr $UpdateOldDisplayName HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayName"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldDisplayNamePresent 1
  ${EndIf}
  ClearErrors
  ReadRegStr $UpdateOldDisplayVersion HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayVersion"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldDisplayVersionPresent 1
  ${EndIf}
  ClearErrors
  ReadRegStr $UpdateOldDisplayIcon HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayIcon"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldDisplayIconPresent 1
  ${EndIf}
  ClearErrors
  ReadRegStr $UpdateOldUninstallString HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "UninstallString"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldUninstallStringPresent 1
  ${EndIf}
  ClearErrors
  ReadRegDWORD $UpdateOldNoModify HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoModify"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldNoModifyPresent 1
  ${EndIf}
  ClearErrors
  ReadRegDWORD $UpdateOldNoRepair HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoRepair"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldNoRepairPresent 1
  ${EndIf}
FunctionEnd

Function RestoreUpdateRegistry
  ${If} $UpdateRegistryCaptured != 1
    Return
  ${EndIf}
  ${If} $UpdateOldInstallDirPresent == 1
    WriteRegStr HKCU "Software\Sandglass" "InstallDir" "$UpdateOldInstallDir"
  ${Else}
    DeleteRegValue HKCU "Software\Sandglass" "InstallDir"
  ${EndIf}
  DeleteRegKey /ifempty HKCU "Software\Sandglass"
  ${If} $UpdateOldDisplayNamePresent == 1
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayName" "$UpdateOldDisplayName"
  ${Else}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayName"
  ${EndIf}
  ${If} $UpdateOldDisplayVersionPresent == 1
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayVersion" "$UpdateOldDisplayVersion"
  ${Else}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayVersion"
  ${EndIf}
  ${If} $UpdateOldDisplayIconPresent == 1
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayIcon" "$UpdateOldDisplayIcon"
  ${Else}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayIcon"
  ${EndIf}
  ${If} $UpdateOldUninstallStringPresent == 1
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "UninstallString" "$UpdateOldUninstallString"
  ${Else}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "UninstallString"
  ${EndIf}
  ${If} $UpdateOldNoModifyPresent == 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoModify" $UpdateOldNoModify
  ${Else}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoModify"
  ${EndIf}
  ${If} $UpdateOldNoRepairPresent == 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoRepair" $UpdateOldNoRepair
  ${Else}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoRepair"
  ${EndIf}
  DeleteRegKey /ifempty HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass"
  ; Run is an explicit user opt-in. Restore only our own value, and only when
  ; this update wrote it; unrelated startup entries are never enumerated or
  ; deleted. Type 2 is REG_EXPAND_SZ, preserved from the snapshot.
  ${If} $UpdateRunChanged == 1
    ${If} $UpdateOldRunPresent == 1
      ${If} $UpdateOldRunType == 2
        WriteRegExpandStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass" "$UpdateOldRunValue"
      ${Else}
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass" "$UpdateOldRunValue"
      ${EndIf}
    ${Else}
      DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass"
    ${EndIf}
  ${EndIf}
FunctionEnd

; Capture the user's opt-in startup value. ReadRegStr gives us the command;
; the .NET registry API supplies the numeric value kind without depending on
; reg.exe's localized display text, so REG_EXPAND_SZ is not silently converted
; to REG_SZ during a portable-to-installed migration.
Function SnapshotUpdateRun
  StrCpy $UpdateOldRunPresent 0
  StrCpy $UpdateOldRunType 0
  StrCpy $UpdateRunChanged 0
  ClearErrors
  ReadRegStr $UpdateOldRunValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass"
  ${IfNot} ${Errors}
    StrCpy $UpdateOldRunPresent 1
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "$$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($\"Software\\Microsoft\\Windows\\CurrentVersion\\Run$\",$$false); if ($$null -eq $$k) { exit 2 }; [Console]::Write([int]$$k.GetValueKind($\"sandglass$\")); $$k.Dispose()"'
    Pop $0
    Pop $1
    ${If} $0 == 0
      StrCpy $2 $1 1
      ${If} $2 == 1
        StrCpy $UpdateOldRunType 1
      ${ElseIf} $2 == 2
        StrCpy $UpdateOldRunType 2
      ${EndIf}
    ${EndIf}
  ${EndIf}
FunctionEnd

; Move an existing link aside only at the point where this update is about to
; replace it. The old bytes can then be restored byte-for-byte on rollback.
Function SnapshotUpdateShortcut
  StrCpy $UpdateShortcutCaptured 0
  StrCpy $UpdateShortcutExisted 0
  StrCpy $UpdateShortcutChanged 0
  StrCpy $UpdateShortcutDirectoryExisted 0
  IfFileExists "$SMPROGRAMS\Sandglass\." 0 +2
    StrCpy $UpdateShortcutDirectoryExisted 1
  StrCpy $UpdateShortcutBackup "$TEMP\Sandglass-update-$UpdateParentPid-shortcut.lnk"
  ; A pre-existing path is not ours to delete. Treat it like a staging
  ; collision and fail before touching the user's Start Menu link.
  ${If} ${FileExists} "$UpdateShortcutBackup"
    Return
  ${EndIf}
  ${If} ${FileExists} "$SMPROGRAMS\Sandglass\Sandglass.lnk"
    ClearErrors
    Rename "$SMPROGRAMS\Sandglass\Sandglass.lnk" "$UpdateShortcutBackup"
    ${If} ${Errors}
      Return
    ${EndIf}
    StrCpy $UpdateShortcutExisted 1
  ${EndIf}
  StrCpy $UpdateShortcutCaptured 1
FunctionEnd

; Snapshot the desktop link independently. A user may have replaced or removed
; either shortcut, and rollback must restore exactly what existed before the
; update rather than manufacture the installer's preferred state.
Function SnapshotUpdateDesktopShortcut
  StrCpy $UpdateDesktopShortcutCaptured 0
  StrCpy $UpdateDesktopShortcutExisted 0
  StrCpy $UpdateDesktopShortcutChanged 0
  StrCpy $UpdateDesktopShortcutBackup "$TEMP\Sandglass-update-$UpdateParentPid-desktop-shortcut.lnk"
  ${If} ${FileExists} "$UpdateDesktopShortcutBackup"
    Return
  ${EndIf}
  ${If} ${FileExists} "$DESKTOP\Sandglass.lnk"
    ClearErrors
    Rename "$DESKTOP\Sandglass.lnk" "$UpdateDesktopShortcutBackup"
    ${If} ${Errors}
      Return
    ${EndIf}
    StrCpy $UpdateDesktopShortcutExisted 1
  ${EndIf}
  StrCpy $UpdateDesktopShortcutCaptured 1
FunctionEnd

; Preserve files that belong to the owner of the install directory. The helper
; is a checked-in, directly tested input to this installer; generating it with
; FileWrite previously let the NSIS compile pass while the emitted PowerShell
; had a syntax error. It runs before activation, so any refusal leaves the old
; installation untouched.
Function PreserveUpdateExtras
  StrCpy $UpdatePreserveOk 0
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  ClearErrors
  File /oname=preserve_update_extras.ps1 "..\tools\preserve_update_extras.ps1"
  ${If} ${Errors}
    Return
  ${EndIf}
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\preserve_update_extras.ps1" -Old "$UpdatePreserveSource" -New "$UpdatePreserveTarget"'
  Pop $0
  Pop $1
  ${If} $0 == 0
    StrCpy $UpdatePreserveOk 1
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
  ; program file is actually free before writing anything. In /UPDATE mode
  ; $UpdateRestartExe is also a source: a portable copy has no install-registry
  ; entry, but its detached observer still owns the global observer mutex.
  ReadRegStr $R0 HKCU "Software\Sandglass" "InstallDir"
  StrCpy $UpdateSourceExe ""
  StrCpy $UpdateHasInstalled 0
  StrCpy $UpdateBackupCreated 0
  StrCpy $UpdateActivated 0
  StrCpy $UpdateNewRemoved 0
  ${If} $R0 != ""
    ClearErrors
    ${If} ${FileExists} "$R0\Sandglass.exe"
      StrCpy $UpdateSourceExe "$R0\Sandglass.exe"
      StrCpy $UpdateHasInstalled 1
    ${EndIf}
  ${EndIf}
  ${If} $UpdateMode == 1
    ${If} $UpdateHasInstalled != 1
      ; InstallDirRegKey can leave a stale path in $INSTDIR. Portable
      ; handoff always targets the documented current-user install location;
      ; never rename or clean the portable source or a stale registry path.
!ifdef SANDGLASS_TEST_UPDATE_TARGET
      ; Test-only compile target keeps portable handoff smoke out of the real
      ; current-user install directory. Ordinary release builds never define it.
      StrCpy $INSTDIR "${SANDGLASS_TEST_UPDATE_TARGET}"
!else
      StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Sandglass"
!endif
    ${EndIf}
  ${If} $UpdateSourceExe == ""
      ; Do not inspect the portable source directory. The updater supplied the
      ; exact executable that launched it and that is the only source path we
      ; need to invoke for the observer handoff.
      StrCpy $UpdateSourceExe "$UpdateRestartExe"
      Call SnapshotUpdateRegistry
      Call SnapshotUpdateRun
    ${Else}
      Call SnapshotUpdateRegistry
      Call SnapshotUpdateRun
    ${EndIf}
    ${If} $UpdateOldRunPresent == 1
      ${If} $UpdateOldRunType == 0
        StrCpy $UpdatePhase "snapshot-run"
        Call UpdateFailure
      ${EndIf}
    ${EndIf}
  ${EndIf}
  ${If} $UpdateSourceExe != ""
    ; --stop now waits for the observer to release the program files and reports
    ; whether it did. It used to signal and return 0 regardless, so this waited a
    ; fixed two seconds instead -- for an exit bounded by a fifteen-second vendor
    ; request per provider -- and then queried the desktop mutex, which says
    ; nothing about the detached observer the comment above names as the holder.
    ExecWait '"$UpdateSourceExe" --stop' $0
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

    ${If} $UpdateHasInstalled == 1
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
    ClearErrors
    CreateDirectory "$UpdateStage"
    ${If} ${Errors}
      StrCpy $UpdatePhase "create-stage"
      Call UpdateFailure
    ${EndIf}
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
    ; Merge owner content into staging while the original install is still
    ; untouched. A reparse point or copy failure therefore aborts before the
    ; rollback rename, leaving the old tree byte-for-byte intact.
    ${If} $UpdateHasInstalled == 1
      StrCpy $UpdatePreserveSource "$R0"
      StrCpy $UpdatePreserveTarget "$UpdateStage"
      Call PreserveUpdateExtras
      ${If} $UpdatePreserveOk != 1
        StrCpy $UpdatePhase "preserve-install-extras"
        Call UpdateFailure
      ${EndIf}
    ${Else}
      StrCpy $UpdatePreserveOk 1
    ${EndIf}
    ; Only an actual prior Sandglass install is moved aside. An empty registry
    ; value is the normal portable-to-installed path and is never a delete or
    ; rename target.
    ${If} $UpdateHasInstalled == 1
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
      StrCpy $UpdateBackupCreated 1
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
    StrCpy $UpdateActivated 1
    StrCpy $UpdateStage ""
  ${EndIf}

  ; Snapshot just before replacing the link. Failures before this point leave
  ; the user's existing Start Menu state completely untouched.
  ${If} $UpdateMode == 1
    Call SnapshotUpdateShortcut
    ${If} $UpdateShortcutCaptured != 1
      StrCpy $UpdatePhase "snapshot-shortcut"
      Call UpdateFailure
    ${EndIf}
    Call SnapshotUpdateDesktopShortcut
    ${If} $UpdateDesktopShortcutCaptured != 1
      StrCpy $UpdatePhase "snapshot-desktop-shortcut"
      Call UpdateFailure
    ${EndIf}
  ${EndIf}
  ClearErrors
  CreateDirectory "$SMPROGRAMS\Sandglass"
  ${If} ${Errors}
    StrCpy $UpdatePhase "create-shortcut-directory"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  CreateShortcut "$SMPROGRAMS\Sandglass\Sandglass.lnk" "$INSTDIR\Sandglass.exe" "" "$INSTDIR\Sandglass.exe"
  ${If} ${Errors}
    StrCpy $UpdatePhase "create-shortcut"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ${If} $UpdateMode == 1
    StrCpy $UpdateShortcutChanged 1
  ${EndIf}
  ${If} $UpdateMode == 1
    ; Rollback owns any path CreateShortcut might create, even if the command
    ; reports an error after leaving partial output behind.
    StrCpy $UpdateDesktopShortcutChanged 1
  ${EndIf}
  ClearErrors
  CreateShortcut "$DESKTOP\Sandglass.lnk" "$INSTDIR\Sandglass.exe" "" "$INSTDIR\Sandglass.exe"
  ${If} ${Errors}
    StrCpy $UpdatePhase "create-desktop-shortcut"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-uninstaller"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  WriteRegStr HKCU "Software\Sandglass" "InstallDir" "$INSTDIR"
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-install-registry"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ; A portable install may have an explicit sandglass Run value pointing to
  ; the portable executable. Preserve its arguments and value kind while
  ; moving that opt-in to the installed executable. No value means no opt-in.
  ${If} $UpdateMode == 1
    ${If} $UpdateHasInstalled != 1
      ${If} $UpdateOldRunPresent == 1
        StrCpy $R2 '"$UpdateRestartExe"'
        StrLen $R3 $R2
        StrCpy $R4 $UpdateOldRunValue $R3
        ${If} $R4 == $R2
          StrCpy $R5 $UpdateOldRunValue "" $R3
          StrCpy $R6 '"$INSTDIR\Sandglass.exe"$R5'
        ${Else}
          StrCpy $R2 "$UpdateRestartExe"
          StrLen $R3 $R2
          StrCpy $R4 $UpdateOldRunValue $R3
          ${If} $R4 != $R2
            StrCpy $R2 ""
          ${Else}
            StrCpy $R5 $UpdateOldRunValue "" $R3
            StrCpy $R6 "$INSTDIR\Sandglass.exe$R5"
          ${EndIf}
        ${EndIf}
        ${If} $R2 != ""
          StrCpy $UpdateRunChanged 1
          ClearErrors
          ${If} $UpdateOldRunType == 2
            WriteRegExpandStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass" "$R6"
          ${Else}
            WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass" "$R6"
          ${EndIf}
          ${If} ${Errors}
            StrCpy $UpdatePhase "write-run-migration"
            Call UpdateFailure
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}
  ClearErrors
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayName" "Sandglass"
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-uninstall-name"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayVersion" "${APPVERSION}"
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-uninstall-version"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "DisplayIcon" "$INSTDIR\Sandglass.exe"
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-uninstall-icon"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-uninstall-string"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoModify" 1
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-uninstall-nomodify"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}
  ClearErrors
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass" "NoRepair" 1
  ${If} ${Errors}
    StrCpy $UpdatePhase "write-uninstall-norepair"
    ${If} $UpdateMode == 1
      Call UpdateFailure
    ${EndIf}
    Abort
  ${EndIf}

  ${If} $UpdateMode == 1
    ; Keep the old tree available until the activated executable proves it can
    ; start its packaged runtime. This catches SAC/startup failures while a
    ; complete rollback is still possible.
    ClearErrors
    ExecWait '"$INSTDIR\Sandglass.exe" --self-test' $0
    ${If} ${Errors}
      StrCpy $UpdatePhase "self-test-launch"
      Call UpdateFailure
    ${EndIf}
    ${If} $0 != 0
      StrCpy $UpdatePhase "self-test"
      Call UpdateFailure
    ${EndIf}
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
    ClearErrors
    ClearErrors
    Call LaunchUpdateDesktop
    ${If} ${Errors}
      StrCpy $UpdatePhase "ready-launch"
      Call UpdateFailure
    ${EndIf}
!ifdef SANDGLASS_TEST_FAULT_POST_ACTIVATION
    ; Test-only fault is deliberately after the new desktop child exists.
    ; This forces StopFailedUpdateChild to terminate that exact child and then
    ; proves the complete post-activation rollback path before readiness.
    StrCpy $UpdatePhase "fault-post-ready-launch"
    Call UpdateFailure
!endif
    Call WaitForUpdateReady
    ${If} $UpdateReadyHandle != ""
      System::Call 'kernel32::CloseHandle(p $UpdateReadyHandle)'
      StrCpy $UpdateReadyHandle ""
    ${EndIf}
    ; Only the real desktop readiness signal retires rollback.  Keep both the
    ; old directory and its shortcut snapshot until that point.
    ${If} $UpdateBackup != ""
      RMDir /r "$UpdateBackup"
    ${EndIf}
    ${If} $UpdateShortcutBackup != ""
      Delete "$UpdateShortcutBackup"
    ${EndIf}
    ${If} $UpdateDesktopShortcutBackup != ""
      Delete "$UpdateDesktopShortcutBackup"
    ${EndIf}
    Delete "$UpdateFailureLog"
  ${ElseIf} ${Silent}
    Exec '"$INSTDIR\Sandglass.exe"'
  ${EndIf}
SectionEnd

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

Function un.CheckSandglassDesktopMutex
  ; Same fail-closed OpenMutexW capture as the installer. A PyInstaller
  ; executable may be renamed while its process is still alive, so Rename of
  ; Sandglass.exe is not evidence that this mutex is free.
  System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\Sandglass.Desktop.SingleInstance") p .r3 ? e'
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

  ; Ask the desktop mutex before any deletion. --stop reaches only the
  ; observer; a running panel puts it back, and the previous Rename of
  ; Sandglass.exe was allowed by Windows while that panel still held this
  ; mutex. Distinct codes: an abort otherwise reports NSIS's generic 2.
  ; /S does not suppress MessageBox, so silent refusal must not wait on one.
  Call un.CheckSandglassDesktopMutex
  Pop $R1
  ${If} $R1 == "held"
    SetErrorLevel 9
    ${IfNot} ${Silent}
      MessageBox MB_ICONSTOP "Sandglass is still running. Close it from the tray icon and run the uninstaller again."
    ${EndIf}
    Abort
  ${ElseIf} $R1 != "free"
    SetErrorLevel 10
    ${IfNot} ${Silent}
      MessageBox MB_ICONSTOP "Windows could not verify whether Sandglass is still running. The uninstaller will not remove it."
    ${EndIf}
    Abort
  ${EndIf}

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
      ${IfNot} ${Silent}
        MessageBox MB_ICONSTOP "Sandglass could not confirm that its background observer stopped. Quit Sandglass from the tray icon and run the uninstaller again."
      ${EndIf}
      Abort
    ${EndIf}
    Call un.CheckSandglassMutex
    Pop $R1
    ${If} $R1 == "held"
      SetErrorLevel 7
      ${IfNot} ${Silent}
        MessageBox MB_ICONSTOP "Sandglass's background observer is still running. Quit Sandglass from the tray icon and run the uninstaller again."
      ${EndIf}
      Abort
    ${ElseIf} $R1 != "free"
      SetErrorLevel 8
      ${IfNot} ${Silent}
        MessageBox MB_ICONSTOP "Windows could not verify whether Sandglass's background observer is running. The uninstaller will not remove it."
      ${EndIf}
      Abort
    ${EndIf}
  ${EndIf}

  Delete "$SMPROGRAMS\Sandglass\Sandglass.lnk"
  RMDir "$SMPROGRAMS\Sandglass"
  Delete "$DESKTOP\Sandglass.lnk"
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
  Delete "$INSTDIR\Sandglass-owned-paths.json"
  RMDir /r "$INSTDIR\THIRD_PARTY_LICENSES"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$INSTDIR\.sandglass-owner"
  RMDir "$INSTDIR"
SectionEnd
