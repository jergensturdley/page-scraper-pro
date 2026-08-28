# Deep Video Native Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in macOS Deep Scan workflow that analyzes public video pages through a local Native Messaging helper using pinned `yt-dlp`, then downloads an explicitly selected preset into a Finder-selected remembered directory.

**Architecture:** A classic Manifest V3 background service worker owns a lazy, long-lived connection to `com.pagescraperpro.ytdlp`; popup instances send validated commands through the background and rehydrate from its job snapshot. A stdlib-only Python Native Messaging supervisor validates framed JSON and runs analysis/downloads in a separate process group whose worker imports a hash-locked `yt-dlp==2026.7.4`. The helper owns Finder selection, filesystem paths, format selectors, progress, cancellation, cleanup, configuration, and redacted diagnostics; the extension never supplies raw options or paths.

**Tech Stack:** Chrome Manifest V3, plain JavaScript/HTML/CSS, Jest 30, Python 3.10+ stdlib `unittest`, Chrome Native Messaging, `yt-dlp==2026.7.4`, macOS `osascript`, optional system `ffmpeg` detected by `yt-dlp`.

---

## File responsibility map

### Native helper
- Create `native_host/__init__.py` — package marker and helper version.
- Create `native_host/constants.py` — protocol, host, size, timeout, and path constants.
- Create `native_host/protocol.py` — length-prefixed JSON reads/writes and envelope validation.
- Create `native_host/validation.py` — allowlisted command payloads, URLs, IDs, presets, and event validation.
- Create `native_host/redaction.py` — URL/query/authorization/path-safe diagnostics.
- Create `native_host/config.py` — private atomic config and recent-job snapshots.
- Create `native_host/finder.py` — constant AppleScript folder chooser and writable-directory validation.
- Create `native_host/worker.py` — `yt_dlp` analyze/download worker and normalized stdout events.
- Create `native_host/jobs.py` — one-job process-group lifecycle, progress relay, cancellation, and cleanup.
- Create `native_host/host.py` — Native Messaging command loop and command dispatcher.
- Create `native_host/installer.py` — user-local venv/source/manifest installation and health validation.
- Create `native_host/uninstaller.py` — confined removal of installed manifest/runtime.
- Create `native_host/healthcheck.py` — framed end-to-end helper health probe.
- Create `native_host/requirements.lock` — exact stable dependency and package hashes.
- Create `native_host/tests/*.py` — stdlib unit/integration tests with no network or copyrighted media.

### Extension
- Create `native-client.js` — testable native-port client, correlation, reconnect, timeouts, and snapshot reducer.
- Create `deep-video.js` — pure Deep Video state reducer, result normalization, and UI-format helpers.
- Modify `background.js` — import the packaged native client and bridge popup commands/events.
- Modify `manifest.json` — add only `nativeMessaging`.
- Modify `popup.html` — add accessible Deep Scan/result/preset/destination/progress controls.
- Modify `popup.js` — wire Deep Scan commands, safe DOM rendering, per-tab state, and job resynchronization.
- Modify `styles.css` — extend the existing always-dark responsive tokens and reduced-motion behavior.
- Modify `tests/popup.test.js` — extension/native-client/state/accessibility regression coverage.
- Modify `README.md` and `TESTING.md` — install, privacy, lawful-use, troubleshooting, and manual acceptance.

## Shared verification commands

Run these after every task that touches the relevant language:

```bash
python3 -m unittest discover -s native_host/tests -v
npm test -- --runInBand
node --check native-client.js
node --check deep-video.js
node --check background.js
node --check popup.js
git diff --check
git status --short
```

No test may access live media sites, read browser cookies, write outside a test temporary directory, or perform a real download.

---

### Task 1: Native Messaging framing and strict schemas

**Files:**
- Create: `native_host/__init__.py`
- Create: `native_host/constants.py`
- Create: `native_host/protocol.py`
- Create: `native_host/validation.py`
- Create: `native_host/tests/__init__.py`
- Create: `native_host/tests/test_protocol.py`
- Create: `native_host/tests/test_validation.py`

- [ ] **Step 1: Create the package constants and failing framing tests**

Use these fixed constants in `native_host/constants.py`:

```python
PROTOCOL_VERSION = 1
HELPER_VERSION = "1.0.0"
HOST_NAME = "com.pagescraperpro.ytdlp"
MAX_MESSAGE_BYTES = 1_048_576
MAX_URL_LENGTH = 8_192
REQUEST_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
ALLOWED_COMMANDS = frozenset({
    "health", "analyze", "choose_directory", "download", "cancel", "status"
})
ALLOWED_PRESETS = frozenset({"best", "1080p", "720p", "audio"})
```

