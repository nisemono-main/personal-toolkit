# Tampermonkey Main Flow

Standalone userscript version of the core queue/sequence flow from the extension.

## What it does

- Injects a docked right-side queue rail on the ChatGPT page
- Opens a modal for sequence drafting, saving, and editing
- Supports sending a sequence from a 1-based start step
- Lets you queue one saved sequence behind another without interrupting the current run
- Supports sending a single message next
- Shows compact current/next prompt labels with hover tooltips instead of printing full messages in the status area
- Lists known user prompts in chat order and lets you jump to them, with an on-demand backfill button for older loaded history
- Waits for the page to become truly idle before sending the next message
- Pauses automatically if ChatGPT shows the rate-limit dialog

## What it does not do

- Crash-page detection or crash-page reload handling
- Background-worker features from the browser extension
- Optimizer / message window management from the extension

## File

- `queue-flow.user.js`

## Install

Load `queue-flow.user.js` into Tampermonkey or a compatible userscript manager on Firefox-based browsers.

## Notes

The sequence list is stored in `localStorage`. The current draft and active queue are stored in `sessionStorage` so they survive normal reloads in the same tab.

The rail reserves horizontal space on the right so ChatGPT's page content can use the remaining width.

`Stop` pauses the current queue and preserves the pending work so `Resume` can continue from the same point. Pressing `Stop` again clears the saved queue, cancels the active run, and returns the tab to a fresh idle state.

The run controls support a range: `Send sequence from - to` starts at the selected 1-based step, and the new `to` field sets an inclusive end step. Leave either field blank to fall back to the full sequence, and both fields clear after a successful `Send sequence` action.

The prompt index shows every user prompt the script has captured for the current chat. Clicking a row jumps to that prompt, and `Load older prompts` crawls upward through loaded history to backfill older entries into the list. Captured prompt history is persisted per chat across browser restarts in `localStorage`.

If ChatGPT shows the "Too many requests" dialog, the script pauses the queue and keeps the pending messages instead of pushing more prompts.
