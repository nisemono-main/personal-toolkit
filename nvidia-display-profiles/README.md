# Auto NVIDIA Display Profiles

A Windows PowerShell utility for switching between configured display profiles. It changes resolution and refresh rate, applies gamma-based brightness and NVIDIA Digital Vibrance, manages monitor device state, and can launch a selected game.

## Why it exists

Switching between a desktop setup and game-specific display settings involved several manual steps. This utility combines those steps behind the `GAME` and `DEFAULT` profiles.

## Setup

1. Create `config.ini` beside `Set-DisplayProfile.ps1`, using `config.example.ini` as the layout reference.
2. Set the display profile values, optional monitor device instance ID, game launch commands, and process names in `config.ini`.
3. Run `Run-GameProfile.vbs` or `Run-DefaultProfileHidden.vbs` as appropriate.

`Set-DisplayProfile.ps1` loads only `config.ini` beside itself. The example is a tracked template; the copied `config.ini` is the file actually used and is ignored by Git. A minimal layout is:

```ini
[primary_monitor]
name=Primary monitor
instance_id=
required=false

[profile:default]
name=DEFAULT
width=1920
height=1080
refresh=60
brightness=50
vibrance=55
disable_monitor_devices=false
color=Cyan

[game:1]
name=My game
game_width=1920
game_height=1080
file_path=
uri=steam://rungameid/730
disable_monitor_devices=false
```

The PowerShell script is used because it can call Windows display, PnP, and NVIDIA APIs directly while keeping the profile logic and validation readable. The VBScript launchers resolve the PowerShell file relative to themselves and can request elevation without requiring a manually typed command window; the hidden default wrapper is suitable for background use. The optional batch wrapper keeps the game-profile flow visible while it runs.

This requires Windows PowerShell 5.1, an NVIDIA display driver for Digital Vibrance, and administrator rights for monitor device changes.