In `test_protocol.py`, cover a fragmented frame, two consecutive frames, invalid UTF-8, malformed JSON, zero-length input, truncated payload, and a declared length above `MAX_MESSAGE_BYTES`. Use `io.BytesIO` only. The happy-path assertion must round-trip:

```python
message = {"version": 1, "id": "req_1", "type": "health", "payload": {}}
wire = io.BytesIO()
write_message(wire, message)
wire.seek(0)
self.assertEqual(read_message(wire), message)
```

- [ ] **Step 2: Run the framing tests and confirm RED**

Run:

```bash
python3 -m unittest native_host.tests.test_protocol -v
```

Expected: import failure because `read_message` and `write_message` do not exist.

- [ ] **Step 3: Implement bounded framing in `protocol.py`**

Implement `read_exact`, `read_message`, and `write_message`. Read the four-byte unsigned little-endian length, reject lengths outside `1..MAX_MESSAGE_BYTES`, decode strict UTF-8, require a JSON object, and flush writes. Define stable exceptions `ProtocolError`, `FrameTooLarge`, and `EndOfStream`. `EndOfStream` is valid only when zero header bytes were read; a partial header is `ProtocolError`.

- [ ] **Step 4: Write failing schema tests**

In `test_validation.py`, assert `validate_command`:
- accepts all six exact command shapes;
- rejects unknown/extra top-level and payload fields;
- rejects protocol versions other than `1`;
- rejects IDs outside `REQUEST_ID_PATTERN`;
- rejects non-HTTP(S), credential-bearing, hostless, or overlong URLs;
- rejects a download payload containing `path`, `format`, `args`, `output_template`, or `cookies`;
- accepts only the four preset enums;
- requires `download` to reference an analyzed `media_id` and optional confirmation token, never a filesystem path.

Use the exact download shape:

```python
{
    "version": 1,
    "id": "req_download_1",
    "type": "download",
    "payload": {"media_id": "media_abc", "preset": "1080p", "confirmed": True},
}
```

- [ ] **Step 5: Implement strict validation in `validation.py`**

Provide:

```python
def normalize_http_url(value: object) -> str: ...
def validate_request_id(value: object) -> str: ...
def validate_command(message: object) -> dict[str, object]: ...
def make_response(request_id: str, payload: dict[str, object]) -> dict[str, object]: ...
def make_error(request_id: str, code: str, message: str) -> dict[str, object]: ...
def validate_event(message: object) -> dict[str, object]: ...
```

Use exact-key comparisons for each command. Normalize schemes/hosts to lowercase, remove URL fragments, preserve path/query for extraction, and reject usernames/passwords. Do not resolve URLs or make network requests.

- [ ] **Step 6: Run helper tests and commit**

Run:

```bash
python3 -m unittest native_host.tests.test_protocol native_host.tests.test_validation -v
git diff --check
git add native_host
 git commit -m "Add native helper protocol validation"
```

Expected: all protocol/schema tests pass and the commit contains no extension changes.

---

### Task 2: Private configuration, Finder selection, and diagnostic redaction

**Files:**
- Create: `native_host/config.py`
- Create: `native_host/finder.py`
- Create: `native_host/redaction.py`
- Create: `native_host/tests/test_config.py`
- Create: `native_host/tests/test_finder.py`
- Create: `native_host/tests/test_redaction.py`

- [ ] **Step 1: Write failing private-config tests**

Using `tempfile.TemporaryDirectory`, test:
- missing config returns `{destination: None, recent_job: None}`;
- save uses a temporary file plus `os.replace`;
- resulting mode is `0o600` and parent directory mode is `0o700`;
- only `destination` and a bounded normalized `recent_job` are persisted;
- malformed JSON, symlink config files, wrong types, and paths outside the injected app root are rejected;
- at most one recent job snapshot with safe scalar fields is retained.

- [ ] **Step 2: Implement `HelperConfig`**

Use an injected `app_dir: Path` and expose:

```python
class HelperConfig:
    def load(self) -> dict[str, object]: ...
    def save_destination(self, destination: Path | None) -> None: ...
    def save_recent_job(self, snapshot: dict[str, object] | None) -> None: ...
    def get_destination(self) -> Path | None: ...
```

Never follow a config symlink. Write JSON with `sort_keys=True`, `ensure_ascii=False`, and restrictive permissions before `os.replace`.

- [ ] **Step 3: Write failing Finder and path-validation tests**

Test `choose_directory(run=mock_run)` for successful POSIX output, user cancellation, AppleScript failure, newline stripping, and the exact subprocess argument list. Test `validate_destination` rejects nonexistent paths, files, symlinks, unwritable directories, the helper app directory, and resolved traversal; it accepts a writable real directory.

