"""Strict schemas for the native helper protocol."""

import ipaddress
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit

from .constants import (
    ALLOWED_COMMANDS,
    ALLOWED_PRESETS,
    MAX_MESSAGE_BYTES,
    MAX_URL_LENGTH,
    PROTOCOL_VERSION,
    REQUEST_ID_PATTERN,
)

_REQUEST_ID_RE = re.compile(REQUEST_ID_PATTERN)
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PHASE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BAD_URI_CHARACTER_RE = re.compile(r"[<>\"{}|^`]")
_BAD_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_EVENT_TYPES = frozenset(
    {"response", "progress", "completed", "failed", "cancelled"}
)
_ENVELOPE_KEYS = {"version", "id", "type", "payload"}
_MAX_EVENT_DEPTH = 10
_MAX_EVENT_ITEMS = 256
_MAX_EVENT_NODES = 2_048
_MAX_EVENT_STRING_LENGTH = 16_384
_MAX_ERROR_MESSAGE_LENGTH = 1_000
_MAX_SAFE_INTEGER = (1 << 53) - 1
_DECIMAL_HOST_LABEL_RE = re.compile(r"^[0-9]+$")
_HEX_HOST_LABEL_RE = re.compile(r"^0x[0-9a-f]+$", re.IGNORECASE)


def _require_exact_keys(
    value: dict[object, object], keys: set[str], label: str
) -> None:
    if set(value) != keys:
        raise ValueError(f"{label} fields do not match schema")


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_loopback
        and not address.is_private
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _reject_ambiguous_numeric_host(hostname: str) -> None:
    labels = hostname.split(".")
    numericish = all(
        _DECIMAL_HOST_LABEL_RE.fullmatch(label)
        or _HEX_HOST_LABEL_RE.fullmatch(label)
        for label in labels
    )
    if not numericish:
        return
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError as exc:
        raise ValueError("ambiguous numeric hostnames are not allowed") from exc
    if not _is_public_address(address):
        raise ValueError("non-global IP addresses are not allowed")


