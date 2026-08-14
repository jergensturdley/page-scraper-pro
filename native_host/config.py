"""Private atomic configuration storage for the native helper."""

import json
import math
import os
import stat
import tempfile
from pathlib import Path

_CONFIG_NAME = "config.json"
_DEFAULT_STATE: dict[str, object] = {"destination": None, "recent_job": None}
_RECENT_JOB_FIELDS = frozenset(
    {
        "job_id",
        "request_id",
        "operation",
        "kind",
        "state",
        "status",
        "phase",
        "percent",
        "error_code",
        "extractor",
        "started_at",
        "finished_at",
    }
)
_MAX_RECENT_FIELDS = 12
_MAX_RECENT_STRING_LENGTH = 256
_MAX_SAFE_INTEGER = (1 << 53) - 1


def _normalize_recent_job(snapshot: object) -> dict[str, object] | None:
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict):
        raise TypeError("recent job must be an object or None")

    normalized: dict[str, object] = {}
    for key in sorted(snapshot):
        if len(normalized) >= _MAX_RECENT_FIELDS:
            break
        if not isinstance(key, str) or key not in _RECENT_JOB_FIELDS:
            continue
        value = snapshot[key]
        if value is None or isinstance(value, bool):
            normalized[key] = value
        elif type(value) is int and abs(value) <= _MAX_SAFE_INTEGER:
            normalized[key] = value
        elif (
            type(value) is float
            and math.isfinite(value)
            and abs(value) <= _MAX_SAFE_INTEGER
        ):
            normalized[key] = value
        elif isinstance(value, str):
            normalized[key] = value[:_MAX_RECENT_STRING_LENGTH]
    return normalized


class HelperConfig:
    """Read and atomically update helper-owned private state."""

    def __init__(self, app_dir: Path):
        if not isinstance(app_dir, Path):
            raise TypeError("app_dir must be a Path")
        if not app_dir.is_absolute():
            raise ValueError("app_dir must be absolute")
        self.app_dir = app_dir
        self.path = app_dir / _CONFIG_NAME

    def _reject_symlinks(self) -> None:
        if self.app_dir.is_symlink():
            raise ValueError("helper app directory must not be a symlink")
        if self.path.is_symlink():
            raise ValueError("config file must not be a symlink")

    def _ensure_private_directory(self) -> None:
        self._reject_symlinks()
        self.app_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._reject_symlinks()
        if not self.app_dir.is_dir():
            raise ValueError("helper app path must be a directory")
        os.chmod(self.app_dir, 0o700)

    def load(self) -> dict[str, object]:
        """Load validated private state without following a config symlink."""
        self._reject_symlinks()
        if not self.path.exists():
            return dict(_DEFAULT_STATE)

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("config path must be a regular file")
                value = json.load(stream)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("helper config is unreadable") from exc

        if not isinstance(value, dict) or set(value) != {"destination", "recent_job"}:
            raise ValueError("helper config fields do not match schema")
        destination = value["destination"]
        if destination is not None:
            if not isinstance(destination, str) or not Path(destination).is_absolute():
                raise ValueError("stored destination must be an absolute path or None")
        try:
            recent_job = _normalize_recent_job(value["recent_job"])
        except TypeError as exc:
            raise ValueError("stored recent job must be an object or None") from exc
        return {"destination": destination, "recent_job": recent_job}

    def _write(self, value: dict[str, object]) -> None:
        self._ensure_private_directory()
        descriptor = -1
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".config-", suffix=".tmp", dir=self.app_dir
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(value, stream, sort_keys=True, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def save_destination(self, destination: Path | None) -> None:
        """Persist an absolute host-owned destination, or clear it."""
        if destination is not None:
            if not isinstance(destination, Path):
                raise TypeError("destination must be a Path or None")
            if not destination.is_absolute():
                raise ValueError("destination must be absolute")
        state = self.load()
        state["destination"] = None if destination is None else str(destination)
        self._write(state)

    def save_recent_job(self, snapshot: dict[str, object] | None) -> None:
        """Persist at most one bounded snapshot containing safe scalar fields."""
        if snapshot is not None and not isinstance(snapshot, dict):
            raise TypeError("recent job must be an object or None")
        state = self.load()
        state["recent_job"] = _normalize_recent_job(snapshot)
        self._write(state)

    def get_destination(self) -> Path | None:
        """Return the stored destination as a path, when configured."""
        destination = self.load()["destination"]
        return None if destination is None else Path(destination)
