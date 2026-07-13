#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('GAME', 'DEFAULT')]
    [string] $Mode
)

$ErrorActionPreference = 'Stop'
$Mode = $Mode.ToUpperInvariant()

function Import-IniFile {
    param([Parameter(Mandatory)] [string] $Path)

    $sections = @{}
    $sectionName = 'global'
    $sections[$sectionName] = @{}

    foreach ($line in Get-Content -LiteralPath $Path -ErrorAction Stop) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith(';') -or $trimmed.StartsWith('#')) {
            continue
        }

        if ($trimmed -match '^\[(?<name>[^\]]+)\]$') {
            $sectionName = $Matches.name.Trim().ToLowerInvariant()
            if (-not $sections.ContainsKey($sectionName)) {
                $sections[$sectionName] = @{}
            }
            continue
        }

        $separator = $trimmed.IndexOf('=')
        if ($separator -le 0) {
            throw "Invalid INI line in ${Path}: $line"
        }

        $key = $trimmed.Substring(0, $separator).Trim().ToLowerInvariant()
        $value = $trimmed.Substring($separator + 1).Trim()
        $sections[$sectionName][$key] = $value
    }

    return $sections
}

function Get-IniValue {
    param(
        [Parameter(Mandatory)] [hashtable] $Config,
        [Parameter(Mandatory)] [string] $Section,
        [Parameter(Mandatory)] [string] $Key,
        [string] $Default = $null
    )

    $sectionData = $Config[$Section.ToLowerInvariant()]
    if ($null -eq $sectionData -or -not $sectionData.ContainsKey($Key.ToLowerInvariant())) {
        return $Default
    }

    return [string]$sectionData[$Key.ToLowerInvariant()]
}

function ConvertTo-IniInt {
    param(
        [Parameter(Mandatory)] [hashtable] $Config,
        [Parameter(Mandatory)] [string] $Section,
        [Parameter(Mandatory)] [string] $Key
    )

    $value = Get-IniValue -Config $Config -Section $Section -Key $Key
    $parsed = 0
    if (-not [int]::TryParse($value, [ref]$parsed)) {
        throw "Expected an integer for [$Section] $Key in the local configuration."
    }

    return $parsed
}

function ConvertTo-IniBool {
    param(
        [Parameter(Mandatory)] [hashtable] $Config,
        [Parameter(Mandatory)] [string] $Section,
        [Parameter(Mandatory)] [string] $Key
    )

    $value = Get-IniValue -Config $Config -Section $Section -Key $Key
    $parsed = $false
    if (-not [bool]::TryParse($value, [ref]$parsed)) {
        throw "Expected true or false for [$Section] $Key in the local configuration."
    }

    return $parsed
}

$configPath = Join-Path $PSScriptRoot 'config.ini'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Missing local configuration: $configPath. Create config.ini using config.example.ini as the layout reference, then set the values for this computer."
}

$config = Import-IniFile -Path $configPath
$primarySection = 'primary_monitor'

if (-not $config.ContainsKey($primarySection)) {
    throw "Configuration is incomplete. [$primarySection] is required in $configPath."
}

$PrimaryMonitorDevice = [pscustomobject]@{
    Name = Get-IniValue -Config $config -Section $primarySection -Key 'name' -Default 'Primary monitor'
    InstanceId = Get-IniValue -Config $config -Section $primarySection -Key 'instance_id' -Default ''
    Required = ConvertTo-IniBool -Config $config -Section $primarySection -Key 'required'
}

$Profiles = @{}
foreach ($sectionName in @($config.Keys | Where-Object { $_ -like 'profile:*' })) {
    $profileName = $sectionName.Substring('profile:'.Length).ToUpperInvariant()
    $Profiles[$profileName] = [pscustomobject]@{
        Name = Get-IniValue -Config $config -Section $sectionName -Key 'name' -Default $profileName
        Width = ConvertTo-IniInt -Config $config -Section $sectionName -Key 'width'
        Height = ConvertTo-IniInt -Config $config -Section $sectionName -Key 'height'
        Refresh = ConvertTo-IniInt -Config $config -Section $sectionName -Key 'refresh'
        Brightness = ConvertTo-IniInt -Config $config -Section $sectionName -Key 'brightness'
        Vibrance = ConvertTo-IniInt -Config $config -Section $sectionName -Key 'vibrance'
        DisableMonitorDevices = ConvertTo-IniBool -Config $config -Section $sectionName -Key 'disable_monitor_devices'
        Color = Get-IniValue -Config $config -Section $sectionName -Key 'color' -Default 'White'
    }
}