The AppleScript must remain a constant:

```python
CHOOSE_FOLDER_SCRIPT = (
    'tell application "Finder" to activate\n'
    'POSIX path of (choose folder with prompt "Choose a Page Scraper Pro download folder")'
)
```

Call only:

```python
["/usr/bin/osascript", "-e", CHOOSE_FOLDER_SCRIPT]
```

- [ ] **Step 4: Implement Finder selection and validation**

Define `FolderSelectionCancelled` separately from `FolderSelectionError`. Resolve the returned path, verify it is writable using `os.access`, reject the private helper root, and return the validated `Path`. No caller-provided string may enter the AppleScript or subprocess arguments.

- [ ] **Step 5: Write and implement redaction tests**

Test that:
- `https://user:pass@example.com/watch?v=secret#token` becomes `https://example.com/watch?<redacted>`;
- authorization/cookie header values become `<redacted>`;
- strings beneath the user's home directory become `~/<redacted>`;
- ordinary extractor names and stable error codes remain useful;
- recursive dictionaries/lists are bounded by depth, entries, and string length.

Implement `redact_url`, `redact_text`, and `redact_mapping`. Never log a full query, fragment, authorization value, cookie, destination filename, or traceback by default.

- [ ] **Step 6: Run helper tests and commit**

Run:

```bash
python3 -m unittest native_host.tests.test_config native_host.tests.test_finder native_host.tests.test_redaction -v
git add native_host
git commit -m "Secure native helper configuration"
```

Expected: all tests pass without opening Finder or touching real Application Support.

---

### Task 3: Cookie-free analysis worker and normalized media model

**Files:**
- Create: `native_host/worker.py`
- Create: `native_host/tests/test_worker_analysis.py`
- Create: `native_host/tests/fixtures/public_info.json`
- Modify: `native_host/validation.py`

- [ ] **Step 1: Add a lawful synthetic extractor fixture**

Create a minimal fixture containing one public media item with title, duration, extractor, thumbnail, subtitles, and formats representing combined 480p, video-only 720p/1080p, and audio-only streams. Include noisy description/comments/headers/cookies fields that must not appear in normalized output, plus a playlist-shaped fixture with two entries that must be reported but not expanded.

- [ ] **Step 2: Write failing analyzer tests**

Inject a fake `YoutubeDL` class and assert `analyze_url`:
- passes `skip_download=True`, `noplaylist=True`, `socket_timeout=15`, `quiet=True`, and no cookie/browser-profile setting;
- calls `extract_info(url, download=False)` exactly once;
- returns a deterministic `media_id`, bounded title/provider/duration/thumbnail/subtitle list, playlist indicator, live state, and available presets;
- never returns raw formats, headers, cookies, descriptions, comments, webpage HTML, or unbounded playlist entries;
- maps unsupported, DRM, timeout, and network exceptions to stable error codes.

- [ ] **Step 3: Implement analysis normalization**

Add:

```python
PRESET_SELECTORS = {
    "best": "bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720p": "bv*[height<=720]+ba/b[height<=720]/b",
    "audio": "ba/b",
}

def analyze_url(url: str, ydl_factory=None) -> dict[str, object]: ...
def normalize_media_info(info: dict[str, object]) -> dict[str, object]: ...
def available_presets(formats: list[dict[str, object]]) -> list[str]: ...
```

Create `media_id` from a SHA-256 digest of normalized URL + extractor + extractor ID, exposing only the first 24 hex characters. Accept thumbnail URLs only through `normalize_http_url`. Bound title/provider to 300/80 characters, subtitles to 100 languages, and result JSON below the protocol limit.

- [ ] **Step 4: Implement worker stdin/stdout event mode**

`python -m native_host.worker` reads exactly one JSON line from stdin. For an `analyze` job it validates the job object, emits newline-delimited JSON events (`phase`, then `completed` or `failed`), flushes every line, and exits nonzero on failure. This is an internal worker protocol, distinct from four-byte Native Messaging framing.

- [ ] **Step 5: Run analysis tests and commit**

Run:

```bash
python3 -m unittest native_host.tests.test_worker_analysis -v
python3 -m native_host.worker <<'EOF'
{"operation":"analyze","url":"file:///tmp/not-allowed"}
EOF
```

Expected: unit tests pass; the manual worker probe emits `invalid_url` and exits nonzero without accessing the filesystem URL.

Commit:

```bash
git add native_host
git commit -m "Add cookie-free deep video analysis"
```

---

