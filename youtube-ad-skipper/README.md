# Skipper

Skipper is a Manifest V3 Chromium extension for YouTube. It detects ad playback, places a blackout overlay over the player, enforces mute while the ad is active, and uses the extension background service worker to perform a trusted skip click when a skip control appears.

The extension also keeps a lifetime skip count in extension storage and displays it on the action badge.

## Install for development

Open the browser’s extensions page, enable developer mode, choose **Load unpacked**, and select this directory. The extension requests YouTube host access and the debugger permission because trusted input is sent through the background service worker.

## Important limitation

This extension is intended to run only as an unpacked extension in browser Developer Mode/debug mode. The skip action uses the `chrome.debugger` API to dispatch a trusted click; YouTube blocks or detects ordinary scripted clicks on the **Skip ad** button. Running the project through the browser’s development extension flow is therefore required for the skip click to remain functional, and a normal production-style installation should not be expected to provide the same behavior.
