"""Cookie-free, single-job media analysis worker."""

from contextlib import contextmanager, nullcontext
import hashlib
import ipaddress
import json
import math
import os
import secrets
import shutil
import socket
import stat
import sys
import urllib.request
from pathlib import Path
import re
import unicodedata
from typing import Callable, Iterator, TextIO

from .constants import MAX_MESSAGE_BYTES
from .validation import normalize_http_url, validate_request_id, validate_worker_job


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
_MAX_FILENAME_BYTES = 180
_FILENAME_SEPARATORS_RE = re.compile(r"[/:\\]+")
_FILENAME_WHITESPACE_RE = re.compile(r"\s+")
_EXTENSION_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")


def _truncate_utf8(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", "ignore")


def _clean_filename_part(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    normalized = _FILENAME_SEPARATORS_RE.sub("-", normalized)
    while ".." in normalized:
        normalized = normalized.replace("..", ".")
    normalized = _FILENAME_WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip(" .")


def _bounded_filename(stem: str, extension: str, suffix: str = "") -> str:
    ending = suffix + (f".{extension}" if extension else "")
    stem_limit = max(1, _MAX_FILENAME_BYTES - len(ending.encode("utf-8")))
    bounded_stem = _truncate_utf8(stem, stem_limit).rstrip(" .") or "download"
    return bounded_stem + ending


def sanitize_filename(title: object, media_id: str, extension: str) -> str:
    """Create a visible, traversal-free basename bounded for common filesystems."""
    clean_extension = extension.lstrip(".") if isinstance(extension, str) else ""
    if _EXTENSION_RE.fullmatch(clean_extension) is None:
        clean_extension = "bin"
    stem = _clean_filename_part(title) or _clean_filename_part(media_id) or "download"
    stem = stem.lstrip(".").strip() or _clean_filename_part(media_id) or "download"
    return _bounded_filename(stem, clean_extension.lower())


def _collision_filename(filename: str, index: int) -> str:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ValueError("filename must be a plain basename")
    if filename in {".", ".."} or filename.startswith("."):
        raise ValueError("filename must be visible")
    if len(filename.encode("utf-8")) > _MAX_FILENAME_BYTES:
        raise ValueError("filename is too long")
    if index == 0:
        return filename
    suffix = Path(filename).suffix
    extension = suffix[1:] if suffix else ""
    stem = filename[:-len(suffix)] if suffix else filename
    return _bounded_filename(stem, extension, f" ({index})")


def collision_safe_path(directory: Path, filename: str) -> Path:
    """Choose a currently unused basename beneath an existing directory."""
    resolved_directory = Path(directory).resolve(strict=True)
    if not resolved_directory.is_dir():
        raise ValueError("download destination must be a directory")
    for index in range(10_000):
        candidate = resolved_directory / _collision_filename(filename, index)
        if not os.path.lexists(candidate):
            return candidate
    raise AnalysisError("download_failed", "No safe output filename was available")


def build_download_options(
    preset: str, temp_dir: Path, progress_hook: object
) -> dict[str, object]:
    """Build fixed yt-dlp options from the extension-facing preset enum."""
    if not isinstance(preset, str) or preset not in PRESET_SELECTORS:
        raise ValueError("invalid download preset")
    try:
        resolved_temp = Path(temp_dir).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("temporary directory does not exist") from exc
    if not resolved_temp.is_dir() or not os.access(resolved_temp, os.W_OK):
        raise ValueError("temporary directory is not writable")
    return {
        "format": PRESET_SELECTORS[preset],
        "outtmpl": str(resolved_temp / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "continuedl": True,
        "overwrites": False,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [progress_hook],
    }


class AnalysisError(Exception):
    """An analysis failure with a stable, protocol-safe error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _UnsafeNetworkTarget(Exception):
    pass


def _validated_download_directory(
    value: Path, code: str, message: str
) -> tuple[Path, os.stat_result]:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        raise AnalysisError(code, message)
    try:
        supplied = os.lstat(value)
        if stat.S_ISLNK(supplied.st_mode) or not stat.S_ISDIR(supplied.st_mode):
            raise AnalysisError(code, message)
        resolved = value.resolve(strict=True)
        opened = os.stat(resolved, follow_symlinks=False)
    except AnalysisError:
        raise
    except (OSError, RuntimeError) as exc:
        raise AnalysisError(code, message) from exc
    mode = stat.S_IMODE(opened.st_mode)
    if not stat.S_ISDIR(opened.st_mode) or mode & 0o111 == 0 or mode & 0o222 == 0:
        raise AnalysisError(code, message)
    return resolved, opened


def _same_directory(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _validated_empty_private_temp(value: Path) -> tuple[Path, os.stat_result]:
    resolved, identity = _validated_download_directory(
        value, "invalid_job", "The temporary directory was not valid"
    )
    mode = stat.S_IMODE(identity.st_mode)
    if identity.st_uid != os.getuid() or mode & 0o077:
        raise AnalysisError(
            "invalid_job", "The temporary directory was not private"
        )
    try:
        with os.scandir(resolved) as entries:
            if next(entries, None) is not None:
                raise AnalysisError(
                    "invalid_job", "The temporary directory was not empty"
                )
    except OSError as exc:
        raise AnalysisError(
            "invalid_job", "The temporary directory could not be inspected"
        ) from exc
    return resolved, identity


def _write_all_descriptor(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise OSError("short write while staging completed download")
        offset += written


def _install_complete_file(
    source: Path,
    destination: Path,
    destination_identity: os.stat_result,
    filename: str,
) -> Path:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_fd = -1
    source_fd = -1
    staging_fd = -1
    staging_name: str | None = None
    final_name: str | None = None
    staging_identity: os.stat_result | None = None
    try:
        directory_fd = os.open(destination, directory_flags)
        opened_destination = os.fstat(directory_fd)
        if not _same_directory(destination_identity, opened_destination):
            raise AnalysisError(
                "unsafe_destination", "The download destination changed"
            )

        source_before = os.lstat(source)
        source_fd = os.open(source, os.O_RDONLY | nofollow)
        source_opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(source_opened.st_mode)
            or not _same_directory(source_before, source_opened)
        ):
            raise AnalysisError(
                "unsafe_download_output", "The completed download changed"
            )

        staging_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
        for _ in range(32):
            candidate = ".pagescraper-pro-" + secrets.token_hex(16) + ".tmp"
            try:
                staging_fd = os.open(
                    candidate, staging_flags, 0o600, dir_fd=directory_fd
                )
                staging_name = candidate
                break
            except FileExistsError:
                continue
        if staging_fd < 0 or staging_name is None:
            raise AnalysisError(
                "download_failed", "A destination staging file was unavailable"
            )

        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            _write_all_descriptor(staging_fd, chunk)
        os.fsync(staging_fd)
        staging_identity = os.fstat(staging_fd)
        os.close(staging_fd)
        staging_fd = -1

        for index in range(10_000):
            candidate = _collision_filename(filename, index)
            try:
                os.link(
                    staging_name,
                    candidate,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                final_name = candidate
                break
            except FileExistsError:
                continue
        if final_name is None or staging_identity is None:
            raise AnalysisError(
                "download_failed", "No safe output filename was available"
            )
        try:
            linked_identity = os.stat(
                final_name, dir_fd=directory_fd, follow_symlinks=False
            )
        except OSError as exc:
            raise AnalysisError(
                "unsafe_destination", "The final output path changed"
            ) from exc
        if not _same_directory(staging_identity, linked_identity):
            raise AnalysisError(
                "unsafe_destination", "The final output path changed"
            )

        try:
            current_destination = os.stat(destination, follow_symlinks=False)
        except OSError as exc:
            raise AnalysisError(
                "unsafe_destination", "The download destination changed"
            ) from exc
        if not _same_directory(opened_destination, current_destination):
            raise AnalysisError(
                "unsafe_destination", "The download destination changed"
            )
        os.unlink(staging_name, dir_fd=directory_fd)
        staging_name = None
        os.fsync(directory_fd)
        try:
            source.unlink()
        except OSError:
            pass
        return destination / final_name
    finally:
        if staging_fd >= 0:
            os.close(staging_fd)
        if staging_name is not None and directory_fd >= 0:
            try:
                os.unlink(staging_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if source_fd >= 0:
            os.close(source_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


def _bounded_progress_number(value: object, maximum: float) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    number = min(number, maximum)
    return int(number) if number.is_integer() else round(number, 2)


def _progress_event(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status == "downloading":
        phase = "downloading"
    elif status in {"started", "processing"}:
        phase = "processing"
    elif status == "finished":
        phase = "finalizing"
    else:
        return None
    event: dict[str, object] = {"type": "progress", "phase": phase}
    downloaded = _bounded_progress_number(value.get("downloaded_bytes"), 10**18)
    total_value = value.get("total_bytes")
    if total_value is None:
        total_value = value.get("total_bytes_estimate")
    total = _bounded_progress_number(total_value, 10**18)
    if downloaded is not None and total not in (None, 0):
        event["percent"] = min(100, round(float(downloaded) * 100 / float(total), 2))
    elif phase == "finalizing":
        event["percent"] = 100
    speed = _bounded_progress_number(value.get("speed"), 10**15)
    eta = _bounded_progress_number(value.get("eta"), _MAX_DURATION_SECONDS)
    if speed is not None:
        event["speed"] = speed
    if eta is not None:
        event["eta"] = eta
    return event


def _download_source(
    info: object, temp_dir: Path, prepared_filename: object = None
) -> Path:
    candidates: list[object] = []
    if isinstance(info, dict):
        for key in ("filepath", "_filename"):
            candidates.append(info.get(key))
        candidates.append(prepared_filename)
        requested = info.get("requested_downloads")
        if isinstance(requested, list):
            for item in reversed(requested):
                if isinstance(item, dict):
                    candidates.extend((item.get("filepath"), item.get("_filename")))
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        try:
            supplied = Path(candidate)
            metadata = os.lstat(supplied)
            resolved = supplied.resolve(strict=True)
            resolved.relative_to(temp_dir)
        except (OSError, RuntimeError, ValueError):
            continue
        if stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            return resolved
    raise AnalysisError(
        "unsafe_download_output", "The completed download was outside its temporary directory"
    )


def _clear_temp_directory(temp_dir: Path) -> None:
    try:
        children = list(temp_dir.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        except OSError:
            continue


def _map_download_exception(exc: BaseException) -> AnalysisError:
    if isinstance(exc, AnalysisError):
        return exc
    message = str(exc).lower()
    if "ffmpeg" in message and any(
        marker in message for marker in ("required", "not found", "missing", "not installed", "merge")
    ):
        return AnalysisError(
            "ffmpeg_required", "ffmpeg is required for the selected download preset"
        )
    mapped = _map_exception(exc)
    if mapped.code in {"unsafe_network_target", "invalid_url", "timeout", "drm_protected"}:
        return mapped
    return AnalysisError("download_failed", "Media download failed")


def download_media(
    url: str,
    media_id: str,
    preset: str,
    temp_dir: Path,
    destination: Path,
    emit: Callable[[dict[str, object]], None],
    ydl_factory: Callable[[dict[str, object]], object] | None = None,
) -> dict[str, str]:
    """Download one allowlisted preset and move one complete file into place."""
    cleanup_temp: Path | None = None
    cleanup_identity: os.stat_result | None = None
    try:
        normalized_url = normalize_http_url(url)
        validated_media_id = validate_request_id(media_id)
        resolved_temp, temp_identity = _validated_empty_private_temp(temp_dir)
        cleanup_temp = resolved_temp
        cleanup_identity = temp_identity
        resolved_destination, destination_identity = _validated_download_directory(
            destination,
            "unsafe_destination",
            "The download destination was not safe",
        )
        if resolved_destination == resolved_temp or resolved_destination.is_relative_to(
            resolved_temp
        ):
            raise AnalysisError(
                "unsafe_destination", "The download destination was not safe"
            )
        if not callable(emit):
            raise AnalysisError("invalid_job", "The progress callback was not valid")
        if ydl_factory is None:
            try:
                from yt_dlp import YoutubeDL
            except ImportError as exc:
                raise AnalysisError(
                    "dependency_unavailable", "yt-dlp is not installed"
                ) from exc
            ydl_factory = YoutubeDL

        def progress_hook(value: object) -> None:
            event = _progress_event(value)
            if event is not None:
                emit(event)

        options = build_download_options(preset, resolved_temp, progress_hook)
        options.update({"quiet": True, "socket_timeout": 15})
        prepared_filename: object = None
        with _network_guard():
            manager = ydl_factory(options)
            ydl_context = (
                manager
                if hasattr(manager, "__enter__") and hasattr(manager, "__exit__")
                else nullcontext(manager)
            )
            with ydl_context as ydl:
                with _guard_ydl_urls(ydl):
                    info = ydl.extract_info(normalized_url, download=True)
                prepare_filename = getattr(ydl, "prepare_filename", None)
                if callable(prepare_filename):
                    try:
                        prepared_filename = prepare_filename(info)
                    except (OSError, TypeError, ValueError):
                        prepared_filename = None

        source = _download_source(info, resolved_temp, prepared_filename)
        current_destination, current_identity = _validated_download_directory(
            destination,
            "unsafe_destination",
            "The download destination was not safe",
        )
        if not _same_directory(destination_identity, current_identity):
            raise AnalysisError(
                "unsafe_destination", "The download destination changed"
            )
        extension = source.suffix.lstrip(".")
        title = info.get("title") if isinstance(info, dict) else None
        final_name = sanitize_filename(title, validated_media_id, extension)
        final_path = _install_complete_file(
            source, current_destination, current_identity, final_name
        )
        return {"filename": final_path.name, "directory": str(current_destination)}
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise _map_download_exception(exc) from exc
    finally:
        if cleanup_temp is not None and cleanup_identity is not None:
            try:
                current_temp, current_identity = _validated_download_directory(
                    temp_dir, "invalid_job", "The temporary directory was not valid"
                )
            except AnalysisError:
                pass
            else:
                if current_temp == cleanup_temp and _same_directory(
                    cleanup_identity, current_identity
                ):
                    _clear_temp_directory(current_temp)


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
    cleaned = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character
        for character in value
    )
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
            else "Worker job does not match an allowed schema"
        )
        return _failed(output_stream, code, message)

    operation = job["operation"]
    if operation == "analyze":
        _emit(output_stream, {"type": "phase", "phase": "analyzing"})
    try:
        if operation == "analyze":
            result = analyze_url(job["url"])
        else:
            result = download_media(
                job["url"],
                job["media_id"],
                job["preset"],
                Path(job["temp_dir"]),
                Path(job["destination"]),
                lambda event: _emit(output_stream, event),
            )
    except AnalysisError as exc:
        if exc.code == "cancelled":
            _emit(output_stream, {"type": "cancelled"})
            return 1
        return _failed(output_stream, exc.code, exc.message)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        mapped = _map_exception(exc) if operation == "analyze" else _map_download_exception(exc)
        return _failed(output_stream, mapped.code, mapped.message)
    _emit(output_stream, {"type": "completed", "result": result})
    return 0


def main() -> int:
    return run_worker(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
