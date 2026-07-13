<#
.SYNOPSIS
  Convert a WizTree CSV export into a Markdown ASCII tree (output_hierarchy.md).

.DESCRIPTION
  - Place this script in a folder and run it (it will pick the newest .csv in the same folder if you don't pass -CsvPath),
    or call it with -CsvPath <path-to-csv>.
  - Output is written next to the script as output_hierarchy.md (or output_hierarchy_1.md, _2, ... to avoid overwrites).
  - Excludes .meta files by default and trims the displayed header to the last N segments of the detected common root (RootDepth).
    Pass -IncludeMeta when .meta files should be included.
  - Fixes applied:
    * Ignore CSV rows that don't look like paths (e.g., stray header/fragments like "e").
    * Detect common root using only absolute-looking paths (drive letters or UNC) when available.
    * When stripping the common root yields an empty relative path, treat the entry as a root-level file (use filename only).
    * No fenced code block wrappers — output is plain Markdown.
#>

param(
    [string]$CsvPath = $null,
    [string]$OutputPath = $null,
    [string]$CommonRoot = $null,
    [string]$ReplaceWith = $null,
    [int]$RootDepth = 1,
    [switch]$IncludeMeta
)

# Accept positional arg (useful when invoked via .bat dropper)
if (-not $CsvPath -and $args -and $args.Count -gt 0) {
    $CsvPath = $args[0]
}

# Script directory (where output will be placed by default)
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }

# If no CSV provided, pick newest CSV in script folder
if (-not $CsvPath) {
    $csvFiles = Get-ChildItem -Path $scriptDir -Filter '*.csv' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if ($csvFiles -and $csvFiles.Count -gt 0) {
        Write-Host "Using CSV: $($csvFiles[0].Name)"
        $CsvPath = $csvFiles[0].FullName
    } else {
        Write-Error "No CSV found. Either pass -CsvPath or put a .csv next to this script."
        exit 1
    }
}

if (-not (Test-Path -Path $CsvPath)) {
    Write-Error "CSV file not found: $CsvPath"
    exit 1
}

# Default output naming (output_hierarchy.md with numeric suffixes if required)
if (-not $OutputPath) {
    $candidate = Join-Path $scriptDir 'output_hierarchy.md'
    if (Test-Path $candidate) {
        $counter = 1
        while ($true) {
            $candidateName = "output_hierarchy_$counter.md"
            $candidate = Join-Path $scriptDir $candidateName
            if (-not (Test-Path $candidate)) { break }
            $counter++
        }
    }
    $OutputPath = $candidate
}

# Helpers
function ConvertTo-NormalizedPath { param([string]$p)
    if ($null -eq $p) { return $p }
    $s = $p.Trim()
    if ($s.StartsWith('"') -and $s.EndsWith('"')) { $s = $s.Substring(1, $s.Length - 2) }
    $s = $s -replace '/','\'
    return $s
}

