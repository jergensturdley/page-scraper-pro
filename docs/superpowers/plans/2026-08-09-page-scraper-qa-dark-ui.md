# Page Scraper Pro QA and Dark UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Page Scraper Pro safe, correct, accessible, responsive, and refined while preserving its dark visual identity.

**Architecture:** Keep scraping logic injectable and data-only, passing a serializable settings snapshot into `chrome.scripting.executeScript`. Popup rendering creates DOM nodes for untrusted values and owns accessible operating states. The manifest relies on user-granted `activeTab`, and the background service worker reports the actual outcome of user actions.

**Tech Stack:** Manifest V3, plain JavaScript/HTML/CSS, Chrome extension APIs, Jest 30.

---

## File structure

- `manifest.json`: minimum extension permissions and user-triggered access model.
- `popup.html`: semantic, labelled popup shell, tabs, live status, and dialogs.
- `popup.js`: scraping, rendering, settings, exports, dialogs, and event behavior.
- `styles.css`: responsive dark design tokens, component states, focus, motion, and dialogs.
- `background.js`: context-menu and clipboard result handling; packaged archive path only.
- `content.js` / `content.css`: remove unsolicited injected indicator while retaining only needed message support.
- `tests/popup.test.js`: regression contracts for rendering safety, scraper settings, exports, video recognition, accessibility, and state.

### Task 1: Establish testable helpers and regression contracts

**Files:**
- Modify: `popup.js`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing tests for pure helpers.** Add tests that assert `getVideoProviderId('https://www.youtube.com/embed/abc123')` returns `{ provider: 'youtube', id: 'abc123'}`, `getVideoProviderId('https://player.vimeo.com/video/42')` returns `{ provider: 'vimeo', id: '42'}`, and `buildExportData('https://example.com/a?x=1', data).url` remains the full URL.
- [ ] **Step 2: Run the focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because these helpers do not exist or export URL uses display hostname.
- [ ] **Step 3: Implement the smallest helper surface.** Add `let currentTabUrl = ''`, `getVideoProviderId(url)`, and `buildExportData(url, data)` in `popup.js`; set `currentTabUrl` from `tab.url`, retain hostname only for `#currentPage`, and call `buildExportData(currentTabUrl, scrapedData)` in `exportJson()`.
- [ ] **Step 4: Re-run the focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: PASS.
- [ ] **Step 5: Commit.** Run: `git add popup.js tests/popup.test.js && git commit -m "Fix scraper export and video URL helpers"`.

### Task 2: Apply settings inside the injected scraper

**Files:**
- Modify: `popup.js:291-462`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing source/behavior tests.** Assert the executeScript args include `settings`; assert fallback signature is `scrapePageFromContentFallback(scrapeSettings)`; assert image admission uses `scrapeSettings.minSize`, `minWidth`, and `minHeight`; assert link collection skips `nav`, `[role="navigation"]`, and `.nav` ancestors only when `excludeNav` is true.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because the injected function has no setting argument and ignores saved options.
- [ ] **Step 3: Implement the settings path.** Invoke executeScript with `args: [{ ...settings }]`; default invalid settings defensively inside the fallback; require configured width/height only when `minSize`; retain the existing 10px technical minimum; conditionally omit navigation links using `link.closest('nav, [role="navigation"], .nav, .navigation')`.
- [ ] **Step 4: Re-run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: PASS.
- [ ] **Step 5: Commit.** Run: `git add popup.js tests/popup.test.js && git commit -m "Apply scraper settings to page extraction"`.

### Task 3: Make untrusted results safe and fully actionable

**Files:**
- Modify: `popup.js:466-729, 1635-1681`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing regression tests.** Assert `renderVideos` has no `item.innerHTML` assignment and creates `.video-url` with `textContent`/`title`; assert its copy/open controls carry explicit accessible labels; assert every video has an `a` element with `href` and `target="_blank"`; assert generated thumbnail URLs cover `/embed/` and `/video/` forms.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because video values are interpolated into HTML and embed forms are not recognized.
- [ ] **Step 3: Implement safe DOM rendering.** Build video wrapper, overlay, type/size labels, URL text, explicit `Open video` anchor, and `Copy video URL` button with DOM APIs. Use `getVideoProviderId` for thumbnail URLs; retain an open link even when no thumbnail is possible. Convert dynamic image selection, link rows, video previews, audio controls, and selection removal to semantic controls with labels and keyboard-native behavior; never build selector strings from raw scraped URLs.
- [ ] **Step 4: Re-run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: PASS.
- [ ] **Step 5: Commit.** Run: `git add popup.js tests/popup.test.js && git commit -m "Harden rendered scraper results"`.

### Task 4: Classify likely content images without hiding all assets

