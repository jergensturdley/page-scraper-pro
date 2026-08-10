# Page Scraper Pro

A Chrome extension for extracting images, videos, audio, links, and readable text from the page the user chooses to inspect.

## Features

- **User-initiated scraping**: Page access is requested only after a popup/side-panel scrape action or a context-menu command
- **Media and link extraction**: Collect images, video/audio sources, embeds, and page links
- **Content filtering**: The default **Content** view ranks likely editorial, gallery, and product images while hiding obvious icons, favicons, tracking pixels, sprites, badges, avatars, and navigation assets; **All assets** remains available as an escape hatch
- **Configurable filters**: Apply minimum image dimensions and optionally exclude navigation links
- **Selection and downloads**: Select supported images, review them, and download individual files or a ZIP
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
2. Open Page Scraper Pro from the toolbar or side panel.
3. Click **Scrape Page Data**. Chrome grants temporary access to that active tab for this user action.
4. Switch result tabs and choose **Content** or **All assets** for images.
5. Select supported images for download, copy values, or export JSON.
6. Use **Clear** to remove the current tab's saved session workspace.

A workspace is stored in `chrome.storage.session`, keyed by tab. It is not durable browsing history: it is removed when its tab closes and does not persist across a browser restart. Settings use extension storage separately.

## File Structure

```
page-scraper-pro/
├── manifest.json      # Extension configuration and least-privilege permissions
├── popup.html         # Popup and side-panel markup
├── popup.js           # UI, user-triggered scraping, filtering, and session state
├── styles.css         # Responsive dark interface styles
├── background.js      # Context menus, downloads, notifications, and ZIP handling
├── libs/
│   └── fflate.umd.js  # Packaged ZIP dependency (no remote executable fallback)
├── icons/             # Extension icons
├── tests/             # Jest regression tests
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

ZIP support is loaded only from the packaged `libs/fflate.umd.js`. If that local dependency cannot load, URL-based ZIP requests fall back to individual downloads and clearly report the reason; the extension never imports executable code from a CDN.

## Development

```bash
npm install
npm test
```

Reload the unpacked extension from `chrome://extensions/` after source changes. Browser APIs, restricted-page errors, context-menu clipboard behavior, and real downloads still require manual verification.

## License

MIT License