function Remove-RootPrefix { param([string]$fullPath, [string]$root)
    if ([string]::IsNullOrEmpty($root)) { return $fullPath }
    $escaped = [regex]::Escape($root)
    return [regex]::Replace($fullPath, "^$escaped[\\\/]*", "", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

function New-Node { return @{ dirs = @{}; files = New-Object System.Collections.Generic.List[string] } }

# Read CSV
$rows = Import-Csv -Path $CsvPath -ErrorAction Stop

# Determine path column (prefer "File Name" case-insensitive)
$propNames = $rows[0].PSObject.Properties.Name
$pathColumn =
    if ($propNames -contains 'File Name') { 'File Name' }
    elseif ($propNames -contains 'FileName') { 'FileName' }
    else {
        # case-insensitive search
        $found = $propNames | Where-Object { $_ -match '(?i)file\s*name' } | Select-Object -First 1
        if ($found) { $found } else { $propNames | Select-Object -First 1 }
    }

if (-not $pathColumn) {
    Write-Error "Could not determine which CSV column contains paths."
    exit 1
}

# Collect and normalize paths, filter out garbage rows
$allPaths = New-Object System.Collections.Generic.List[string]
foreach ($r in $rows) {
    $raw = $r.$pathColumn
    if (-not $raw) { continue }

    $p = ConvertTo-NormalizedPath $raw

    # Skip obvious non-path garbage: e.g., single-letter fragments, header text that slipped in, or very short strings
    if ($p -match '^\s*$') { continue }
    if ($p -eq $pathColumn) { continue }            # sometimes header text appears
    if ($p.Length -lt 3) { continue }

    # Only include entries that look like a path or a filename in a path (contain a backslash) OR are root-like ending with '\'
    if ($p -match '[\\\/]') {
        $allPaths.Add($p)
    } else {
        # also accept plain filenames with extensions (e.g., "README.md"), but only if they include a dot
        if ($p -match '\.[A-Za-z0-9]+$') { $allPaths.Add($p) }
    }
}

if ($allPaths.Count -eq 0) {
    Write-Error "No valid paths found in CSV."
    exit 1
}

# Prefer absolute-looking paths for common-root detection (drive-letter or UNC)
$absPaths = $allPaths | Where-Object { $_ -match '^[A-Za-z]:\\' -or $_ -match '^\\\\' }

$pathsForRoot = if ($absPaths.Count -gt 0) { $absPaths } else { $allPaths }

# Auto-detect common root (safe handling of segments)
if (-not $CommonRoot) {
    $segLists = @()
    foreach ($p in $pathsForRoot) {
        $segments = ($p -split '[\\\/]') | Where-Object { $_ -ne '' }
        $segLists += ,@($segments)
    }

    if ($segLists.Count -eq 0) {
        $CommonRoot = ''
    } else {
        $minLen = ($segLists | ForEach-Object { $_.Length } | Measure-Object -Minimum).Minimum
        $commonSegments = New-Object System.Collections.Generic.List[string]

        for ($i = 0; $i -lt $minLen; $i++) {
            $val = [string]$segLists[0][$i]
            $allMatch = $true
            foreach ($lst in $segLists) {
                $a = [string]$lst[$i]
                if ($a.ToLowerInvariant() -ne $val.ToLowerInvariant()) { $allMatch = $false; break }
            }
            if ($allMatch) { $commonSegments.Add($val) } else { break }
        }

        if ($commonSegments.Count -eq 0) {
            # fallback to drive root of first absolute-looking path, or empty
            if ($pathsForRoot[0] -match '^[A-Za-z]:\\') {
                $CommonRoot = ([System.IO.Path]::GetPathRoot($pathsForRoot[0])).TrimEnd('\')
            } else {
                $CommonRoot = ''
            }
        } else {
            $CommonRoot = ($commonSegments -join '\')
        }
    }
}

$CommonRoot = ConvertTo-NormalizedPath $CommonRoot
if ($CommonRoot.EndsWith('\')) { $CommonRoot = $CommonRoot.TrimEnd('\') }

# Build tree using paths relative to CommonRoot
$rootNode = New-Node

foreach ($p in $allPaths) {
    # skip directory-only listing rows that are not useful (but allow directories if they are meaningful)
    # if it ends with '\' it's a directory entry; we'll skip directory-only rows to rely on file rows for structure
    if ($p.EndsWith('\')) { continue }
    if (-not $IncludeMeta -and $p.ToLowerInvariant().EndsWith('.meta')) { continue }

    $rel = if ($CommonRoot) { Remove-RootPrefix -fullPath $p -root $CommonRoot } else { $p }

    # If Remove-RootPrefix returned empty (file at the common root), set rel to filename only
    if ([string]::IsNullOrEmpty($rel)) {
        $rel = [System.IO.Path]::GetFileName($p)
    }

    # If rel still doesn't contain a path separator, treat it as a top-level file or filename
    $parts = ($rel -split '[\\\/]') | Where-Object { $_ -ne '' }
    if ($parts.Count -eq 0) { continue }
    $fileName = $parts[-1]
    $dirs = if ($parts.Count -gt 1) { $parts[0..($parts.Count -2)] } else { @() }

    # Build nodes
    $node = $rootNode
    foreach ($d in $dirs) {
        if (-not $node.dirs.ContainsKey($d)) { $node.dirs[$d] = New-Node }
        $node = $node.dirs[$d]
    }
    if (-not $node.files.Contains($fileName)) { $node.files.Add($fileName) }
}

# Determine header text: either ReplaceWith or last N segments of CommonRoot (RootDepth)
if ($ReplaceWith) {
    $header = $ReplaceWith
} else {
    if ([string]::IsNullOrEmpty($CommonRoot)) {
        # If we didn't detect a common root, display a generic header
        $header = "Project root\"
    } else {
        $segments = ($CommonRoot -split '[\\\/]') | Where-Object { $_ -ne '' }
        if ($RootDepth -ge $segments.Count) {
            $header = $CommonRoot
        } elseif ($segments.Count -gt 0) {
            $startIndex = $segments.Count - $RootDepth
            $tail = $segments[$startIndex .. ($segments.Count - 1)]
            $header = ($tail -join '\')
        } else {
            $header = $CommonRoot
        }
        if (-not $header.EndsWith('\')) { $header = $header + '\' }
    }
}

# Render tree to lines (no fenced code block)
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add($header)

function Write-TreeChildren {
    param([hashtable]$node, [string]$prefix)
    $dirNames = $node.dirs.Keys | Sort-Object
    $fileNames = $node.files | Sort-Object

    $children = @()
    foreach ($d in $dirNames) { $children += @{ type='dir'; name=$d } }
    foreach ($f in $fileNames) { $children += @{ type='file'; name=$f } }

    for ($i = 0; $i -lt $children.Count; $i++) {
        $child = $children[$i]
        $isLast = ($i -eq $children.Count - 1)

        if ($isLast) {
            $connector = '└──'
            $nextPrefixPart = '    '
        } else {
            $connector = '├──'
            $nextPrefixPart = '│   '
        }

        if ($child.type -eq 'dir') {
            $lines.Add($prefix + $connector + ' ' + $child.name + '\')
            Write-TreeChildren -node $node.dirs[$child.name] -prefix ($prefix + $nextPrefixPart)
        } else {
            $lines.Add($prefix + $connector + ' ' + $child.name)
        }
    }
}

Write-TreeChildren -node $rootNode -prefix ''

# Write lines directly to the markdown file (no triple-backticks)
[System.IO.File]::WriteAllLines($OutputPath, $lines, [System.Text.Encoding]::UTF8)
Write-Host "Wrote tree to: $OutputPath"