**Files:**
- Modify: `popup.html`
- Modify: `popup.js`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing classification tests.** Evaluate a pure `classifyImageCandidate(candidate)` helper and assert: favicon/link-icon/tracking-pixel/sprite candidates classify as junk; tiny icon/logo/avatar/header-nav images score below the content threshold; large images in `main article figure`, gallery, or product contexts score as content; uncertain images remain available with reasons; malformed values do not throw.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because classification and reason metadata do not exist.
- [ ] **Step 3: Implement deterministic scoring.** Capture safe image context flags inside the injected scraper, call a dependency-free classification routine available to the injected function, and attach `{ classification: 'content' | 'junk', classificationScore, classificationReasons }` to each image. Never discard a candidate solely for a heuristic score; retain only the existing technical safety exclusions.
- [ ] **Step 4: Make Content the default view.** Replace the initial All chip with active `Content`, retain `All assets`, Large, XL, and Domain controls, set `currentImageFilter = 'content'`, and reset to Content when changing tabs. Filter by classification in `renderImages` while keeping correct visible/total counts.
- [ ] **Step 5: Run focused and full tests.** Run: `npm test -- --runInBand tests/popup.test.js && npm test -- --runInBand && node --check popup.js`. Expected: PASS.
- [ ] **Step 6: Commit.** Run: `git add popup.html popup.js tests/popup.test.js && git commit -m "Filter likely content images by default"`.

### Task 5: Restore per-tab scrape workspaces during the browser session

**Files:**
- Modify: `popup.html`
- Modify: `popup.js`
- Modify: `background.js`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing cache-model tests.** Assert pure helpers create a versioned cache key from tab ID, serialize scraped data plus `scrapedAt`, active tab, image filter, both search terms, and selected image URLs, and trim optional page text before structured data when an explicit size budget is exceeded. Assert another tab ID cannot restore the entry.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because cache serialization/restoration helpers do not exist.
- [ ] **Step 3: Implement successful-scrape persistence.** After `getCurrentTab`, read `chrome.storage.session` for that tab key; restore compatible data and UI state, call `updateUI`, and show `Restored from this tab · scraped <relative time>`. After a successful scrape, save the versioned workspace; cache failures show a non-destructive warning while retaining in-memory results.
- [ ] **Step 4: Persist workspace changes safely.** Track current result tab in `switchTab`; debounce session writes after tab/filter/search/selection changes; intersect restored selections with restored image URLs; cancel obsolete timers when the active tab changes.
- [ ] **Step 5: Add explicit and automatic cleanup.** Add a labelled `Clear results` action that removes only the current tab key and resets in-memory UI. In `background.js`, register `chrome.tabs.onRemoved` to remove that tab's session-cache key. Preserve Chrome-session-only behavior by using `storage.session`, never `storage.local`.
- [ ] **Step 6: Run focused and full tests.** Run: `npm test -- --runInBand tests/popup.test.js && npm test -- --runInBand && node --check popup.js && node --check background.js`. Expected: PASS.
- [ ] **Step 7: Commit.** Run: `git add popup.html popup.js background.js tests/popup.test.js && git commit -m "Restore per-tab scrape sessions"`.

### Task 6: Correct user-visible operation state and clipboard reporting

**Files:**
- Modify: `popup.html`
- Modify: `popup.js:291-351, 1493-1501`
- Modify: `background.js:50-57, 101-106`
- Modify: `content.js:169-175`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing tests.** Assert the status node has `role="status"` and `aria-live="polite"`; assert scraping toggles `aria-busy`; assert `updateUI` accepts a success timestamp instead of always writing `Last scraped`; assert the error branch writes a durable error/retry message; assert the content `copyToClipboard` handler responds with success or error and background sends notification only after a successful response.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because status is generic, scrape failures are overwritten, and clipboard response is ignored.
- [ ] **Step 3: Implement truthful feedback.** Add persistent live status and a non-blocking toast with `role="status"`; set/remove `aria-busy`; preserve error state after failure; handle async `sendResponse` with `return true` in content; have background await/check the response and notify error on rejection.
- [ ] **Step 4: Re-run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: PASS.
- [ ] **Step 5: Commit.** Run: `git add popup.html popup.js background.js content.js tests/popup.test.js && git commit -m "Report scraper operations accurately"`.

### Task 7: Build semantic, keyboard-complete popup controls and dialogs

**Files:**
- Modify: `popup.html`
- Modify: `popup.js:209-288, 1620-1757`
- Modify: `styles.css`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing accessibility tests.** Assert `.tab-list` has `role="tablist"`, tab buttons have `role="tab"`, `aria-controls`, and `aria-selected`; panes have `role="tabpanel"` and `aria-labelledby`; all static icon-only buttons have `aria-label`; download and settings surfaces use `<dialog>` or `role="dialog"`, `aria-modal="true"`, labelled headings, Escape close, and focus restoration.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because tabs/dialogs are non-semantic and icon buttons are unnamed.
- [ ] **Step 3: Implement accessible interaction model.** Add semantic tab wiring in markup and synchronize selected/hidden state in `switchTab`, including arrow-key navigation. Replace the download panel with a labelled dialog/backdrop pattern; construct settings with DOM APIs, labels, numeric input constraints, focus capture, Escape/outside close, and restoration to the opener. Give every action an explicit accessible name and retain visible focus styles.
- [ ] **Step 4: Re-run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: PASS.
- [ ] **Step 5: Commit.** Run: `git add popup.html popup.js styles.css tests/popup.test.js && git commit -m "Make popup controls keyboard accessible"`.

