# CopyClean

CopyClean turns verbose Unity-style clipboard logs into compact message-and-source entries. The PowerShell script extracts the first message and the most useful file/line reference from each log entry, while the VBScript wrapper runs it silently.

## Usage

Create a shortcut or hotkey for `run-compact-silent.vbs`. It finds `compact-clipboard-logs.ps1` beside itself, reads the current clipboard, and replaces it with the compact form. No project-specific directory is required.

The script uses Windows PowerShell clipboard cmdlets and is intended for Windows.

PowerShell is used for the native `Get-Clipboard`/`Set-Clipboard` operations and convenient regular-expression processing. The VBScript wrapper is intended for a shortcut or hotkey: launching it through `wscript.exe` avoids a command prompt window and lets the clipboard cleanup happen quietly in the background.

Example transformation:

```text
Input:
NullReferenceException: Object reference not set
  at Player.Update() (at Assets/Scripts/Player.cs:42)

Output:
NullReferenceException: Object reference not set
Assets/Scripts/Player.cs:42
```
