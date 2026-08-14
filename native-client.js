/**
 * Native Messaging client for Page Scraper Pro.
 *
 * Exposed as:
 *   - globalThis.PageScraperNative (browser / extension service worker)
 *   - module.exports / require('native-client.js') (Jest / Node.js)
 *
 * Protocol: Chrome Native Messaging with framed JSON.
 * Host name: com.pagescraperpro.ytdlp
 * Protocol version: 1
 */

'use strict';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Chrome Native Messaging host name registered in the manifest. */
var HOST_NAME = 'com.pagescraperpro.ytdlp';

/** Framing overhead: 4-byte little-endian length prefix. */
var FRAME_OVERHEAD_BYTES = 4;

/** Maximum inbound/outbound message size in bytes (1 MB). */
var MAX_MESSAGE_BYTES = 1_048_576;

/** Maximum JSON string length accepted inside a validated snapshot field. */
var MAX_SNAPSHOT_STRING = 16_384;

/**
 * Command types accepted by the helper.
 * Corresponds to ALLOWED_COMMANDS in the native host.
 */
var ALLOWED_COMMANDS = Object.freeze([
  'health',
  'analyze',
  'choose_directory',
  'download',
  'cancel',
  'status',
]);

/**
 * Terminal event types received from the helper.
 * 'response' carries the initial ack/nack for every command.
 * 'progress' is non-terminal and carries incremental job updates.
 * 'completed', 'failed', and 'cancelled' are terminal.
 */
var TERMINAL_EVENTS = Object.freeze({
  response: true,
  completed: true,
  failed: true,
  cancelled: true,
});

/** Bounded snapshot fields — no arbitrary keys may enter state. */
var SNAPSHOT_FIELDS = Object.freeze([
  'job_id',
  'request_id',
  'operation',
  'kind',
  'state',
  'status',
  'phase',
  'percent',
  'speed',
  'eta',
  'error_code',
  'extractor',
  'started_at',
  'finished_at',
]);

var REQUEST_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
var SNAPSHOT_STRING_RE = new RegExp(
  '^.{0,' + MAX_SNAPSHOT_STRING + '}$'
);

/**
 * Safe subset of snapshot values — scalars, no objects/arrays.
 * Guards against prototype-pollution and accidental nested data.
 */
var SNAPSHOT_SCALARS = Object.freeze({
  job_id: true,
  request_id: true,
  operation: true,
  kind: true,
  state: true,
  status: true,
  phase: true,
  error_code: true,
  extractor: true,
  started_at: true,
  finished_at: true,
  percent: true,   // number
  speed: true,     // number
  eta: true,       // number
});

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

/**
 * Incrementing counter mixed with random hex to produce unique IDs.
 * Two concurrent sends in the same millisecond get different IDs.
 */
var _idCounter = 0;

function generateRequestId() {
  var ms = Date.now();
  var inc = (++_idCounter & 0xffff).toString(36);
  var rnd = (Math.random() * 0xffffff | 0).toString(36);
  // Fit within 64 chars: format is <36 chars base36 time>.<4 chars inc>.<5 chars rnd>
  var id = ms.toString(36) + '-' + inc + '-' + rnd;
  return id.length <= 64 ? id : id.slice(0, 64);
}

// ---------------------------------------------------------------------------
// Message validation
// ---------------------------------------------------------------------------

/**
 * Validate the structural envelope of a Native Messaging message.
 * Throws on any invalid field; returns the message on success.
 *
 * @param {*} message
 * @returns {{ version: number, id: string, type: string, payload: object }}
 */
function validateNativeMessage(message) {
  if (message === null || typeof message !== 'object' || Array.isArray(message)) {
    throw new Error('native message must be a non-null plain object');
  }
  // version
  if (typeof message.version !== 'number' || !Number.isFinite(message.version)) {
    throw new Error('message version must be a finite number');
  }
  if (message.version !== 1) {
    throw new Error('unsupported protocol version');
  }
  // id
  if (typeof message.id !== 'string' || !REQUEST_ID_RE.test(message.id)) {
    throw new Error('message id must match ^[A-Za-z0-9_-]{1,64}$');
  }
  // type
  if (typeof message.type !== 'string') {
    throw new Error('message type must be a string');
  }
  // payload
  if (typeof message.payload !== 'object' || message.payload === null || Array.isArray(message.payload)) {
    throw new Error('message payload must be a non-null object');
  }
  return message;
}

