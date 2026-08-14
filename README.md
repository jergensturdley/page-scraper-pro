# Page Scraper Pro

A Chrome extension for extracting images, videos, audio, links, and readable text from the page the user chooses to inspect.

## Features

- **User-initiated scraping**: Page access is temporary and begins only when the user invokes the extension from the toolbar or context menu
- **Media and link extraction**: Collect images, video/audio sources, embeds, and page links
- **Deep video download**: Optional native helper discovers and downloads video media with explicit quality presets via the OS-native save dialog; no cookies or browsing profile data is used
- **Content filtering**: The default **Content** view ranks likely editorial, gallery, and product images while hiding obvious icons, favicons, tracking pixels, sprites, badges, avatars, and navigation assets; **All assets** remains available as an escape hatch
- **Configurable filters**: Apply minimum image dimensions and optionally exclude navigation links
- **Selection and downloads**: Select supported images for download, review them, and download individual files or a ZIP
- **Copy and export**: Copy results to the clipboard or export the current scrape as JSON
- **Context menu**: Copy page image URLs, link URLs, or selected text from the page where the menu was invoked
- **Responsive UI**: Use the popup or Chrome side panel with keyboard-accessible controls
- **Per-tab session workspaces**: Restore the most recent scrape for each open tab during the current browser session

The extension does not inject a persistent content script, page overlay, image highlighting, or decorative page UI. Restricted Chrome pages and other pages where Chrome blocks script injection cannot be scraped; the extension reports the failure instead of claiming success.

## Installation

1. Download or clone this repository.
2. Open Chrome and navigate to `chrome://extensions/`.
3. Enable **Developer mode**.
4. Click **Load unpacked** and select the extension folder.
5. The extension icon will appear in the toolbar.

## Usage

1. Navigate to the page you want to inspect.
2. Invoke Page Scraper Pro from the toolbar. Opening the extension from the toolbar or invoking its context menu grants temporary access to the current page.
3. Click **Scrape Page Data**. Clicking **Scrape Page Data** uses that existing grant; the button itself does not grant access.
4. Switch result tabs and choose **Content** or **All assets** for images.
5. Select supported images for download, copy values, or export JSON.
6. Use **Clear** to remove the current tab's saved session workspace.

A persistent side panel may need the extension to be invoked again for the current page. Navigating the tab to a different origin invalidates the grant, so invoke the extension again before scraping the new page.

A workspace is stored in `chrome.storage.session`, keyed by tab. It is not durable browsing history: it is removed when its tab closes and does not persist across a browser restart. Settings use extension storage separately.

---

# Deep Video Download (Native Helper)

The optional native helper extends Page Scraper Pro with full video discovery and downloading. It runs as a separate process on your computer, keeping yt-dlp, your filesystem paths, and your download history out of the Chrome extension itself.

## Quick Start

**Step 1 — Find your extension ID**

1. Open `chrome://extensions/`.
2. Enable **Developer mode** (toggle in the top right).
3. Load this repository with **Load unpacked** if you have not already.
4. Look below the extension name for the ID (for example, `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`).

**Step 2 — Install the helper**

```bash
python3 -m native_host.installer --extension-id <your-extension-id>
```

Reload the extension at `chrome://extensions/`. The **Deep Scan** button now appears in the Videos tab.

**Step 3 — Run a deep scan**

1. Navigate to a page with a video (YouTube, Vimeo, or any page with an embedded video).
2. Open Page Scraper Pro from the toolbar.
3. Switch to the **Videos** tab.
4. Click **Deep Scan this page**.
5. Choose a quality preset and click **Download**.
6. The OS-native save dialog opens; pick your folder and confirm.

## Prerequisites

- **Python 3.10 or later** — check with `python3 --version`
- **yt-dlp** — install with `pip install yt-dlp` or `brew install yt-dlp`
- **ffmpeg** (optional) — detected automatically; only needed when you want transcoding or format conversion beyond what yt-dlp provides natively

Check everything is working:

```bash
python3 -m native_host.healthcheck
```

Expected output: no errors, exits `0`.

## Command Reference

```bash
# Install or repair the helper
python3 -m native_host.installer --extension-id <chrome-extension-id>

# Validate the installation (check Python, yt-dlp, ffmpeg, protocol handshake)
python3 -m native_host.healthcheck

# Update after requirements changes (reinstalls with pinned version)
python3 -m native_host.installer --extension-id <chrome-extension-id>

# Remove the helper and Native Messaging registration
python3 -m native_host.uninstaller --extension-id <chrome-extension-id>
```

### Troubleshooting

| Problem | Check |
|---|---|
| **"Deep Scan" button is missing** | Reinstall the helper with the correct extension ID |
| **healthcheck exits non-zero** | Read the error message; most common cause is missing `yt-dlp` (`pip install yt-dlp`) |
| **`yt-dlp` not found in healthcheck** | Make sure `yt-dlp` is installed and on your PATH; try `pip show yt-dlp` |
| **`ffmpeg` version invalid** | `ffmpeg` is optional; the helper works without it; install `ffmpeg` via `brew install ffmpeg` (macOS) or your package manager |
| **Download fails with "not installed"** | Reinstall: `python3 -m native_host.installer --extension-id <chrome-extension-id>` |
| **Another download is already running** | Wait for the current download to finish or click **Cancel**, then try again (only one job at a time) |
| **Uninstall not removing files** | Run the uninstaller with `sudo` only if the helper was previously installed by a different user (not recommended) |

