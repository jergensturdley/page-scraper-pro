// ─── deep-video.js ─────────────────────────────────────────────────────────────
// Chrome extension native-messaging helper for deep video scraping.
// Loads the NativeHostClient and provides a simple state-machine interface
// for the popup and background scripts.
//
// Responsibilities:
//  - Establish and manage the native-messaging connection to the helper app.
//  - Provide an immutable initial state and a pure reducer.
//  - Manage correlation IDs so stale responses cannot overwrite active state.
//  - Surface a normalized display object with safe HTML escaping.
//  - Expose preset options even when the helper is unavailable.
//
// Usage (popup.js / background.js):
//   var dv = loadDeepVideoModule();
//   var state = dv.DEEP_VIDEO_INITIAL_STATE;
//   state = dv.reduceDeepVideoState(state, { type: 'analyze', payload: { url, provider } });
//   if (state.phase === 'ready') { renderPresets(state.presets); }
//
// Integration with background.js:
//   background.js imports this module via importScripts() and exposes
//   the deep-scan trigger on the runtime port so the popup can request
//   a deep scan without duplicating the native-connection logic.
// ─────────────────────────────────────────────────────────────────────────────

'use strict';

// ─── State machine ───────────────────────────────────────────────────────────
var DEEP_VIDEO_INITIAL_STATE = Object.freeze({
  // Phase: idle | checking | ready | downloading | completed | error
  phase: 'idle',
  // Incremented on every navigation or retry to reject stale responses
  generation: 0,
  // Populated after a successful result
  source_url: null,
  media_id: null,
  title: null,
  provider: null,
  presets: null,
  thumbnail_url: null,
  // Populated during/after a download
  active_preset: null,
  progress_percent: null,
  destination: null,
  // Populated on error
  error_code: null,
  error: null,
  // Internal: tracks the native-messaging job correlation ID
  active_job_id: null,
  // Timestamp of last successful scan (for session persistence)
  scanned_at: null,
});

function reduceDeepVideoState(state, action) {
  // Start from a copy of the current state so all fields are preserved
  var next = {};
  for (var k in state) {
    if (Object.prototype.hasOwnProperty.call(state, k)) {
      next[k] = state[k];
    }
  }

  switch (action.type) {
    case 'analyze':
      // Always reset and transition to checking, incrementing generation
      next.phase = 'checking';
      next.generation = (state.generation || 0) + 1;
      next.source_url = safeString(action.payload && action.payload.url) || null;
      next.media_id = safeString(action.payload && action.payload.media_id) || null;
      next.title = null;
      next.provider = safeString(action.payload && action.payload.provider) || null;
      next.presets = null;
      next.thumbnail_url = null;
      next.active_preset = null;
      next.progress_percent = null;
      next.destination = null;
      next.error_code = null;
      next.error = null;
      next.active_job_id = null;
      break;

    case 'result': {
      // Only apply if the response generation matches current generation
      var respGen = action.payload && action.payload.generation;
      if (respGen !== undefined && respGen !== state.generation) {
        // Stale result — discard silently
        break;
      }
      // Guard: only accept results when in 'checking' phase
      if (state.phase !== 'checking') break;
      next.phase = 'ready';
      next.scanned_at = Date.now();
      next.media_id = safeString(action.payload && action.payload.media_id) || state.media_id;
      next.title = safeString(action.payload && action.payload.title) || null;
      next.provider = safeString(action.payload && action.payload.provider) || state.provider;
      // Presets: normalise to array or null
      var rawPresets = action.payload && action.payload.presets;
      next.presets = Array.isArray(rawPresets) ? rawPresets : null;
      // Normalise thumbnail — only allow http/https
      var rawThumb = action.payload && action.payload.thumbnail_url;
      next.thumbnail_url = normalizeThumbnail(rawThumb);
      next.progress_percent = null;
      next.error_code = null;
      next.error = null;
      break;
    }

    case 'download':
      if (state.phase === 'ready' && state.media_id) {
        next.phase = 'downloading';
        next.generation = (state.generation || 0) + 1;
        next.active_preset = safeString(action.payload && action.payload.preset) || null;
        next.progress_percent = 0;
        next.destination = null;
        next.error_code = null;
        next.error = null;
      }
      break;

    case 'progress':
      if (state.phase === 'downloading') {
        // Reject stale progress events with wrong job_id
        var currentJob = state.active_job_id || state.media_id;
        if (action.payload && action.payload.job_id && currentJob) {
          if (String(action.payload.job_id) !== String(currentJob)) {
            break; // stale event, ignore
          }
        }
        var newPercent = typeof (action.payload && action.payload.percent) === 'number'
          ? action.payload.percent
          : state.progress_percent;
        // Monotonic progress: ignore decreases
        if (newPercent >= (state.progress_percent || 0)) {
          next.progress_percent = Math.min(100, Math.max(0, newPercent));
        }
        // Update phase from payload if provided
        if (action.payload && action.payload.phase) {
          next.phase = safeString(action.payload.phase) || 'downloading';
        }
      }
      break;

    case 'completed':
      if (state.phase === 'downloading') {
        next.phase = 'completed';
        next.progress_percent = 100;
        next.error_code = null;
        next.error = null;
      }
      break;

    case 'failed':
      if (state.phase === 'downloading') {
        next.phase = 'error';
        next.error_code = safeString(action.payload && action.payload.error_code);
        next.error = safeString(action.payload && action.payload.error);
      }
      break;

    case 'cancel':
      // Always return to idle, preserving source URL
      next.phase = 'idle';
      next.active_preset = null;
      next.destination = null;
      next.progress_percent = null;
      next.error_code = null;
      next.error = null;
      break;

    case 'retry':
      if (state.phase === 'error') {
        next.phase = 'ready';
        next.error_code = null;
        next.error = null;
        next.active_preset = null;
        next.destination = null;
        next.progress_percent = null;
      }
      break;

    case 'navigate':
      // Any navigation resets to idle, increments generation
      next.phase = 'idle';
      next.generation = (state.generation || 0) + 1;
      next.source_url = safeString(action.payload && action.payload.url) || null;
      next.media_id = null;
      next.title = null;
      next.provider = null;
      next.presets = null;
      next.thumbnail_url = null;
      next.active_preset = null;
      next.progress_percent = null;
      next.destination = null;
      next.error_code = null;
      next.error = null;
      next.active_job_id = null;
      break;

    case 'helper_unavailable':
      next.phase = 'error';
      next.error_code = 'helper_unavailable';
      next.error = null;
      next.presets = null;
      break;

    default:
      // Unknown action type — return current state unchanged
      return state;
  }

  return next;
}

