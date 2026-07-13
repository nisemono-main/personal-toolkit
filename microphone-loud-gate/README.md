# Loud Gate

Loud Gate is a Windows microphone router with a sample-accurate lookahead peak limiter. It opens the physical microphone and virtual-audio output as one full-duplex stream, limits sudden peaks, exposes configurable global hotkeys, and can run continuously through a logon scheduled task.

## Setup

Use Python 3.11 or newer on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\loud_gate.py
```

The first interactive run finds microphone/VB-CABLE pairs that PortAudio can open together through the same Windows audio backend. WASAPI is preferred. It stores the selected route in `%APPDATA%\loud-gate\config.ini`; device indexes are refreshed from the saved names and backend when Windows renumbers them. The program reads that exact file at startup, while `config.example.ini` beside the script is only a layout reference. Use `--run` for a non-interactive start, `--setup` to select devices again, and `--install-startup` or `--uninstall-startup` to manage the scheduled task.

## Audio assumptions

Loud Gate is designed for Windows audio routing through the standard **VB-CABLE** driver. Download it from the official [VB-Audio Virtual Cables page](https://vb-audio.com/Cable/), install the Windows package, and select the normal `CABLE Input (VB-Audio Virtual Cable)` playback endpoint as Loud Gate's output. Applications that should receive the processed microphone should use the matching `CABLE Output (VB-Audio Virtual Cable)` recording endpoint as their microphone.

The default sample-rate assumption is **44,100 Hz**. Setup validates the complete full-duplex route at 44,100 Hz, the endpoint defaults, and 48,000 Hz, then saves the first working rate. For predictable behavior, set the physical microphone, `CABLE Input`, and `CABLE Output` formats to that saved rate in Windows Sound settings. The limiter uses an exact sample delay and remains correct when the audio backend changes callback size.

The script assumes one physical microphone input and one VB-CABLE playback/output endpoint. It does not create the virtual cable itself, mix multiple microphones, or replace the Windows recording endpoint used by applications. The expected route is:

```text
physical microphone (WASAPI input)
             |
             v
one full-duplex Loud Gate stream -> CABLE Input (WASAPI output)
                                             |
                                             v
                   applications use CABLE Output as microphone
```

## Configuration and hotkeys

The normal setup path is to create the real configuration by running `loud_gate.py` once and selecting a validated route. If you need to create it manually, create `%APPDATA%\loud-gate\config.ini` with the layout documented in `config.example.ini`; do not place it beside the script because that file is never loaded. Both saved host APIs must describe the same backend. `input_channel` is zero-based, and `sample_rate` must be supported by the complete input/output pair. Changes take effect after restarting Loud Gate.

```ini
[loud-gate]
version=6
input_device_index=17
input_device_name=Headset Microphone (Maonocaster G1 Neo)
input_device_hostapi=Windows WASAPI
input_channel=0
output_device_index=16
output_device_name=CABLE Input (VB-Audio Virtual Cable)
output_device_hostapi=Windows WASAPI
sample_rate=44100
threshold_db=-18.0
release_ms=150.0
lookahead_ms=25.0
```

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

Python is used because NumPy provides efficient preallocated float32 DSP workspaces and sounddevice exposes PortAudio's full-duplex streams and timing information, while `ctypes` connects the service to Windows global-hotkey APIs. The callback uses one direct input-to-output path without a user-space clock-bridging ring. The generated startup launcher uses Windows Script Host so scheduled startup can remain hidden without opening a console window.

The program uses only environment-based user data paths and paths derived from its own executable/script location.