### Task 8: Refine the responsive dark operate interface

**Files:**
- Modify: `styles.css`
- Modify: `popup.html`
- Modify: `popup.js:1504-1567, 466-628`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing contracts.** Assert CSS does not force `overflow: hidden` on the document; assert it defines `prefers-reduced-motion`; assert variables cover focus, error, success, and muted text; assert no `outerWidth >= 400` mode heuristic remains; assert filtered image/link counts describe visible and total items and empty copy differentiates a filter miss from no scraped results.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because layout is clipped, motion ignores preferences, and counts/mode are misleading.
- [ ] **Step 3: Implement the dark refinement.** Establish charcoal semantic tokens with AA-compliant muted text and a single blue action accent. Use CSS grid/flex and media queries for 320px+ popup and wide side-panel composition, retaining vertical scrolling where needed. Remove width-based mode detection; leave each available navigation control visible based on actual API availability. Apply native focus-visible, hover, disabled, loading, error, empty, selected, and reduced-motion states. Update filtered counts and empty-state copy.
- [ ] **Step 4: Re-run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: PASS.
- [ ] **Step 5: Commit.** Run: `git add popup.html popup.js styles.css tests/popup.test.js && git commit -m "Polish responsive dark scraper interface"`.

### Task 9: Reduce extension surface and remove remote executable fallback

**Files:**
- Modify: `manifest.json`
- Modify: `content.js`
- Modify: `content.css`
- Modify: `background.js:476-498`
- Modify: `tests/popup.test.js`
- Modify: `README.md`

- [ ] **Step 1: Write failing manifest/source tests.** Assert manifest has no `content_scripts` and no `host_permissions`; assert content source contains no page-overlay element; assert background does not contain a CDN `importScripts` URL and reports packaged ZIP failure rather than remote fallback.
- [ ] **Step 2: Run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: FAIL because content scripts and all-url host access are persistent and archive code tries remote fallback.
- [ ] **Step 3: Implement least privilege.** Remove declarative all-origin injection and `<all_urls>` host access, relying on the existing user-click `activeTab` execution. Remove page indicator/highlight code and orphan content CSS. Keep only local `libs/fflate.umd.js` archive support and present a clear error when it cannot load. Update README permissions and manual test notes.
- [ ] **Step 4: Re-run focused tests.** Run: `npm test -- --runInBand tests/popup.test.js`. Expected: PASS.
- [ ] **Step 5: Commit.** Run: `git add manifest.json content.js content.css background.js README.md tests/popup.test.js && git commit -m "Reduce scraper extension permissions"`.

### Task 10: Verify the complete extension path

**Files:**
- Modify: `TESTING.md`

- [ ] **Step 1: Add a manual checklist.** Document reload, popup/side-panel widths, success/failure/retry, settings filtering/navigation toggle, malicious-looking iframe dimensions, YouTube/Vimeo embeds, JSON full URL, clipboard denial, keyboard tabs/dialogs, downloads, and reduced motion.
- [ ] **Step 2: Run all automated checks.** Run: `npm test -- --runInBand && node --check popup.js && node --check background.js && node --check content.js`. Expected: all Jest tests pass and all syntax checks exit 0.
- [ ] **Step 3: Run the design detector.** Run: `node /Users/jhonniey/.agents/skills/impeccable/scripts/detect.mjs --json popup.html`. Expected: exit 0 or only reviewed false positives; fix any valid finding before proceeding.
- [ ] **Step 4: Review the final diff.** Run: `git diff --check HEAD~8..HEAD && git status --short`. Expected: no whitespace errors and only intended tracked changes.
- [ ] **Step 5: Commit verification documentation.** Run: `git add TESTING.md && git commit -m "Expand extension QA checklist"`.

## Self-review

- Spec coverage: Tasks 1–3 cover correctness, settings, exports, embeds, and rendering safety. Task 4 adds explainable default content-image classification. Task 5 adds per-tab session workspace continuity. Tasks 6–8 cover truthful feedback, accessibility, responsive dark design, and UI state. Task 9 covers permissions and executable-code policy. Task 10 covers verification and manual QA.
- Placeholder scan: clear; each task contains concrete paths, commands, and changes.
- Naming consistency: `currentTabUrl`, `getVideoProviderId`, `buildExportData`, `scrapePageFromContentFallback(scrapeSettings)`, and `switchTab` are used consistently.
