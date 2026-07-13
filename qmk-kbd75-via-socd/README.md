# QMK KBD75 Showcase Exception

This directory is a curated exception to the repository’s ground-up-project rule. It contains a small personal QMK keymap contribution alongside the upstream KBD75 board snapshot required to describe and place that contribution.

## Ownership map

### Personal work

[`kbd75/keymaps/via_socd`](./kbd75/keymaps/via_socd) is the showcase contribution:

- `keymap.c` defines the four layers, the VIA-friendly layout, the Game layer, and the logic that enables SOCD cleaning only while that layer is active.
- `config.h` selects the four dynamic keymap layers.
- `rules.mk` enables VIA and configures the lean feature set for this keymap.
- `keymap.json` declares the external SOCD Cleaner module dependency.
- The directory README documents the intended behavior and usage.

### Upstream or dependency work

The following files are retained only as board context and should not be described as original implementation:

- `kbd75/readme.md`
- `kbd75/rev1/**`
- `kbd75/rev2/**`
- `kbd75/keymaps/default/**`
- `kbd75/keymaps/iso/**`
- QMK firmware APIs, board definitions, layout macros, and build tooling
- Getreuer’s `getreuer/socd_cleaner` community module, referenced but not authored here

The outer repository README and this ownership map are intentional provenance labels for this public repository.
