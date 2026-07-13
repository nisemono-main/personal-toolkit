' run-compact-silent.vbs
' Silent launcher for compact-clipboard-logs.ps1.
' Place this .vbs file in the same folder as compact-clipboard-logs.ps1.
' Create a Windows shortcut to this .vbs and assign your hotkey.

Option Explicit
Dim fso, shell, scriptFolder, psPath, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' Get folder where this VBS resides
scriptFolder = fso.GetParentFolderName(WScript.ScriptFullName)
If scriptFolder = "" Then
    scriptFolder = "."
End If

psPath = fso.BuildPath(scriptFolder, "compact-clipboard-logs.ps1")

' If PowerShell script missing, silently exit
If Not fso.FileExists(psPath) Then
    WScript.Quit 0
End If

' Build command; -NonInteractive reduces chance of prompts
cmd = "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & psPath & """"

' Run hidden (window style 0), don't wait
shell.Run cmd, 0, False

' Clean up
Set fso = Nothing
Set shell = Nothing
WScript.Quit 0