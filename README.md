# Utility Workbench

A curated collection of small Windows utilities, browser tools, and workflow experiments built from scratch. Each project is intentionally self-contained so it can be read, configured, and used independently.

## Projects

| Directory | What it is | Main technologies |
| --- | --- | --- |
| [`nvidia-display-profiles`](./nvidia-display-profiles) | Switches display modes, brightness, digital vibrance, monitor state, and game launch profiles. | PowerShell, Windows APIs, NVIDIA NVAPI |
| [`chatgpt-userscripts`](./chatgpt-userscripts) | Browser userscripts for ChatGPT queue workflows and crash recovery. | JavaScript, Tampermonkey |
| [`clipboard-log-cleaner`](./clipboard-log-cleaner) | Compacts verbose clipboard logs into a short message and source location. | PowerShell, VBScript |
| [`microphone-loud-gate`](./microphone-loud-gate) | Routes and limits microphone audio with a lookahead limiter and global mute hotkey. | Python, NumPy, sounddevice |
| [`youtube-ad-skipper`](./youtube-ad-skipper) | Chromium extension that detects YouTube ads, mutes playback, and performs trusted skip clicks. | JavaScript, Manifest V3 |
| [`streamdeck-launcher`](./streamdeck-launcher) | Configurable launcher for applications, URIs, folders, and companion scripts. | VBScript |
| [`wiztree-tree-formatter`](./wiztree-tree-formatter) | Converts a WizTree CSV export into a readable Markdown tree. | PowerShell |
| [`qmk-kbd75-via-socd`](./qmk-kbd75-via-socd) | Showcase exception: a personal VIA/SOCD keymap documented beside the upstream KBD75 context it depends on. | QMK, C |

The `qmk-kbd75-via-socd` directory is the only intentional provenance exception. Its README separates the personal `via_socd` contribution from the upstream board snapshot and community SOCD dependency.

## Configuration

Machine-specific settings belong in ignored local files. For `nvidia-display-profiles`, copy `config.example.ini` to `config.ini` beside `Set-DisplayProfile.ps1`; for `streamdeck-launcher`, copy `launcher.example.ini` to `launcher.ini` beside `launcher.vbs`. Loud Gate is the exception: its tracked `config.example.ini` documents the format, while the running program reads and writes `%APPDATA%\loud-gate\config.ini`. Relative paths are resolved from the project or launcher directory where practical.

## Safety and scope

These utilities can change display settings, audio routing, browser behavior, or launch elevated processes. Read the project README before use and review local configuration values before running anything.
