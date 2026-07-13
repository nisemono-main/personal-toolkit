# Utility Workbench

A curated collection of small Windows utilities, browser tools, and workflow experiments built from scratch. Each project is intentionally self-contained so it can be read, configured, and used independently.

## Projects

| Directory | What it is | Main technologies |
| --- | --- | --- |
| [`nvidia-display-profiles`](./nvidia-display-profiles) | Applies display profiles that switch resolution, refresh rate, brightness, Digital Vibrance, monitor state, and game launch behavior. | PowerShell, Windows APIs, NVIDIA NVAPI |
| [`chatgpt-userscripts`](./chatgpt-userscripts) | Browser userscripts for ChatGPT queue workflows and crash recovery. | JavaScript, Tampermonkey |
| [`unity-log-compactor`](./unity-log-compactor) | Compacts verbose Unity exception stacks into readable message-and-source summaries. | PowerShell, VBScript |
| [`microphone-loud-gate`](./microphone-loud-gate) | Routes and limits microphone audio with a lookahead limiter and configurable global hotkeys. | Python, NumPy, sounddevice |
| [`youtube-ad-skipper`](./youtube-ad-skipper) | Chromium extension that detects YouTube ads, mutes playback, and performs trusted skip clicks. | JavaScript, Manifest V3 |
| [`streamdeck-launcher`](./streamdeck-launcher) | Configurable launcher for applications, URIs, folders, and companion scripts. | VBScript |
| [`wiztree-tree-formatter`](./wiztree-tree-formatter) | Converts a WizTree CSV export into a readable Markdown tree. | PowerShell |
| [`qmk-kbd75-via-socd`](./qmk-kbd75-via-socd) | Showcase exception: a personal VIA/SOCD keymap documented beside the upstream KBD75 context it depends on. | QMK, C |

The `qmk-kbd75-via-socd` directory is the only intentional provenance exception. Its README separates the personal `via_socd` contribution from the upstream board snapshot and community SOCD dependency.

## Configuration

Only configuration layouts and safe examples are tracked. Machine-specific values remain in ignored local files.

| Project | Create or use | Runtime lookup |
| --- | --- | --- |
| `nvidia-display-profiles` | Create `config.ini` beside `Set-DisplayProfile.ps1`, using `config.example.ini` as the layout reference. | The PowerShell script loads that exact sibling `config.ini`. |
| `streamdeck-launcher` | Create `launcher.ini` beside `launcher.vbs`, using `launcher.example.ini` as the layout reference. | The VBScript launcher loads that exact sibling `launcher.ini`. |
| `microphone-loud-gate` | Run the initial setup; it creates `%APPDATA%\loud-gate\config.ini`. | Loud Gate reads and updates the file in `%APPDATA%`; its repository example is documentation only. |

Relative paths in configuration are resolved from the relevant project or launcher directory where practical.

## Safety and scope

These utilities can change display settings, audio routing, browser behavior, or launch elevated processes. Read the project README before use and review local configuration values before running anything.