## How It Works

**Architecture**

```
Chrome Extension (popup.js)
       │ chrome.runtime.sendMessage
       ▼
Background Service Worker (background.js)
       │ chrome.runtime.sendNativeMessage
       ▼
Native Helper (host.py — long-lived Python process)
       │ subprocess (isolated venv)
       ▼
yt-dlp (discovers metadata / downloads media)
```

The extension sends only the page URL, media ID, preset name, and job ID to the helper. The helper owns all yt-dlp interaction, file paths, and output.

**Discovery (no cookies)**

When you click **Deep Scan**, the helper runs `yt-dlp --dump-json` on the page URL with no authentication flags. It discovers video metadata (title, thumbnail, available formats) without using your browser cookies or session. This means:

- It works on **public pages** where no login is required.
- It **cannot** access videos behind a login, paywall, or DRM protection.
- It does **not** read or send your browsing cookies to any server.

**Quality presets**

| Preset | What it does |
|---|---|
| **Best quality** | Downloads the highest-quality format yt-dlp can find |
| **1080p** | Selects a 1080p-or-lower format |
| **720p** | Selects a 720p-or-lower format |
| **Audio only** | Downloads audio-only (no video track) |

**Single-job rule**

Only one download can run at a time. While a download is active, the **Deep Scan** and **Download** buttons are disabled. Cancel the current job first.

**Finder and filesystem**

The helper asks for your download destination folder using the macOS native save dialog. The chosen path is stored by the helper, not by the extension. The extension never sees or transmits your filesystem paths.

**Progress and cancellation**

The helper streams progress frames back through the extension to the popup as a live progress bar. **Cancel** sends only the active job ID — no preset, path, or internal options are transmitted.

**Logs**

The helper writes structured logs to:

```
~/Library/Application Support/com.pagescraperpro.ytdlp/logs/
```

Three files of up to 512 KiB each, rotating automatically. Sensitive paths (local filesystem locations, user-chosen directories) are redacted before writing.

**No DRM bypass**

The helper does not bypass DRM, paywalls, login gates, or content protection. It extracts media only from URLs yt-dlp can reach without authentication.

**Pinned dependencies**

On first install, the helper records the exact `yt-dlp` version in `requirements.lock`. Subsequent installs reuse that pinned version. To update to a new version:

```bash
python3 -m native_host.installer --extension-id <chrome-extension-id>
```

This fetches the latest `yt-dlp` matching the lock, then re-locks.

## File Structure

```
page-scraper-pro/
├── manifest.json              # Extension configuration and least-privilege permissions
├── popup.html                 # Popup and side-panel markup
├── popup.js                   # UI, user-triggered scraping, filtering, and session state
├── styles.css                 # Responsive dark interface styles
├── background.js              # Context menus, downloads, notifications, ZIP, native messaging bridge
├── deep-video.js             # Deep video state reducer (browser/service worker compatible)
├── native-client.js           # Native messaging client with bounded snapshot and event stream
├── native_host/              # Python native helper (optional, installed separately)
│   ├── host.py               # Framed host loop and command dispatcher
│   ├── jobs.py               # Job manager with subprocess supervision and cancellation
│   ├── worker.py             # yt-dlp subprocess wrapper
│   ├── finder.py             # OS-native directory picker
│   ├── installer.py          # Hash-pinned installation and manifest registration
│   ├── uninstaller.py         # Manifest cleanup and directory removal
│   ├── healthcheck.py        # Protocol handshake and dependency validation
│   └── tests/                # Unit tests for each module
├── libs/
│   ├── fflate.umd.js        # Packaged fflate 0.8.2 UMD dependency
│   └── fflate.LICENSE        # Upstream MIT license and attribution
├── icons/                    # Extension icons
├── tests/                    # Jest regression tests
└── README.md
```

## Permissions

Page Scraper Pro declares no broad host permission and no `<all_urls>` access.

- `activeTab`: Temporarily access only the tab involved in the user's toolbar or context-menu action
- `scripting`: Run the requested scraper or clipboard action in that temporarily authorized tab
- `storage`: Save settings and per-tab session workspaces
- `contextMenus`: Provide right-click scrape/copy commands
- `downloads`: Save selected files and generated archives
- `notifications`: Report context-menu and download outcomes
- `sidePanel`: Offer the same interface in Chrome's side panel
- `nativeMessaging`: Communicate with the optional page-scraper-pro native helper

ZIP support uses the official `umd/index.js` build from the pinned npm package `fflate@0.8.2`, vendored as `libs/fflate.umd.js`; its MIT license and copyright attribution are preserved in `libs/fflate.LICENSE`. The library is loaded only from the packaged `libs/fflate.umd.js`. If that local dependency cannot load, URL-based ZIP requests fall back to individual downloads and clearly report the reason; the extension never imports executable code from a CDN.

## Development

```bash
npm install
npm test
```

Reload the unpacked extension from `chrome://extensions/` after source changes. Browser APIs, restricted-page errors, context-menu clipboard behavior, and real downloads still require manual verification.

## License

MIT License
