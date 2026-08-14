# Page Scraper Pro verification

This file separates repeatable automated checks from Chrome-only acceptance checks. A checked automated result does **not** imply that the Chrome checklist has been run.

## Automated verification record (2026-08-14)

Run from the repository root. These are the results recorded for the Task 10 verification pass:

| Command | Result |
| --- | --- |
| `npm test -- --runInBand` | PASS — 1 suite, 181 tests |
| `python3 -m unittest discover -s native_host/tests -v` | PASS — 193 native_host tests |
| `node --check popup.js` | PASS |
| `node --check background.js` | PASS |
| `node --check deep-video.js` | PASS |
| `node --check native-client.js` | PASS |
| `node --check libs/fflate.umd.js` | PASS |
| `node /Users/jhonniey/.agents/skills/impeccable/scripts/detect.mjs --json popup.html` | PASS — `[]` (no findings) |
| `python3 -m unittest discover -s native_host/tests -v` | PASS — 193 tests |
| Installer offline-test-fixture lifecycle (`PAGE_SCRAPER_TEST_HOME`) | PASS — install → healthcheck → uninstall in temp HOME, no residual files |
| `node -e "const m=JSON.parse(require('fs').readFileSync('manifest.json')); if(m.host_permissions||m.content_scripts) process.exit(1); if(!m.permissions.includes('nativeMessaging')) process.exit(1)"` | PASS |
| `git diff --check` | PASS |
| `git status --short` | Expected worktree state — `M README.md M TESTING.md` |

Additional packaging checks:
- `JSON.parse` successfully parsed `manifest.json`.
- `manifest.json` contains no `content.js`, `content.css`, or `content_scripts` reference.
- The manifest uses `activeTab`, `scripting`, and `nativeMessaging`; it does not request `<all_urls>` or declarative host access.

### Automated test coverage

**Extension tests (`tests/popup.test.js`, 181 tests):**
- Popup URL helper regressions (6 skipped)
- Popup UI bug fixes (4 skipped)
- Rendered video security and controls (8 tests)
- Image content classification (19 tests)
- Scraper settings injection (13 tests)
- Per-tab session workspace cache (20 tests)
- Task 6 operation state and clipboard reporting (7 skipped)
- Confirmed popup clipboard and ZIP actions (14 tests)
- Task 7 keyboard accessibility (13 skipped)
- Responsive dark Operate interface (10 skipped)
- Accessible result feedback and export actions (7 skipped)
- Least-privilege extension packaging (8 tests)
- deep-video.js state model (22 tests)
- deep-video.js helper unavailable (2 tests)
- deep-video.js behavior and accessibility (14 tests)
- Task 9: Tab/document lifecycle and session persistence (14 tests)

**Native host tests (`native_host/tests/`, 193 tests):**
- `test_config.py` — Config dataclasses, lock file, and environment detection
- `test_redaction.py` — Structured redaction with regex and path-style patterns
- `test_validation.py` — Message validation, bounded scalar/array/object, length limits
- `test_protocol.py` — Framed read/write, EOF, oversized message rejection
- `test_filenames.py` — Collision-safe sanitization and extension preservation
- `test_finder.py` — Valid/invalid directory resolution, remembered path
- `test_worker_analysis.py` — yt-dlp JSON extraction, malformed JSON handling
- `test_worker_download.py` — Format resolution, progress relay, cancellation
- `test_host.py` — Framed host loop, all command responses, error redacted
- `test_installer.py` — Staged install, rollback, offline-test-fixture, hash lock

### Static responsive smoke check

Chromium headless rendered `popup.html` at 320×700 and 760×800; screenshots were inspected from `snapshots/task10-popup-320x700.png` and `snapshots/task10-popup-760x800.png` (the directory is ignored by Git). The dark layout rendered at both sizes. At 320 px, `body.scrollWidth === innerWidth === 320`, so there was no body-level horizontal overflow; the narrow UI intentionally keeps dense control rows compact/scrollable. At 760 px, document and viewport widths were both 760 with no horizontal overflow.

