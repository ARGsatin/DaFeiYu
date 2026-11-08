' DaFeiYu desktop launcher (no console window).
' Double-click this file: it starts desktop.py with pythonw.exe and shows a tray icon.
' Keep this file ASCII-only: WSH may read .vbs as ANSI, so non-ASCII text can garble.
'
' Why WMI instead of Shell.Run: pythonw.exe in .venv is unsigned, and starting it
' through the shell (Shell.Run / ShellExecute) makes Windows show
' "Open File - Security Warning: publisher cannot be verified", which blocks the
' launch until the user clicks Run. WMI Win32_Process.Create goes through
' CreateProcess directly, so no such warning appears.

Option Explicit

Dim fso, sh, here, pyw, venvPyw, target, vers, v, hive, ok
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(here, "desktop.py")

pyw = ""
' 1) prefer the project venv: it holds all the dependencies
venvPyw = fso.BuildPath(here, ".venv\Scripts\pythonw.exe")
If fso.FileExists(venvPyw) Then
    pyw = venvPyw
Else
    ' 2) fall back to a system Python found in the registry
    vers = Array("3.13", "3.14", "3.12", "3.11", "3.10")
    For Each hive In Array("HKLM", "HKCU")
        For Each v In vers
            On Error Resume Next
            pyw = sh.RegRead(hive & "\SOFTWARE\Python\PythonCore\" & v & "\InstallPath\ExecutableW")
            Err.Clear
            On Error GoTo 0
            If pyw <> "" Then
                If fso.FileExists(pyw) Then
                    pyw = fso.BuildPath(fso.GetParentFolderName(pyw), "pythonw.exe")
                    ok = fso.FileExists(pyw)
                    If ok Then Exit For
                    pyw = ""
                End If
            End If
        Next
        If pyw <> "" Then Exit For
    Next

    If pyw = "" Or Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
End If

Dim cmdLine, wmi, ret
cmdLine = """" & pyw & """ """ & target & """"
ret = -1
On Error Resume Next
Set wmi = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
If Err.Number = 0 Then
    ret = wmi.Get("Win32_Process").Create(cmdLine, here, Null)
    If Err.Number <> 0 Then ret = -1
    Err.Clear
End If
If Err.Number <> 0 Then ret = -1
On Error GoTo 0

If ret <> 0 Then
    ' WMI unavailable: fall back to the classic Shell.Run path
    On Error Resume Next
    sh.CurrentDirectory = here
    sh.Run cmdLine, 0, False
    If Err.Number <> 0 Then
        MsgBox "Could not start pythonw.exe." & vbCrLf & vbCrLf & _
               "Install Python 3 and make sure it is on PATH, or run desktop.py manually.", _
               16, "DaFeiYu"
    End If
    On Error GoTo 0
End If
