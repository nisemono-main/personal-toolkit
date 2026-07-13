Option Explicit

' Usage:
'   wscript.exe launcher.vbs steam
'   wscript.exe launcher.vbs losslessscaling
'   wscript.exe launcher.vbs spotify
'   wscript.exe launcher.vbs wcap
'   wscript.exe launcher.vbs vivaldi
'   wscript.exe launcher.vbs game
'   wscript.exe launcher.vbs default
'   wscript.exe launcher.vbs explorer "C:\random path\RandomFolder"
'
' Add or edit entries in launcher.ini as needed.

Const PROFILE_KIND_EXE = "exe"
Const PROFILE_KIND_URI = "uri"
Const PROFILE_KIND_SCRIPT = "script"
Const PROFILE_KIND_FOLDER = "folder"
Const PROFILE_KIND_DYNAMIC_FOLDER = "dynamic-folder"

Dim shell, fso, profiles, config, scriptFolder
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptFolder = fso.GetParentFolderName(WScript.ScriptFullName)
Set config = LoadConfig(fso.BuildPath(scriptFolder, "launcher.ini"))
Set profiles = BuildProfiles()

If WScript.Arguments.Count = 0 Then
    Fail 1, "Missing profile. Use: steam, losslessscaling, spotify, wcap, vivaldi, game, default, or explorer <folder>"
End If

Dim profileName
profileName = LCase(Trim(WScript.Arguments(0)))

If Not profiles.Exists(profileName) Then
    Fail 1, "Unknown profile: " & profileName & " (" & ListProfiles(profiles) & ")"
End If

RunProfile profiles(profileName)

Function BuildProfiles()
    Dim result
    Set result = CreateObject("Scripting.Dictionary")
    result.CompareMode = 1

    AddConfiguredExeProfile result, "steam", "steam_exe"
    AddUriProfile result, "losslessscaling", ConfigValue("losslessscaling_uri")
    AddConfiguredExeProfile result, "spotify", "spotify_exe"
    AddConfiguredExeProfile result, "wcap", "wcap_exe"
    AddConfiguredExeProfileWithArgs result, "vivaldi", "vivaldi_exe", "vivaldi_args"

    AddConfiguredScriptProfile result, "game", "game_profile_vbs"
    AddConfiguredScriptProfile result, "default", "default_profile_vbs"

    ' Dynamic folder profile: the remaining arguments form the folder path.
    AddDynamicFolderProfile result, "explorer"

    Set BuildProfiles = result
End Function

Sub AddConfiguredExeProfile(profiles, name, key)
    Dim target
    target = ConfigValue(key)
    If Len(Trim(target)) > 0 Then AddExeProfile profiles, name, target
End Sub

Sub AddConfiguredExeProfileWithArgs(profiles, name, pathKey, argsKey)
    Dim target
    target = ConfigValue(pathKey)
    If Len(Trim(target)) > 0 Then AddExeProfileWithArgs profiles, name, target, ConfigValue(argsKey)
End Sub

Sub AddConfiguredScriptProfile(profiles, name, key)
    Dim target
    target = ConfigValue(key)
    If Len(Trim(target)) > 0 Then AddScriptProfile profiles, name, target
End Sub

Function ConfigValue(key)
    If config.Exists(LCase(Trim(key))) Then
        ConfigValue = config(LCase(Trim(key)))
    Else
        ConfigValue = vbNullString
    End If
End Function

Function LoadConfig(configPath)
    Dim result, file, line, separator, key, value
    If Not fso.FileExists(configPath) Then
        Fail 1, "Missing local configuration: " & configPath & ". Create launcher.ini using launcher.example.ini as the layout reference, then set the values for this computer."
    End If

    Set result = CreateObject("Scripting.Dictionary")
    result.CompareMode = 1
    Set file = fso.OpenTextFile(configPath, 1, False)

    Do Until file.AtEndOfStream
        line = Trim(file.ReadLine)
        If Len(line) > 0 And Left(line, 1) <> ";" And Left(line, 1) <> "#" Then
            separator = InStr(line, "=")
            If separator > 1 Then
                key = LCase(Trim(Left(line, separator - 1)))
                value = Trim(Mid(line, separator + 1))
                If Len(key) > 0 Then result(key) = value
            End If
        End If
    Loop

    file.Close
    Set LoadConfig = result
End Function

Sub RunProfile(profile)
    Select Case LCase(profile("kind"))
        Case PROFILE_KIND_EXE
            LaunchExecutable profile("target"), profile("args")
        Case PROFILE_KIND_URI
            LaunchUri profile("target")
        Case PROFILE_KIND_SCRIPT
            LaunchScript profile("target"), profile("args")
        Case PROFILE_KIND_FOLDER
            OpenFolder profile("target")
        Case PROFILE_KIND_DYNAMIC_FOLDER
            If WScript.Arguments.Count < 2 Then
                Fail 1, "Missing folder path for explorer."
            End If
            OpenFolder JoinArguments(1)
        Case Else
            Fail 4, "Unsupported profile kind: " & profile("kind")
    End Select
End Sub

Sub AddExeProfile(profiles, name, exePath)
    AddExeProfileWithArgs profiles, name, exePath, vbNullString
