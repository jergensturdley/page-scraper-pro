"""Cookie-free, single-job media analysis worker."""

from contextlib import contextmanager, nullcontext
import hashlib
import ipaddress
import json
import math
import os
import socket
import sys
import urllib.request
from typing import Callable, Iterator, TextIO

from .constants import MAX_MESSAGE_BYTES
from .validation import normalize_http_url, validate_worker_job


PRESET_SELECTORS = {
    "best": "bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720p": "bv*[height<=720]+ba/b[height<=720]/b",
    "audio": "ba/b",
}

_MAX_DURATION_SECONDS = 3_155_760_000
_PROXY_OPTION_SUFFIX = "_proxy"
_LIVE_STATES = frozenset(
    {"not_live", "is_live", "was_live", "post_live", "is_upcoming"}
)


class AnalysisError(Exception):
    """An analysis failure with a stable, protocol-safe error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _UnsafeNetworkTarget(Exception):
    pass


def _is_global_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.is_global
        and not address.is_loopback
        and not address.is_private
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _request_url(request: object) -> object:
    if isinstance(request, str):
        return request
    full_url = getattr(request, "full_url", None)
    if full_url is not None:
        return full_url
    get_full_url = getattr(request, "get_full_url", None)
    if callable(get_full_url):
        return get_full_url()
    return getattr(request, "url", None)


def _validate_request_url(request: object) -> str:
    try:
        return normalize_http_url(_request_url(request))
    except ValueError as exc:
        raise AnalysisError("invalid_url", "Only public HTTP(S) URLs are allowed") from exc


def _patch_request_constructor(
    request_class: type[object], restorations: list[tuple[object, str, object]]
) -> None:
    original = getattr(request_class, "__init__", None)
    if not callable(original):
        return

    def guarded_init(instance: object, *args: object, **kwargs: object) -> None:
        requested_url = args[0] if args else kwargs.get("url")
        try:
            normalize_http_url(requested_url)
        except ValueError as exc:
            raise AnalysisError(
                "invalid_url", "Only public HTTP(S) URLs are allowed"
            ) from exc
        original(instance, *args, **kwargs)

    restorations.append((request_class, "__init__", original))
    setattr(request_class, "__init__", guarded_init)


def _socket_address_parts(
    family: int, address: object
) -> tuple[str, int]:
    if family == socket.AF_INET:
        if not isinstance(address, tuple) or len(address) != 2:
            raise _UnsafeNetworkTarget("unsupported IPv4 socket address")
    elif family == socket.AF_INET6:
        if not isinstance(address, tuple) or len(address) not in {2, 3, 4}:
            raise _UnsafeNetworkTarget("unsupported IPv6 socket address")
    else:
        raise _UnsafeNetworkTarget("only INET socket connections are allowed")
    host, port = address[:2]
    if (
        not isinstance(host, str)
        or not host
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 0 <= port <= 65_535
    ):
        raise _UnsafeNetworkTarget("unsupported INET socket address")
    return host, port


def _numeric_socket_address(family: int, address: object) -> bool:
    host, _ = _socket_address_parts(family, address)
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return False
    expected_version = 4 if family == socket.AF_INET else 6
    if parsed.version != expected_version or not _is_global_address(host):
        raise _UnsafeNetworkTarget("socket address is not global")
    return True


@contextmanager
def _network_guard() -> Iterator[None]:
    """Revalidate DNS results and every visible request URL during extraction."""
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    proxy_environment = {
        key: value
        for key, value in os.environ.items()
        if key.lower().endswith(_PROXY_OPTION_SUFFIX)
    }
    restorations: list[tuple[object, str, object]] = []

    def guarded_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        answers = original_getaddrinfo(*args, **kwargs)
        for answer in answers:
            try:
                resolved = answer[4][0]
            except (IndexError, TypeError) as exc:
                raise _UnsafeNetworkTarget("resolver returned an invalid address") from exc
            if not isinstance(resolved, str) or not _is_global_address(resolved):
                raise _UnsafeNetworkTarget("resolver returned a non-global address")
        return answers

    def validated_connect_address(instance: socket.socket, address: object) -> object:
        family = instance.family
        if _numeric_socket_address(family, address):
            return address
        host, port = _socket_address_parts(family, address)
        answers = guarded_getaddrinfo(
            host,
            port,
            family=family,
            type=instance.type,
            proto=instance.proto,
        )
        for answer in answers:
            try:
                answer_family, answer_type, _, _, sockaddr = answer
            except (TypeError, ValueError) as exc:
                raise _UnsafeNetworkTarget("resolver returned an invalid answer") from exc
            if answer_family != family or answer_type != instance.type:
                continue
            if not _numeric_socket_address(family, sockaddr):
                raise _UnsafeNetworkTarget("resolver returned a hostname address")
            return sockaddr
        raise _UnsafeNetworkTarget("resolver returned no compatible address")

    def guarded_connect(instance: socket.socket, address: object) -> object:
        return original_connect(instance, validated_connect_address(instance, address))

    def guarded_connect_ex(instance: socket.socket, address: object) -> int:
        return original_connect_ex(
            instance, validated_connect_address(instance, address)
        )

    original_redirect = urllib.request.HTTPRedirectHandler.redirect_request

    def guarded_redirect(
        handler: object,
        request: object,
        file_pointer: object,
        code: object,
        message: object,
        headers: object,
        new_url: object,
    ) -> object:
        try:
            normalize_http_url(new_url)
        except ValueError as exc:
            raise AnalysisError(
                "invalid_url", "Only public HTTP(S) URLs are allowed"
            ) from exc
        return original_redirect(
            handler, request, file_pointer, code, message, headers, new_url
        )

    try:
        for key in list(os.environ):
            if key.lower().endswith(_PROXY_OPTION_SUFFIX):
                del os.environ[key]
        socket.getaddrinfo = guarded_getaddrinfo
        restorations.append((socket, "getaddrinfo", original_getaddrinfo))
        socket.socket.connect = guarded_connect
        restorations.append((socket.socket, "connect", original_connect))
        socket.socket.connect_ex = guarded_connect_ex
        restorations.append((socket.socket, "connect_ex", original_connect_ex))
        _patch_request_constructor(urllib.request.Request, restorations)
        restorations.append(
            (urllib.request.HTTPRedirectHandler, "redirect_request", original_redirect)
        )
        urllib.request.HTTPRedirectHandler.redirect_request = guarded_redirect

        # yt-dlp has its own Request class. Patch it only when the lazy import
        # loaded the module; injected test factories require no yt-dlp install.
        networking_module = sys.modules.get("yt_dlp.networking.common")
        yt_request = getattr(networking_module, "Request", None)
        if isinstance(yt_request, type) and yt_request is not urllib.request.Request:
            _patch_request_constructor(yt_request, restorations)
        yield
    finally:
        for owner, name, original in reversed(restorations):
            setattr(owner, name, original)
        for key in list(os.environ):
            if key.lower().endswith(_PROXY_OPTION_SUFFIX):
                del os.environ[key]
        os.environ.update(proxy_environment)


@contextmanager
def _guard_ydl_urls(ydl: object) -> Iterator[None]:
    """Guard yt-dlp's public request entry points, including redirect sends."""
    restorations: list[tuple[object, str, object]] = []

    def patch_callable(owner: object, name: str) -> None:
        original = getattr(owner, name, None)
        if not callable(original):
            return

        def guarded(request: object, *args: object, **kwargs: object) -> object:
            _validate_request_url(request)
            return original(request, *args, **kwargs)

        restorations.append((owner, name, original))
        setattr(owner, name, guarded)

    patch_callable(ydl, "urlopen")
    director = getattr(ydl, "_request_director", None)
    if director is not None:
        patch_callable(director, "send")
    try:
        yield
    finally:
        for owner, name, original in reversed(restorations):
            setattr(owner, name, original)