### Task 4: Preset downloads, safe filenames, progress, and atomic completion

**Files:**
- Modify: `native_host/worker.py`
- Create: `native_host/tests/test_worker_download.py`
- Create: `native_host/tests/test_filenames.py`

- [ ] **Step 1: Write failing preset/filename tests**

Assert the extension-facing preset enum is the only input to `build_download_options`. Exact selectors must come from `PRESET_SELECTORS`. Test generated output names for blank titles, Unicode, reserved separators, `..`, control characters, 255-character input, collisions, and hidden-file prefixes. Every result must be a plain basename and remain within 180 UTF-8 bytes.

Use these interfaces:

```python
def sanitize_filename(title: object, media_id: str, extension: str) -> str: ...
def collision_safe_path(directory: Path, filename: str) -> Path: ...
def build_download_options(preset: str, temp_dir: Path, progress_hook) -> dict[str, object]: ...
```

- [ ] **Step 2: Implement safe output and preset options**

The worker chooses the template beneath its injected temporary directory and sets `format` from `PRESET_SELECTORS`, `noplaylist=True`, `continuedl=True`, and `overwrites=False`. Do not set cookie, browser, proxy, exec, postprocessor command, or caller-supplied template options. Audio uses the source-compatible best-audio container; do not promise MP3.

- [ ] **Step 3: Write failing download lifecycle tests**

With a fake `YoutubeDL`, temporary directories, and a fake progress emitter, cover:
- progress normalization for downloading, merging/post-processing, and finished phases;
- bounded percent/speed/ETA values and absence of source URLs/filenames in progress messages;
- successful file discovery and `os.replace` into the validated destination;
- collision suffixes without overwrite;
- separate-stream format plus missing `ffmpeg` mapping to `ffmpeg_required`;
- extractor failure cleanup;
- destination changed/unwritable before final move;
- no final path escaping the destination after symlink/path resolution checks.

- [ ] **Step 4: Implement `download_media` and atomic move**

The host-created job supplies a trusted temporary directory and already validated destination; the extension never supplies either. The worker still revalidates both. On success, derive the actual downloaded file from `requested_downloads`/prepared filename, sanitize the final basename, re-resolve the destination, verify confinement, and `os.replace` only after the file is complete. Emit only display-safe basename and directory.

- [ ] **Step 5: Add worker download CLI mode and run tests**

The internal worker accepts:

```json
{
  "operation": "download",
  "url": "https://example.test/public-video",
  "media_id": "media_abc",
  "preset": "720p",
  "temp_dir": "/trusted/host/generated/temp",
  "destination": "/trusted/host/validated/destination"
}
```

Only `host.py` may create that object. Validate it again in the worker and emit `progress`, `completed`, `failed`, or `cancelled` newline events.

Run:

```bash
python3 -m unittest native_host.tests.test_worker_download native_host.tests.test_filenames -v
git add native_host
git commit -m "Add safe preset video downloads"
```

---

### Task 5: Job manager, cancellation, host dispatcher, and status recovery

**Files:**
- Create: `native_host/jobs.py`
- Create: `native_host/host.py`
- Create: `native_host/tests/test_jobs.py`
- Create: `native_host/tests/test_host.py`

- [ ] **Step 1: Write failing job-manager tests**

Inject `popen`, clock, temp-root, and event callback. Assert:
- only one active job is accepted; a second returns `busy`;
- subprocess args are exactly `[sys.executable, "-m", "native_host.worker"]`;
- `shell` is false/absent, `start_new_session=True`, and the internal job travels through stdin;
- newline worker events are schema-validated and relayed with the owning job ID;
- analyze timeout and download timeout terminate the process group;
- cancel sends `SIGTERM`, waits two seconds, escalates to `SIGKILL`, removes the generated temp directory, and reports `cancelled`;
- process failure or invalid stdout becomes a stable error and cleanup still runs;
- recent completed/failed snapshot is bounded and saved through `HelperConfig`.

- [ ] **Step 2: Implement `JobManager`**

Expose:

```python
class JobManager:
    def start_analyze(self, request_id: str, url: str) -> dict[str, object]: ...
    def start_download(self, request_id: str, job: dict[str, object]) -> dict[str, object]: ...
    def cancel(self, job_id: str) -> dict[str, object]: ...
    def snapshot(self) -> dict[str, object]: ...
    def close(self) -> None: ...
```

Use one daemon reader thread for worker stdout and a lock around mutable job state. The Native Messaging output callback must be serialized by `host.py`; worker threads never write stdout directly.

- [ ] **Step 3: Write failing host-dispatch tests**

