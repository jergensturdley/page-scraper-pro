# Page Scraper Pro

A Chrome extension for extracting images, videos, audio, links, and readable text from the page the user chooses to inspect.

## Features

- **User-initiated scraping**: Page access is temporary and begins only when the user invokes the extension from the toolbar or context menu
- **Media and link extraction**: Collect images, video/audio sources, embeds, and page links
- **Deep video download**: Optional native helper discovers and downloads video media with explicit quality presets (best, 1080p, 720p, audio) via the OS-native save dialog; no cookies or browsing profile data is used
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

## Deep Video Download (native helper)

Page Scraper Pro can use an optional native helper to discover and download video media from supported pages. The helper runs as a native messaging host, keeping yt-dlp, cookies, and filesystem paths out of the extension and off the user's browsing profile.

### Prerequisites

- Python 3.10 or later
- `yt-dlp` — install via `pip install yt-dlp` or your system package manager
- `ffmpeg` (optional — detected automatically; only needed for transcoding or format conversion)

### Finding your extension ID

1. Open `chrome://extensions/`, enable **Developer mode**, and load this repository.
2. Copy the ID shown below the extension name (for example, `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`).

### Installing the helper

```bash
python3 -m native_host.installer --extension-id <chrome-extension-id>
```

After installation reload the extension. The **Deep Scan** button appears in the Videos tab when the helper is available.

### Validating the installation

```bash
python3 -m native_host.healthcheck
```

Exits 0 on success; reports missing dependencies and protocol handshake status.

### Updating

```bash
python3 -m native_host.installer --extension-id <chrome-extension-id>
```

The installer pins the current `yt-dlp` version on first install and reuses the pinned version on subsequent runs. Pass `--help` for all options.

### Uninstalling

```bash
python3 -m native_host.uninstaller --extension-id <chrome-extension-id>
```

Removes the helper, its Native Messaging manifest, and helper-owned state. Downloaded media files are not touched.

### How it works

**Protocol:** The extension and helper communicate over stdin/stdout using Chrome Native Messaging (4-byte length prefix, JSON body). The helper is long-lived; only one job runs at a time.

**Discovery without cookies:** The helper runs `yt-dlp --dump-json` on the page URL with no authentication. It discovers and extracts metadata without reading browser cookies or session state:
- It works on public and openly accessible pages.
- It cannot access paywalled, login-gated, or DRM-protected content.
- It does not read or send browsing cookies to any server.

**Single-job behavior:** Only one active download runs at a time. A second download request while one is active returns a `busy` error.

**Finder:** The helper asks for a destination folder using the OS-native save dialog. The path is stored by the helper. The extension never sees or sends filesystem paths.

**Explicit download:** The user selects a preset (best quality, 1080p, 720p, or audio only) in the popup and confirms. The extension sends only the media ID, preset name, and confirmation flag.

**Cancellation:** Cancel sends only the active job ID to the helper. No preset, path, or internal options are transmitted.

**Progress:** The helper streams progress frames through the service worker to the popup as a live progress bar.

**Logs:** Structured logs live under `~/Library/Application Support/com.pagescraperpro.ytdlp/logs/` with 512 KiB rotation. Sensitive paths are redacted before writing.

**No DRM bypass:** The helper does not bypass DRM, paywalls, login gates, or content protection. It extracts media only from URLs `yt-dlp` can reach without authentication.

## File Structure

```
page-scraper-pro/
├── manifest.json           # Extension configuration and least-privilege permissions
├── popup.html              # Popup and side-panel markup
├── popup.js                # UI, user-triggered scraping, filtering, and session state
├── styles.css              # Responsive dark interface styles
├── background.js           # Context menus, downloads, notifications, ZIP, and native messaging bridge
├── deep-video.js           # Deep video state reducer (browser/service worker compatible)
├── native-client.js        # Native messaging client with bounded snapshot and event stream
├── native_host/            # Python native helper (optional, installed separately)
│   ├── host.py             # Framed host loop and command dispatcher
│   ├── jobs.py             # Job manager with subprocess supervision and cancellation
│   ├── worker.py           # yt-dlp subprocess wrapper
│   ├── finder.py           # OS-native directory picker
│   ├── installer.py        # Hash-pinned installation and manifest registration
│   ├── uninstaller.py      # Manifest cleanup and directory removal
│   ├── healthcheck.py      # Protocol handshake and dependency validation
│   └── tests/              # Unit tests for each module
├── libs/
│   ├── fflate.umd.js       # Packaged fflate 0.8.2 UMD dependency
│   └── fflate.LICENSE      # Upstream MIT license and attribution
├── icons/                  # Extension icons
├── tests/                  # Jest regression tests
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
