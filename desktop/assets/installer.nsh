!macro customInstall
  ; Keep sandbox isolation enabled. Prepare only explicit public runtime files;
  ; the script does not grant user profiles or inherit new permissions.
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\prepare-runtime-access.ps1" -ProgramDirectory "$INSTDIR"' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Could not prepare sandbox read access to Companion Studio runtime files."
    SetErrorLevel 1
    Quit
  ${EndIf}
!macroend
