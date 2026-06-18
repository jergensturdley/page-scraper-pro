# Page Scraper Pro - Testing Instructions

## Reload the Extension
1. Go to `chrome://extensions`
2. Turn on **Developer mode** (top right toggle)
3. Click **Reload** on your extension card (or remove and re-add)

## Test Steps
1. Visit any webpage with images (e.g., news site, image gallery)
2. Click the extension icon
3. Click **Scrape Page Data**
4. Verify the image count shows correct number
5. Click **Images** button to download images individually
6. Check bottom of screen for "Started X downloads!" toast
7. Look in **chrome://downloads** for downloaded files

## Video Scraping
1. Visit a page with videos (YouTube, Vimeo, or pages with HTML5 video)
2. Click **Scrape Page Data**
3. Click the **Videos** tab to view extracted videos
4. Video types supported:
   - YouTube embeds (thumbnail preview shown)
   - Vimeo embeds (thumbnail preview shown)
   - HTML5 `<video>` elements
   - Direct video file links (.mp4, .webm, .ogg, .mov)
5. Click the copy button to copy video URLs, or click thumbnails to open in new tab

## New Features
- **Images**: Download each image individually
- **Videos**: Extract video URLs from embedded and direct video elements
- **ZIP**: Creates a ZIP archive (downloads images first, then creates ZIP)
- **Copy URLs**: Copy all image URLs to clipboard
- **Filter by size**: Use filter chips (All, Large, X-Large, By Domain) to filter images
- **Search filter**: Type in the filter box to search by alt text or URL
- **Link search**: Filter links by URL or text

## Troubleshooting
- If downloads don't appear:
  - Check chrome://downloads directly
  - Look in your default Downloads folder
  - Check console for errors (F12 in popup)