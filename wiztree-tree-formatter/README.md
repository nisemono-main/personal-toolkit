# WizTree Formatter

WizTree Formatter converts a WizTree CSV export into a plain Markdown tree. It detects the path column, finds a common root, removes unhelpful metadata rows, and writes a readable hierarchy without requiring a fixed source directory.

## Get WizTree

Download WizTree from the [official WizTree site](https://diskanalyzer.com/), using its download page rather than an unverified mirror. WizTree is useful here because its graphical interface lets you interactively scan a drive or folder, navigate the visual tree, and export only the exact repository or project directories you want to document. It also provides large-file discovery, duplicate-file finding, and CSV export features.

The formatter is intentionally separate from WizTree: WizTree performs the interactive selection and analysis, while this project turns the selected CSV export into a compact, reviewable Markdown hierarchy.

## Usage

Drop a CSV export onto `file_drop.bat`, or run PowerShell directly:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\formatter.ps1 -CsvPath .\export.csv
```

Without `-CsvPath`, the formatter selects the newest CSV beside the script. Use `-OutputPath`, `-CommonRoot`, `-ReplaceWith`, or `-RootDepth` when the default tree header and output location need adjustment. Metadata files are excluded by default; pass `-IncludeMeta` when they are relevant.

CSV exports and generated `output_hierarchy*.md` files are ignored by the repository because they are machine-specific output rather than the formatter itself.

PowerShell is used for its built-in CSV import, Windows path handling, and straightforward command-line parameters. The batch dropper is only a convenience entry point; it locates the PowerShell script beside itself, so no fixed source directory is required.

Example output:

```text
ProjectRoot\
├── Assets\
│   ├── Scripts\
│   └── Textures\
└── README.md
```
