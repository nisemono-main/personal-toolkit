# Loud Gate

Loud Gate is a Windows microphone router with a lookahead peak limiter. It selects a physical microphone and virtual-audio output, limits sudden peaks, exposes configurable global hotkeys, and can run continuously through a logon scheduled task.

## Setup

Use Python 3.10 or newer on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\loud_gate.py
```

The first interactive run discovers likely devices and stores the selected device names and audio settings in `%APPDATA%\loud-gate\config.ini`. The program reads that exact file at startup; `config.example.ini` beside the script is documentation only and is not read automatically. Use `--run` for a non-interactive start, `--setup` to select devices again, and `--install-startup` or `--uninstall-startup` to manage the scheduled task.

Edit the `[hotkeys]` section in `%APPDATA%\loud-gate\config.ini` to change the bindings. The default layout is:

```ini
[hotkeys]
mute_hotkey=F13
stop_hotkey=Ctrl+Shift+F13
threshold_down_hotkey=F14
threshold_up_hotkey=Ctrl+F14
threshold_step_db=5.0
```

Supported key names include `F1`–`F24`, letters, digits, and common names such as `Esc`, `Space`, `Enter`, `Home`, `End`, `Left`, `Right`, `Up`, and `Down`. Use `+` between modifiers and the key. Restart Loud Gate after changing the bindings; Windows rejects combinations already registered by another application.

Python is used because NumPy and sounddevice provide practical building blocks for real-time audio buffers, device discovery, and the lookahead limiter, while `ctypes` connects the service to Windows global-hotkey APIs. The generated startup launcher uses Windows Script Host so scheduled startup can remain hidden without opening a console window.

The program uses only environment-based user data paths and paths derived from its own executable/script location.
