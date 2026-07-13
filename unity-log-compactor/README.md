# Unity Log Compactor

Unity Log Compactor turns verbose Unity exception output into compact, readable message-and-source summaries. Instead of carrying an entire multi-frame stack through the clipboard, it keeps the exception’s first message and the most useful final file/line location from each entry. The PowerShell script performs the parsing, while the VBScript wrapper runs it silently.

## Usage

Create a shortcut or hotkey for `run-compact-silent.vbs`. It finds `compact-clipboard-logs.ps1` beside itself, reads the current clipboard, and replaces it with the compact form. No project-specific directory is required.

The script uses Windows PowerShell clipboard cmdlets and is intended for Windows.

PowerShell is used for the native `Get-Clipboard`/`Set-Clipboard` operations and convenient regular-expression processing. The VBScript wrapper is intended for a shortcut or hotkey: launching it through `wscript.exe` avoids a command prompt window and lets the clipboard cleanup happen quietly in the background.

Example transformation with multiple stack frames and entries:

```text
Input:
NullReferenceException: Object reference not set to an instance of an object
  at PlayerController.Update () (at Assets/Scripts/PlayerController.cs:214)
  at GameLoop.Tick () (at Assets/Scripts/GameLoop.cs:87)
  at UnityEngine.PlayerLoop.UpdateFunction.Invoke ()

ArgumentException: The value is out of range
  at Inventory.AddItem (Item item) (at Assets/Scripts/Inventory.cs:63)
  at ShopController.Buy () (at Assets/Scripts/ShopController.cs:119)
  at UnityEngine.Events.InvokableCall.Invoke ()

Output:
NullReferenceException: Object reference not set to an instance of an object
Assets/Scripts/GameLoop.cs:87

ArgumentException: The value is out of range
Assets/Scripts/ShopController.cs:119
```
