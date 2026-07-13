# KBD75 Rev2 VIA + SOCD Cleaner

This is the personal showcase portion of the surrounding QMK snapshot. The
keymap behavior and configuration in this directory are the original work being
highlighted; QMK itself and Getreuer's `getreuer/socd_cleaner` module are
upstream dependencies.

Target:

```sh
kbdfans/kbd75/rev2:via_socd
```

This keymap enables VIA and imports Getreuer's `getreuer/socd_cleaner`
community module.

Layer map:

- Layer 0: `BASE`
- Layer 1: `FN`
- Layer 2: `GAME`
- Layer 3: `SPARE`

SOCD Cleaner is off by default and is automatically enabled only while layer 2
(`GAME`) is active. The configured pairs are `KC_W`/`KC_S` and `KC_A`/`KC_D`,
using `SOCD_CLEANER_LAST`, which is last-input priority with reactivation.

In VIA, assign either of these to any key:

- `TG(2)` for press-on / press-off Game mode.
- `MO(2)` for hold-to-enable Game mode.

There is also a built-in fallback: hold `FN` and press `G` to toggle layer 2.

Commands to use after installing the QMK CLI/build environment:

```sh
qmk compile -kb kbdfans/kbd75/rev2 -km via_socd
qmk flash -kb kbdfans/kbd75/rev2 -km via_socd
```

No build or flash command has been run by this setup.