/**
 * Reduce a snapshot with one incoming event.
 *
 * Rules:
 * - 'response' establishes a new active job if it carries a job_id.
 * - 'progress' updates the snapshot only when job_id matches the active job.
 * - 'completed', 'failed', 'cancelled' update the snapshot only when
 *   job_id matches the active job (or any terminal event when no job_id
 *   is present, to handle stray events gracefully).
 * - Snapshot values are coerced to safe scalars; strings are bounded.
 * - Snapshot is never replaced — fields are merged into the existing object.
 *
 * @param {object|null} snapshot  Current snapshot.
 * @param {{ type: string, payload: object }} event
 * @returns {object}  New snapshot (may be the same object if mutated).
 */
function reduceNativeSnapshot(snapshot, event) {
  var type = event.type;
  var payload = event.payload;

  // response establishes a new active job — even from an empty snapshot
  if (type === 'response') {
    var data = payload.data;
    if (typeof data !== 'object' || data === null) return snapshot;
    var incomingJobId = data.job_id;
    if (typeof incomingJobId !== 'string') return snapshot;
    var next = { request_id: event.id };
    copyBoundedScalar(next, data, 'job_id');
    copyBoundedScalar(next, data, 'operation');
    copyBoundedScalar(next, data, 'kind');
    copyBoundedScalar(next, data, 'state');
    copyBoundedScalar(next, data, 'status');
    copyBoundedScalar(next, data, 'phase');
    copyBoundedScalar(next, data, 'percent');
    copyBoundedScalar(next, data, 'speed');
    copyBoundedScalar(next, data, 'eta');
    copyBoundedScalar(next, data, 'error_code');
    copyBoundedScalar(next, data, 'extractor');
    copyBoundedScalar(next, data, 'started_at');
    copyBoundedScalar(next, data, 'finished_at');
    return next;
  }

  // All other event types (progress, completed, failed, cancelled) require an existing snapshot.
  if (!snapshot) return null;

  // progress and terminal events apply only to the active job
  var activeJobId = snapshot.job_id;
  var eventJobId = payload.job_id;

  // If event has no job_id, apply to active snapshot (graceful handling).
  // If event has a job_id, it must match the active job.
  if (eventJobId !== void 0 && eventJobId !== activeJobId) {
    return snapshot; // stale event — ignore
  }

  var next = Object.create(null);
  for (var i = 0; i < SNAPSHOT_FIELDS.length; i++) {
    var key = SNAPSHOT_FIELDS[i];
    next[key] = snapshot[key];
  }

  // Apply payload fields to snapshot
  copyBoundedScalar(next, payload, 'state');
  copyBoundedScalar(next, payload, 'status');
  copyBoundedScalar(next, payload, 'phase');
  copyBoundedScalar(next, payload, 'percent');
  copyBoundedScalar(next, payload, 'speed');
  copyBoundedScalar(next, payload, 'eta');
  copyBoundedScalar(next, payload, 'error_code');
  copyBoundedScalar(next, payload, 'extractor');
  copyBoundedScalar(next, payload, 'started_at');
  copyBoundedScalar(next, payload, 'finished_at');
  copyBoundedScalar(next, payload, 'kind');
  copyBoundedScalar(next, payload, 'operation');
  // For completed/failed/cancelled, also update job_id if present in payload
  if (eventJobId !== void 0) {
    copyBoundedScalar(next, payload, 'job_id');
  }

  return next;
}

/**
 * Copy one bounded scalar field from src to dest if the value is safe.
 */
function copyBoundedScalar(dest, src, key) {
  var value = src[key];
  if (value === void 0) return;
  if (SNAPSHOT_SCALARS[key]) {
    if (typeof value === 'string') {
      if (!SNAPSHOT_STRING_RE.test(value)) return;
      dest[key] = value;
    } else if (typeof value === 'number' && Number.isFinite(value)) {
      dest[key] = value;
    } else if (typeof value === 'boolean') {
      dest[key] = value;
    }
  }
}

// ---------------------------------------------------------------------------
// NativeHostClient
// ---------------------------------------------------------------------------

/**
 * @param {object} options
 * @param {object} options.runtime        chrome.runtime or a compatible shim.
 * @param {string} [options.hostName]    Native Messaging host name.
 * @param {number} [options.timeoutMs]   Per-request timeout (default 30000).
 * @param {function(): number} [options.now]  Time source (default Date.now).
 */
