# Loud Gate

Loud Gate routes one physical microphone through a sample-accurate lookahead limiter into VB-CABLE, with configurable global hotkeys and optional Windows-startup integration.

## Support boundary

Loud Gate is Windows-only and currently supports and checks **Windows WASAPI** device pairs only.

- Setup shows only WASAPI microphone inputs and WASAPI `CABLE Input` playback endpoints.
- The exact input/output pair is opened before it can be selected.
- WDM-KS, DirectSound, MME, ASIO, and other backends are not tested by this project. Using them is at your own risk.
- If the VB-Audio endpoint is missing, setup and runtime log a warning instead of silently selecting another audio route.

## Requirements

- Windows
- Python 3.11 or newer
- [VB-CABLE](https://vb-audio.com/Cable/) from VB-Audio
- A physical microphone exposed through Windows WASAPI

Install VB-CABLE, then use `CABLE Input (VB-Audio Virtual Cable)` as Loud Gate's output. Applications that should receive the processed microphone must use `CABLE Output (VB-Audio Virtual Cable)` as their microphone input.

## Quick start

From this directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\loud_gate.py
```

The first run performs interactive setup. It lists only validated WASAPI microphone-to-VB-CABLE pairs and saves the selection to:

```text
%APPDATA%\loud-gate\config.ini
```

Use these commands afterwards:

```powershell
# Start without prompting
.\.venv\Scripts\python.exe .\loud_gate.py --run

# Select a different validated pair
.\.venv\Scripts\python.exe .\loud_gate.py --setup
```

`config.example.ini` is a layout reference. It is not loaded automatically and should not be placed beside the script as the live configuration.

## Audio route

```text
physical microphone (WASAPI input)
              │
              ▼
one full-duplex Loud Gate stream
              │
              ▼
CABLE Input (WASAPI playback/output)
              │
              ▼
applications use CABLE Output as microphone input
```

For predictable results, set the physical microphone, `CABLE Input`, and `CABLE Output` to the same Windows format. The reference configuration is **44,100 Hz**. Setup validates 44,100 Hz, the endpoint defaults, and 48,000 Hz, then saves the first working rate.

Loud Gate assumes one physical microphone and one VB-CABLE route. It does not create VB-CABLE, mix multiple microphones, or replace the recording device selected inside other applications.

## Configuration

The live file is `%APPDATA%\loud-gate\config.ini`. Run `--setup` when changing devices; edit the file directly only when you understand the device names and WASAPI pairing.

The core layout is:

```ini
[loud-gate]
version=6
input_device_index=17
input_device_name=Your Microphone
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

Important values:

- `input_channel` is zero-based. Use setup to select the microphone channel.
- `sample_rate` must be supported by the complete WASAPI pair.
- `threshold_db` is the limiter ceiling; lower values reduce peaks more strongly.
- `release_ms` controls how quickly attenuation recovers.
- `lookahead_ms` allows the limiter to react before a peak reaches the output.

### Hotkeys

Edit the `[hotkeys]` section in the same live INI file. Every combination must be unique and available to Windows:

```ini
[hotkeys]
mute_hotkey=F13
stop_hotkey=Ctrl+Shift+F13
threshold_down_hotkey=F14
threshold_up_hotkey=Ctrl+F14
threshold_step_db=5.0
```

Supported keys include `F1`–`F24`, letters, digits, and names such as `Esc`, `Space`, `Enter`, `Home`, `End`, `Left`, `Right`, `Up`, and `Down`. Use `+` between modifiers and the key. Restart Loud Gate after configuration changes.

## Windows startup

After setup, install the logon task:

```powershell
.\.venv\Scripts\python.exe .\loud_gate.py --install-startup
```

This creates a scheduled task and a generated `%APPDATA%\loud-gate\startup_launcher.vbs`. VBS is used because Windows Script Host can start the background Python process without opening a command window. If the bounded startup attempt fails, it shows a visible error window and records the startup phase and exception in `%APPDATA%\loud-gate\loud_gate.log`.

If the Python or repository path changes, run `--install-startup` again so the launcher is regenerated. If Task Scheduler never starts the launcher, inspect the task's History and Last Run Result.

Remove the task with:

```powershell
.\.venv\Scripts\python.exe .\loud_gate.py --uninstall-startup
```

## Troubleshooting

### VB-CABLE is missing

Install the Windows package from the [official VB-Audio Cable page](https://vb-audio.com/Cable/), then confirm that both `CABLE Input` appears under Playback and `CABLE Output` appears under Recording. Re-run `--setup` afterwards.

### No route is listed

The microphone and `CABLE Input` must both be present as Windows WASAPI endpoints and must open together at one sample rate. Check Windows Sound settings, endpoint formats, and whether another application has taken exclusive control. Other backends are outside the tested support boundary.

### The log reports full-scale input

That means the microphone signal is already reaching digital full scale before Loud Gate can limit it. Reduce the physical microphone/interface gain and check Windows enhancements or automatic gain control in the application using `CABLE Output`.

The log file is `%APPDATA%\loud-gate\loud_gate.log`.

## Implementation notes

Python is used because NumPy provides the preallocated float32 DSP workspaces and `sounddevice` exposes PortAudio's WASAPI full-duplex stream. The audio callback processes input directly into output without a user-space clock-bridging queue. `ctypes` provides the Windows global-hotkey and startup integration.

The generated VBS launcher is used for hidden background startup; Python is used for device setup, configuration, hotkeys, and audio processing.
