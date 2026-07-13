# Loud Gate

Loud Gate is a Windows microphone utility that routes a selected WASAPI input through a sample-accurate lookahead limiter into a selected WASAPI output. VB-CABLE remains the recommended application-routing target. It includes configurable global hotkeys, a setup/control window, independent background runtime control, and optional logon autorun.

## What it does

~~~text
selected input (Windows WASAPI input)
              │
              ▼
Loud Gate runtime: full-duplex stream + lookahead limiter
              │
              ▼
selected output (Windows WASAPI playback endpoint)
              │
              ▼
applications select the appropriate processed endpoint
~~~

The project supports and validates Windows WASAPI device pairs only. The manager shows all usable WASAPI input and output endpoints, preserves the configured selections, and validates the exact pair before saving. WDM-KS, DirectSound, MME, ASIO, and other audio backends are untested and used at your own risk.

## Requirements and audio assumptions

- Windows 10 or newer
- Python 3.11+ when running from source, or the published LoudGate.exe
- A physical microphone exposed through Windows WASAPI
- VB-AUDIO VB-CABLE from https://vb-audio.com/Cable/

Install the Windows VB-CABLE package from VB-Audio, then confirm that Windows exposes both CABLE Input (VB-Audio Virtual Cable) under playback and CABLE Output (VB-Audio Virtual Cable) under recording. Loud Gate assumes a 44,100 Hz route by default and selects a rate that the complete WASAPI pair can open; matching the physical microphone, CABLE Input, and CABLE Output Windows formats at 44,100 Hz is the safest setup. The selected input channel is zero-based.

If VB-CABLE endpoints are missing, Loud Gate warns and does not silently substitute an untested backend. The manager may still select another WASAPI output, but that routing is untested and at your own risk.

## Manager and runtime

The application is one executable with two process modes:

- LoudGate.exe with no arguments opens the manager.
- LoudGate.exe --runtime --background runs only the audio runtime.

The manager owns configuration, device discovery, status display, autorun, and Save/Run/Stop commands. The runtime owns the audio stream, limiter, hotkeys, health monitoring, and log. They communicate through a local authenticated Windows named pipe.

This separation is intentional: closing or reopening the manager only disconnects and reconnects the control window. It does not stop, restart, mute, or otherwise interrupt the background audio runtime. Use Stop or the configured stop hotkey when the audio process should actually stop.

The manager provides:

- explicit WASAPI input and output selection, including non-VB-CABLE outputs;
- input channel, limiter threshold, release, and lookahead fields;
- press-to-capture mute, stop, and threshold hotkeys;
- runtime state with CPU and latency beside it;
- Save, Run, and Stop controls;
- automatic device-list updates and callback-health details only when a problem exists;
- log opening in the Startup tab;
- Install autorun and Remove autorun controls.

## Quick start from source

From this directory:

~~~powershell
py -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.\\.venv\\Scripts\\python.exe .\\loud_gate.py
~~~

The manager discovers WASAPI endpoints in the background. Select the input and output explicitly, review the values and captured hotkeys, then use Save followed by Run. To apply changed settings to an active runtime, press Stop and then Run.

The runtime writes its log to:

~~~text
%APPDATA%\\loud-gate\\loud_gate.log
~~~

The manager reads the current status from the running runtime. It shows only Running or Stopped during normal use, with CPU load and measured stream latency beside the status. The indicator is red when stopped and green when running; startup transitions use orange.

## Configuration

The live configuration is exactly:

~~~text
%APPDATA%\\loud-gate\\config.ini
~~~

Use the manager to create or update it. config.example.ini is only a tracked layout reference; it is not loaded automatically. If you create the live file manually, use the same section and key layout shown below, fill in both selected device names and host APIs, then Stop and Run the runtime after saving.