The static `file://` run logged the expected `chrome.tabs.onActivated` error because extension APIs do not exist in a normal headless file page. It found no independent markup/style console failure. This smoke check does **not** exercise extension APIs, popup lifecycle, side-panel capability detection, downloads, clipboard, storage, injection, native messaging, or deep video functionality. Those remain Chrome-manual checks below.

## Manual Chrome acceptance checklist (pending)

Record Chrome version, OS, extension commit, test URLs, and pass/fail notes. Inspect both the popup console and service-worker console while testing. Use ordinary `http://`/`https://` pages unless a step explicitly tests a restricted page.

### 1. Install, invocation, and responsive shell

- [ ] Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select this repository. Confirm the card loads without manifest errors.
- [ ] After every code change, click **Reload** on the extension card, then reopen the extension UI. Confirm no stale popup/side-panel instance is being judged.
- [ ] Invoke the toolbar popup on a normal page. Confirm the dark Operate layout, visible focus treatment, readable labels/counts/status, and working page scroll.
- [ ] Open the same UI as the side panel. At a narrow panel width (approximately 320 px), confirm no page-level horizontal clipping/overflow, control rows remain usable, and all actions can be reached. Repeat at a wide width (approximately 760 px) and confirm the content expands without a fixed popup-width column.
- [ ] Resize to exactly 320 px where practical and test populated results, long URLs, filters, footer actions, settings, and downloads—not only the empty state.
- [ ] In DevTools Rendering, emulate `prefers-reduced-motion: reduce`. Confirm transitions/animations are removed or materially reduced and no state change depends on motion.

### 2. `activeTab`, reinvocation, and restricted pages

- [ ] On a normal page, reload the extension and the page, but do not invoke the extension. Confirm it has no persistent site-access grant. Click the toolbar action, then **Scrape Page Data**; confirm this user invocation grants the current-tab access needed for the scrape.
- [ ] In that same tab, navigate to a different-origin page. Confirm the old grant/results are not reused. Invoke the toolbar action again and scrape; confirm the new user gesture grants access to the new origin.
- [ ] Try `chrome://extensions`, the Chrome Web Store, a `view-source:` page, and another browser-protected page. Confirm scraping fails safely with an understandable status, controls recover, and neither console shows an uncaught extension error.
- [ ] If file-URL behavior is tested, explicitly enable **Allow access to file URLs** on the extension card and record that exception; do not treat it as ordinary web access.

### 3. Image content classification and filtering

Use a controlled HTTP test page containing: a 16x16 `favicon.ico`, a small `site-logo.png`, a 1x1 `tracking-pixel.gif`, a small `tracker.png`, a genuinely tiny UI icon, a large editorial image, a large product/gallery image, a legitimate content image named `pixel-art.png`, and a normal-size image whose descriptive text contains "tracking". Give each a visible label and known dimensions.

- [ ] After scraping, confirm **Content** is the active default and its visible/total count is clear.
- [ ] Confirm obvious favicon/logo/tracker/1x1 tracking samples are excluded from **Content**.
- [ ] Confirm the large editorial/product samples and legitimate `pixel-art.png` are retained. A normal-size legitimate image must not be rejected only because "pixel" or "tracking" occurs in its name/alt text.
- [ ] Choose **All assets** and confirm every captured asset, including excluded favicon/logo/tracker samples, becomes available.
- [ ] Exercise **Large**, **XL**, and **Domain**, then type a search matching both alt text and a URL. Confirm "no filter matches" is distinguishable from "no scraped data."
- [ ] On a controlled page containing both navigation and ordinary content links, turn **Exclude navigation links** off and scrape again; confirm navigation links are included. Turn it on and run the next scrape; confirm navigation links are excluded while ordinary content links remain.

### 4. Per-tab workspace, invalidation, and clearing

- [ ] In tab A, scrape a page, activate a non-default result tab, change the image filter, enter image and link searches, and select multiple images. Close/reopen the popup and side panel; confirm results, active result tab, filter, searches, and selections restore for tab A.
- [ ] Scrape different content in tab B with different UI state. Switch A -> B -> A and confirm each tab restores only its own workspace with no cross-tab flash or late async overwrite.
- [ ] Repeat across two browser windows. Switching a tab in the inactive window must not replace the active window's workspace.
- [ ] Start a scrape/restore in A and immediately activate B. Confirm pending A work is invalidated and cannot update B.
- [ ] Navigate A to a new document (including same-tab cross-origin navigation), then return to the extension. Confirm A's old document workspace is rejected and a fresh invocation/scrape is required. A hash-only change should not incorrectly represent a different document.
- [ ] Click **Clear results**. Confirm counts, rendered data, searches, selections, status/toast, and cached restore state clear; reopening must not resurrect the cleared workspace.

