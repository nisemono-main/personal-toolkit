Option Explicit

Dim shell
Dim fso
Dim scriptDir
Dim batchFile

Set shell = CreateObject("Shell.Application")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batchFile = fso.BuildPath(scriptDir, "Run-GameProfile.bat")

shell.ShellExecute batchFile, "", scriptDir, "runas", 1
