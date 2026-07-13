# Loud Gate

Loud Gate is a Windows microphone router with a lookahead peak limiter. It selects a physical microphone and virtual-audio output, limits sudden peaks, exposes configurable global hotkeys, and can run continuously through a logon scheduled task.

## Setup

Use Python 3.10 or newer on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\loud_gate.py
```

The first interactive run discovers likely devices and stores the selected device names and audio settings in `%APPDATA%\loud-gate\config.ini`. The program reads that exact file at startup; `config.example.ini` beside the script is a repository layout reference only and is not read automatically. Use `--run` for a non-interactive start, `--setup` to select devices again, and `--install-startup` or `--uninstall-startup` to manage the scheduled task.

## Audio assumptions

Loud Gate is designed for Windows audio routing through the standard **VB-CABLE** driver. Download it from the official [VB-Audio Virtual Cables page](https://vb-audio.com/Cable/), install the Windows package, and select the normal `CABLE Input (VB-Audio Virtual Cable)` playback endpoint as Loud Gate's output. Applications that should receive the processed microphone should use the matching `CABLE Output (VB-Audio Virtual Cable)` recording endpoint as their microphone.

The default sample-rate assumption is **44,100 Hz**. During setup and startup, Loud Gate negotiates a common rate from 44,100 Hz, the selected devices' defaults, and 48,000 Hz. For predictable latency and device compatibility, set the physical microphone and VB-CABLE endpoints to the same rate in Windows Sound settings; 44,100 Hz is the reference configuration used by this project. The configured lookahead and block processing then operate at the rate that was successfully selected.

The script assumes one physical microphone input and one VB-CABLE playback/output endpoint. It does not create the virtual cable itself, mix multiple microphones, or replace the Windows recording endpoint used by applications. The expected route is:

```text
physical microphone -> Loud Gate input -> CABLE Input playback endpoint
                                                     |
                                                     v
                              applications use CABLE Output as microphone input
```

## Configuration and hotkeys

The normal setup path is to create the real configuration by running `loud_gate.py` once and selecting the devices. If you need to create it manually, create `%APPDATA%\loud-gate\config.ini` with the layout documented in `config.example.ini`; do not place it beside the script because that file is never loaded. Changes take effect after restarting Loud Gate.

Edit the `[hotkeys]` section in the real `%APPDATA%\loud-gate\config.ini` to change the bindings. Every configured combination must be unique and available to Windows. The default layout is:

```ini
[hotkeys]
mute_hotkey=F13
stop_hotkey=Ctrl+Shift+F13
threshold_down_hotkey=F14
threshold_up_hotkey=Ctrl+F14
threshold_step_db=5.0
```

Supported key names include `F1`–`F24`, letters, digits, and common names such as `Esc`, `Space`, `Enter`, `Home`, `End`, `Left`, `Right`, `Up`, and `Down`. Use `+` between modifiers and the key. Loud Gate rejects duplicate bindings in its own configuration, and Windows rejects combinations already registered by another application.

## Windows startup

After the interactive setup, install the logon task:

```powershell
.\.venv\Scripts\python.exe .\loud_gate.py --install-startup
```

The command creates a scheduled task and generates `%APPDATA%\loud-gate\startup_launcher.vbs`. The VBS launcher uses Windows Script Host so the normal background start does not open a command window; it waits for the hidden Python process and shows a visible error window if startup exits unsuccessfully after its bounded retry period. The original failure and startup phase remain in `%APPDATA%\loud-gate\loud_gate.log`.

If the Python executable or repository location changes, run `--install-startup` again to regenerate the launcher with the current paths. If Task Scheduler never launches the task at all, inspect the task's History and Last Run Result because no child process exists to display the popup.

Remove the task with:

```powershell
.\.venv\Scripts\python.exe .\loud_gate.py --uninstall-startup
```

Python is used because NumPy and sounddevice provide practical building blocks for real-time audio buffers, device discovery, and the lookahead limiter, while `ctypes` connects the service to Windows global-hotkey APIs. The generated startup launcher uses Windows Script Host so scheduled startup can remain hidden without opening a console window.

The program uses only environment-based user data paths and paths derived from its own executable/script location.
