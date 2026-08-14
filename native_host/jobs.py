"""Subprocess supervision for isolated native-helper jobs."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .constants import ALLOWED_PRESETS, MAX_MESSAGE_BYTES, PROTOCOL_VERSION
from .validation import (
    normalize_http_url,
    validate_event,
    validate_request_id,
    validate_worker_job,
)

_WORKER_COMMAND = [sys.executable, "-m", "native_host.worker"]
_PHASE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DEFAULT_TIMEOUTS = {"analyze": 60.0, "download": 3600.0}
_ANALYZE_RESULT_KEYS = {
    "media_id", "title", "provider", "duration", "thumbnail", "subtitles",
    "is_playlist", "playlist_count", "live_status", "available_presets",
}
_LIVE_STATES = {"not_live", "is_live", "is_upcoming", "post_live", "was_live", "unknown"}
_ERROR_MESSAGES = {
    "analysis_failed": "Media analysis failed",
    "dependency_unavailable": "A required helper dependency is unavailable",
    "download_failed": "Media download failed",
    "drm_protected": "This media is DRM protected",
    "ffmpeg_required": "ffmpeg is required for the selected download preset",
    "invalid_job": "The worker job was not valid",
    "invalid_url": "Only public HTTP(S) URLs are allowed",
    "network_error": "Media analysis could not reach the site",
    "timeout": "The media operation timed out",
    "unsafe_destination": "The download destination was not safe",
    "unsafe_download_output": "The completed download was not safe",
    "unsafe_network_target": "A network address was not public",
    "unsupported_url": "No supported extractor was found",
}
_ANALYZE_ERROR_CODES = {
    "analysis_failed", "dependency_unavailable", "drm_protected", "invalid_url",
    "network_error", "timeout", "unsafe_network_target", "unsupported_url",
}
_DOWNLOAD_ERROR_CODES = {
    "dependency_unavailable", "download_failed", "drm_protected", "ffmpeg_required",
    "invalid_job", "invalid_url", "timeout", "unsafe_destination",
    "unsafe_download_output", "unsafe_network_target",
}


def _error(code: str, message: str) -> dict[str, object]:
    return {"accepted": False, "error": {"code": code, "message": message}}


def _json_loads(line: str) -> object:
    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON value")

    return json.loads(line, parse_constant=reject_constant)


class JobManager:
    """Run one isolated worker process at a time and relay validated events."""

    def __init__(
        self,
        *,
        config: object,
        event_callback: Callable[[dict[str, object]], None],
        popen: Callable[..., object] = subprocess.Popen,
        clock: Callable[[], float] = time.time,
        temp_root: Path | None = None,
        timeouts: dict[str, int | float] | None = None,
    ) -> None:
        self._config = config
        self._event_callback = event_callback
        self._popen = popen
        self._clock = clock
        self._temp_root = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
        self._timeouts = dict(_DEFAULT_TIMEOUTS)
        if timeouts is not None:
            for operation in self._timeouts:
                value = timeouts.get(operation)
                if type(value) in (int, float) and value > 0:
                    self._timeouts[operation] = float(value)
        self._lock = threading.Lock()
        self._active: dict[str, object] | None = None
        self._closed = False
        try:
            loaded = self._config.load()
            recent = loaded.get("recent_job") if isinstance(loaded, dict) else None
            self._recent = dict(recent) if isinstance(recent, dict) else None
        except Exception:
            self._recent = None

    def start_analyze(self, request_id: str, url: str) -> dict[str, object]:
        try:
            request_id = validate_request_id(request_id)
            worker_job = validate_worker_job({"operation": "analyze", "url": url})
        except (TypeError, ValueError):
            return _error("invalid_job", "The analysis job was invalid")
        return self._start(request_id, "analyze", worker_job, None)

    def start_download(
        self, request_id: str, job: dict[str, object]
    ) -> dict[str, object]:
        try:
            request_id = validate_request_id(request_id)
            if not isinstance(job, dict) or set(job) != {
                "url", "media_id", "preset", "destination"
            }:
                raise ValueError("download fields do not match schema")
        except (TypeError, ValueError):
            return _error("invalid_job", "The download job was invalid")

        temp_dir: Path | None = None
        try:
            self._temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp_dir = Path(tempfile.mkdtemp(prefix="job_", dir=self._temp_root))
            os.chmod(temp_dir, 0o700)
            worker_job = validate_worker_job({
                "operation": "download",
                "url": job["url"],
                "media_id": job["media_id"],
                "preset": job["preset"],
                "temp_dir": str(temp_dir),
                "destination": str(job["destination"]),
            })
        except (OSError, TypeError, ValueError):
            if temp_dir is not None:
                self._cleanup_temp(temp_dir)
            return _error("invalid_job", "The download job was invalid")
        return self._start(request_id, "download", worker_job, temp_dir)

    def _start(
        self,
        request_id: str,
        operation: str,
        worker_job: dict[str, object],
        temp_dir: Path | None,
    ) -> dict[str, object]:
        job_id = "job_" + secrets.token_hex(16)
        started_at = self._clock()
        with self._lock:
            if self._closed:
                if temp_dir is not None:
                    self._cleanup_temp(temp_dir)
                return _error("helper_unavailable", "The helper is closing")
            if self._active is not None:
                if temp_dir is not None:
                    self._cleanup_temp(temp_dir)
                return _error("busy", "Another helper job is already active")
            # Reserve the single slot before spawning so concurrent starts cannot race.
            active: dict[str, object] = {
                "job_id": job_id,
                "request_id": request_id,
                "operation": operation,
                "state": "starting",
                "started_at": started_at,
                "temp_dir": temp_dir,
                "destination": worker_job.get("destination"),
                "process": None,
                "timer": None,
                "terminal": False,
                "finalized": threading.Event(),
                "phase": "starting",
            }
            self._active = active

        try:
            process = self._popen(
                _WORKER_COMMAND,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                start_new_session=True,
            )
            if process.stdin is None or process.stdout is None:
                raise OSError("worker pipes unavailable")
            encoded = json.dumps(
                worker_job, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
            process.stdin.write(encoded + "\n")
            process.stdin.flush()
            process.stdin.close()
        except Exception:
            try:
                if "process" in locals():
                    self._terminate_process(process)
            except Exception:
                pass
            self._finish_start_failure(job_id, temp_dir)
            return _error("helper_unavailable", "The worker could not be started")

        timer = threading.Timer(
            self._timeouts[operation], self._timeout_job, args=(job_id,)
        )
        timer.daemon = True
        with self._lock:
            if self._active is not active or active["terminal"]:
                should_stop = True
            else:
                active["process"] = process
                active["timer"] = timer
                active["state"] = "running"
                active["phase"] = "analyzing" if operation == "analyze" else "downloading"
                should_stop = False
        if should_stop:
            self._terminate_process(process)
            self._cleanup_temp(temp_dir)
            return _error("helper_unavailable", "The helper job was interrupted")

        reader = threading.Thread(
            target=self._read_worker, args=(job_id,), daemon=True,
            name=f"native-worker-{job_id}",
        )
        active["reader"] = reader
        timer.start()
        reader.start()
        return {"accepted": True, "job_id": job_id}

    def _finish_start_failure(self, job_id: str, temp_dir: Path | None) -> None:
        with self._lock:
            if self._active is not None and self._active.get("job_id") == job_id:
                self._active = None
        self._cleanup_temp(temp_dir)

    def _read_worker(self, job_id: str) -> None:
        with self._lock:
            active = self._active
            if active is None or active.get("job_id") != job_id:
                return
            process = active.get("process")
        if process is None or process.stdout is None:
            self._fail_if_active(
                job_id, "worker_failed", "The worker stopped unexpectedly"
            )
            return

        finished_here = False
        try:
            while True:
                line = process.stdout.readline(MAX_MESSAGE_BYTES + 2)
                if line == "":
                    break
                if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES or not line.endswith("\n"):
                    raise ValueError("worker event exceeds framing limit")
                internal = self._validate_internal_event(job_id, _json_loads(line))
                if internal["type"] in {"completed", "failed", "cancelled"}:
                    if self._claim_terminal(job_id):
                        finished_here = True
                        self._settle_and_finalize(job_id, internal, process)
                    break
                self._relay_progress(job_id, internal)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            if self._claim_terminal(job_id):
                finished_here = True
                self._settle_and_finalize(
                    job_id,
                    self._failed_event(
                        job_id,
                        "invalid_worker_output",
                        "The worker returned invalid output",
                    ),
                    process,
                )
        except Exception:
            if self._claim_terminal(job_id):
                finished_here = True
                self._settle_and_finalize(
                    job_id,
                    self._failed_event(
                        job_id, "worker_failed", "The worker stopped unexpectedly"
                    ),
                    process,
                )
        finally:
            if not finished_here and self._claim_terminal(job_id):
                self._settle_and_finalize(
                    job_id,
                    self._failed_event(
                        job_id, "worker_failed", "The worker stopped unexpectedly"
                    ),
                    process,
                )
            try:
                process.stdout.close()
            except Exception:
                pass

    def _settle_and_finalize(
        self, job_id: str, event: dict[str, object], process: object
    ) -> None:
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass
        forced_termination = False
        try:
            returncode = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            forced_termination = True
            self._terminate_process(process)
            returncode = getattr(process, "returncode", None)
        except Exception:
            forced_termination = True
            self._terminate_process(process)
            returncode = getattr(process, "returncode", None)
        if event.get("type") == "completed" and (
            forced_termination or returncode != 0
        ):
            event = self._failed_event(
                job_id, "worker_failed", "The worker stopped unexpectedly"
            )
        self._finalize(job_id, event)

    @staticmethod
    def _bounded_worker_text(value: object, limit: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > limit
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("invalid worker text")
        return value

    def _validate_analysis_result(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or set(value) != _ANALYZE_RESULT_KEYS:
            raise ValueError("invalid analysis completion")
        media_id = value["media_id"]
        if not isinstance(media_id, str) or re.fullmatch(r"[0-9a-f]{24}", media_id) is None:
            raise ValueError("invalid analyzed media identifier")
        title = self._bounded_worker_text(value["title"], 300)
        provider = self._bounded_worker_text(value["provider"], 80)
        duration = value["duration"]
        if duration is not None and (
            type(duration) is not int or not 0 <= duration <= 3_155_760_000
        ):
            raise ValueError("invalid analyzed duration")
        thumbnail = value["thumbnail"]
        if thumbnail is not None:
            if not isinstance(thumbnail, str) or normalize_http_url(thumbnail) != thumbnail:
                raise ValueError("invalid analyzed thumbnail")
        subtitles = value["subtitles"]
        if (
            not isinstance(subtitles, list)
            or len(subtitles) > 100
            or any(
                not isinstance(language, str)
                or not language
                or len(language) > 32
                or any(ord(character) < 32 or ord(character) == 127 for character in language)
                for language in subtitles
            )
            or subtitles != sorted(set(subtitles))
        ):
            raise ValueError("invalid analyzed subtitles")
        is_playlist = value["is_playlist"]
        playlist_count = value["playlist_count"]
        if type(is_playlist) is not bool or type(playlist_count) is not int:
            raise ValueError("invalid analyzed playlist data")
        if playlist_count < 0 or playlist_count > (1 << 53) - 1:
            raise ValueError("invalid analyzed playlist count")
        if not is_playlist and playlist_count != 0:
            raise ValueError("invalid analyzed playlist count")
        live_status = value["live_status"]
        if not isinstance(live_status, str) or live_status not in _LIVE_STATES:
            raise ValueError("invalid analyzed live status")
        presets = value["available_presets"]
        preset_order = ("best", "1080p", "720p", "audio")
        if (
            not isinstance(presets, list)
            or len(presets) > len(preset_order)
            or any(not isinstance(preset, str) or preset not in ALLOWED_PRESETS for preset in presets)
            or len(set(presets)) != len(presets)
            or presets != [preset for preset in preset_order if preset in presets]
            or (is_playlist and presets)
        ):
            raise ValueError("invalid analyzed presets")
        return {
            "media_id": media_id,
            "title": title,
            "provider": provider,
            "duration": duration,
            "thumbnail": thumbnail,
            "subtitles": subtitles,
            "is_playlist": is_playlist,
            "playlist_count": playlist_count,
            "live_status": live_status,
            "available_presets": presets,
        }

    def _validate_download_result(
        self, value: object, expected_destination: object
    ) -> dict[str, object]:
        if not isinstance(value, dict) or set(value) != {"filename", "directory"}:
            raise ValueError("invalid download completion")
        filename = value["filename"]
        directory = value["directory"]
        if (
            not isinstance(filename, str)
            or not filename
            or len(filename.encode("utf-8")) > 180
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or any(ord(character) < 32 or ord(character) == 127 for character in filename)
        ):
            raise ValueError("invalid completed filename")
        if not isinstance(directory, str) or directory != expected_destination:
            raise ValueError("invalid completed directory")
        return {"filename": filename, "directory": directory}

    @staticmethod
    def _validate_worker_failure(
        value: object, operation: str
    ) -> dict[str, str]:
        if not isinstance(value, dict) or set(value) != {"code", "message"}:
            raise ValueError("invalid worker failure")
        code = value["code"]
        message = value["message"]
        allowed = _ANALYZE_ERROR_CODES if operation == "analyze" else _DOWNLOAD_ERROR_CODES
        if (
            not isinstance(code, str)
            or code not in allowed
            or not isinstance(message, str)
            or not message
            or len(message) > 1000
        ):
            raise ValueError("invalid worker failure")
        return {"code": code, "message": _ERROR_MESSAGES[code]}

    def _validate_internal_event(
        self, job_id: str, value: object
    ) -> dict[str, object]:
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise ValueError("invalid worker event")
        with self._lock:
            active = self._active
            if active is None or active.get("job_id") != job_id:
                raise ValueError("stale worker event")
            request_id = str(active["request_id"])
            operation = str(active["operation"])
            expected_destination = active.get("destination")
        kind = value["type"]
        if operation == "analyze" and kind == "phase":
            if set(value) != {"type", "phase"} or value["phase"] != "analyzing":
                raise ValueError("invalid analyze phase")
            envelope = {
                "version": PROTOCOL_VERSION,
                "id": request_id,
                "type": "progress",
                "payload": {"job_id": job_id, "phase": "analyzing"},
            }
        elif operation == "download" and kind == "progress":
            allowed = {"type", "phase", "percent", "speed", "eta"}
            if (
                not {"type", "phase"} <= set(value)
                or not set(value) <= allowed
                or value["phase"] not in {"downloading", "processing", "finalizing"}
            ):
                raise ValueError("invalid download progress")
            payload: dict[str, object] = {
                "job_id": job_id,
                "phase": value["phase"],
            }
            for field in ("percent", "speed", "eta"):
                if field in value:
                    payload[field] = value[field]
            envelope = {
                "version": PROTOCOL_VERSION,
                "id": request_id,
                "type": "progress",
                "payload": payload,
            }
        elif kind == "completed":
            if set(value) != {"type", "result"}:
                raise ValueError("invalid completion")
            if operation == "analyze":
                result = self._validate_analysis_result(value["result"])
            else:
                result = self._validate_download_result(
                    value["result"], expected_destination
                )
            envelope = {
                "version": PROTOCOL_VERSION,
                "id": request_id,
                "type": "completed",
                "payload": {"job_id": job_id, "result": result},
            }
        elif kind == "failed":
            if set(value) != {"type", "error"}:
                raise ValueError("invalid failure")
            error = self._validate_worker_failure(value["error"], operation)
            envelope = {
                "version": PROTOCOL_VERSION,
                "id": request_id,
                "type": "failed",
                "payload": {"job_id": job_id, "error": error},
            }
        elif kind == "cancelled":
            if set(value) != {"type"}:
                raise ValueError("invalid cancellation")
            envelope = {
                "version": PROTOCOL_VERSION,
                "id": request_id,
                "type": "cancelled",
                "payload": {"job_id": job_id},
            }
        else:
            raise ValueError("unexpected worker event")
        return validate_event(envelope)

    def _relay_progress(self, job_id: str, event: dict[str, object]) -> None:
        with self._lock:
            active = self._active
            if (
                active is None or active.get("job_id") != job_id
                or active.get("terminal")
            ):
                return
            payload = event["payload"]
            if isinstance(payload, dict):
                active["phase"] = payload.get("phase", active.get("phase"))
                if "percent" in payload:
                    active["percent"] = payload["percent"]
        self._emit(event)

    def _claim_terminal(self, job_id: str) -> bool:
        with self._lock:
            active = self._active
            if (
                active is None or active.get("job_id") != job_id
                or active.get("terminal")
            ):
                return False
            active["terminal"] = True
            timer = active.get("timer")
        if timer is not None:
            timer.cancel()
        return True

    def _failed_event(
        self, job_id: str, code: str, message: str
    ) -> dict[str, object]:
        with self._lock:
            active = self._active
            if active is None or active.get("job_id") != job_id:
                raise ValueError("stale worker failure")
            request_id = str(active["request_id"])
        return validate_event({
            "version": PROTOCOL_VERSION,
            "id": request_id,
            "type": "failed",
            "payload": {
                "job_id": job_id,
                "error": {"code": code, "message": message},
            },
        })

    def _fail_if_active(self, job_id: str, code: str, message: str) -> bool:
        if not self._claim_terminal(job_id):
            return False
        self._finalize(job_id, self._failed_event(job_id, code, message))
        return True

    def _timeout_job(self, job_id: str) -> None:
        if not self._claim_terminal(job_id):
            return
        with self._lock:
            active = self._active
            process = active.get("process") if active is not None else None
        if process is not None:
            self._terminate_process(process)
        self._finalize(job_id, self._failed_event(
            job_id, "timeout", "The helper job timed out"
        ))

    def cancel(self, job_id: str) -> dict[str, object]:
        try:
            job_id = validate_request_id(job_id)
        except ValueError:
            return {"cancelled": False, "error": {
                "code": "unknown_job", "message": "No matching active job was found"
            }}
        if not self._claim_terminal(job_id):
            return {"cancelled": False, "error": {
                "code": "unknown_job", "message": "No matching active job was found"
            }}
        with self._lock:
            active = self._active
            process = active.get("process") if active is not None else None
            request_id = str(active["request_id"]) if active is not None else job_id
        if process is not None:
            self._terminate_process(process)
        event = validate_event({
            "version": PROTOCOL_VERSION, "id": request_id,
            "type": "cancelled", "payload": {"job_id": job_id},
        })
        self._finalize(job_id, event)
        return {"cancelled": True, "job_id": job_id}

    def _terminate_process(self, process: object) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass

    def _finalize(self, job_id: str, event: dict[str, object]) -> None:
        now = self._clock()
        with self._lock:
            active = self._active
            if active is None or active.get("job_id") != job_id:
                return
            timer = active.get("timer")
            temp_dir = active.get("temp_dir")
            finalized = active.get("finalized")
            operation = str(active["operation"])
            request_id = str(active["request_id"])
            started_at = active["started_at"]
            event_type = str(event["type"])
            active["state"] = event_type
            snapshot: dict[str, object] = {
                "job_id": job_id,
                "request_id": request_id,
                "operation": operation,
                "state": event_type,
                "started_at": started_at,
                "finished_at": now,
            }
            if event_type == "failed":
                payload = event.get("payload")
                if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                    snapshot["error_code"] = payload["error"].get("code")
            self._recent = dict(snapshot)
        if timer is not None:
            timer.cancel()
        try:
            self._cleanup_temp(temp_dir if isinstance(temp_dir, Path) else None)
            try:
                self._config.save_recent_job(snapshot)
            except Exception:
                pass
            self._emit(event)
        finally:
            with self._lock:
                if self._active is active:
                    self._active = None
            if isinstance(finalized, threading.Event):
                finalized.set()

    def _emit(self, event: dict[str, object]) -> None:
        try:
            self._event_callback(event)
        except Exception:
            pass

    @staticmethod
    def _cleanup_temp(temp_dir: Path | None) -> None:
        if temp_dir is None:
            return
        try:
            shutil.rmtree(temp_dir)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            active = self._active
            current = None
            if active is not None:
                current = {
                    "job_id": active["job_id"],
                    "request_id": active["request_id"],
                    "operation": active["operation"],
                    "state": active["state"],
                    "phase": active.get("phase"),
                    "started_at": active["started_at"],
                }
                if "percent" in active:
                    current["percent"] = active["percent"]
            recent = dict(self._recent) if isinstance(self._recent, dict) else None
        return {"current": current, "recent": recent}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            active = self._active
            job_id = str(active["job_id"]) if active is not None else None
            terminal = bool(active.get("terminal")) if active is not None else False
            finalized = active.get("finalized") if active is not None else None
        if job_id is not None and not terminal:
            self.cancel(job_id)
        if isinstance(finalized, threading.Event):
            finalized.wait()