Test exact responses for health, analyze, choose_directory, download, cancel, and status. Confirm:
- analyze stores a bounded media record keyed by `media_id` after completion;
- download rejects unknown/stale media IDs, `confirmed is not True`, invalid presets, or missing destination;
- download resolves URL/destination only from host-owned analysis/config state;
- choose_directory stores only a helper-validated path;
- status returns helper/protocol/yt-dlp/ffmpeg versions, display-safe folder, and current/recent job;
- every exception is converted to a safe stable error and redacted diagnostics.

- [ ] **Step 4: Implement the framed host loop**

`host.main(stdin=sys.stdin.buffer, stdout=sys.stdout.buffer)` must:
1. read one bounded message;
2. validate it;
3. dispatch through a `HostDispatcher`;
4. write the correlated response under one output lock;
5. let `JobManager` use the same locked writer for progress events;
6. on EOF, cancel/clean the active job and exit.

Configure a rotating file log under the private app directory with three files of at most 512 KiB each. Apply `redact_mapping` before logging structured context.

- [ ] **Step 5: Add end-to-end framed host tests**

Feed multiple framed commands through `BytesIO`, inject fake analyzer/job/Finder dependencies, and decode all response/event frames. Include malformed input followed by termination, request correlation, and a popup-reopen `status` request while a fake download is active.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
python3 -m unittest native_host.tests.test_jobs native_host.tests.test_host -v
git add native_host
git commit -m "Add native helper job supervision"
```

---

### Task 6: Hash-locked user-local installer, health check, and uninstall

**Files:**
- Create: `native_host/requirements.lock`
- Create: `native_host/installer.py`
- Create: `native_host/uninstaller.py`
- Create: `native_host/healthcheck.py`
- Create: `native_host/tests/test_installer.py`
- Create: `native_host/tests/test_healthcheck.py`

- [ ] **Step 1: Add the reviewed dependency lock**

Write exactly:

```text
yt-dlp==2026.7.4 \
    --hash=sha256:b094813404f87a9dd2186f00815231df32e5fd8a5403be0f807b3bb2d21a4432 \
    --hash=sha256:f11f2b11d5a8ac4059f9bdf29fa4407dc7c6bb00c5097e95ca22a7a9db518266
```

The stable release and hashes were resolved from PyPI during planning. Installation must use `pip install --require-hashes --no-deps -r requirements.lock`.

- [ ] **Step 2: Write failing installer tests**

Using injected roots and command/copy functions, test:
- Chrome extension IDs must match `^[a-p]{32}$`;
- Python below 3.10 is rejected;
- no command contains `sudo`;
- app/manifest paths remain under the injected user roots;
- venv creation, hash-required install, source copy, launcher, manifest, permissions, and health probe occur in order;
- rerun repairs files without duplicating config or erasing destination;
- wrong existing manifest origin is deliberately replaced only for the explicit new ID;
- any failed stage leaves either the prior healthy install or removes the new staging directory;
- launcher and manifest use absolute paths and exact `allowed_origins`.

The generated manifest shape is:

```json
{
  "name": "com.pagescraperpro.ytdlp",
  "description": "Page Scraper Pro local yt-dlp helper",
  "path": "/absolute/user/path/to/page-scraper-pro-native-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"]
}
```

- [ ] **Step 3: Implement staged idempotent install**

Provide CLI:

```bash
python3 -m native_host.installer --extension-id <32-char-id>
```

Create a sibling staging directory, venv, locked dependency, copied package, and executable launcher (`0o700`). Run the framed health check through the staged launcher, then atomically swap it into `~/Library/Application Support/Page Scraper Pro/native/`. Write the Native Messaging manifest mode `0o600`. Preserve validated config across repair installs. Never use root, Homebrew, a network listener, or an unpinned package.

- [ ] **Step 4: Implement health check**

`healthcheck.py` launches the installed host, writes one framed health command, reads one bounded response with a five-second timeout, validates protocol/helper/yt-dlp fields, terminates the child, and exits nonzero on mismatch. It must never start an analysis or download.

- [ ] **Step 5: Implement confined uninstall and tests**

CLI:

```bash
python3 -m native_host.uninstaller --extension-id <32-char-id>
```

Verify the expected manifest name/path/origin before removing it and the expected helper runtime. Reject symlinks or unexpected paths. Do not remove the remembered download directory or any media. Add `--keep-config` to retain only private config for a later reinstall; default removes the helper config/logs.

- [ ] **Step 6: Run installer tests and commit**

Run:

```bash
python3 -m unittest native_host.tests.test_installer native_host.tests.test_healthcheck -v
git add native_host
git commit -m "Add macOS native helper installer"
```

---

### Task 7: Testable extension native client and background bridge

**Files:**
- Create: `native-client.js`
- Modify: `background.js`
- Modify: `manifest.json`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing native-client tests**

Load `native-client.js` through the existing VM helper. With a fake native `Port`, test:
- lazy `connectNative('com.pagescraperpro.ytdlp')`;
- one connection shared across popup commands;
- generated request IDs and pending-response correlation;
- one-megabyte outgoing/incoming JSON limit;
- schema/version/type rejection;
- request timeout cleanup;
- disconnect rejects pending requests as `helper_unavailable` and records a safe snapshot;
- reconnection happens only on a later explicit command;
- progress/completed/failed/cancelled events update one bounded snapshot and notify subscribers;
- stale/unknown job events cannot overwrite the active snapshot.

- [ ] **Step 2: Implement `native-client.js` as a packaged UMD module**

Expose under `globalThis.PageScraperNative` and `module.exports` for Jest:

```javascript
class NativeHostClient {
  constructor({ runtime, hostName, timeoutMs = 30000, now = Date.now }) {}
  send(type, payload = {}) {}
  snapshot() {}
  subscribe(listener) {}
  disconnect() {}
}