// ─── Helpers ────────────────────────────────────────────────────────────────
function safeString(value) {
  if (typeof value !== 'string') return null;
  var trimmed = value.trim();
  // Truncate very long strings (e.g. titles) to 1024 chars
  if (trimmed.length > 1024) return trimmed.slice(0, 1024);
  return trimmed;
}

function normalizeThumbnail(url) {
  if (typeof url !== 'string') return null;
  url = url.trim();
  // Only allow http/https URLs (block file://, data:, etc.)
  if (/^https?:\/\//i.test(url)) return url;
  return null;
}

/**
 * Returns the display-safe preset options for the UI.
 * Always returns an array (possibly empty) so the popup never crashes.
 */
function availablePresetOptions(state) {
  if (!state || !Array.isArray(state.presets) || state.presets.length === 0) {
    return [];
  }
  return state.presets.slice(); // return a copy
}

/**
 * Returns a display-safe copy of the deep-video result.
 * All user-facing strings are HTML-escaped.
 */
function normalizeDeepVideoResult(raw) {
  if (!raw || typeof raw !== 'object') raw = {};
  return {
    media_id: typeof raw.media_id === 'string' ? raw.media_id : null,
    title: escHtml(safeString(raw.title)),
    provider: typeof raw.provider === 'string' ? raw.provider : null,
    presets: Array.isArray(raw.presets) ? raw.presets.slice() : [],
    thumbnail_url: normalizeThumbnail(raw.thumbnail_url),
    error_code: typeof raw.error_code === 'string' ? raw.error_code : null,
  };
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ─── Module export ──────────────────────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DEEP_VIDEO_INITIAL_STATE: DEEP_VIDEO_INITIAL_STATE,
    reduceDeepVideoState: reduceDeepVideoState,
    availablePresetOptions: availablePresetOptions,
    normalizeDeepVideoResult: normalizeDeepVideoResult,
  };
} else if (typeof window !== 'undefined') {
  // Browser / extension popup environment — expose as globals
  window.DEEP_VIDEO_INITIAL_STATE = DEEP_VIDEO_INITIAL_STATE;
  window.reduceDeepVideoState = reduceDeepVideoState;
  window.availablePresetOptions = availablePresetOptions;
  window.normalizeDeepVideoResult = normalizeDeepVideoResult;
}
