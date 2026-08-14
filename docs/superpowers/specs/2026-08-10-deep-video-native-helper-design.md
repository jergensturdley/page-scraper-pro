# Deep Video Discovery and Native yt-dlp Helper Design

## Goal

Extend Page Scraper Pro with an explicit **Deep Scan** workflow that uses a locally installed macOS Native Messaging helper and an isolated pinned `yt-dlp` installation. The workflow analyzes public HTTP(S) pages without cookies, presents simple download presets, lets the user choose and remember a Finder destination, and downloads only after explicit confirmation.

The existing extension-only scraper remains the fast default. Deep Scan is an opt-in escalation for pages whose video content is hidden behind player metadata, manifests, or provider extractors.

## Scope

### Included
- macOS developer/local installation for the current user; no root access.
- Chrome Native Messaging connection owned by the extension service worker.
- Public-content metadata extraction through `yt-dlp`, with cookies disabled.
- Single-video discovery and explicit downloads.
- Presets: Best, up to 1080p, up to 720p, and Audio only.
- Finder folder selection, remembered as the default and changeable before download.
- Structured progress, cancellation, retry, and actionable errors.
- Local health check, diagnostic logging with redaction, uninstall, and idempotent reinstall.

### Excluded from version one
- Browser cookies, authenticated/private/age-gated extraction, credential prompts, or proxy configuration.
- Automatic downloads, bulk playlist downloads, subscriptions, or scheduled jobs.
- Windows/Linux support, signed/notarized macOS packaging, or a network/localhost service.
- User-supplied `yt-dlp` flags, output templates, executable paths, or destination paths.
- Circumvention of DRM or access controls. DRM-protected results are reported as unavailable, never bypassed.

## Architecture

### Extension components

1. **Popup/side-panel Deep Video workspace**
   - Adds a Deep Scan action beside the existing fast video results.
   - Renders normalized media metadata and the four simple presets.
   - Shows the remembered destination, Change Folder, Download, Cancel, Retry, and current progress.
   - Preserves the always-dark responsive and accessible design system.

2. **Background native-host client**
   - `background.js` owns one lazy `chrome.runtime.connectNative('com.pagescraperpro.ytdlp')` connection.
   - Popup instances communicate with the service worker, not directly with the helper, so popup closure does not orphan an active job.
   - The client validates every helper message, correlates request/job IDs, reconnects safely, and exposes the current job snapshot to newly opened popup instances.
   - Adding this feature requires only the `nativeMessaging` extension permission; it does not restore host permissions.

3. **Per-tab deep-scan state**
   - Discovery metadata and current job references are associated with the exact tab/document URL using the same URL and generation principles as the existing session workspace.
   - A tab navigation invalidates stale discovery results. A running download remains a helper job and is not relabeled as belonging to the new document.

### Native helper components

The installer creates the helper under:

`~/Library/Application Support/Page Scraper Pro/native/`

It contains:
- an isolated Python virtual environment;
- the exact locked `yt-dlp` dependency and hashes;
- `host.py`, the Native Messaging protocol process;
- `worker.py`, the isolated analyze/download worker;
- private configuration and redacted rotating logs.

The Native Messaging manifest is installed at:

`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.pagescraperpro.ytdlp.json`

The installer requires the unpacked extension ID as an explicit argument and writes that exact `chrome-extension://<id>/` origin to `allowed_origins`. Reinstalling with a different extension ID updates the manifest deliberately; the helper never accepts arbitrary extensions.

### Process model

`host.py` remains the small protocol supervisor. It does not perform extraction in its message-reading loop. Analyze and download operations run in a separate process group using the isolated environment's Python and `worker.py`, which imports the pinned `yt_dlp` library directly.

This separation provides:
- structured `progress_hooks` rather than parsing human-formatted stderr;
- responsive cancellation while a download runs;
- hard timeouts and process-group termination;
- cleanup of partial files after cancellation or failure;
- no `shell=True` and no user-controlled command construction.

## Protocol

Chrome Native Messaging's four-byte little-endian length prefix carries UTF-8 JSON. Both directions enforce a one-megabyte message limit. Larger extractor output is normalized and truncated before transmission.

Every message has:
- `version`: protocol version `1`;
- `id`: caller-generated request identifier;
- `type`: an allowlisted command/event name;
- `payload`: a schema-validated object.

