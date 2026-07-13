# Crash Reload Userscript

Standalone two-part userscript setup for crash detection and recovery.

## What it does

- `crash-heartbeat.user.js` plants a hidden DOM heartbeat on every normal page
- `crash-reload.user.js` watches for the crash title and for the heartbeat disappearing or going stale
- If the page looks crashed and the URL is still a normal `http` or `https` page, it tries to restore or reload it

## Files

- `crash-heartbeat.user.js`
- `crash-reload.user.js`

## Install

Install both scripts into Tampermonkey, Violentmonkey, or another userscript manager that supports Firefox-based browsers.

## Behavior

- The heartbeat script keeps a hidden marker in the DOM and updates it on an interval
- The reloader gives the page a short arm-up delay so it does not judge the initial load too early
- If the title becomes `Tab crash reporter`, the reloader first tries the restore button, then falls back to browser reload primitives
- If the heartbeat never appears, disappears, or stops updating after it has already been seen, the reloader treats that as a crash signal and reloads the tab

## Notes

- This is best suited to pages where the URL stays on the same normal website and only the tab state changes
- If Firefox turns the tab into a browser-restricted crash page that Tampermonkey cannot inject into, no userscript can reliably run there