function NativeHostClient(options) {
  this._runtime = options.runtime;
  this._hostName = options.hostName || HOST_NAME;
  this._timeoutMs = options.timeoutMs !== void 0 ? options.timeoutMs : 30000;
  this._now = options.now || Date.now;

  /** @type {object|null} Current bounded snapshot. */
  this._snapshot = null;

  /** @type {Array<function(object): void>} Subscribers. */
  this._subscribers = [];

  /**
   * Pending requests keyed by request ID.
   * @type {Map<string, { resolve: function, reject: function, deadline: number }>}
   */
  this._pending = new globalThis.Map();

  /** Lazily-created Native Messaging port. */
  this._port = null;

  /** True after disconnect() is called. */
  this._disconnected = false;

  /** Bound message handler — stored so it can be removed on disconnect. */
  this._onMessage = this._onMessage.bind(this);

  /** Bound disconnect handler. */
  this._onDisconnect = this._onDisconnect.bind(this);
}

/**
 * Lazily connect to the native host and return the shared port.
 * Subsequent calls return the same port.
 * @returns {object} Native Messaging port.
 */
NativeHostClient.prototype._connect = function () {
  if (this._disconnected) {
    throw new Error('client is disconnected');
  }
  if (this._port) return this._port;

  // Use injected test port if available (set by test hooks).
  if (this._testPort) {
    var port = this._testPort;
    this._testPort = null;
    port.onMessage.addListener(this._onMessage);
    port.onDisconnect.addListener(this._onDisconnect);
    this._port = port;
    return port;
  }

  var connectFn = typeof globalThis.connectNative === 'function'
    ? globalThis.connectNative
    : this._runtime.connectNative.bind(this._runtime);
  var port = connectFn(this._hostName);
  port.onMessage.addListener(this._onMessage);
  port.onDisconnect.addListener(this._onDisconnect);
  this._port = port;
  return port;
};

/** @internal Test-only: inject a pre-made port for unit testing. */
NativeHostClient.prototype._setTestPort = function (port) {
  this._testPort = port;
};

/**
 * Handle an inbound message from the native host.
 * @param {{ version: *, id: *, type: *, payload: * }} message
 */
NativeHostClient.prototype._onMessage = function (message) {
  if (this._disconnected) return;

  var id, entry;
  try {
    var validated = validateNativeMessage(message);
    id = validated.id;

    // Deliver to a pending request if one exists.
    entry = this._pending.get(id);
    if (entry) {
      this._pending.delete(id);
      clearTimeout(entry._timer);
      if (validated.type === 'response') {
        var payload = validated.payload;
        if (payload && payload.ok === true && payload.data) {
          // Reduce snapshot with response data
          this._snapshot = reduceNativeSnapshot(this._snapshot, validated);
          entry.resolve(payload.data);
        } else {
          var err = new Error(
            payload && payload.error_code
              ? 'helper error: ' + payload.error_code
              : 'helper returned error'
          );
          err.code = (payload && payload.error_code) || 'helper_error';
          entry.reject(err);
        }
      } else {
        // Non-response responses (progress/terminal without pending request)
        this._snapshot = reduceNativeSnapshot(this._snapshot, validated);
      }
    } else {
      // Unsolicited event (progress, completed, failed, cancelled).
      // Only update snapshot if job_id matches the active snapshot.
      this._snapshot = reduceNativeSnapshot(this._snapshot, validated);
    }

    this._notifySubscribers();
  } catch (err) {
    // Schema validation failure — drop the message.
    // Do NOT update snapshot or resolve any pending request.
  }
};

/**
 * Handle port disconnect.
 */
NativeHostClient.prototype._onDisconnect = function () {
  if (this._disconnected) return;
  this._port = null;
  this._disconnected = true;

  // Mark snapshot as unavailable and reject pending requests.
  if (this._snapshot) {
    this._snapshot = Object.assign({}, this._snapshot, {
      error_code: 'helper_unavailable',
    });
  } else {
    // Create a minimal unavailable snapshot if none exists yet.
    var snap = Object.create(null);
    snap.error_code = 'helper_unavailable';
    snap.state = 'failed';
    this._snapshot = snap;
  }
  var pending = this._pending;
  this._pending = new globalThis.Map();
  for (var entry of pending.values()) {
    clearTimeout(entry._timer);
    var err = new Error('helper unavailable');
    err.code = 'helper_unavailable';
    Promise.resolve(null).then(function() {
      try { entry.reject(err); } catch (_) {}
    }).catch(function(_) {});
  }
  this._notifySubscribers();
};

/**
 * Send a command to the native helper.
 *
 * @param {string} type     Must be one of the six ALLOWED_COMMANDS.
 * @param {object} [payload={}]
 * @returns {Promise<object>} Resolves with the response data.
 */
