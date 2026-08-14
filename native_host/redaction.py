"""Bounded redaction helpers for local diagnostic context."""

import math
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_REDACTED = "<redacted>"
_TRUNCATED = "<truncated>"
_MAX_DEPTH = 5
_MAX_ENTRIES = 32
_MAX_STRING_LENGTH = 512
_MAX_KEY_LENGTH = 128
_HEADER_RE = re.compile(
    r"(?im)^(\s*(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)[^\r\n]*"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "destination",
        "destination-path",
        "file",
        "filepath",
        "file-path",
        "filename",
        "file-name",
        "output",
        "output-path",
        "traceback",
        "stacktrace",
        "stack-trace",
    }
)
_URL_KEYS = frozenset({"url", "source-url", "webpage-url", "request-url"})


def _bounded(value: str) -> str:
    if len(value) <= _MAX_STRING_LENGTH:
        return value
    return value[: _MAX_STRING_LENGTH - len(_TRUNCATED)] + _TRUNCATED


def redact_url(value: str) -> str:
    """Remove URL credentials and all query/fragment contents."""
    if not isinstance(value, str):
        raise TypeError("URL must be a string")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        base = re.split(r"[?#]", value, maxsplit=1)[0]
        return _bounded(re.sub(r"(?<=//)[^/@\s]+@", "", base))

    if not parsed.scheme or hostname is None:
        base = re.split(r"[?#]", value, maxsplit=1)[0]
        return _bounded(re.sub(r"(?<=//)[^/@\s]+@", "", base))
    host = hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    redacted = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    if parsed.query:
        redacted += "?<redacted>"
    elif parsed.fragment:
        redacted += "#<redacted>"
    return _bounded(redacted)


def _redact_embedded_url(match: re.Match[str]) -> str:
    candidate = match.group(0)
    trailing = ""
    while candidate and candidate[-1] in ".,;!)]}":
        trailing = candidate[-1] + trailing
        candidate = candidate[:-1]
    return redact_url(candidate) + trailing


def redact_text(
    value: str,
    *,
    home: Path | None = None,
    max_length: int = _MAX_STRING_LENGTH,
) -> str:
    """Redact secret headers, URL parameters, and paths below the user home."""
    if not isinstance(value, str):
        raise TypeError("diagnostic text must be a string")
    if type(max_length) is not int or max_length < len(_TRUNCATED):
        raise ValueError("max_length is too small")

    redacted = _HEADER_RE.sub(lambda match: match.group(1) + _REDACTED, value)
    redacted = _URL_RE.sub(_redact_embedded_url, redacted)

    private_home = home if home is not None else Path.home()
    if not isinstance(private_home, Path):
        raise TypeError("home must be a Path")
    home_text = str(private_home)
    if home_text and home_text != os.sep:
        home_pattern = re.compile(
            rf"{re.escape(home_text)}(?:{re.escape(os.sep)}[^\s\"'<>]*)?"
        )
        redacted = home_pattern.sub("~/<redacted>", redacted)

    if len(redacted) > max_length:
        redacted = redacted[: max_length - len(_TRUNCATED)] + _TRUNCATED
    return redacted


def _normalized_key(key: str) -> str:
    return key.casefold().replace("_", "-")


def _redact_value(value: object, *, key: str | None, home: Path, depth: int) -> object:
    if depth > _MAX_DEPTH:
        return _TRUNCATED
    normalized_key = _normalized_key(key) if key is not None else None
    if normalized_key in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, str):
        if normalized_key in _URL_KEYS:
            return redact_url(value)
        return redact_text(value, home=home)
    if value is None or isinstance(value, bool) or type(value) is int:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else "<non-finite>"
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        items = list(value.items())
        for raw_key, item in items[:_MAX_ENTRIES]:
            text_key = redact_text(str(raw_key), home=home)[:_MAX_KEY_LENGTH]
            redacted[text_key] = _redact_value(
                item, key=text_key, home=home, depth=depth + 1
            )
        if len(items) > _MAX_ENTRIES:
            redacted[_TRUNCATED] = len(items) - _MAX_ENTRIES
        return redacted
    if isinstance(value, (list, tuple)):
        redacted_items = [
            _redact_value(item, key=None, home=home, depth=depth + 1)
            for item in value[:_MAX_ENTRIES]
        ]
        if len(value) > _MAX_ENTRIES:
            redacted_items.append(_TRUNCATED)
        return redacted_items
    return redact_text(str(value), home=home)


def redact_mapping(
    value: dict[str, object], *, home: Path | None = None
) -> dict[str, object]:
    """Return recursively redacted diagnostic context within fixed bounds."""
    if not isinstance(value, dict):
        raise TypeError("diagnostic context must be a dictionary")
    private_home = home if home is not None else Path.home()
    if not isinstance(private_home, Path):
        raise TypeError("home must be a Path")
    return _redact_value(value, key=None, home=private_home, depth=0)
