"""Native Messaging host dispatcher and serialized framed I/O loop."""

from __future__ import annotations

from collections import OrderedDict
import importlib.metadata
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Callable

from .config import HelperConfig
from .constants import ALLOWED_PRESETS, HELPER_VERSION, PROTOCOL_VERSION
from .finder import (
    FolderSelectionCancelled,
    FolderSelectionError,
    choose_directory,
    validate_destination,
)
from .protocol import EndOfStream, ProtocolError, read_message, write_message
from .redaction import redact_mapping
from .validation import (
    make_error,
    make_response,
    validate_command,
    validate_event,
    validate_request_id,
)

_MEDIA_TTL_SECONDS = 30 * 60
_MAX_MEDIA_RECORDS = 32
_LOG_MAX_BYTES = 512 * 1024
_LOG_BACKUPS = 2
_LOG_NAME = "host.log"
_DEFAULT_TRUSTED_ROOT = Path.home() / "Library" / "Application Support"
_DEFAULT_APP_DIR = _DEFAULT_TRUSTED_ROOT / "Page Scraper Pro" / "native"
_SNAPSHOT_FIELDS = frozenset({
    "job_id", "request_id", "operation", "kind", "state", "status", "phase",
    "percent", "speed", "eta", "error_code", "extractor", "started_at", "finished_at",
})


def _safe_exception_message(code: str) -> str:
    messages = {
        "invalid_command": "The command was not valid",
        "invalid_frame": "The native message frame was not valid",
        "internal_error": "The helper could not complete the request",
        "folder_unavailable": "A download folder could not be selected",
        "folder_cancelled": "Folder selection was cancelled",
        "unsafe_destination": "The download destination is unavailable",
        "confirmation_required": "Download confirmation is required",
        "unknown_media": "Analyze this media again before downloading",
        "stale_media": "The analysis expired; analyze this media again",
        "preset_unavailable": "The selected preset is unavailable",
        "destination_required": "Choose a download folder before downloading",
        "busy": "Another helper request with this ID is already active",
    }
    return messages[code]


def _default_yt_dlp_version() -> str:
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"
    except Exception:
        return "unknown"


def _default_ffmpeg_version() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        return "unavailable"
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    first_line = (result.stdout or "").splitlines()[:1]
    if not first_line:
        return "unknown"
    parts = first_line[0].split()
    return parts[2][:128] if len(parts) >= 3 else "unknown"


def _check_real_directory(path: Path) -> None:
    """Create a private app directory, but never accept a symlink component leaf."""
    try:
        if path.exists() or path.is_symlink():
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("private app directory must be a real directory")
        else:
            path.mkdir(mode=0o700, parents=True)
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("private app directory must be a real directory")
        path.chmod(0o700)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("private app directory is unavailable") from exc


class _NoFollowRotatingFileHandler(RotatingFileHandler):
    """Open every active log with no-follow semantics and private permissions."""

    def _open(self):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.baseFilename, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("host log path must be a regular file")
            os.fchmod(descriptor, 0o600)
            return os.fdopen(
                descriptor,
                mode="a",
                encoding=self.encoding,
                errors=self.errors,
            )
        except Exception:
            if "descriptor" in locals():
                os.close(descriptor)
            raise