def _bounded_text(value: object, limit: int, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(" " if ord(character) < 32 else character for character in value)
    cleaned = cleaned.strip()
    return (cleaned or fallback)[:limit]


def _safe_duration(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(int(value), _MAX_DURATION_SECONDS)


def _safe_thumbnail(value: object) -> str | None:
    try:
        return normalize_http_url(value)
    except ValueError:
        return None


def _subtitle_languages(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    languages = {
        _bounded_text(language, 32, "")
        for language in value
        if isinstance(language, str)
    }
    languages.discard("")
    return sorted(languages)[:100]


def available_presets(formats: list[dict[str, object]]) -> list[str]:
    """Return supported presets in a stable UI order without exposing formats."""
    usable = [item for item in formats if isinstance(item, dict)]
    if not usable:
        return []
    video_heights: list[float] = []
    has_audio = False
    for item in usable:
        video_codec = item.get("vcodec")
        audio_codec = item.get("acodec")
        if isinstance(audio_codec, str) and audio_codec.lower() != "none":
            has_audio = True
        if isinstance(video_codec, str) and video_codec.lower() != "none":
            height = item.get("height")
            if (
                not isinstance(height, bool)
                and isinstance(height, (int, float))
                and math.isfinite(height)
                and height > 0
            ):
                video_heights.append(float(height))

    result = ["best"]
    maximum_height = max(video_heights, default=0)
    if maximum_height >= 1080:
        result.append("1080p")
    if maximum_height >= 720:
        result.append("720p")
    if has_audio:
        result.append("audio")
    return result


def normalize_media_info(info: dict[str, object]) -> dict[str, object]:
    """Reduce extractor output to a deterministic, bounded allowlist."""
    if not isinstance(info, dict):
        raise AnalysisError("analysis_failed", "Extractor returned invalid media data")

    provider = _bounded_text(
        info.get("extractor") or info.get("extractor_key"), 80, "unknown"
    )
    extractor_id = _bounded_text(info.get("id"), 300, "unknown")
    source_url = info.get("webpage_url") or info.get("original_url")
    try:
        normalized_url = normalize_http_url(source_url)
    except ValueError:
        normalized_url = ""
    digest_source = f"{normalized_url}\n{provider}\n{extractor_id}".encode("utf-8")
    media_id = hashlib.sha256(digest_source).hexdigest()[:24]

    entries = info.get("entries")
    is_playlist = info.get("_type") in {"playlist", "multi_video"} or isinstance(
        entries, list
    )
    playlist_count = len(entries) if isinstance(entries, list) else 0
    formats_value = info.get("formats")
    formats = formats_value if isinstance(formats_value, list) else []
    safe_formats = [item for item in formats if isinstance(item, dict)]
    live_status = info.get("live_status")
    if live_status not in _LIVE_STATES:
        live_status = "is_live" if info.get("is_live") is True else "unknown"

    normalized: dict[str, object] = {
        "media_id": media_id,
        "title": _bounded_text(info.get("title"), 300, "Untitled"),
        "provider": provider,
        "duration": _safe_duration(info.get("duration")),
        "thumbnail": _safe_thumbnail(info.get("thumbnail")),
        "subtitles": _subtitle_languages(info.get("subtitles")),
        "is_playlist": is_playlist,
        "playlist_count": playlist_count,
        "live_status": live_status,
        "available_presets": [] if is_playlist else available_presets(safe_formats),
    }
    encoded = json.dumps(
        normalized, allow_nan=False, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise AnalysisError("analysis_failed", "Normalized media data is too large")
    return normalized


def _map_exception(exc: BaseException) -> AnalysisError:
    if isinstance(exc, AnalysisError):
        return exc
    if isinstance(exc, _UnsafeNetworkTarget):
        return AnalysisError(
            "unsafe_network_target", "A network address was not public"
        )
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "drm" in name or "drm" in message:
        return AnalysisError("drm_protected", "This media is DRM protected")
    if "unsupported" in name or "unsupported" in message:
        return AnalysisError("unsupported_url", "No supported extractor was found")
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in message:
        return AnalysisError("timeout", "Media analysis timed out")
    if isinstance(exc, (OSError, ConnectionError)) or any(
        marker in message
        for marker in ("network", "connection", "dns", "temporary failure")
    ):
        return AnalysisError("network_error", "Media analysis could not reach the site")
    if isinstance(exc, ValueError):
        return AnalysisError("invalid_url", "Only public HTTP(S) URLs are allowed")
    return AnalysisError("analysis_failed", "Media analysis failed")


def analyze_url(
    url: str, ydl_factory: Callable[[dict[str, object]], object] | None = None
) -> dict[str, object]:
    """Analyze one public URL without cookies, profiles, or media downloads."""
    try:
        normalized_url = normalize_http_url(url)
    except ValueError as exc:
        raise AnalysisError("invalid_url", "Only public HTTP(S) URLs are allowed") from exc

    if ydl_factory is None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise AnalysisError(
                "dependency_unavailable", "yt-dlp is not installed"
            ) from exc
        ydl_factory = YoutubeDL

    options: dict[str, object] = {
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "quiet": True,
        "proxy": "",
    }
    try:
        with _network_guard():
            manager = ydl_factory(options)
            ydl_context = (
                manager
                if hasattr(manager, "__enter__") and hasattr(manager, "__exit__")
                else nullcontext(manager)
            )
            with ydl_context as ydl:
                with _guard_ydl_urls(ydl):
                    info = ydl.extract_info(normalized_url, download=False)
        return normalize_media_info(info)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise _map_exception(exc) from exc


def _emit(stream: TextIO, event: dict[str, object]) -> None:
    encoded = json.dumps(
        event, allow_nan=False, ensure_ascii=False, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise AnalysisError("analysis_failed", "Worker event is too large")
    stream.write(encoded + "\n")
    stream.flush()


def _failed(stream: TextIO, code: str, message: str) -> int:
    _emit(stream, {"type": "failed", "error": {"code": code, "message": message}})
    return 1


def run_worker(input_stream: TextIO, output_stream: TextIO) -> int:
    """Read exactly one JSON line and emit newline-delimited worker events."""
    line = input_stream.readline(MAX_MESSAGE_BYTES + 2)
    if not line or len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
        return _failed(output_stream, "invalid_job", "Worker job is missing or too large")
    if input_stream.read(1):
        return _failed(output_stream, "invalid_job", "Worker accepts exactly one line")
    try:
        job_value = json.loads(
            line,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (ValueError, TypeError, RecursionError):
        return _failed(output_stream, "invalid_job", "Worker job is not valid JSON")

    exact_analyze_shape = (
        isinstance(job_value, dict)
        and set(job_value) == {"operation", "url"}
        and job_value.get("operation") == "analyze"
    )
    try:
        job = validate_worker_job(job_value)
    except ValueError:
        code = "invalid_url" if exact_analyze_shape else "invalid_job"
        message = (
            "Only public HTTP(S) URLs are allowed"
            if code == "invalid_url"
            else "Worker job does not match the analyze schema"
        )
        return _failed(output_stream, code, message)

    _emit(output_stream, {"type": "phase", "phase": "analyzing"})
    try:
        result = analyze_url(job["url"])
    except AnalysisError as exc:
        return _failed(output_stream, exc.code, exc.message)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        mapped = _map_exception(exc)
        return _failed(output_stream, mapped.code, mapped.message)
    _emit(output_stream, {"type": "completed", "result": result})
    return 0


def main() -> int:
    return run_worker(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