### 5. Scrape status, failures, and hostile/malformed data

- [ ] Run a successful scrape. Confirm the button exposes a busy state during work, controls recover, counts/results update, and the persistent success status is not immediately overwritten by a transient toast.
- [ ] With valid results still displayed, induce a same-workspace scrape failure (for example, temporarily make script injection fail without navigating the document). Confirm the failure is announced and the last successful results remain available rather than being destructively replaced. Record the exact failure method.
- [ ] Remove the induced failure and retry without navigating. Confirm the retry succeeds, clears the failure state, and replaces the prior results/status with the newly scraped state.
- [ ] Confirm a restricted-page failure from section 2 is handled distinctly from an empty successful result.
- [ ] Scrape a prepared page containing malformed URLs, missing optional fields, `javascript:` media/link candidates, and unknown video embed URLs. Confirm rendering does not execute script, create an unsafe clickable URL, interpolate markup, or crash; malformed items get safe defaults or are omitted.
- [ ] Corrupt a saved session value only in a disposable test profile. Confirm malformed cached objects are rejected and the UI starts clean rather than crashing.

### 6. Video behavior and exported URLs

- [ ] Scrape a page containing a YouTube embed, Vimeo embed, native HTML5 `<video>`, and direct `.mp4`/`.webm` links. Confirm YouTube/Vimeo previews resolve only for real provider hosts and native/direct videos have labelled open/copy controls.
- [ ] Include a lookalike provider hostname and an unknown embed provider. Confirm it is not misidentified as YouTube/Vimeo and receives a usable accessible fallback/open link when the URL is safe.
- [ ] Include iframes whose `width`/`height` attributes contain missing, negative, fractional, nonnumeric, extremely large, and malicious-looking string values. Confirm scraping and video rendering do not throw, distort/overflow the layout, or create unsafe actions; safe video open/copy actions still work.
- [ ] On a page whose URL includes a path, query string, and hash, export/copy JSON. Confirm the top-level page URL is the complete original URL rather than only its origin.

### 7. Deep video download (native helper)

Prerequisites: Install the native helper as described in README.md. Record the extension ID used.

#### Helper installation and availability

- [ ] With the helper installed, open the popup and navigate to the Videos tab. Confirm the **Deep Scan this page** button is visible.
- [ ] Without the helper installed (or after uninstalling), open the popup. Confirm the Deep Scan button is hidden or the UI shows installation guidance when the button is present.
- [ ] Run `python3 -m native_host.healthcheck`. Confirm it exits 0 and reports `yt-dlp` and `ffmpeg` availability.

#### Analyze (public page discovery)

Use a public YouTube video page.

- [ ] Click **Deep Scan**. Confirm the UI transitions to a "Scanning..." status.
- [ ] Confirm a valid scan result shows: thumbnail, title, provider name, and four preset options (best, 1080p, 720p, audio).
- [ ] Confirm the thumbnail is a safe HTTP(S) image. Confirm the title is displayed as plain text (not HTML).
- [ ] Confirm the scan does not use browser cookies (verify in the helper logs or network tab).
- [ ] Scan a second public video page. Navigate back. Confirm each page's scan result is preserved per tab.

#### Preset selection and download confirmation

- [ ] Select the **1080p** preset and click **Download**. Confirm the OS-native save dialog opens for the destination folder.
- [ ] After confirming the destination, confirm the UI transitions to a progress bar.
- [ ] Confirm the progress bar updates as the download proceeds.
- [ ] Confirm a completed download shows a completion message.
- [ ] Cancel an in-progress download. Confirm the download stops and the UI returns to the ready state.
- [ ] During a download, click Deep Scan again. Confirm the second request is rejected or queued (single-job behavior).
- [ ] Try each preset (best, 1080p, 720p, audio). Confirm the correct format is used.