$LaunchableGames = @()
foreach ($sectionName in @($config.Keys | Where-Object { $_ -like 'game:*' } | Sort-Object)) {
    $argumentKeys = @(
        $config[$sectionName].Keys |
            Where-Object { $_ -like 'argument_*' } |
            Sort-Object { [int]($_ -replace '^argument_', '') }
    )
    $argumentList = @($argumentKeys | ForEach-Object { [string]$config[$sectionName][$_] })
    $LaunchableGames += [pscustomobject]@{
        Name = Get-IniValue -Config $config -Section $sectionName -Key 'name'
        GameWidth = ConvertTo-IniInt -Config $config -Section $sectionName -Key 'game_width'
        GameHeight = ConvertTo-IniInt -Config $config -Section $sectionName -Key 'game_height'
        FilePath = Get-IniValue -Config $config -Section $sectionName -Key 'file_path' -Default ''
        ArgumentList = $argumentList
        Uri = Get-IniValue -Config $config -Section $sectionName -Key 'uri' -Default ''
        SnapTapMessage = Get-IniValue -Config $config -Section $sectionName -Key 'snap_tap_message' -Default ''
        DisableMonitorDevices = ConvertTo-IniBool -Config $config -Section $sectionName -Key 'disable_monitor_devices'
    }
}

