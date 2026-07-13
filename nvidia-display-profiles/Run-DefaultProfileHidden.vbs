Option Explicit

Dim shell
Dim fso
Dim scriptDir
Dim powerShellScript
Dim arguments

Set shell = CreateObject("Shell.Application")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
powerShellScript = fso.BuildPath(scriptDir, "Set-DisplayProfile.ps1")
arguments = "-NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File " & Quote(powerShellScript) & " DEFAULT"

shell.ShellExecute "powershell.exe", arguments, scriptDir, "runas", 0

Function Quote(ByVal value)
    Quote = Chr(34) & value & Chr(34)
End Function