function validateNativeMessage(message) {}
function reduceNativeSnapshot(snapshot, event) {}
```

Only allow the six command types and four progress terminal event types. Never log full source URLs or native payloads.

- [ ] **Step 3: Add the exact manifest permission test**

Update the existing exact permission-set test from seven to eight entries by adding only `nativeMessaging`. Continue asserting `host_permissions` and `content_scripts` are absent.

- [ ] **Step 4: Bridge popup messages in `background.js`**

At the top load only the packaged module:

```javascript
importScripts('native-client.js');
```

Create one `NativeHostClient`. Add a single popup action `deepVideoCommand` whose `command` must be one of the six allowlisted enums; forward its validated payload. Add `deepVideoStatus` to return the current snapshot without connecting if no helper has been used. Broadcast bounded state changes with `chrome.runtime.sendMessage({ action: 'deepVideoEvent', event })`, ignoring the expected “receiving end does not exist” error when the popup is closed.

- [ ] **Step 5: Run JS tests and commit**

Run:

```bash
npm test -- --runInBand
node --check native-client.js
node --check background.js
node -e "JSON.parse(require('fs').readFileSync('manifest.json'))"
git add native-client.js background.js manifest.json tests/popup.test.js
git commit -m "Connect extension to native video helper"
```

Expected: prior 129 tests and new native-client tests pass; permissions remain least-privilege.

---

### Task 8: Accessible Deep Video state model and popup workspace

**Files:**
- Create: `deep-video.js`
- Modify: `popup.html`
- Modify: `popup.js`
- Modify: `styles.css`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing pure state tests**

Define states `idle`, `checking`, `scanning`, `ready`, `choosing_folder`, `downloading`, `completed`, and `error`. Test legal transitions and reject stale generation/source URL/job events. Test normalized media/preset/folder/progress data, bounded titles/path/error text, HTTP(S)-only thumbnail, monotonic progress, and preservation of existing scrape results after Deep Scan failure.

- [ ] **Step 2: Implement `deep-video.js`**

Expose:

```javascript
const DEEP_VIDEO_INITIAL_STATE = Object.freeze({ phase: 'idle', generation: 0 });
function reduceDeepVideoState(state, action) {}
function normalizeDeepVideoResult(payload) {}
function normalizeDeepVideoProgress(payload) {}
function availablePresetOptions(result) {}
```

Use explicit action/phase tables rather than accepting arbitrary events. No function may generate yt-dlp options, paths, or filenames.

- [ ] **Step 3: Add semantic static markup**

Inside the Videos tab add:
- `#deepScanBtn` labelled “Deep scan this page”;
- `#deepVideoStatus` persistent polite atomic status;
- `#deepVideoPanel` with labelled result title/provider/duration/thumbnail;
- `#deepVideoPresets` fieldset/radiogroup for Best, 1080p, 720p, Audio only;
- `#deepVideoDestination`, `#chooseDeepVideoFolderBtn`;
- `#deepVideoDownloadBtn`, `#deepVideoCancelBtn`, `#deepVideoRetryBtn`;
- native progress element plus textual phase/percentage;
- installation guidance region shown for `helper_unavailable`.

All controls must be static or created through DOM APIs, have action-oriented accessible names, and remain hidden/disabled according to reducer state rather than removed while focused.

- [ ] **Step 4: Wire popup commands and safe rendering**

