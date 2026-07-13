# compact-clipboard-logs.ps1
# Silent clipboard compacting for Unity-like logs.
# Place in the same folder as run-compact-silent.vbs and launch via the VBS wrapper to run hidden.

param()

# Compact a text containing one or more log entries separated by blank lines.
function ConvertTo-CompactLog {
    param([string]$text)

    if ([string]::IsNullOrWhiteSpace($text)) { return "" }

    # Split entries by one or more blank lines
    $entries = [regex]::Split($text.Trim(), "\r?\n\s*\r?\n")

    $out = New-Object System.Collections.Generic.List[string]

    foreach ($entry in $entries) {
        $lines = $entry -split "\r?\n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
        if ($lines.Count -eq 0) { continue }

        $message = $lines[0]

        # Try multiple regexes (in order) to find stack frames / file:line information
        $fileLine = $null

        # 1) Unity format: (at Assets/...:123)
        $m = [regex]::Matches($entry, "\(at\s+([^:()]+):([0-9]+)\)")
        if ($m.Count -gt 0) {
            $last = $m[$m.Count - 1]
            $fileLine = ($last.Groups[1].Value.TrimEnd()) + ":" + $last.Groups[2].Value
        }

        # 2) Windows absolute path with drive letter C:\...:123
        if (-not $fileLine) {
            $m2 = [regex]::Matches($entry, "([A-Za-z]:\\[^:\r\n]+):([0-9]+)")
            if ($m2.Count -gt 0) {
                $last = $m2[$m2.Count - 1]
                $fileLine = $last.Groups[1].Value + ":" + $last.Groups[2].Value
            }
        }

        # 3) Generic fallback: last token that looks like path:line (no parentheses required)
        if (-not $fileLine) {
            $m3 = [regex]::Matches($entry, "([^\s()<>]+):([0-9]+)")
            if ($m3.Count -gt 0) {
                $last = $m3[$m3.Count - 1]
                $fileLine = $last.Groups[1].Value + ":" + $last.Groups[2].Value
            }
        }

        if (-not $fileLine) { $fileLine = "(no file info)" }

        $out.Add(("$message`r`n$fileLine"))
    }

    return ($out -join "`r`n`r`n")
}

# Read clipboard silently
try {
    $orig = Get-Clipboard -Raw -ErrorAction Stop
} catch {
    # If clipboard read fails (no clipboard available), exit silently
    exit 0
}

if (-not $orig) { exit 0 }

$result = ConvertTo-CompactLog $orig

# Only update clipboard if result is non-empty
if ([string]::IsNullOrWhiteSpace($result)) { exit 0 }

# Write back to clipboard (silent)
try {
    Set-Clipboard -Value $result -ErrorAction Stop
} catch {
    # ignore errors silently
}

exit 0