### Extension-to-helper commands
- `health`: report helper, protocol, `yt-dlp`, and `ffmpeg` status.
- `analyze`: inspect one validated HTTP(S) URL without downloading.
- `choose_directory`: open the explicit Finder directory picker.
- `download`: start one confirmed preset for an analyzed result.
- `cancel`: cancel one active job owned by this extension connection.
- `status`: return the remembered folder and current/recent job snapshot.

### Helper-to-extension events
- `response`: command completion or structured rejection.
- `progress`: bounded progress, speed, ETA, and phase.
- `completed`: final filename, directory, and media summary.
- `failed`: stable error code and safe user-facing message.
- `cancelled`: confirmed process termination and partial-file cleanup.

Unknown versions, types, fields with dangerous shapes, duplicate active IDs, invalid state transitions, and oversized frames are rejected. Raw Python tracebacks and unredacted extractor output never cross into the extension UI.

## Discovery

The existing DOM scraper remains unchanged and immediate. Deep Scan sends only the active page's normalized HTTP(S) URL to the helper.

The worker invokes the `yt_dlp` Python API with download disabled, cookies disabled, playlist expansion disabled, a bounded socket timeout, and a bounded overall process timeout. It normalizes extractor output into:
- title and provider;
- duration and live/upcoming status;
- safe HTTP(S) thumbnail;
- playlist indicator without expanding entries;
- available subtitles/languages;
- enough format facts to determine preset availability;
- DRM/unsupported indication when reported by the extractor.

The helper drops raw comments, descriptions beyond the agreed display limit, cookies, headers, extractor internals, and unrelated playlist entries. URL query values are not written to normal logs.

## Presets

The extension displays only presets that the helper can resolve:

- **Best**: best compatible video plus audio, falling back to the best combined format.
- **1080p**: best video no higher than 1080p plus audio, with a compatible combined fallback.
- **720p**: best video no higher than 720p plus audio, with a compatible combined fallback.
- **Audio only**: best available audio in its source-compatible container; version one does not promise MP3 conversion.

The helper, not the extension, owns the exact format selectors. The extension sends only the preset enum. If separate streams require merging and `ffmpeg` is unavailable, the helper returns a stable `ffmpeg_required` error and, where possible, advertises a compatible single-file fallback preset.

Playlists are identified but cannot be bulk-selected or downloaded in version one.

## Finder destination and filesystem safety

`choose_directory` launches `/usr/bin/osascript` with a constant, bundled AppleScript that opens the standard folder chooser. No URL, title, filename, or caller text is interpolated into AppleScript.

After selection, the helper:
- resolves the real path;
- requires an existing writable directory;
- rejects the helper runtime/config directory and other unsafe targets;
- stores the chosen path in a user-private helper config with restrictive permissions;
- returns only the display-safe directory path to the extension.

The remembered folder is the default for later downloads and can be changed explicitly. The extension cannot submit a path in a download command.

Filenames are generated inside the helper, sanitized, length-bounded, collision-safe, and joined beneath the validated destination. Temporary and partial artifacts live in a helper-managed temporary directory. Completion uses an atomic move into the destination after verifying that the resolved final path remains beneath that destination.

## Download lifecycle

1. User completes Deep Scan.
2. User chooses one available preset.
3. User confirms Download.
4. If no valid remembered destination exists, the helper opens Finder selection first.
5. The helper creates a job ID, launches the worker process group, and emits progress.
6. The worker downloads to its temporary area and merges only when required and supported.
7. The helper atomically moves the completed file, emits `completed`, and records only a bounded recent job snapshot.
8. Cancel terminates the entire process group, waits for exit, removes partial files, and emits `cancelled`.

Only one download job runs at a time in version one. A second request receives `busy`; it is not silently queued. Closing and reopening the popup restores progress by querying the service-worker-held native connection. Closing Chrome terminates the host and therefore cancels/cleans an unfinished local job.

## Error model

Stable error codes cover:
- `helper_unavailable` / `protocol_mismatch`;
- `invalid_url` / `restricted_url`;
- `unsupported_site` / `drm_protected`;
- `analysis_timeout` / `network_error`;
- `folder_cancelled` / `folder_unwritable`;
- `format_unavailable` / `ffmpeg_required`;
- `busy` / `download_failed` / `cancel_failed`.

Failures remain visible with Retry, Change Folder, or installation guidance as appropriate. Successful existing scrape results are preserved when Deep Scan fails.

