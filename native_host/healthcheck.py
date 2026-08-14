"""Bounded framed end-to-end health probe for the installed native host."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import queue
import signal
import stat
import subprocess
import sys
import threading
from typing import Callable

from .constants import HELPER_VERSION, PROTOCOL_VERSION
from .protocol import ProtocolError, read_message, write_message
from .validation import validate_event

EXPECTED_YT_DLP_VERSION = "2026.7.4"
HEALTH_TIMEOUT_SECONDS = 5.0


class HealthCheckError(RuntimeError):
    """Raised when the installed helper does not pass strict framed health."""


def _validate_launcher(launcher: Path) -> Path:
    if not isinstance(launcher, Path):
        launcher = Path(launcher)
    if not launcher.is_absolute():
        raise HealthCheckError("launcher path must be absolute")
    try:
        metadata = os.lstat(launcher)
    except OSError as exc:
        raise HealthCheckError("launcher is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise HealthCheckError("launcher must be a real regular file")
    if metadata.st_uid != os.getuid():
        raise HealthCheckError("launcher is not owned by the current user")
    if stat.S_IMODE(metadata.st_mode) != 0o700 or not os.access(launcher, os.X_OK):
        raise HealthCheckError("launcher must be private and executable")
    return launcher


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _terminate_process_group(process: object) -> None:
    stdin = getattr(process, "stdin", None)
    if stdin is not None:
        try:
            stdin.close()
        except (OSError, ValueError):
            pass
    group_id = getattr(process, "pid", None)
    if type(group_id) is not int or group_id <= 0:
        return
    try:
        os.killpg(group_id, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=0.5)
    except Exception:
        pass
    # The session leader may already have exited while a descendant inherited
    # its process group. Do not treat a reaped leader as proof the group ended.
    if _process_group_exists(group_id):
        try:
            os.killpg(group_id, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.wait(timeout=0.5)
    except Exception:
        pass


def _read_in_thread(stream: object, output: queue.Queue) -> None:
    try:
        output.put((True, read_message(stream)))
    except BaseException as exc:  # transport failures are returned to the caller
        output.put((False, exc))


def _validate_health_response(message: object) -> dict[str, object]:
    try:
        event = validate_event(message)
    except (TypeError, ValueError) as exc:
        raise HealthCheckError("health response schema is invalid") from exc
    expected_envelope = {
        "version": PROTOCOL_VERSION,
        "id": "healthcheck",
        "type": "response",
    }
    if any(event.get(key) != value for key, value in expected_envelope.items()):
        raise HealthCheckError("health response correlation is invalid")
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise HealthCheckError("helper reported unhealthy")
    data = payload.get("data")
    expected_keys = {
        "helper_version", "protocol_version", "yt_dlp_version", "ffmpeg_version"
    }
    if not isinstance(data, dict) or set(data) != expected_keys:
        raise HealthCheckError("health response fields are invalid")
    if data["helper_version"] != HELPER_VERSION:
        raise HealthCheckError("helper version mismatch")
    if type(data["protocol_version"]) is not int or data["protocol_version"] != PROTOCOL_VERSION:
        raise HealthCheckError("protocol version mismatch")
    if data["yt_dlp_version"] != EXPECTED_YT_DLP_VERSION:
        raise HealthCheckError("yt-dlp version mismatch")
    ffmpeg = data["ffmpeg_version"]
    if not isinstance(ffmpeg, str) or not ffmpeg or len(ffmpeg) > 128:
        raise HealthCheckError("ffmpeg version is invalid")
    return dict(data)


def check_health(
    launcher: Path,
    *,
    timeout: float = HEALTH_TIMEOUT_SECONDS,
    popen: Callable[..., object] = subprocess.Popen,
) -> dict[str, object]:
    """Send exactly one framed health command and validate strict versions."""
    launcher = _validate_launcher(launcher)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
        or timeout > HEALTH_TIMEOUT_SECONDS
    ):
        raise HealthCheckError("health timeout is invalid")
    try:
        process = popen(
            [str(launcher)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HealthCheckError("helper could not be launched") from exc
    reader: threading.Thread | None = None
    try:
        if process.stdin is None or process.stdout is None:
            raise HealthCheckError("helper pipes are unavailable")
        try:
            write_message(process.stdin, {
                "version": PROTOCOL_VERSION,
                "id": "healthcheck",
                "type": "health",
                "payload": {},
            })
        except (OSError, ProtocolError) as exc:
            raise HealthCheckError("health command could not be written") from exc
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        reader = threading.Thread(
            target=_read_in_thread,
            args=(process.stdout, result_queue),
            name="native-health-reader",
            daemon=True,
        )
        reader.start()
        try:
            succeeded, value = result_queue.get(timeout=float(timeout))
        except queue.Empty as exc:
            raise HealthCheckError("health check timed out") from exc
        if not succeeded:
            raise HealthCheckError("health response could not be read") from value
        return _validate_health_response(value)
    finally:
        _terminate_process_group(process)
        stdout_stream = getattr(process, "stdout", None)
        if stdout_stream is not None:
            try:
                stdout_stream.close()
            except OSError:
                pass
        if reader is not None:
            reader.join(timeout=0.5)


def _default_home() -> Path:
    # Allow tests to isolate healthcheck from the real user home.
    test_home = os.environ.get("PAGE_SCRAPER_TEST_HOME")
    if test_home:
        return Path(test_home)
    return Path.home()


def default_launcher_path() -> Path:
    return (
        _default_home()
        / "Library"
        / "Application Support"
        / "Page Scraper Pro"
        / "native"
        / "page-scraper-pro-native-host"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Page Scraper Pro native helper")
    parser.add_argument("--launcher", type=Path, default=default_launcher_path())
    arguments = parser.parse_args(argv)
    try:
        check_health(arguments.launcher)
    except HealthCheckError as exc:
        print(f"health check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
