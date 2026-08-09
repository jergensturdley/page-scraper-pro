# Repository Guidelines

## Project Structure & Module Organization

Page Scraper Pro is a dependency-light Chrome Manifest V3 extension. Runtime files live at the repository root: `popup.html`, `popup.js`, and `styles.css` implement the popup and side panel; `content.js` and `content.css` run in web pages; `background.js` is the service worker; and `manifest.json` wires these pieces together. Keep extension icons in `icons/` and the vendored ZIP helper in `libs/`. Jest checks live in `tests/`; `snapshots/` contains generated debugging data and is ignored by Git.

## Build, Test, and Development Commands

- `npm install` installs the pinned development dependencies.
- `npm test` runs the Jest suite once.
- `npm test -- --watch` reruns tests while files change.

There is no build step. Load the repository directly from `chrome://extensions` using **Load unpacked**, then click **Reload** after edits. Exercise browser-dependent behavior manually; see `TESTING.md` for the current checklist and inspect the popup/service-worker consoles for errors.

## Coding Style & Naming Conventions

Use plain JavaScript, HTML, and CSS; do not introduce a framework or bundler without a demonstrated need. Match the existing two-space indentation and semicolon usage. Use `camelCase` for variables and functions, `UPPER_SNAKE_CASE` only for true constants, and kebab-case CSS classes. Prefer small named functions and browser-native APIs. Keep permissions and content-script registration centralized in `manifest.json`. No formatter or linter is configured, so preserve nearby style and avoid unrelated reformatting.

## Testing Guidelines

Jest 30 is the automated test framework. Name tests `tests/*.test.js` and group behavior with descriptive `describe` and `test` labels. Add focused regression coverage for changes to popup structure, styles, or manifest wiring. Run `npm test` before submitting. There is no enforced coverage threshold; prioritize a test that would fail before the fix. Browser APIs and real downloads still require the manual checks in `TESTING.md`.

## Commit & Pull Request Guidelines

History currently contains only `initial commit`, so no formal convention exists. Use short, imperative subjects such as `Fix duplicate image downloads`, and keep each commit focused. Pull requests should explain the user-visible change, list automated and manual verification, and link relevant issues. Include before/after screenshots or a short recording for popup, side-panel, or styling changes. Call out new Chrome permissions explicitly.

## Security & Configuration

Never commit credentials, personal browsing data, generated snapshots, packaged `.crx`/`.zip` files, or `node_modules/`. Treat scraped page content as untrusted, avoid injecting it as HTML, and request only the minimum Chrome permissions needed.