def configure_logging(app_dir: Path = _DEFAULT_APP_DIR) -> logging.Logger:
    """Configure one private, bounded rotating log and refuse a symlink target."""
    if not isinstance(app_dir, Path) or not app_dir.is_absolute():
        raise ValueError("log app directory must be an absolute Path")
    _check_real_directory(app_dir)
    log_path = app_dir / _LOG_NAME
    try:
        metadata = os.lstat(log_path)
    except FileNotFoundError:
        metadata = None
    except OSError as exc:
        raise ValueError("host log path is unavailable") from exc
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
    ):
        raise ValueError("host log path must be a regular file, not a symlink")

    logger = logging.getLogger(f"native_host.host.{app_dir}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for previous in logger.handlers:
        previous.close()
    logger.handlers.clear()
    handler = _NoFollowRotatingFileHandler(
        log_path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUPS,
        encoding="utf-8",
        delay=False,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _diagnostic(
    logger: object | None,
    level: str,
    message: str,
    context: dict[str, object] | None = None,
) -> None:
    if logger is None:
        return
    method = getattr(logger, level, None)
    if not callable(method):
        return
    try:
        safe_context = redact_mapping(context or {})
        encoded = json.dumps(
            {"message": message, "context": safe_context},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        method(encoded)
    except Exception:
        # Diagnostics must never break the native protocol.
        return


def _display_path(value: Path | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    try:
        home = Path.home()
        relative = value.relative_to(home)
        text = "~" if not relative.parts else str(Path("~") / relative)
    except (ValueError, RuntimeError):
        pass
    text = "".join(" " if ord(character) < 32 or ord(character) == 127 else character for character in text)
    return text[:512]


def _safe_snapshot(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    safe: dict[str, object] = {}
    for key in sorted(value):
        if key not in _SNAPSHOT_FIELDS or len(safe) >= 16:
            continue
        item = value[key]
        if item is None or isinstance(item, bool):
            safe[key] = item
        elif type(item) is int and abs(item) <= (1 << 53) - 1:
            safe[key] = item
        elif (
            type(item) is float
            and math.isfinite(item)
            and abs(item) <= (1 << 53) - 1
        ):
            safe[key] = item
        elif isinstance(item, str):
            safe[key] = item[:256]
    return safe


class HostDispatcher:
    """Validate and dispatch the six extension-facing host commands."""

    def __init__(
        self,
        config: object,
        jobs: object | None = None,
        *,
        app_dir: Path = _DEFAULT_APP_DIR,
        choose_directory_func: Callable[..., Path] = choose_directory,
        validate_destination_func: Callable[[Path, Path], Path] = validate_destination,
        yt_dlp_version_func: Callable[[], str] = _default_yt_dlp_version,
        ffmpeg_version_func: Callable[[], str] = _default_ffmpeg_version,
        clock: Callable[[], float] = time.time,
        event_writer: Callable[[dict[str, object]], None] | None = None,
        logger: object | None = None,
        job_manager_factory: Callable[..., object] | None = None,
    ):
        if not isinstance(app_dir, Path) or not app_dir.is_absolute():
            raise ValueError("app_dir must be an absolute Path")
        self.config = config
        self.app_dir = app_dir
        self.choose_directory_func = choose_directory_func
        self.validate_destination_func = validate_destination_func
        self.yt_dlp_version_func = yt_dlp_version_func
        self.ffmpeg_version_func = ffmpeg_version_func
        self.clock = clock
        self.event_writer = event_writer or (lambda event: None)
        self.logger = logger
        self._media: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._pending: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        if jobs is None:
            if job_manager_factory is None:
                from .jobs import JobManager
                job_manager_factory = JobManager
            jobs = job_manager_factory(
                config=config,
                event_callback=self.handle_job_event,
            )
        self.jobs = jobs

    def set_event_writer(
        self, writer: Callable[[dict[str, object]], None]
    ) -> None:
        if not callable(writer):
            raise TypeError("event writer must be callable")
        with self._lock:
            self.event_writer = writer

    def _versions(self) -> dict[str, object]:
        yt_dlp = self.yt_dlp_version_func()
        ffmpeg = self.ffmpeg_version_func()
        if not isinstance(yt_dlp, str) or not yt_dlp:
            yt_dlp = "unknown"
        if not isinstance(ffmpeg, str) or not ffmpeg:
            ffmpeg = "unknown"
        return {
            "helper_version": HELPER_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "yt_dlp_version": yt_dlp[:128],
            "ffmpeg_version": ffmpeg[:128],
        }

    @staticmethod
    def _result_event(request_id: str, result: object) -> dict[str, object]:
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            error = result["error"]
            code = error.get("code")
            message = error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                try:
                    return make_error(request_id, code, message)
                except ValueError:
                    pass
            return make_error(
                request_id, "internal_error", _safe_exception_message("internal_error")
            )
        if not isinstance(result, dict):
            return make_error(
                request_id, "internal_error", _safe_exception_message("internal_error")
            )
        return make_response(request_id, result)

    def _load_destination(self) -> Path | None:
        getter = getattr(self.config, "get_destination", None)
        if callable(getter):
            value = getter()
        else:
            state = self.config.load()
            value = state.get("destination") if isinstance(state, dict) else None
            value = None if value is None else Path(value)
        if value is None:
            return None
        if not isinstance(value, Path):
            value = Path(value)
        return value

    def _purge_expired(self, now: float, *, keep: str | None = None) -> None:
        for media_id, record in list(self._media.items()):
            if media_id == keep:
                continue
            stored_at = record.get("stored_at")
            if not isinstance(stored_at, (int, float)) or now - float(stored_at) > _MEDIA_TTL_SECONDS:
                del self._media[media_id]

    def _lookup_media(self, media_id: str) -> tuple[str, dict[str, object] | None]:
        now = float(self.clock())
        with self._lock:
            record = self._media.get(media_id)
            if record is None:
                self._purge_expired(now)
                return "unknown", None
            stored_at = record.get("stored_at")
            if not isinstance(stored_at, (int, float)) or now - float(stored_at) > _MEDIA_TTL_SECONDS:
                del self._media[media_id]
                self._purge_expired(now)
                return "stale", None
            self._purge_expired(now, keep=media_id)
            return "known", dict(record)

    def _cache_analysis(
        self, request_id: str, result: object, pending: dict[str, object]
    ) -> None:
        if not isinstance(result, dict):
            return
        media_id = result.get("media_id")
        presets = result.get("available_presets")
        url = pending.get("url")
        try:
            media_id = validate_request_id(media_id)
        except ValueError:
            return
        if (
            not isinstance(url, str)
            or not isinstance(presets, list)
            or any(not isinstance(item, str) or item not in ALLOWED_PRESETS for item in presets)
        ):
            return
        advertised = tuple(dict.fromkeys(presets))
        with self._lock:
            self._purge_expired(float(self.clock()))
            self._media[media_id] = {
                "url": url,
                "presets": advertised,
                "stored_at": float(self.clock()),
            }
            self._media.move_to_end(media_id)
            while len(self._media) > _MAX_MEDIA_RECORDS:
                self._media.popitem(last=False)

    def handle_job_event(self, event: dict[str, object]) -> None:
        """Intercept validated terminal events before relaying them to Chrome."""
        try:
            safe_event = validate_event(event)
            request_id = safe_event["id"]
            event_type = safe_event["type"]
            event_job_id = safe_event["payload"]["job_id"]
            with self._lock:
                pending = self._pending.get(request_id)
            pending_matches = (
                pending is not None
                and pending.get("job_id") in {None, event_job_id}
            )
            if event_type == "completed" and pending_matches:
                result = safe_event["payload"]["result"]
                if pending.get("operation") == "analyze":
                    self._cache_analysis(request_id, result, pending)
                elif pending.get("operation") == "download" and isinstance(result, dict):
                    result = dict(result)
                    result["directory"] = _display_path(pending.get("destination"))
                    safe_event = validate_event({
                        **safe_event,
                        "payload": {**safe_event["payload"], "result": result},
                    })
            if event_type in {"completed", "failed", "cancelled"} and pending_matches:
                with self._lock:
                    if self._pending.get(request_id) is pending:
                        self._pending.pop(request_id, None)
            self.event_writer(safe_event)
        except Exception as exc:
            _diagnostic(
                self.logger,
                "error",
                "job event rejected",
                {"error": str(exc), "event": event},
            )

    def _dispatch_health(
        self, request_id: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        return make_response(request_id, self._versions())

    def _dispatch_analyze(
        self, request_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        url = payload["url"]
        pending: dict[str, object] = {"operation": "analyze", "url": url}
        with self._lock:
            if request_id in self._pending:
                return make_error(
                    request_id, "busy", _safe_exception_message("busy")
                )
            self._pending[request_id] = pending
        result = self.jobs.start_analyze(request_id, url)
        accepted = isinstance(result, dict) and result.get("accepted") is True
        with self._lock:
            if self._pending.get(request_id) is pending:
                if accepted and isinstance(result.get("job_id"), str):
                    pending["job_id"] = result["job_id"]
                elif not accepted:
                    self._pending.pop(request_id, None)
        return self._result_event(request_id, result)

    def _dispatch_choose_directory(
        self, request_id: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        try:
            selected = self.choose_directory_func(app_dir=self.app_dir)
            if not isinstance(selected, Path):
                selected = Path(selected)
            validated = self.validate_destination_func(selected, self.app_dir)
            self.config.save_destination(validated)
            return make_response(
                request_id, {"destination": _display_path(validated)}
            )
        except FolderSelectionCancelled:
            return make_error(
                request_id, "folder_cancelled", _safe_exception_message("folder_cancelled")
            )
        except Exception as exc:
            _diagnostic(
                self.logger, "warning", "folder selection failed", {"error": str(exc)}
            )
            return make_error(
                request_id, "folder_unavailable", _safe_exception_message("folder_unavailable")
            )

    def _dispatch_download(
        self, request_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        if payload.get("confirmed") is not True:
            return make_error(
                request_id,
                "confirmation_required",
                _safe_exception_message("confirmation_required"),
            )
        media_id = payload["media_id"]
        state, record = self._lookup_media(media_id)
        if state == "unknown":
            return make_error(
                request_id, "unknown_media", _safe_exception_message("unknown_media")
            )
        if state == "stale":
            return make_error(
                request_id, "stale_media", _safe_exception_message("stale_media")
            )
        if record is None:
            return make_error(
                request_id, "unknown_media", _safe_exception_message("unknown_media")
            )
        preset = payload["preset"]
        if preset not in record["presets"]:
            return make_error(
                request_id,
                "preset_unavailable",
                _safe_exception_message("preset_unavailable"),
            )
        destination = self._load_destination()
        if destination is None:
            return make_error(
                request_id,
                "destination_required",
                _safe_exception_message("destination_required"),
            )
        try:
            destination = self.validate_destination_func(destination, self.app_dir)
        except (FolderSelectionError, ValueError, TypeError, OSError) as exc:
            _diagnostic(
                self.logger,
                "warning",
                "configured destination rejected",
                {"error": str(exc), "destination": str(destination)},
            )
            return make_error(
                request_id,
                "unsafe_destination",
                _safe_exception_message("unsafe_destination"),
            )
        trusted_job = {
            "url": record["url"],
            "media_id": media_id,
            "preset": preset,
            "destination": str(destination),
        }
        pending: dict[str, object] = {
            "operation": "download",
            "destination": destination,
        }
        with self._lock:
            if request_id in self._pending:
                return make_error(
                    request_id, "busy", _safe_exception_message("busy")
                )
            self._pending[request_id] = pending
        result = self.jobs.start_download(request_id, trusted_job)
        accepted = isinstance(result, dict) and result.get("accepted") is True
        with self._lock:
            if self._pending.get(request_id) is pending:
                if accepted and isinstance(result.get("job_id"), str):
                    pending["job_id"] = result["job_id"]
                elif not accepted:
                    self._pending.pop(request_id, None)
        return self._result_event(request_id, result)

    def _dispatch_cancel(
        self, request_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return self._result_event(request_id, self.jobs.cancel(payload["job_id"]))

    def _dispatch_status(
        self, request_id: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        destination = self._load_destination()
        snapshot = self.jobs.snapshot()
        if not isinstance(snapshot, dict):
            snapshot = {}
        data = self._versions()
        data.update({
            "destination": _display_path(destination),
            "current_job": _safe_snapshot(snapshot.get("current")),
            "recent_job": _safe_snapshot(snapshot.get("recent")),
        })
        return make_response(request_id, data)

    def dispatch(self, message: object) -> dict[str, object]:
        request_id = "host_error"
        if isinstance(message, dict):
            try:
                request_id = validate_request_id(message.get("id"))
            except ValueError:
                pass
        try:
            command = validate_command(message)
            request_id = command["id"]
            command_type = command["type"]
            payload = command["payload"]
            handlers = {
                "health": self._dispatch_health,
                "analyze": self._dispatch_analyze,
                "choose_directory": self._dispatch_choose_directory,
                "download": self._dispatch_download,
                "cancel": self._dispatch_cancel,
                "status": self._dispatch_status,
            }
            return handlers[command_type](request_id, payload)
        except ValueError as exc:
            _diagnostic(
                self.logger,
                "warning",
                "command rejected",
                {"request_id": request_id, "error": str(exc)},
            )
            return make_error(
                request_id, "invalid_command", _safe_exception_message("invalid_command")
            )
        except Exception as exc:
            _diagnostic(
                self.logger,
                "error",
                "command dispatch failed",
                {"request_id": request_id, "error": str(exc), "command": message},
            )
            return make_error(
                request_id, "internal_error", _safe_exception_message("internal_error")
            )

    def close(self) -> None:
        try:
            self.jobs.close()
        except Exception as exc:
            _diagnostic(
                self.logger, "error", "job manager close failed", {"error": str(exc)}
            )


class _SerializedWriter:
    """Serialize all frames and defer synchronous events until their response."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.lock = threading.RLock()
        self.defer_events = False
        self.queued: list[dict[str, object]] = []

    def event(self, message: dict[str, object]) -> None:
        with self.lock:
            if self.defer_events:
                self.queued.append(message)
            else:
                write_message(self.stream, message)

    def dispatch_and_write(
        self, dispatch: Callable[[], dict[str, object]]
    ) -> None:
        with self.lock:
            self.defer_events = True
            try:
                response = dispatch()
                write_message(self.stream, response)
            finally:
                self.defer_events = False
            queued, self.queued = self.queued, []
            for event in queued:
                write_message(self.stream, event)

    def write_direct(self, message: dict[str, object]) -> None:
        with self.lock:
            write_message(self.stream, message)


def _default_dispatcher(
    event_writer: Callable[[dict[str, object]], None],
    app_dir: Path,
    logger: object,
) -> HostDispatcher:
    trusted_root = app_dir.parent
    config = HelperConfig(app_dir=app_dir, trusted_root=trusted_root)
    return HostDispatcher(
        config=config,
        app_dir=app_dir,
        event_writer=event_writer,
        logger=logger,
    )


def main(
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    *,
    dispatcher: HostDispatcher | None = None,
    app_dir: Path = _DEFAULT_APP_DIR,
    logger: object | None = None,
    dispatcher_factory: Callable[..., HostDispatcher] | None = None,
) -> int:
    """Run the bounded Native Messaging loop until EOF or malformed framing."""
    input_stream = stdin if stdin is not None else sys.stdin.buffer
    output_stream = stdout if stdout is not None else sys.stdout.buffer
    if logger is None:
        try:
            logger = configure_logging(app_dir)
        except Exception:
            # Native Messaging must not write diagnostics to stdout/stderr.
            logger = None
    writer = _SerializedWriter(output_stream)
    if dispatcher is None:
        factory = dispatcher_factory or _default_dispatcher
        dispatcher = factory(
            event_writer=writer.event,
            app_dir=app_dir,
            logger=logger,
        )
    else:
        dispatcher.set_event_writer(writer.event)
    exit_code = 0
    try:
        while True:
            try:
                message = read_message(input_stream)
            except EndOfStream:
                break
            except ProtocolError as exc:
                exit_code = 1
                _diagnostic(
                    logger, "error", "native frame rejected", {"error": str(exc)}
                )
                try:
                    writer.write_direct(make_error(
                        "host_error",
                        "invalid_frame",
                        _safe_exception_message("invalid_frame"),
                    ))
                except Exception as write_exc:
                    _diagnostic(
                        logger,
                        "error",
                        "native frame error response failed",
                        {"error": str(write_exc)},
                    )
                break
            writer.dispatch_and_write(lambda: dispatcher.dispatch(message))
    except Exception as exc:
        exit_code = 1
        _diagnostic(logger, "error", "native host loop failed", {"error": str(exc)})
    finally:
        dispatcher.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