NativeHostClient.prototype.send = function (type, payload) {
  var self = this;
  payload = payload || {};

  // Explicit send() after disconnect() resets the disconnected flag so the
  // client can reconnect.  This allows callers to recover from transient
  // connection failures without creating a new NativeHostClient instance.
  this._disconnected = false;

  // Validate command type synchronously so callers can use try/catch
  if (!ALLOWED_COMMANDS.includes(type)) {
    throw new Error('invalid command type: ' + type);
  }

  // Serialize payload and check size limit
  var payloadJson;
  try {
    payloadJson = JSON.stringify(payload);
  } catch (err) {
    throw new Error('payload is not JSON serializable');
  }
  if (payloadJson.length > MAX_MESSAGE_BYTES) {
    throw new Error('payload exceeds 1 MB limit');
  }

  var port;
  try {
    port = this._connect();
  } catch (err) {
    return Promise.reject(err);
  }

  var id = generateRequestId();
  var deadline = this._now() + this._timeoutMs;

  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      if (self._pending.has(id)) {
        self._pending.delete(id);
        try {
          var err = new Error('request timed out');
          err.code = 'timeout';
          reject(err);
        } catch (_) {}
        // Update snapshot with timeout state so subscribers are notified.
        var next = Object.create(null);
        next.error_code = 'timeout';
        next.state = 'failed';
        self._snapshot = next;
        self._notifySubscribers();
      }
    }, self._timeoutMs);
    // Store the timer on the entry so it can be cleared on delivery
    var entry = { resolve: resolve, reject: reject, deadline: deadline, _timer: timer };
    self._pending.set(id, entry);

    var envelope = {
      version: 1,
      id: id,
      type: type,
      payload: payload,
    };

    // Validate envelope size
    var envelopeJson;
    try {
      envelopeJson = JSON.stringify(envelope);
    } catch (err) {
      clearTimeout(timer);
      self._pending.delete(id);
      reject(new Error('envelope exceeds 1 MB limit'));
      return;
    }
    if (envelopeJson.length > MAX_MESSAGE_BYTES) {
      clearTimeout(timer);
      self._pending.delete(id);
      reject(new Error('envelope exceeds 1 MB limit'));
      return;
    }

    port.postMessage(envelope);
  });
};

/**
 * Return the current bounded snapshot.
 * @returns {object|null}
 */
NativeHostClient.prototype.snapshot = function () {
  return this._snapshot;
};

/**
 * Register a subscriber for snapshot changes.
 * @param {function(object): void} listener
 * @returns {function(): void} Unsubscribe function.
 */
NativeHostClient.prototype.subscribe = function (listener) {
  var self = this;
  this._subscribers.push(listener);
  return function () {
    var i = self._subscribers.indexOf(listener);
    if (i >= 0) self._subscribers.splice(i, 1);
  };
};

/**
 * Disconnect the native port and reject pending requests.
 * No automatic reconnection occurs.
 */
NativeHostClient.prototype.disconnect = function () {
  if (this._disconnected) return;
  if (this._port) {
    var port = this._port;
    this._port = null; // clear immediately so _onDisconnect does not loop back here
    try { port.disconnect(); } catch (_) {}
    try { port.onMessage.removeListener(this._onMessage); } catch (_) {}
    try { port.onDisconnect.removeListener(this._onDisconnect); } catch (_) {}
  }
  this._onDisconnect();
};

/**
 * Notify all subscribers of the current snapshot.
 */
NativeHostClient.prototype._notifySubscribers = function () {
  var snap = this._snapshot;
  var subs = this._subscribers.slice(); // snapshot to avoid mutation during iteration
  for (var i = 0; i < subs.length; i++) {
    try { subs[i](snap); } catch (_) {}
  }
};

// ---------------------------------------------------------------------------
// Module exports (UMD)
// ---------------------------------------------------------------------------

var PageScraperNative = {
  NativeHostClient: NativeHostClient,
  validateNativeMessage: validateNativeMessage,
  reduceNativeSnapshot: reduceNativeSnapshot,
  HOST_NAME: HOST_NAME,
  MAX_MESSAGE_BYTES: MAX_MESSAGE_BYTES,
  ALLOWED_COMMANDS: ALLOWED_COMMANDS,
};

// UMD: browser / extension service worker
if (typeof globalThis !== 'undefined') {
  globalThis.PageScraperNative = PageScraperNative;
}

// UMD: CommonJS (Jest / Node.js)
if (typeof module !== 'undefined' && module.exports) {
  module.exports = PageScraperNative;
}