#### Error handling

- [ ] On an unsupported page (no video media found), confirm a clear "not found" or empty-state message appears.
- [ ] On a page that causes a download error, confirm the error is shown with an actionable message.
- [ ] If `ffmpeg` is missing, confirm a helpful message about the optional dependency.
- [ ] Disconnect the network during a download. Confirm the failure is reported clearly and the UI recovers.

#### Session persistence

- [ ] With a ready deep-video result showing, close the popup. Reopen it on the same tab. Confirm the previous scan result (thumbnail, title, presets) is restored.
- [ ] During an active download, close and reopen the popup. Confirm the progress resumes or the download is shown as still active.
- [ ] Switch to a different tab while a download is active. Confirm the progress updates are still received.
- [ ] Navigate the tab to a new page. Confirm the deep-video result is cleared (new scan required).

#### No DRM bypass

- [ ] Attempt to use the helper on a known DRM-protected or paywalled video page. Confirm the helper reports an appropriate error and does not extract protected content.

### 8. Keyboard and assistive technology

- [ ] Using the keyboard only, move through the result tablist with the supported arrow/Home/End behavior; confirm focus, `aria-selected`, tab panels, and activation stay synchronized.
- [ ] Tab to every image filter and activate it with Space/Enter. Confirm pressed/active state is exposed.
- [ ] Reach an image selection control by keyboard, toggle selection, and confirm its accessible name/state changes. Confirm ordinary image-link activation still opens the image; test the documented Shift-click selection behavior separately.
- [ ] With VoiceOver, NVDA, or another screen reader, confirm scrape busy/success/failure status, copy/download feedback, filter/result changes where announced, and errors are understandable. Repeated toasts must reuse one live region rather than accumulate duplicate announcements.

### 9. Settings and download dialogs

For both the settings dialog and download dialog:

- [ ] Open with mouse and keyboard. Confirm it is a native modal, has an announced title, moves focus to the intended first control, and keeps background content inert.
- [ ] Close via its close button, Escape, and backdrop where supported. Confirm focus returns to the exact opener and closing does not trigger a background action.
- [ ] At narrow and wide widths, confirm the dialog fits the viewport and its contents scroll rather than cover inaccessible controls.
- [ ] In settings, submit blank, zero, negative, fractional, nonnumeric, and non-finite/out-of-range numeric values. Confirm saving is blocked, the first invalid field is focused/identified, and persisted valid settings are unchanged. Then save valid positive integers and confirm they affect the next scrape.

### 10. Clipboard context menu

- [ ] Select page text, right-click, and invoke each Page Scraper Pro copy context-menu action that applies. Confirm the clipboard contains the expected selection/value and success notification appears only after the write completes.
- [ ] Force clipboard failure in a disposable profile (deny access or use an inaccessible/restricted tab). Confirm no false success appears; an actionable failure notification is shown and the service worker remains responsive.
- [ ] Repeat actions several times to confirm listeners/actions are not duplicated after service-worker restarts.

### 11. Downloads and ZIP fallback

Use a page with multiple same-name files, query-string image URLs, a failed/404 asset, and selected/unselected images. Keep `chrome://downloads` and the service-worker console open.

- [ ] Download **selected images** and confirm only selected items are requested with safe, non-colliding filenames.
- [ ] Run the **individual image** download path and confirm successful files appear in `chrome://downloads`; failures are reported without aborting unrelated items.
- [ ] Run **ZIP** download and inspect the archive: expected successful assets and filenames are present, failed fetches are reported, and the archive is valid.
- [ ] In a disposable unpacked copy, remove/rename `libs/fflate.umd.js`, reload, and request ZIP. Confirm the packaged-library failure is clearly reported and the extension deliberately falls back to individual downloads; restore the file afterward. Confirm there is no CDN/network script fallback.
- [ ] Exercise video/audio/URL/JSON download/copy actions as applicable and confirm each action matches its label and current filtered/selected scope.

## Completion record

Do not mark Chrome-only items complete from Jest, source inspection, static screenshots, or a normal `file://` render. Attach screenshots or notes for failures, record popup/service-worker console output, and list any untested item explicitly in the release report.