$processNames = Get-IniValue -Config $config -Section 'games' -Key 'process_names' -Default ''
$GameProcessNames = @($processNames -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

if ($Profiles.Count -eq 0 -or $LaunchableGames.Count -eq 0) {
    throw "Configuration must include at least one [profile:*] section and one [game:*] section in $configPath."
}

if (-not $Profiles.ContainsKey($Mode)) {
    throw "Mode '$Mode' is not configured in $configPath."
}

if (-not [Environment]::Is64BitProcess) {
    throw 'Run this script from 64-bit PowerShell. nvapi64.dll cannot be loaded by a 32-bit PowerShell process.'
}

Add-Type -AssemblyName System.Windows.Forms

if (-not ('UnifiedDisplayNative' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class UnifiedDisplayNative {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    public struct DEVMODE {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string dmDeviceName;
        public short dmSpecVersion;
        public short dmDriverVersion;
        public short dmSize;
        public short dmDriverExtra;
        public int dmFields;
        public int dmPositionX;
        public int dmPositionY;
        public int dmDisplayOrientation;
        public int dmDisplayFixedOutput;
        public short dmColor;
        public short dmDuplex;
        public short dmYResolution;
        public short dmTTOption;
        public short dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string dmFormName;
        public short dmLogPixels;
        public int dmBitsPerPel;
        public int dmPelsWidth;
        public int dmPelsHeight;
        public int dmDisplayFlags;
        public int dmDisplayFrequency;
        public int dmICMMethod;
        public int dmICMIntent;
        public int dmMediaType;
        public int dmDitherType;
        public int dmReserved1;
        public int dmReserved2;
        public int dmPanningWidth;
        public int dmPanningHeight;
    }

    [DllImport("user32.dll", CharSet = CharSet.Ansi)]
    public static extern bool EnumDisplaySettings(
        string lpszDeviceName,
        int iModeNum,
        ref DEVMODE lpDevMode);

    [DllImport("user32.dll", CharSet = CharSet.Ansi)]
    public static extern int ChangeDisplaySettingsEx(
        string lpszDeviceName,
        ref DEVMODE lpDevMode,
        IntPtr hwnd,
        uint dwflags,
        IntPtr lParam);

    [DllImport("gdi32.dll", CharSet = CharSet.Ansi)]
    public static extern IntPtr CreateDC(
        string lpszDriver,
        string lpszDevice,
        string lpszOutput,
        IntPtr lpInitData);

    [DllImport("gdi32.dll")]
    public static extern bool DeleteDC(IntPtr hdc);

    [DllImport("gdi32.dll")]
    public static extern bool SetDeviceGammaRamp(IntPtr hdc, ushort[] lpRamp);

    public const int ENUM_CURRENT_SETTINGS = -1;
    public const int DM_PELSWIDTH = 0x00080000;
    public const int DM_PELSHEIGHT = 0x00100000;
    public const int DM_DISPLAYFREQUENCY = 0x00400000;
    public const int CDS_UPDATEREGISTRY = 0x00000001;
    public const int CDS_TEST = 0x00000002;
    public const int DISP_CHANGE_SUCCESSFUL = 0;
}
"@
}

if (-not ('UnifiedNvApi' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class UnifiedNvApi {
    const uint NVAPI_INITIALIZE = 0x0150E828;
    const uint NVAPI_GET_ERROR_MESSAGE = 0x6C2D048C;
    const uint NVAPI_GET_ASSOCIATED_NVIDIA_DISPLAY_HANDLE = 0x35C29134;
    const uint NVAPI_GET_ASSOCIATED_DISPLAY_OUTPUT_ID = 0xD995937E;
    const uint NVAPI_GET_DVC_INFO_EX = 0x0E45002D;
    const uint NVAPI_SET_DVC_LEVEL_EX = 0x4A82C2B1;

    const int NVAPI_OK = 0;

    public static string LastError = "";

    [DllImport("nvapi64.dll", CallingConvention = CallingConvention.Cdecl)]
    static extern IntPtr nvapi_QueryInterface(uint id);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int NvAPI_Initialize_t();

    [UnmanagedFunctionPointer(CallingConvention.Cdecl, CharSet = CharSet.Ansi)]
    delegate int NvAPI_GetErrorMessage_t(int nr, StringBuilder szDesc);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl, CharSet = CharSet.Ansi)]
    delegate int NvAPI_GetAssociatedNvidiaDisplayHandle_t(string szDisplayName, ref IntPtr pNvDispHandle);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int NvAPI_GetAssociatedDisplayOutputId_t(IntPtr hNvDisplay, ref uint pOutputId);

    [StructLayout(LayoutKind.Sequential)]
    public struct NV_DISPLAY_DVC_INFO_EX {
        public uint version;
        public int currentLevel;
        public int minLevel;
        public int maxLevel;
        public int defaultLevel;
    }

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int NvAPI_GetDVCInfoEx_t(IntPtr hNvDisplay, uint outputId, ref NV_DISPLAY_DVC_INFO_EX pDVCInfo);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int NvAPI_SetDVCLevelEx_t(IntPtr hNvDisplay, uint outputId, ref NV_DISPLAY_DVC_INFO_EX pDVCInfo);

    static T GetDelegate<T>(uint id) where T : class {
        IntPtr ptr = nvapi_QueryInterface(id);
        if (ptr == IntPtr.Zero) {
            throw new Exception("NvAPI function 0x" + id.ToString("X8") + " was not found.");
        }

        return (T)(object)Marshal.GetDelegateForFunctionPointer(ptr, typeof(T));
    }

    static string StatusText(int status) {
        try {
            var getError = GetDelegate<NvAPI_GetErrorMessage_t>(NVAPI_GET_ERROR_MESSAGE);
            var buffer = new StringBuilder(64);
            if (getError(status, buffer) == NVAPI_OK) {
                return buffer.ToString();
            }
        } catch {
        }

        return "NvAPI status " + status;
    }

    static bool Fail(string operation, int status) {
        LastError = operation + " failed (" + StatusText(status) + ").";
        return false;
    }

    public static bool SetDigitalVibranceForDisplay(string windowsDisplayName, int level0To100) {
        LastError = "";

        if (String.IsNullOrWhiteSpace(windowsDisplayName)) {
            LastError = "Windows display name is empty.";
            return false;
        }

        if (level0To100 < 0 || level0To100 > 100) {
            LastError = "Digital Vibrance must be between 0 and 100.";
            return false;
        }

        try {
            var initialize = GetDelegate<NvAPI_Initialize_t>(NVAPI_INITIALIZE);
            int status = initialize();
            if (status != NVAPI_OK) {
                return Fail("NvAPI_Initialize", status);
            }

            var getHandle = GetDelegate<NvAPI_GetAssociatedNvidiaDisplayHandle_t>(NVAPI_GET_ASSOCIATED_NVIDIA_DISPLAY_HANDLE);
            IntPtr displayHandle = IntPtr.Zero;
            status = getHandle(windowsDisplayName, ref displayHandle);
            if (status != NVAPI_OK || displayHandle == IntPtr.Zero) {
                return Fail("NvAPI_GetAssociatedNvidiaDisplayHandle for " + windowsDisplayName, status);
            }

            var getOutputId = GetDelegate<NvAPI_GetAssociatedDisplayOutputId_t>(NVAPI_GET_ASSOCIATED_DISPLAY_OUTPUT_ID);
            uint outputId = 0;
            status = getOutputId(displayHandle, ref outputId);
            if (status != NVAPI_OK || outputId == 0) {
                return Fail("NvAPI_GetAssociatedDisplayOutputId for " + windowsDisplayName, status);
            }

            var getInfo = GetDelegate<NvAPI_GetDVCInfoEx_t>(NVAPI_GET_DVC_INFO_EX);
            var info = new NV_DISPLAY_DVC_INFO_EX();
            info.version = (uint)(Marshal.SizeOf(typeof(NV_DISPLAY_DVC_INFO_EX)) | (1 << 16));

            status = getInfo(displayHandle, outputId, ref info);
            if (status != NVAPI_OK) {
                return Fail("NvAPI_GetDVCInfoEx", status);
            }

            int mappedLevel = info.minLevel + (int)Math.Round((level0To100 / 100.0) * (info.maxLevel - info.minLevel), MidpointRounding.AwayFromZero);
            mappedLevel = Math.Max(info.minLevel, Math.Min(info.maxLevel, mappedLevel));
            info.currentLevel = mappedLevel;

            var setLevel = GetDelegate<NvAPI_SetDVCLevelEx_t>(NVAPI_SET_DVC_LEVEL_EX);
            status = setLevel(displayHandle, outputId, ref info);
            if (status != NVAPI_OK) {
                return Fail("NvAPI_SetDVCLevelEx", status);
            }

            return true;
        } catch (DllNotFoundException ex) {
            LastError = "Could not load nvapi64.dll. Install or repair the NVIDIA display driver. " + ex.Message;
            return false;
        } catch (Exception ex) {
            LastError = ex.Message;
            return false;
        }
    }
}
"@
}

function Write-Status {
    param(
        [Parameter(Mandatory)] [string] $Message,
        [ConsoleColor] $Color = 'Green'
    )

    Write-Host "  [OK] $Message" -ForegroundColor $Color
}

function Write-Section {
    param(
        [Parameter(Mandatory)] [string] $Title,
        [ConsoleColor] $Color = 'White'
    )

    Write-Host ''
    Write-Host $Title -ForegroundColor $Color
}

function Write-Detail {
    param(
        [Parameter(Mandatory)] [string] $Label,
        [Parameter(Mandatory)] [string] $Value,
        [ConsoleColor] $ValueColor = 'White'
    )

    Write-Host ("  {0,-10} " -f "${Label}:") -ForegroundColor DarkGray -NoNewline
    Write-Host $Value -ForegroundColor $ValueColor
}

function New-DevMode {
    $modeInfo = New-Object UnifiedDisplayNative+DEVMODE
    $modeInfo.dmSize = [System.Runtime.InteropServices.Marshal]::SizeOf($modeInfo)
    return $modeInfo
}

function Get-DisplayChangeMessage {
    param([int] $Code)

    switch ($Code) {
        0 { return 'DISP_CHANGE_SUCCESSFUL' }
        1 { return 'DISP_CHANGE_RESTART: restart required' }
        -1 { return 'DISP_CHANGE_FAILED' }
        -2 { return 'DISP_CHANGE_BADMODE: unsupported mode' }
        -3 { return 'DISP_CHANGE_NOTUPDATED: registry update failed' }
        -4 { return 'DISP_CHANGE_BADFLAGS' }
        -5 { return 'DISP_CHANGE_BADPARAM' }
        -6 { return 'DISP_CHANGE_BADDUALVIEW' }
        default { return "Unknown display change result $Code" }
    }
}

function Get-CurrentDisplayMode {
    param([Parameter(Mandatory)] [string] $DeviceName)

    $modeInfo = New-DevMode
    if (-not [UnifiedDisplayNative]::EnumDisplaySettings($DeviceName, [UnifiedDisplayNative]::ENUM_CURRENT_SETTINGS, [ref] $modeInfo)) {
        throw "Could not query current display settings for $DeviceName."
    }

    return $modeInfo
}

function Format-DisplayMode {
    param([Parameter(Mandatory)] $ModeInfo)

    return "$($ModeInfo.dmPelsWidth)x$($ModeInfo.dmPelsHeight) @ $($ModeInfo.dmDisplayFrequency)Hz"
}

function Test-DisplayModeMatch {
    param(
        [Parameter(Mandatory)] $ModeInfo,
        [Parameter(Mandatory)] [int] $Width,
        [Parameter(Mandatory)] [int] $Height,
        [Parameter(Mandatory)] [int] $Refresh
    )

    return (
        $ModeInfo.dmPelsWidth -eq $Width -and
        $ModeInfo.dmPelsHeight -eq $Height -and
        $ModeInfo.dmDisplayFrequency -eq $Refresh
    )
}

function Get-PrimaryDisplayTarget {
    $screens = @([System.Windows.Forms.Screen]::AllScreens)
    $primaryScreen = $screens | Where-Object { $_.Primary } | Select-Object -First 1

    if ($null -eq $primaryScreen) {
        $inventory = @($screens | ForEach-Object {
            "  - $($_.DeviceName) primary=$($_.Primary) bounds=$($_.Bounds)"
        })

        if ($inventory.Count -eq 0) {
            $inventory = @('  - <Screen.AllScreens returned no displays>')
        }

        throw "Could not find a primary display. Windows reported:$([Environment]::NewLine)$($inventory -join [Environment]::NewLine)"
    }

    $modeInfo = Get-CurrentDisplayMode -DeviceName $primaryScreen.DeviceName

    return [pscustomobject]@{
        DisplayName = $primaryScreen.DeviceName
        Width = $modeInfo.dmPelsWidth
        Height = $modeInfo.dmPelsHeight
        Refresh = $modeInfo.dmDisplayFrequency
    }
}

function Set-DisplayMode {
    param(
        [Parameter(Mandatory)] [string] $DeviceName,
        [Parameter(Mandatory)] [int] $Width,
        [Parameter(Mandatory)] [int] $Height,
        [Parameter(Mandatory)] [int] $Refresh
    )

    $modeInfo = Get-CurrentDisplayMode -DeviceName $DeviceName
    if (Test-DisplayModeMatch -ModeInfo $modeInfo -Width $Width -Height $Height -Refresh $Refresh) {
        Write-Status "Resolution already set to $(Format-DisplayMode $modeInfo)"
        return
    }

    $modeInfo.dmPelsWidth = $Width
    $modeInfo.dmPelsHeight = $Height
    $modeInfo.dmDisplayFrequency = $Refresh
    $modeInfo.dmFields = [UnifiedDisplayNative]::DM_PELSWIDTH -bor [UnifiedDisplayNative]::DM_PELSHEIGHT -bor [UnifiedDisplayNative]::DM_DISPLAYFREQUENCY

    $testResult = [UnifiedDisplayNative]::ChangeDisplaySettingsEx($DeviceName, [ref] $modeInfo, [IntPtr]::Zero, [UnifiedDisplayNative]::CDS_TEST, [IntPtr]::Zero)
    if ($testResult -ne [UnifiedDisplayNative]::DISP_CHANGE_SUCCESSFUL) {
        throw "Windows rejected $($Width)x$($Height) @ $($Refresh)Hz for $DeviceName. Result: $(Get-DisplayChangeMessage $testResult)."
    }

    $applyResult = [UnifiedDisplayNative]::ChangeDisplaySettingsEx($DeviceName, [ref] $modeInfo, [IntPtr]::Zero, [UnifiedDisplayNative]::CDS_UPDATEREGISTRY, [IntPtr]::Zero)
    if ($applyResult -ne [UnifiedDisplayNative]::DISP_CHANGE_SUCCESSFUL) {
        throw "Windows failed to apply $($Width)x$($Height) @ $($Refresh)Hz for $DeviceName. Result: $(Get-DisplayChangeMessage $applyResult)."
    }

    Start-Sleep -Milliseconds 750

    $after = Get-CurrentDisplayMode -DeviceName $DeviceName
    if (-not (Test-DisplayModeMatch -ModeInfo $after -Width $Width -Height $Height -Refresh $Refresh)) {
        throw "Windows accepted the mode change, but verification reported $(Format-DisplayMode $after) instead of $($Width)x$($Height) @ $($Refresh)Hz."
    }

    Write-Status "Resolution changed to $(Format-DisplayMode $after)"
}

function Set-DisplayBrightness {
    param(
        [Parameter(Mandatory)] [string] $DeviceName,
        [Parameter(Mandatory)] [ValidateRange(0, 100)] [int] $Level
    )

    $brightnessValue = [int] [Math]::Round(($Level / 100.0) * 255.0, [MidpointRounding]::AwayFromZero)
    $ramp = New-Object 'System.UInt16[]' 768

    for ($i = 0; $i -lt 256; $i++) {
        $value = $i * ($brightnessValue + 128)
        if ($value -gt 65535) {
            $value = 65535
        }

        $word = [uint16] $value
        $ramp[$i] = $word
        $ramp[$i + 256] = $word
        $ramp[$i + 512] = $word
    }

    $deviceContext = [UnifiedDisplayNative]::CreateDC('DISPLAY', $DeviceName, $null, [IntPtr]::Zero)
    if ($deviceContext -eq [IntPtr]::Zero) {
        throw "Could not create a display device context for $DeviceName."
    }

    try {
        if (-not [UnifiedDisplayNative]::SetDeviceGammaRamp($deviceContext, $ramp)) {
            throw "Windows failed to apply brightness $Level% to $DeviceName."
        }
    } finally {
        $null = [UnifiedDisplayNative]::DeleteDC($deviceContext)
    }

    Write-Status "Brightness set to $Level%"
}

function Set-DigitalVibrance {
    param(
        [Parameter(Mandatory)] [string] $DisplayName,
        [Parameter(Mandatory)] [int] $Level
    )

    if (-not [UnifiedNvApi]::SetDigitalVibranceForDisplay($DisplayName, $Level)) {
        throw [UnifiedNvApi]::LastError
    }

    Write-Status "Digital Vibrance set to $Level%"
}

function Get-MonitorDeviceState {
    param([Parameter(Mandatory)] [object[]] $Devices)

    foreach ($deviceSpec in $Devices) {
        $id = [string] $deviceSpec.InstanceId
        $name = [string] $deviceSpec.Name
        $required = [bool] $deviceSpec.Required

        try {
            $device = Get-PnpDevice -InstanceId $id -ErrorAction Stop |
                Select-Object Status, FriendlyName, InstanceId, Problem, ConfigManagerErrorCode

            if ((-not $required) -and (Test-MonitorDevicePhantom -Device $device)) {
                Write-Status "Optional monitor not connected: $name [$id]" DarkGray
                continue
            }

            $device | Add-Member -NotePropertyName MonitorName -NotePropertyValue $name -Force
            $device | Add-Member -NotePropertyName Required -NotePropertyValue $required -Force
            $device
        } catch {
            if ($required) {
                throw "Required monitor device was not found: $name [$id]. $($_.Exception.Message)"
            }

            Write-Status "Optional monitor not present: $name [$id]" DarkGray
        }
    }
}

function Test-MonitorDeviceEnabled {
    param([Parameter(Mandatory)] $Device)

    return (
        [string] $Device.Status -eq 'OK' -and
        [string] $Device.Problem -eq 'CM_PROB_NONE' -and
        [string] $Device.ConfigManagerErrorCode -eq 'CM_PROB_NONE'
    )
}

function Test-MonitorDeviceDisabled {
    param([Parameter(Mandatory)] $Device)

    return (
        [string] $Device.Problem -eq 'CM_PROB_DISABLED' -or
        [string] $Device.ConfigManagerErrorCode -eq 'CM_PROB_DISABLED'
    )
}

function Test-MonitorDevicePhantom {
    param([Parameter(Mandatory)] $Device)

    return (
        [string] $Device.Problem -eq 'CM_PROB_PHANTOM' -or
        [string] $Device.ConfigManagerErrorCode -eq 'CM_PROB_PHANTOM'
    )
}

function Get-SecondaryMonitorDeviceSpecs {
    param([Parameter(Mandatory)] [string] $PrimaryInstanceId)

    $getPnpDevice = Get-Command Get-PnpDevice -ErrorAction SilentlyContinue
    if ($null -eq $getPnpDevice) {
        throw 'Required cmdlet is not available: Get-PnpDevice.'
    }

    foreach ($device in @(Get-PnpDevice -Class Monitor -ErrorAction Stop)) {
        if ([string] $device.InstanceId -eq $PrimaryInstanceId) {
            continue
        }

        if ([string] $device.InstanceId -like 'DISPLAY\DEFAULT_MONITOR*') {
            continue
        }

        $name = [string] $device.FriendlyName
        if ([string]::IsNullOrWhiteSpace($name)) {
            $name = [string] $device.InstanceId
        }

        [pscustomobject]@{
            Name = $name
            InstanceId = [string] $device.InstanceId
            Required = $false
        }
    }
}

function Get-ManagedMonitorDeviceSpecs {
    param([switch] $IncludePrimary)

    if ([string]::IsNullOrWhiteSpace($PrimaryMonitorDevice.InstanceId)) {
        return @()
    }

    $devices = @()

    if ($IncludePrimary) {
        $devices += $PrimaryMonitorDevice
    }

    $devices += @(Get-SecondaryMonitorDeviceSpecs -PrimaryInstanceId $PrimaryMonitorDevice.InstanceId)
    return $devices
}

function Format-MonitorDeviceState {
    param([Parameter(Mandatory)] $Device)

    return "$($Device.MonitorName) / $($Device.FriendlyName) [$($Device.InstanceId)] status=$($Device.Status) problem=$($Device.Problem) code=$($Device.ConfigManagerErrorCode)"
}

function Set-MonitorDevicesEnabled {
    param(
        [Parameter(Mandatory)] [object[]] $Devices,
        [Parameter(Mandatory)] [bool] $Enabled
    )

    foreach ($commandName in @('Get-PnpDevice', 'Enable-PnpDevice', 'Disable-PnpDevice')) {
        if ($null -eq (Get-Command $commandName -ErrorAction SilentlyContinue)) {
            throw "Required cmdlet is not available: $commandName."
        }
    }

    $principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Monitor device enable/disable requires an elevated PowerShell session.'
    }

    $changed = $false
    $targetText = if ($Enabled) { 'enabled' } else { 'disabled' }

    $deviceStates = @(Get-MonitorDeviceState -Devices $Devices)
    if ($deviceStates.Count -eq 0) {
        Write-Status "No monitor devices need to be $targetText"
        return
    }

    foreach ($device in $deviceStates) {
        if ($Enabled) {
            if (Test-MonitorDeviceEnabled -Device $device) {
                Write-Status "Monitor already enabled: $($device.MonitorName)"
                continue
            }

            Enable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction Stop
            $changed = $true
        } else {
            if (Test-MonitorDeviceDisabled -Device $device) {
                Write-Status "Monitor already disabled: $($device.MonitorName)"
                continue
            }

            Disable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction Stop
            $changed = $true
        }
    }

    if ($changed) {
        Start-Sleep -Seconds 2
    }

    $states = @(Get-MonitorDeviceState -Devices $Devices)
    $badStates = @($states | Where-Object {
        if ($Enabled) {
            -not (Test-MonitorDeviceEnabled -Device $_)
        } else {
            -not (Test-MonitorDeviceDisabled -Device $_)
        }
    })

    if ($badStates.Count -gt 0) {
        $details = @($badStates | ForEach-Object { Format-MonitorDeviceState -Device $_ })
        throw "One or more monitor devices did not become $targetText`: $([Environment]::NewLine)$($details -join [Environment]::NewLine)"
    }

    Write-Status "Monitor devices are $targetText"
}

