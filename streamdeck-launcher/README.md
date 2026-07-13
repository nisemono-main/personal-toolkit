# Streamdeck Launcher

A small VBScript launcher for starting applications, Steam URIs, folders, and companion scripts from a Stream Deck button, shortcut, or command line.

It works especially well as a front end for `nvidia-display-profiles`: one Stream Deck button can launch the interactive game profile, while another can restore the normal desktop profile after playing.

## Setup

1. Create `launcher.ini` beside `launcher.vbs`, using `launcher.example.ini` as the layout reference.
2. Set executable paths and optional relative companion-script paths in `launcher.ini`.
3. Bind commands such as `wscript.exe launcher.vbs steam` or `wscript.exe launcher.vbs explorer "C:\Some\Folder"` to shortcuts or buttons.

`launcher.vbs` loads only `launcher.ini` beside itself. The example is a tracked layout reference; create that exact sibling filename so the launcher uses your settings. A typical layout is:

```ini
steam_exe=%ProgramFiles(x86)%\Steam\steam.exe
spotify_exe=%APPDATA%\Spotify\Spotify.exe
vivaldi_exe=%ProgramFiles%\Vivaldi\Application\vivaldi.exe
game_profile_vbs=..\nvidia-display-profiles\Run-GameProfile.vbs
default_profile_vbs=..\nvidia-display-profiles\Run-DefaultProfileHidden.vbs
script_host_exe=%SystemRoot%\System32\wscript.exe
```

VBScript is used because `wscript.exe` can launch profiles, folders, URIs, and companion scripts without opening a command prompt; it is convenient for quiet Stream Deck buttons and background shortcuts. Executable paths can use Windows environment variables. Relative paths are resolved from the directory containing `launcher.vbs`, so the launcher remains portable when the repository is moved.

## Stream Deck examples

Create a **System → Open** or equivalent command action for `wscript.exe`, then pass the launcher script and profile name as arguments:

| Button purpose | Program | Arguments |
| --- | --- | --- |
| Launch a game display profile | `wscript.exe` | `launcher.vbs game` |
| Restore the desktop display profile | `wscript.exe` | `launcher.vbs default` |
| Open Steam | `wscript.exe` | `launcher.vbs steam` |
| Open a selected folder | `wscript.exe` | `launcher.vbs explorer "C:\Some\Folder"` |

The `game` and `default` entries use the relative paths in `launcher.ini` to call `Run-GameProfile.vbs` and `Run-DefaultProfileHidden.vbs` from `nvidia-display-profiles`. This keeps the two projects loosely coupled: the launcher owns the button-facing command, while the NVIDIA project owns display changes, monitor handling, elevation, and game selection. The relative paths continue to work after moving the repository as long as both project directories remain siblings.

The personal `launcher.ini` is intentionally ignored by Git. Keep the tracked `launcher.example.ini` layout current, but store machine-specific executable paths such as the local capture utility only in the ignored file.
