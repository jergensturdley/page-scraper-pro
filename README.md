# Page Scraper Pro

A Chrome extension for scraping images, videos, URLs, and text content from web pages. Features a compact, distraction-free UI with selective download capabilities.

## Features

- **Image Scraping**: Extract all images from a page with dimensions
- **Video Scraping**: Extract embedded and direct video URLs (YouTube, Vimeo, MP4, WebM, etc.)
- **Link Extraction**: Grab all URLs with link text
- **Text Content**: Extract clean page text
- **Image Selection**: Click images to select them for download
- **Download Panel**: Slide-out panel to review and download selected items
- **Compact UI**: Dark theme designed to fit snugly in the popup with no scrollbars
- **One-click Copy**: Copy results to clipboard
- **JSON Export**: Export scraped data as JSON
- **Context Menu**: Right-click to scrape directly from context menu
- **Settings**: Filter small images, exclude navigation links

## Installation

1. Download or clone this repository
2. Open Chrome and navigate to `chrome://extensions/`
3. Enable "Developer mode" in the top right
4. Click "Load unpacked" and select the extension folder
5. The extension icon will appear in your toolbar

## Usage

1. Navigate to any web page you want to scrape
2. Click the Page Scraper Pro icon in your toolbar
3. Click "Scrape Page Data" to extract images, videos, links, and text
4. Switch between tabs to view different data types
5. **Select images**: Click individual images to select them (shift-click for multi-select)
6. **Download selected**: Click "Download" in the selection toolbar to open the download panel
7. Use copy buttons or export to JSON

## File Structure

```
page-scraper-pro/
├── manifest.json      # Extension configuration
├── popup.html         # Main popup UI
├── popup.js           # Popup logic
├── styles.css         # Compact dark theme styles
├── content.js         # Page scraping functions
├── content.css        # Content script styles
├── background.js      # Background service worker
├── icons/             # Extension icons
│   ├── icon16.svg
│   ├── icon48.svg
│   └── icon128.svg
└── README.md
```

## Permissions

- `activeTab`: Access the current tab for scraping
- `storage`: Save settings
- `contextMenus`: Add right-click menu options
- `<all_urls>`: Scrape data from any website

## Development

To modify or extend the extension:

1. Edit the source files
2. Reload the extension in `chrome://extensions/`
3. Test on any web page

## License

MIT License