def normalize_http_url(value: object) -> str:
    """Validate a public HTTP(S) URL without resolving or requesting it."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("URL must be a bounded string")
    if (
        any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or "\\" in value
        or _BAD_URI_CHARACTER_RE.search(value) is not None
        or _BAD_PERCENT_ESCAPE_RE.search(value) is not None
    ):
        raise ValueError("URL contains malformed URI characters")
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

    try:
        normalized_host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("URL hostname is not valid IDNA") from exc
    if normalized_host.endswith("."):
        normalized_host = normalized_host[:-1]
    if not normalized_host:
        raise ValueError("URL must include a host")
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise ValueError("local hostnames are not allowed")
    _reject_ambiguous_numeric_host(normalized_host)
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not _is_public_address(address):
        raise ValueError("non-global IP addresses are not allowed")

    # Task 3 network fetches must revalidate every resolved and redirected IP
    # immediately before connection; syntax checks alone cannot stop DNS rebinding.
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


def _validate_payload(
    command_type: str, payload: dict[object, object]
) -> dict[str, object]:
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


def _validate_json_value(
    value: object, depth: int = 0, node_count: list[int] | None = None
) -> None:
    if node_count is None:
        node_count = [0]
    node_count[0] += 1
    if node_count[0] > _MAX_EVENT_NODES or depth > _MAX_EVENT_DEPTH:
        raise ValueError("event data exceeds structural limits")
    if value is None or isinstance(value, bool):
        return
    if type(value) is int:
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("event integer exceeds the safe range")
        return
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("event number is outside the finite safe range")
        return
    if isinstance(value, str):
        if len(value) > _MAX_EVENT_STRING_LENGTH:
            raise ValueError("event string exceeds maximum length")
        return
    if isinstance(value, list):
        if len(value) > _MAX_EVENT_ITEMS:
            raise ValueError("event list has too many items")
        for item in value:
            _validate_json_value(item, depth + 1, node_count)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_EVENT_ITEMS:
            raise ValueError("event object has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > _MAX_EVENT_STRING_LENGTH:
                raise ValueError("event object keys must be bounded strings")
            _validate_json_value(item, depth + 1, node_count)
        return
    raise ValueError("event data is not JSON-safe")


def _validate_bounded_json(value: object) -> None:
    _validate_json_value(value)
    try:
        encoded = json.dumps(
            value, allow_nan=False, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("event data is not JSON-safe") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("event data exceeds maximum encoded size")


def _validate_error(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("error must be an object")
    _require_exact_keys(value, {"code", "message"}, "error")
    code = value["code"]
    message = value["message"]
    if not isinstance(code, str) or _ERROR_CODE_RE.fullmatch(code) is None:
        raise ValueError("invalid error code")
    if (
        not isinstance(message, str)
        or not message
        or len(message) > _MAX_ERROR_MESSAGE_LENGTH
    ):
        raise ValueError("error message must be a bounded non-empty string")
    return {"code": code, "message": message}


def _validate_progress_number(value: object, field: str) -> int | float:
    if type(value) is int:
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError(f"progress {field} exceeds the safe integer range")
        return value
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError(f"progress {field} must be a bounded finite number")
        return value
    raise ValueError(f"progress {field} must be a number")


def _validate_event_payload(
    event_type: str, payload: dict[object, object]
) -> dict[str, object]:
    if event_type == "response":
        if payload.get("ok") is True:
            _require_exact_keys(payload, {"ok", "data"}, "response payload")
            data = payload["data"]
            if not isinstance(data, dict):
                raise ValueError("response data must be an object")
            validated = {"ok": True, "data": data}
        elif payload.get("ok") is False:
            _require_exact_keys(payload, {"ok", "error"}, "response payload")
            validated = {"ok": False, "error": _validate_error(payload["error"])}
        else:
            raise ValueError("response ok must be a boolean")
    elif event_type == "progress":
        required = {"job_id", "phase"}
        optional = {"percent", "speed", "eta"}
        keys = set(payload)
        if not required <= keys or not keys <= required | optional:
            raise ValueError("progress payload fields do not match schema")
        job_id = validate_request_id(payload["job_id"])
        phase = payload["phase"]
        if not isinstance(phase, str) or _PHASE_RE.fullmatch(phase) is None:
            raise ValueError("invalid progress phase")
        validated = {"job_id": job_id, "phase": phase}
        for field in optional & keys:
            number = _validate_progress_number(payload[field], field)
            if number < 0 or (field == "percent" and number > 100):
                raise ValueError(f"progress {field} is outside its allowed range")
            validated[field] = number
    elif event_type == "completed":
        _require_exact_keys(payload, {"job_id", "result"}, "completed payload")
        result = payload["result"]
        if not isinstance(result, dict):
            raise ValueError("completed result must be an object")
        validated = {
            "job_id": validate_request_id(payload["job_id"]),
            "result": result,
        }
    elif event_type == "failed":
        _require_exact_keys(payload, {"job_id", "error"}, "failed payload")
        validated = {
            "job_id": validate_request_id(payload["job_id"]),
            "error": _validate_error(payload["error"]),
        }
    elif event_type == "cancelled":
        _require_exact_keys(payload, {"job_id"}, "cancelled payload")
        validated = {"job_id": validate_request_id(payload["job_id"])}
    else:
        raise ValueError("unknown event")
    _validate_bounded_json(validated)
    return validated


def make_response(request_id: str, payload: dict[str, object]) -> dict[str, object]:
    """Build a successful command response envelope."""
    event = {
        "version": PROTOCOL_VERSION,
        "id": validate_request_id(request_id),
        "type": "response",
        "payload": {"ok": True, "data": payload},
    }
    return validate_event(event)


def make_error(request_id: str, code: str, message: str) -> dict[str, object]:
    """Build a stable structured command rejection."""
    event = {
        "version": PROTOCOL_VERSION,
        "id": validate_request_id(request_id),
        "type": "response",
        "payload": {"ok": False, "error": {"code": code, "message": message}},
    }
    return validate_event(event)


def validate_event(message: object) -> dict[str, object]:
    """Validate an exact helper-to-extension event schema."""
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
    validated = {
        "version": PROTOCOL_VERSION,
        "id": event_id,
        "type": event_type,
        "payload": _validate_event_payload(event_type, payload),
    }
    _validate_bounded_json(validated)
    return validated