~~~ini
[loud-gate]
version=6
input_device_index=17
input_device_name=Headset Microphone (example)
input_device_hostapi=Windows WASAPI
input_channel=0
output_device_index=16
output_device_name=CABLE Input (VB-Audio Virtual Cable)
output_device_hostapi=Windows WASAPI
sample_rate=44100
threshold_db=-18.0
release_ms=150.0
lookahead_ms=25.0

[hotkeys]
mute_hotkey=F13
stop_hotkey=Ctrl+Shift+F13
threshold_down_hotkey=F14
threshold_up_hotkey=Ctrl+F14
threshold_step_db=5.0
~~~

The device indexes are only hints; runtime resolution prefers the saved names and validates the current pair before opening it. input_channel is zero-based. Lower threshold_db limits peaks more aggressively, release_ms controls recovery, and lookahead_ms controls how far ahead peaks are inspected. Every hotkey combination must be unique and available to Windows. Configuration changes are pending until saved; apply them to a running runtime by stopping it and running it again.

## Windows autorun

Use Install autorun in the manager, or from a console:

~~~powershell
.\\.venv\\Scripts\\python.exe .\\loud_gate.py --install-startup
~~~

Autorun starts the runtime mode, not the manager. Each autorun session starts with a fresh log so old sessions cannot make the file grow indefinitely. The generated %APPDATA%\\loud-gate\\startup_launcher.vbs is used because Windows Script Host can launch the background process without opening a command window. It waits for the bounded startup attempt and displays a visible error dialog if the runtime cannot start, while the detailed phase and exception are written to the log. Removing autorun does not stop a runtime that is already running.

~~~powershell
.\\.venv\\Scripts\\python.exe .\\loud_gate.py --uninstall-startup
~~~

The scheduled task runs in the logged-in user's session because WASAPI endpoints and global hotkeys are session-sensitive. If the executable path changes, install autorun again so the generated launcher points at the current executable.

## Building and publishing LoudGate.exe

The repository workflow builds on windows-latest with PyInstaller. It always uploads the executable as a workflow artifact. A tag whose name starts with v also creates a GitHub Release and attaches the built executable:

~~~powershell
git tag v1.0.0
git push origin v1.0.0
~~~

The release asset is a single windowed LoudGate.exe; Python, NumPy, sounddevice, PortAudio, and the Tk manager are bundled into it. A local build uses the same inputs:

~~~powershell
.\\.venv\\Scripts\\python.exe -m pip install -r requirements-build.txt
.\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name LoudGate --hidden-import=loud_gate_app.manager --collect-all sounddevice .\\loud_gate.py
~~~

## Troubleshooting

### No suitable device pair is available

Confirm that the chosen input and output are visible as Windows WASAPI endpoints. Check Windows device format settings, matching 44,100 Hz formats, and whether another program has taken exclusive control. Other PortAudio backends are outside this project's tested boundary.

### The manager is red but the runtime was running

Red means that the manager cannot currently reach the runtime pipe. Reopen the manager or inspect the log. If the runtime process exited, use Run after correcting the reported startup phase. Closing the manager itself does not cause this condition.

### Audio is silent or distorted

Select CABLE Output as the microphone in the consuming application, keep the physical microphone gain below digital full scale, and ensure the input and VB-CABLE formats match. The runtime log records callback overflows, underflows, callback errors, and other actionable stream failures; expected near-full-scale input peaks are retained only as internal diagnostic counters.

### Global hotkey capture or registration fails

Click a hotkey field and press the actual combination; typed or pasted text is ignored. At runtime, another application may own the combination, or two Loud Gate fields may use the same combination. Choose unused bindings, save, then Stop and Run.

## Implementation notes

Python is used for device discovery, configuration, the Tk manager, and runtime orchestration. NumPy provides preallocated float32 DSP workspaces, and sounddevice exposes the PortAudio WASAPI full-duplex stream. The audio callback contains no user-space clock-bridging queue, which keeps the route direct and bounded. ctypes provides global-hotkey integration. VBS is used only for hidden logon launching and visible startup-failure reporting; it is not part of the audio path.