function Stop-ConfiguredGames {
    param([Parameter(Mandatory)] [string[]] $ProcessNames)

    $stopped = New-Object System.Collections.Generic.List[string]
    $stoppedIds = New-Object System.Collections.Generic.List[int]

    foreach ($processName in $ProcessNames) {
        $processes = @(Get-Process -Name $processName -ErrorAction SilentlyContinue)
        foreach ($process in $processes) {
            $stoppedIds.Add($process.Id) | Out-Null
            Stop-Process -Id $process.Id -Force -ErrorAction Stop
            $stopped.Add("$($process.ProcessName).exe") | Out-Null
        }
    }

    if ($stopped.Count -eq 0) {
        Write-Status 'No configured game processes were running'
        return
    }

    foreach ($processId in $stoppedIds) {
        try {
            Wait-Process -Id $processId -Timeout 5 -ErrorAction SilentlyContinue
        } catch {
        }
    }

    $uniqueNames = @($stopped | Sort-Object -Unique)
    Write-Status "Stopped game processes: $($uniqueNames -join ', ')"
}

function Write-GameOption {
    param(
        [Parameter(Mandatory)] [int] $Index,
        [Parameter(Mandatory)] $Game
    )

    Write-Host ("  {0}. " -f $Index) -ForegroundColor DarkGray -NoNewline

    switch ($Game.Name) {
        'VALORANT' {
            Write-Host $Game.Name -ForegroundColor Red
        }
        'Counter-Strike 2' {
            Write-Host 'Counter-' -ForegroundColor DarkYellow -NoNewline
            Write-Host 'Strike' -ForegroundColor Blue -NoNewline
            Write-Host ' 2' -ForegroundColor White
        }
        'Marvel Rivals' {
            Write-Host $Game.Name -ForegroundColor Yellow
        }
        default {
            Write-Host $Game.Name -ForegroundColor White
        }
    }
}