Diagnostics rotate locally and include timestamps, error codes, extractor name, and bounded technical context. URL query strings, fragments, authorization values, cookies, and filesystem content are redacted. Diagnostics are never transmitted.

## Installation and updates

The developer installer:
- runs without `sudo`;
- verifies Python availability and supported version;
- creates or repairs the isolated environment;
- installs only exact dependency versions from a hash-locked requirements file;
- installs helper sources and restrictive file permissions;
- writes the Chrome Native Messaging manifest for the explicit extension ID;
- runs `health` framing and dependency checks;
- is safe to rerun.

An uninstall command removes the Native Messaging manifest and helper runtime after confirming their expected paths. It does not delete downloaded media. The helper never self-updates. Updating `yt-dlp` requires changing and reviewing the lock file, then explicitly rerunning the installer.

## Accessibility and dark UI integration

The Deep Video workspace follows the existing always-dark semantic tokens and responsive layout. It adds no light theme.

- Deep Scan, Change Folder, Download, Cancel, and Retry are native labelled buttons.
- Scan state and download progress use concise polite live regions; failures use persistent status text.
- Presets are a labelled single-selection radio group.
- Progress is exposed with a native/ARIA progressbar and textual percentage/phase.
- Keyboard focus moves to discovery results after success, to the actionable error after failure, and returns sensibly after Finder interaction.
- Reduced-motion mode disables nonessential progress animation without hiding numerical state.
- Long titles, paths, provider strings, and error text wrap without horizontal overflow at 320px.

## Security properties

- No cookies or Chrome profile access.
- No localhost listener, remote control endpoint, telemetry, or background scheduler.
- No raw command-line options, shell execution, caller paths, or caller output templates.
- HTTP(S)-only analysis URLs with length and scheme validation.
- Exact extension origin allowlist in the native-host manifest.
- Protocol/schema/message-size enforcement on both sides.
- One bounded child process group and deterministic cleanup.
- Destination confinement and collision-safe generated names.
- No DRM or access-control bypass.
- No automatic or scan-triggered download.

## Testing

### Automated helper tests
- Native Messaging framing, partial reads, multiple frames, malformed JSON, and oversized rejection.
- Command and event schema validation, protocol mismatch, and invalid transitions.
- HTTP(S) URL allowlist and restricted/malformed URL rejection.
- Finder cancellation, stored-folder validation, permissions, and destination confinement.
- Preset mapping and `ffmpeg` fallback decisions.
- Filename sanitization, collisions, traversal attempts, and atomic completion.
- Mocked analyze success/unsupported/timeout/network/DRM cases.
- Mocked download progress, completion, partial failure, cancellation, process-group cleanup, and busy rejection.
- Diagnostic redaction for query values, fragments, headers, and filesystem-sensitive strings.
- Idempotent installer, wrong extension ID, health check, and uninstall path validation.

### Automated extension tests
- Native host missing, protocol mismatch, reconnect, and stale response rejection.
- Deep Scan result normalization and safe DOM rendering.
- Preset availability/selection and explicit confirmation.
- Remembered folder display and Change Folder flows.
- Progress, completion, error, Retry, and Cancel UI state.
- Popup reopen/status resynchronization and tab-navigation invalidation.
- Keyboard, focus, live-region, reduced-motion, and 320px overflow contracts.
- Existing 129-test scraper regression suite remains green.

### Manual macOS acceptance
- Install, reinstall, health check, and uninstall without administrator privileges.
- Load unpacked extension and verify Chrome establishes only the named Native Messaging connection.
- Analyze public test media without downloading or reading cookies.
- Finder selection, remembered default, Change Folder, cancelled picker, and unwritable-folder recovery.
- Best, 1080p, 720p, and Audio-only downloads on lawful public test media.
- Missing `ffmpeg` explanation and compatible fallback.
- Progress accuracy, popup closure/reopen, cancellation, and partial cleanup.
- Offline, unsupported, restricted, helper-missing, helper-version, and timeout failures.
- Existing fast scrape, per-tab cache, content filter, dark layout, clipboard, archive, and permission behavior remain intact.

## Documentation

README and TESTING documentation will include:
- explicit macOS developer installation, health, update, and uninstall commands;
- how to find and pass the unpacked extension ID;
- the new `nativeMessaging` permission and its exact purpose;
- cookie-free/public-content and no-DRM boundaries;
- Finder destination behavior and where helper configuration/logs live;
- lawful-use notice and the difference between discovery and explicit download;
- automated verification and truthful pending manual Chrome checks.
