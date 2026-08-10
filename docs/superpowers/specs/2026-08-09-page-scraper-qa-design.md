# Page Scraper Pro QA and Dark UI Refinement

## Goal
Make the Manifest V3 extension reliable, safe, keyboard-accessible, and polished while preserving its dark utility-focused identity and existing feature set.

## Scope

### Functional correctness and safety
- Apply persisted image-size and navigation-link settings inside the injected scraper.
- Render scraped values through DOM APIs rather than HTML interpolation; preserve full tab URL separately for exports.
- Recognize YouTube embed and Vimeo player URLs, always supplying an explicit safe open action for videos.
- Keep persistent scrape status truthful after failures.
- Confirm clipboard writes before presenting success feedback.
- Use packaged ZIP support only; do not load executable dependencies remotely.
- Stop auto-injecting the page overlay and reduce persistent host access: scrape only after a user action using `activeTab`.


### Content-image classification
- Classify every extracted image without discarding uncertain results. The default `content` view shows likely article, product, gallery, and editorial imagery; an `all` view remains available as an escape hatch.
- Use a balanced, deterministic score: reject explicit favicon/tracking/sprite inputs; penalize tiny dimensions, extreme aspect ratios, junk-like names (`icon`, `logo`, `avatar`, `badge`), and navigation/header/footer/button placement; reward larger dimensions, meaningful alternative text, and figure/picture/main/article/gallery/product context.
- Store the classification and concise reason with each image so behavior is explainable and regression-testable.
- Keep existing Large and XL filters as secondary views, but make Content the initial and reset state.

### Per-tab session continuity
- Cache each successful scrape in `chrome.storage.session`, keyed by Chrome tab ID, and restore it only for that same tab.
- Persist the active result tab, image filter, image/link search strings, and selected-image URLs alongside the result data.
- Identify restored results visibly with their original scrape time and retain a clear Rescrape action.
- Debounce workspace-state writes, handle session-storage quota failures without losing the active in-memory scrape, and omit or trim oversized optional text before dropping structured result data.
- Clear cached state when its tab closes and expose an explicit Clear results action. Session data must not survive a browser restart.

### Operate-mode dark interface
- Retain dark charcoal surfaces and introduce semantic tokens for warm neutral text, borders, focus, success/error states, and one blue action accent.
- Structure the popup around page context, a single dominant scrape action, status feedback, result tabs, and export/download actions.
- Make popup/side-panel behavior CSS-responsive instead of inferring it from window width.
- Keep all labels, functionality, and factual product copy unless a clarity change is needed for an error or state.

### Accessibility and resilience
- Use semantic tabs with selected state and associated panels.
- Use native buttons/links for result interactions; label every icon-only action.
- Add `aria-live` progress/result announcements, `aria-busy` during scraping, and persistent error recovery.
- Implement keyboard-operable, focus-managed settings and download dialogs, including Escape and focus restoration.
- Meet WCAG AA contrast, visible focus, sensible target sizes, and reduced-motion preferences.
- Ensure lists and controls tolerate long untrusted text and narrow side-panel widths.

## Implementation boundaries
- `popup.html`, `popup.js`, and `styles.css` own the popup/side-panel interface.
- `background.js` owns context-menu, tab-cache cleanup, and download/clipboard orchestration.
- `content.js` is retained only for user-triggered content-script communication where needed; it must not append permanent page UI.
- `manifest.json` centralizes permissions and uses the minimum required access.
- Jest tests exercise pure/safely exported behavior and source-level UI contracts without requiring Chrome at test runtime, including deterministic image classification and per-tab cache serialization/restoration.

## Verification
- Follow red-green-refactor for every behavioral correction.
- Run targeted Jest tests, the full suite, JavaScript syntax checks, and the Impeccable detector after UI changes.
- Perform the documented manual Chrome checks for popup, side panel, settings, failed scrape, video embeds, keyboard-only operation, clipboard failure, exports, and downloads.