function Invoke-GameSelection {
    param([Parameter(Mandatory)] [object[]] $Games)

    Write-Host '  Select game to launch:' -ForegroundColor White
    for ($i = 0; $i -lt $Games.Count; $i++) {
        Write-GameOption -Index ($i + 1) -Game $Games[$i]
    }
    Write-Host '  x. Abort' -ForegroundColor DarkGray

    while ($true) {
        $rawChoice = Read-Host 'Choice'
        if ($rawChoice.Trim().Equals('x', [StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }

        $choice = 0

        if (
            [int]::TryParse($rawChoice, [ref] $choice) -and
            $choice -ge 1 -and
            $choice -le $Games.Count
        ) {
            return $Games[$choice - 1]
        }

        Write-Host "Enter a number from 1 to $($Games.Count), or x to abort." -ForegroundColor Yellow
    }
}

function Start-SelectedGame {
    param([Parameter(Mandatory)] $Game)

    Write-Host ''
    Write-Host "  $($Game.SnapTapMessage)" -ForegroundColor Yellow
    $null = Read-Host '  Press Enter to launch'

    if (-not [string]::IsNullOrWhiteSpace($Game.FilePath)) {
        if (-not (Test-Path -LiteralPath $Game.FilePath -PathType Leaf)) {
            throw "Game launcher was not found: $($Game.FilePath)"
        }

        Start-Process -FilePath $Game.FilePath -ArgumentList $Game.ArgumentList
        Write-Status "Launch requested: $($Game.Name)"
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($Game.Uri)) {
        Start-Process -FilePath $Game.Uri
        Write-Status "Launch requested: $($Game.Name)"
        return
    }

    throw "No launch command is configured for $($Game.Name)."
}

function Set-ProfileState {
    param([Parameter(Mandatory)] $DisplayProfile)

    Write-Host '========================================' -ForegroundColor DarkGray
    Write-Host " Display Profile: $($DisplayProfile.Name)" -ForegroundColor $DisplayProfile.Color
    Write-Host '========================================' -ForegroundColor DarkGray
    Write-Detail -Label 'Target' -Value "$($DisplayProfile.Width)x$($DisplayProfile.Height) @ $($DisplayProfile.Refresh)Hz" -ValueColor $DisplayProfile.Color
    Write-Detail -Label 'Brightness' -Value "$($DisplayProfile.Brightness)%" -ValueColor $DisplayProfile.Color
    Write-Detail -Label 'Vibrance' -Value "$($DisplayProfile.Vibrance)%" -ValueColor $DisplayProfile.Color

    if ($DisplayProfile.Name -eq 'DEFAULT') {
        Write-Section -Title 'Game Processes' -Color DarkCyan
        Stop-ConfiguredGames -ProcessNames $GameProcessNames

        Write-Section -Title 'Monitor Devices' -Color DarkCyan
        Set-MonitorDevicesEnabled -Devices @(Get-ManagedMonitorDeviceSpecs -IncludePrimary) -Enabled $true
    }

    Write-Section -Title 'Display' -Color DarkCyan
    $primaryTarget = Get-PrimaryDisplayTarget
    Write-Detail -Label 'Primary' -Value "$($primaryTarget.DisplayName) [$($primaryTarget.Width)x$($primaryTarget.Height) @ $($primaryTarget.Refresh)Hz]" -ValueColor White

    Set-DisplayMode -DeviceName $primaryTarget.DisplayName -Width $DisplayProfile.Width -Height $DisplayProfile.Height -Refresh $DisplayProfile.Refresh

    $primaryTarget = Get-PrimaryDisplayTarget
    Set-DisplayBrightness -DeviceName $primaryTarget.DisplayName -Level $DisplayProfile.Brightness
    Set-DigitalVibrance -DisplayName $primaryTarget.DisplayName -Level $DisplayProfile.Vibrance

    if ($DisplayProfile.Name -eq 'GAME' -and $DisplayProfile.DisableMonitorDevices) {
        Write-Section -Title 'Monitor Devices' -Color DarkCyan
        Set-MonitorDevicesEnabled -Devices @(Get-ManagedMonitorDeviceSpecs) -Enabled $false
    } elseif ($DisplayProfile.Name -eq 'GAME') {
        Write-Section -Title 'Monitor Devices' -Color DarkCyan
        Write-Status 'Monitor devices left enabled for this game' DarkGray
    }
}

function Copy-DisplayProfile {
    param([Parameter(Mandatory)] $DisplayProfile)

    return [pscustomobject]@{
        Name = $DisplayProfile.Name
        Width = $DisplayProfile.Width
        Height = $DisplayProfile.Height
        Refresh = $DisplayProfile.Refresh
        Brightness = $DisplayProfile.Brightness
        Vibrance = $DisplayProfile.Vibrance
        DisableMonitorDevices = $DisplayProfile.DisableMonitorDevices
        Color = $DisplayProfile.Color
    }
}

$exitCode = 0

try {
    $selectedProfile = Copy-DisplayProfile -DisplayProfile $Profiles[$Mode]
    $selectedGame = $null

    if ($Mode -eq 'GAME') {
        Write-Section -Title 'Game Selection' -Color DarkCyan
        $selectedGame = Invoke-GameSelection -Games $LaunchableGames
        if ($null -eq $selectedGame) {
            Write-Host ''
            Write-Host 'Aborted.' -ForegroundColor DarkGray
            exit 0
        }

        $selectedProfile.Width = $selectedGame.GameWidth
        $selectedProfile.Height = $selectedGame.GameHeight
        $selectedProfile.DisableMonitorDevices = $selectedGame.DisableMonitorDevices
    }

    Set-ProfileState -DisplayProfile $selectedProfile

    if ($Mode -eq 'GAME') {
        Write-Section -Title 'Launch' -Color DarkCyan
        Start-SelectedGame -Game $selectedGame
    }
} catch {
    $exitCode = 1
    Write-Host "[FAIL] $($_.Exception.Message)" -ForegroundColor Red
}
exit $exitCode
