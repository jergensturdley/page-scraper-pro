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
    r"(?i)(?<![a-z0-9-])"
    r"("
    r"(?:proxy-authorization|authorization|set-cookie|cookie|"
    r"x-api-key|api-key|x-auth-token)"
    r"\s*:\s*"
    r")"
    r"[^\r\n]*"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_ROOT_NAMES = (
    r"(?:Users|Volumes|private|tmp|var|etc|Library|Applications|System|"
    r"opt|usr|bin|sbin|dev|home)"
)
_PATH_START = (
    rf"(?:~/|file:/{{1,3}}|//{_ROOT_NAMES}(?:/|(?=$))|"
    rf"/{_ROOT_NAMES}(?:/|(?=$)))"
)
_DOUBLE_QUOTED_PATH_RE = re.compile(
    rf'"(?P<path>{_PATH_START}[^"\r\n]*)"', re.IGNORECASE
)
_SINGLE_QUOTED_PATH_RE = re.compile(
    rf"'(?P<path>{_PATH_START}[^'\r\n]*)'", re.IGNORECASE
)
_UNQUOTED_EXTENSION_PATH_RE = re.compile(
    rf"(?<![a-zA-Z0-9_~:/])"
    rf"(?P<path>{_PATH_START}[^\r\n\"'<>]*?"
    rf"\.(?:[a-z][a-z0-9]{{0,15}}|3gp|7z)"
    rf"(?:\.[a-z][a-z0-9]{{0,15}})?)"
    rf"(?=$|[\s:;,.!?—)\]])",
    re.IGNORECASE,
)
_UNQUOTED_EXTENSIONLESS_PATH_RE = re.compile(
    rf"(?<![a-zA-Z0-9_~:/])"
    rf"(?P<path>{_PATH_START}[^\s\r\n\"'<>:;,.!?—)\]]+)",
    re.IGNORECASE,
)
_SENSITIVE_COMPONENTS = frozenset(
    {
        "authorization",
        "cookie",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "destination",
        "path",
        "filename",
        "output",
        "file",
        "filepath",
        "traceback",
        "stacktrace",
    }
)
_SENSITIVE_COMPOUNDS = frozenset(
    {
        ("api", "key"),
        ("private", "key"),
        ("session", "id"),
        ("file", "path"),
    }
)
_URL_KEYS = frozenset({("url",), ("source", "url"), ("webpage", "url"), ("request", "url")})


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


def _path_replacement(path: str) -> str:
    if path.startswith("~/"):
        return "~/<redacted>"
    if path.casefold().startswith("file:"):
        return "file:<redacted>"
    return _REDACTED


def _redact_quoted_path(match: re.Match[str]) -> str:
    quote = match.group(0)[0]
    return quote + _path_replacement(match.group("path")) + quote


def _redact_unquoted_path(match: re.Match[str]) -> str:
    return _path_replacement(match.group("path"))


def _replace_home_prefix(value: str, home: Path) -> str:
    home_text = str(home)
    if not home_text or home_text == os.sep:
        return value
    pattern = re.compile(
        rf"(?<![a-zA-Z0-9_]){re.escape(home_text)}"
        rf"(?=/|$|[\s:;,.!?—)\]])"
    )
    return pattern.sub("~", value)


def redact_text(
    value: str,
    *,
    home: Path | None = None,
    max_length: int = _MAX_STRING_LENGTH,
) -> str:
    """Redact secret headers, URL parameters, and filesystem paths."""
    if not isinstance(value, str):
        raise TypeError("diagnostic text must be a string")
    if type(max_length) is not int or max_length < len(_TRUNCATED):
        raise ValueError("max_length is too small")

    redacted = _HEADER_RE.sub(lambda match: match.group(1) + _REDACTED, value)
    redacted = _URL_RE.sub(_redact_embedded_url, redacted)

    private_home = home if home is not None else Path.home()
    if not isinstance(private_home, Path):
        raise TypeError("home must be a Path")
    redacted = _replace_home_prefix(redacted, private_home)
    redacted = _DOUBLE_QUOTED_PATH_RE.sub(_redact_quoted_path, redacted)
    redacted = _SINGLE_QUOTED_PATH_RE.sub(_redact_quoted_path, redacted)
    redacted = _UNQUOTED_EXTENSION_PATH_RE.sub(_redact_unquoted_path, redacted)
    redacted = _UNQUOTED_EXTENSIONLESS_PATH_RE.sub(
        _redact_unquoted_path, redacted
    )

    if len(redacted) > max_length:
        redacted = redacted[: max_length - len(_TRUNCATED)] + _TRUNCATED
    return redacted


def _key_components(key: str) -> tuple[str, ...]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", expanded)
    return tuple(re.findall(r"[a-z0-9]+", expanded.casefold()))


def _is_sensitive_key(key: str) -> bool:
    components = _key_components(key)
    if any(component in _SENSITIVE_COMPONENTS for component in components):
        return True
    return any(
        pair in _SENSITIVE_COMPOUNDS
        for pair in zip(components, components[1:])
    )


def _redact_value(value: object, *, key: str | None, home: Path, depth: int) -> object:
    if depth > _MAX_DEPTH:
        return _TRUNCATED
    components = _key_components(key) if key is not None else ()
    if key is not None and _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        if components in _URL_KEYS:
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
                item, key=str(raw_key), home=home, depth=depth + 1
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
