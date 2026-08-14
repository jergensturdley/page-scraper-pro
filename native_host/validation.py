"""Strict schemas for the native helper protocol."""

import re
from urllib.parse import urlsplit, urlunsplit

from .constants import (
    ALLOWED_COMMANDS,
    ALLOWED_PRESETS,
    MAX_URL_LENGTH,
    PROTOCOL_VERSION,
    REQUEST_ID_PATTERN,
)

_REQUEST_ID_RE = re.compile(REQUEST_ID_PATTERN)
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EVENT_TYPES = frozenset({"response", "progress", "completed", "failed", "cancelled"})
_ENVELOPE_KEYS = {"version", "id", "type", "payload"}


def _require_exact_keys(value: dict[object, object], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ValueError(f"{label} fields do not match schema")


def normalize_http_url(value: object) -> str:
    """Validate and normalize an HTTP(S) URL without resolving it."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("URL must be a bounded string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("URL contains control characters")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL is malformed") from exc
    if scheme not in {"http", "https"}:
        raise ValueError("URL scheme must be HTTP or HTTPS")
    if not hostname:
        raise ValueError("URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")
    if any(character.isspace() or ord(character) < 32 for character in hostname):
        raise ValueError("URL host is malformed")

    normalized_host = hostname.lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    normalized = urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))
    if len(normalized) > MAX_URL_LENGTH:
        raise ValueError("URL exceeds maximum length")
    return normalized


def validate_request_id(value: object) -> str:
    """Return a request identifier only when it matches the wire format."""
    if not isinstance(value, str) or _REQUEST_ID_RE.fullmatch(value) is None:
        raise ValueError("invalid request identifier")
    return value


def _validate_payload(command_type: str, payload: dict[object, object]) -> dict[str, object]:
    if command_type in {"health", "choose_directory", "status"}:
        _require_exact_keys(payload, set(), f"{command_type} payload")
        return {}
    if command_type == "analyze":
        _require_exact_keys(payload, {"url"}, "analyze payload")
        return {"url": normalize_http_url(payload["url"])}
    if command_type == "cancel":
        _require_exact_keys(payload, {"job_id"}, "cancel payload")
        return {"job_id": validate_request_id(payload["job_id"])}
    if command_type == "download":
        keys = set(payload)
        required = {"media_id", "preset"}
        if keys not in (required, required | {"confirmed"}):
            raise ValueError("download payload fields do not match schema")
        media_id = validate_request_id(payload["media_id"])
        preset = payload["preset"]
        if not isinstance(preset, str) or preset not in ALLOWED_PRESETS:
            raise ValueError("invalid download preset")
        validated: dict[str, object] = {"media_id": media_id, "preset": preset}
        if "confirmed" in payload:
            confirmed = payload["confirmed"]
            if not isinstance(confirmed, bool):
                raise ValueError("confirmed must be a boolean")
            validated["confirmed"] = confirmed
        return validated
    raise ValueError("unknown command")


def validate_command(message: object) -> dict[str, object]:
    """Validate an extension-to-helper command and normalize safe values."""
    if not isinstance(message, dict):
        raise ValueError("command must be an object")
    _require_exact_keys(message, _ENVELOPE_KEYS, "command")
    if type(message["version"]) is not int or message["version"] != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    request_id = validate_request_id(message["id"])
    command_type = message["type"]
    if not isinstance(command_type, str) or command_type not in ALLOWED_COMMANDS:
        raise ValueError("unknown command")
    payload = message["payload"]
    if not isinstance(payload, dict):
        raise ValueError("command payload must be an object")
    return {
        "version": PROTOCOL_VERSION,
        "id": request_id,
        "type": command_type,
        "payload": _validate_payload(command_type, payload),
    }


def make_response(request_id: str, payload: dict[str, object]) -> dict[str, object]:
    """Build a command response envelope."""
    validated_id = validate_request_id(request_id)
    if not isinstance(payload, dict):
        raise ValueError("response payload must be an object")
    return {
        "version": PROTOCOL_VERSION,
        "id": validated_id,
        "type": "response",
        "payload": payload,
    }


def make_error(request_id: str, code: str, message: str) -> dict[str, object]:
    """Build a stable structured command rejection."""
    if not isinstance(code, str) or _ERROR_CODE_RE.fullmatch(code) is None:
        raise ValueError("invalid error code")
    if not isinstance(message, str) or not message:
        raise ValueError("error message must be a non-empty string")
    return make_response(
        request_id,
        {"ok": False, "error": {"code": code, "message": message}},
    )


def validate_event(message: object) -> dict[str, object]:
    """Validate the common envelope for a helper-to-extension event."""
    if not isinstance(message, dict):
        raise ValueError("event must be an object")
    _require_exact_keys(message, _ENVELOPE_KEYS, "event")
    if type(message["version"]) is not int or message["version"] != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    event_id = validate_request_id(message["id"])
    event_type = message["type"]
    if not isinstance(event_type, str) or event_type not in _EVENT_TYPES:
        raise ValueError("unknown event")
    payload = message["payload"]
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")
    return {
        "version": PROTOCOL_VERSION,
        "id": event_id,
        "type": event_type,
        "payload": dict(payload),
    }
