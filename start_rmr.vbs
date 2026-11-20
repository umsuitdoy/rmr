Option Explicit

Dim shell, files, folder, marker, lockfile, needsSync, result, stream
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
marker = folder & "\.venv\.rmr-ready"
lockfile = folder & "\uv.lock"

needsSync = Not files.FileExists(marker) Or Not files.FileExists(folder & "\.venv\Scripts\rmr-desktop.exe")
If Not needsSync Then
    If Not files.FileExists(lockfile) Then
        needsSync = True
    ElseIf files.GetFile(lockfile).DateLastModified > files.GetFile(marker).DateLastModified Or files.GetFile(folder & "\pyproject.toml").DateLastModified > files.GetFile(marker).DateLastModified Then
        needsSync = True
    End If
End If

On Error Resume Next
If needsSync Then
    result = shell.Run("cmd.exe /c title RMR Setup && uv sync", 1, True)
    If Err.Number <> 0 Or result <> 0 Then
        MsgBox "RMR setup failed. Check that uv is installed, then try again.", vbExclamation, "RMR Desktop"
        WScript.Quit 1
    End If
    Set stream = files.CreateTextFile(marker, True)
    stream.Close
    If Err.Number <> 0 Then
        MsgBox "Could not save setup status. Please try again.", vbExclamation, "RMR Desktop"
        WScript.Quit 1
    End If
End If
result = shell.Run("uv run --no-sync rmr-desktop", 0, True)
If Err.Number <> 0 Or result <> 0 Then
    MsgBox "RMR Desktop could not start. Run 'uv run rmr-desktop' in a terminal for details.", vbExclamation, "RMR Desktop"
End If