End Sub

Sub AddExeProfileWithArgs(profiles, name, exePath, args)
    profiles.Add LCase(Trim(name)), CreateProfile(PROFILE_KIND_EXE, exePath, args)
End Sub

Sub AddUriProfile(profiles, name, uri)
    profiles.Add LCase(Trim(name)), CreateProfile(PROFILE_KIND_URI, uri, vbNullString)
End Sub

Sub AddScriptProfile(profiles, name, scriptPath)
    AddScriptProfileWithArgs profiles, name, scriptPath, vbNullString
End Sub

Sub AddScriptProfileWithArgs(profiles, name, scriptPath, args)
    profiles.Add LCase(Trim(name)), CreateProfile(PROFILE_KIND_SCRIPT, scriptPath, args)
End Sub

Sub AddFolderProfile(profiles, name, folderPath)
    profiles.Add LCase(Trim(name)), CreateProfile(PROFILE_KIND_FOLDER, folderPath, vbNullString)
End Sub

Sub AddDynamicFolderProfile(profiles, name)
    profiles.Add LCase(Trim(name)), CreateProfile(PROFILE_KIND_DYNAMIC_FOLDER, vbNullString, vbNullString)
End Sub

Function CreateProfile(kind, target, args)
    Dim profile
    Set profile = CreateObject("Scripting.Dictionary")
    profile.Add "kind", kind
    profile.Add "target", target
    profile.Add "args", args
    Set CreateProfile = profile
End Function

Sub LaunchExecutable(exePath, args)
    Dim resolvedExePath, workingDir
    resolvedExePath = ResolvePath(exePath)

    If Not fso.FileExists(resolvedExePath) Then
        Fail 2, "File not found: " & resolvedExePath
    End If

    workingDir = fso.GetParentFolderName(resolvedExePath)
    If Len(workingDir) > 0 And fso.FolderExists(workingDir) Then
        shell.CurrentDirectory = workingDir
    End If

    shell.Run BuildCommand(resolvedExePath, args), 1, False
End Sub

Sub LaunchUri(uri)
    shell.Run uri, 1, False
End Sub

Sub LaunchScript(scriptPath, args)
    Dim resolvedScriptPath, workingDir, scriptHostPath
    resolvedScriptPath = ResolvePath(scriptPath)

    If Not fso.FileExists(resolvedScriptPath) Then
        Fail 2, "File not found: " & resolvedScriptPath
    End If

    workingDir = fso.GetParentFolderName(resolvedScriptPath)
    If Len(workingDir) > 0 And fso.FolderExists(workingDir) Then
        shell.CurrentDirectory = workingDir
    End If

    scriptHostPath = ResolvePath(ConfigValue("script_host_exe"))
    If Not fso.FileExists(scriptHostPath) Then
        Fail 2, "File not found: " & scriptHostPath
    End If

    shell.Run BuildCommand(scriptHostPath, Quote(resolvedScriptPath) & AppendArguments(args)), 1, False
End Sub

Sub OpenFolder(folderPath)
    Dim resolvedPath, explorerPath
    resolvedPath = ResolvePath(folderPath)

    If Not fso.FolderExists(resolvedPath) Then
        Fail 3, "Folder not found: " & resolvedPath
    End If

    explorerPath = shell.ExpandEnvironmentStrings("%SystemRoot%\explorer.exe")
    shell.Run BuildCommand(explorerPath, Quote(resolvedPath)), 1, False
End Sub

Function BuildCommand(executablePath, arguments)
    Dim command
    command = Quote(executablePath)
    If Len(Trim(arguments)) > 0 Then
        command = command & " " & Trim(arguments)
    End If
    BuildCommand = command
End Function

Function ResolvePath(pathValue)
    Dim expandedPath
    expandedPath = shell.ExpandEnvironmentStrings(pathValue)

    If Not IsAbsolutePath(expandedPath) Then
        expandedPath = fso.BuildPath(scriptFolder, expandedPath)
    End If

    On Error Resume Next
    ResolvePath = fso.GetAbsolutePathName(expandedPath)
    If Err.Number <> 0 Then
        Err.Clear
        ResolvePath = expandedPath
    End If
    On Error GoTo 0
End Function

Function IsAbsolutePath(pathValue)
    IsAbsolutePath = (Len(pathValue) >= 2 And Mid(pathValue, 2, 1) = ":") Or _
        (Len(pathValue) >= 2 And Left(pathValue, 2) = "\\")
End Function

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Function AppendArguments(args)
    If Len(Trim(args)) = 0 Then
        AppendArguments = vbNullString
    Else
        AppendArguments = " " & Trim(args)
    End If
End Function

Function JoinArguments(startIndex)
    Dim i, joined
    joined = ""

    For i = startIndex To WScript.Arguments.Count - 1
        If Len(joined) > 0 Then
            joined = joined & " "
        End If
        joined = joined & WScript.Arguments(i)
    Next

    JoinArguments = joined
End Function

Function ListProfiles(profiles)
    ListProfiles = Join(profiles.Keys, ", ")
End Function

Sub Fail(exitCode, message)
    WScript.Echo message
    WScript.Quit exitCode
End Sub