Load `deep-video.js` before `popup.js`. Implement `sendDeepVideoCommand(command, payload)`, `renderDeepVideo(state)`, and action handlers. Analyze sends only the exact current normalized page URL. Download sends only `{media_id, preset, confirmed: true}`. Choose Folder sends no path. Retry repeats only the last safe action. Cancel sends only the active job ID from trusted state.

Never use scraped/native values with `innerHTML`; use `textContent`, safe image URL normalization, and native attributes.

- [ ] **Step 5: Extend always-dark responsive CSS**

Reuse existing semantic tokens. At 320px stack metadata, presets, destination, and actions without horizontal overflow. Provide visible focus, disabled/loading/error/completed states, a practical progress target, wrapped paths/titles, and `prefers-reduced-motion` rules. Do not add gradients, glass, light theme, or a second accent color.

- [ ] **Step 6: Add behavior/accessibility tests**

Cover helper missing, scan success/failure, unavailable preset, Finder cancel/success, explicit confirmation, download progress/completion/failure, Cancel/Retry, no raw options/paths, DOM-safe strings, radio labels, live status, progress semantics, focus movement, and 320px structural contracts.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
npm test -- --runInBand
node --check deep-video.js
node --check popup.js
node ~/.agents/skills/impeccable/scripts/detect.mjs --json popup.html
git add deep-video.js popup.html popup.js styles.css tests/popup.test.js
git commit -m "Add accessible deep video workspace"
```

---

### Task 9: Exact-tab persistence, popup resynchronization, and navigation safety

**Files:**
- Modify: `popup.js`
- Modify: `background.js`
- Modify: `deep-video.js`
- Modify: `tests/popup.test.js`

- [ ] **Step 1: Write failing tab/document lifecycle tests**

Mock tabs A/B in two windows and source URLs A1/A2. Assert:
- analysis metadata restores only for the exact tab ID + normalized source URL;
- navigation A1→A2 invalidates stale analysis and increments generation;
- other-window activation is ignored by that popup instance;
- a running helper download remains a background job but is no longer presented as belonging to A2;
- stale analyze completion from A1 cannot render in A2;
- popup close/reopen requests `deepVideoStatus` and resumes the current matching job/progress;
- popup reopen during a job for another tab shows a truthful global-download summary without relabeling current page metadata;
- completed/error snapshots remain bounded and do not store direct media URLs or destination contents in `chrome.storage.local`.

- [ ] **Step 2: Extend the versioned session workspace safely**

Add a bounded `deepVideo` analysis snapshot to the existing per-tab `chrome.storage.session` value. Store only normalized display metadata, preset availability, `media_id`, source URL, and scan time. Do not store format URLs, command options, logs, or the destination; query the helper/background for the destination.

Update the existing size-budget trimming so page text is still discarded before structured scraper data, and Deep Video thumbnails/descriptions are trimmed before core scrape arrays.

- [ ] **Step 3: Guard every asynchronous continuation**

Capture the existing `{tabId, windowId, generation}` token before analyze, folder, and status requests. Apply results only when both the token and source URL still match. Download events are correlated by helper job ID and rendered page association; they must not mutate the new page's scan metadata after navigation.

- [ ] **Step 4: Implement popup reopen/status synchronization**

On initialization, restore the per-tab scan snapshot, then request `deepVideoStatus`. Merge only a matching active job; otherwise show the current page's last analysis plus a separate bounded “Download in progress/completed” status when relevant. Reconnect failures show helper guidance without erasing stored fast-scrape or scan metadata.

- [ ] **Step 5: Run race tests and commit**

Run:

```bash
npm test -- --runInBand
node --check deep-video.js
node --check popup.js
node --check background.js
git add popup.js background.js deep-video.js tests/popup.test.js
git commit -m "Persist deep video sessions safely"
```

Expected: all stale-tab/window/navigation and popup-reopen tests pass.

---

### Task 10: Documentation, installer exercise, and complete verification

**Files:**
- Modify: `README.md`
- Modify: `TESTING.md`
- Modify: `docs/superpowers/specs/2026-08-10-deep-video-native-helper-design.md` only if implementation names changed during approved review
- Modify: `native_host/tests/*` or `tests/popup.test.js` only for a narrowly discovered regression

- [ ] **Step 1: Document exact developer workflows**

README must include:

```bash
# Install or repair for the unpacked extension ID
python3 -m native_host.installer --extension-id <chrome-extension-id>

# Validate framed protocol and dependencies
python3 -m native_host.healthcheck

# Explicitly update after reviewing requirements.lock
python3 -m native_host.installer --extension-id <chrome-extension-id>

# Remove helper and Native Messaging manifest; downloaded media is untouched
python3 -m native_host.uninstaller --extension-id <chrome-extension-id>
```

Explain how to find the unpacked extension ID, why `nativeMessaging` is required, the exact Application Support locations, Finder destination behavior, cookie-free/public-content boundary, no DRM bypass, single-job behavior, optional `ffmpeg`, logs/redaction, and explicit-download policy.

- [ ] **Step 2: Extend `TESTING.md` with pending macOS acceptance**

Add unchecked real-Chrome steps for install/reinstall/uninstall, wrong extension ID, helper absent/version mismatch, health, public analyze-without-download, all presets, playlist non-expansion, Finder remember/change/cancel/unwritable cases, missing `ffmpeg`, popup reopen, two tabs/windows/navigation, offline/unsupported/DRM/timeout errors, progress/cancel/partial cleanup, collision-safe outputs, and regression checks for fast scrape/session/dark UI/clipboard/ZIP/permissions.

Clearly separate automated evidence from pending manual Chrome/Finder/download evidence.

- [ ] **Step 3: Run the complete automated matrix**

Run:

```bash
python3 -m unittest discover -s native_host/tests -v
npm test -- --runInBand
node --check native-client.js
node --check deep-video.js
node --check popup.js
node --check background.js
node --check libs/fflate.umd.js
node ~/.agents/skills/impeccable/scripts/detect.mjs --json popup.html
node -e "const m=JSON.parse(require('fs').readFileSync('manifest.json')); if(m.host_permissions||m.content_scripts) process.exit(1); if(!m.permissions.includes('nativeMessaging')) process.exit(1)"
git diff --check
git status --short
```

Expected: every command exits `0`, detector prints `[]`, and status shows only intended documentation changes before commit.

- [ ] **Step 4: Exercise installer lifecycle in a temporary HOME**

Use dependency injection/test mode so no real Chrome configuration changes:

```bash
TMP_HOME="$(mktemp -d)"
PAGE_SCRAPER_TEST_HOME="$TMP_HOME" python3 -m native_host.installer \
  --extension-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --offline-test-fixture
PAGE_SCRAPER_TEST_HOME="$TMP_HOME" python3 -m native_host.healthcheck
PAGE_SCRAPER_TEST_HOME="$TMP_HOME" python3 -m native_host.uninstaller \
  --extension-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
find "$TMP_HOME" -type f -print
rm -rf "$TMP_HOME"
```

`--offline-test-fixture` must install a fake locked `yt_dlp` module supplied only by the test suite; it must never exist in normal production installation. Expected: install and health succeed, uninstall leaves no helper/manifest files, and no network or real user path is touched.

- [ ] **Step 5: Perform truthful real macOS checks where available**

Load unpacked Chrome and follow the new checklist using lawful public test media. Do not claim checks requiring Finder, Native Messaging, network extraction, `ffmpeg`, or downloads unless actually observed. Record exact environment/version and leave unavailable checks unchecked.

- [ ] **Step 6: Commit verification documentation**

Run:

```bash
git add README.md TESTING.md native_host/tests tests/popup.test.js
git commit -m "Document deep video helper verification"
git status --short
```

Expected: clean worktree. Request one final holistic functionality/security review and one accessibility/UI review before reporting completion.

---

## Plan self-review

- **Spec coverage:** Tasks 1–2 cover framing, schemas, private config, Finder, and redaction. Tasks 3–4 cover cookie-free discovery, normalized presets, explicit safe downloads, progress, and atomic output. Task 5 covers host supervision, single-job status, cancellation, and popup-reopen support. Task 6 covers pinned installation, health, and uninstall. Tasks 7–9 cover least-privilege extension integration, accessible dark UI, exact-tab persistence, and races. Task 10 covers documentation, automated verification, installer exercise, and truthful manual acceptance.
- **Boundary consistency:** The extension sends only command enums, HTTP(S) page URL, media ID, preset enum, confirmation, and job ID. It never supplies cookies, raw flags, format selectors, executable paths, output templates, or destination paths. Finder and filesystem state remain helper-owned.
- **Type consistency:** Protocol version is `1`; host name is `com.pagescraperpro.ytdlp`; preset enums are `best`, `1080p`, `720p`, and `audio`; extension bridge action is `deepVideoCommand`; state phases and message types are defined once and reused.
- **Dependency consistency:** `yt-dlp==2026.7.4` is exact and hash-locked; Python is 3.10+; tests use stdlib and mocks; `ffmpeg` remains optional and detected rather than installed.
- **Placeholder scan:** The plan contains no deferred implementation markers or unspecified error-handling steps. Angle-bracket values appear only in user commands where the actual unpacked extension ID must be supplied.
