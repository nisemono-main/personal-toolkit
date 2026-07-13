# Streamdeck Launcher

A small VBScript launcher for starting applications, Steam URIs, folders, and companion scripts from a Stream Deck button, shortcut, or command line.

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
