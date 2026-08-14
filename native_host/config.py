"""Private atomic configuration storage for the native helper."""

import fcntl
import json
import math
import os
import secrets
import stat
from pathlib import Path
from typing import Callable

_CONFIG_NAME = "config.json"
_LOCK_NAME = ".config.lock"
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
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


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


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


class HelperConfig:
    """Read and atomically update helper-owned private state."""

    def __init__(self, app_dir: Path, trusted_root: Path):
        if not isinstance(app_dir, Path) or not isinstance(trusted_root, Path):
            raise TypeError("app_dir and trusted_root must be Paths")
        if not app_dir.is_absolute() or not trusted_root.is_absolute():
            raise ValueError("app_dir and trusted_root must be absolute")
        if ".." in app_dir.parts or ".." in trusted_root.parts:
            raise ValueError("configuration paths must not contain traversal")
        try:
            relative = app_dir.relative_to(trusted_root)
        except ValueError as exc:
            raise ValueError("app_dir must be beneath trusted_root") from exc
        self.app_dir = app_dir
        self.trusted_root = trusted_root
        self.path = app_dir / _CONFIG_NAME
        self._relative_parts = relative.parts

    def _open_trusted_root(self) -> int:
        try:
            before = os.lstat(self.trusted_root)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ValueError("trusted root must be a real directory")
            descriptor = os.open(self.trusted_root, _DIRECTORY_FLAGS)
        except OSError as exc:
            raise ValueError("trusted root is unavailable") from exc
        after = os.fstat(descriptor)
        if not stat.S_ISDIR(after.st_mode) or not _same_file(before, after):
            os.close(descriptor)
            raise ValueError("trusted root changed during validation")
        return descriptor

    def _open_app_directory(self, *, create: bool) -> int | None:
        descriptor = self._open_trusted_root()
        try:
            for component in self._relative_parts:
                try:
                    before = os.stat(
                        component, dir_fd=descriptor, follow_symlinks=False
                    )
                except FileNotFoundError:
                    if not create:
                        os.close(descriptor)
                        return None
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    before = os.stat(
                        component, dir_fd=descriptor, follow_symlinks=False
                    )
                if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                    raise ValueError(
                        "helper directory components must be real directories"
                    )
                try:
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except OSError as exc:
                    raise ValueError(
                        "helper directory component could not be opened"
                    ) from exc
                after = os.fstat(child)
                if not stat.S_ISDIR(after.st_mode) or not _same_file(before, after):
                    os.close(child)
                    raise ValueError("helper directory changed during validation")
                os.close(descriptor)
                descriptor = child
            if create:
                os.fchmod(descriptor, 0o700)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _entry_stat(directory_fd: int, name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    @classmethod
    def _config_stat(cls, directory_fd: int) -> os.stat_result | None:
        value = cls._entry_stat(directory_fd, _CONFIG_NAME)
        if value is not None and (
            stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode)
        ):
            raise ValueError("config path must be a regular file, not a symlink")
        return value

    @classmethod
    def _open_lock(cls, directory_fd: int, *, exclusive: bool) -> int:
        descriptor = -1
        before: os.stat_result | None = None
        for _ in range(16):
            before = cls._entry_stat(directory_fd, _LOCK_NAME)
            if before is not None and (
                stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode)
            ):
                raise ValueError("config lock must be a regular file, not a symlink")
            try:
                if before is None:
                    descriptor = os.open(
                        _LOCK_NAME,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW,
                        0o600,
                        dir_fd=directory_fd,
                    )
                else:
                    descriptor = os.open(
                        _LOCK_NAME,
                        os.O_RDWR | _FILE_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                break
            except FileExistsError:
                continue
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ValueError("config lock could not be opened") from exc
        if descriptor < 0:
            raise ValueError("config lock could not be opened")
        try:
            opened = os.fstat(descriptor)
            current = cls._entry_stat(directory_fd, _LOCK_NAME)
            if (
                not stat.S_ISREG(opened.st_mode)
                or current is None
                or not _same_file(opened, current)
                or (before is not None and not _same_file(before, opened))
            ):
                raise ValueError("config lock changed during validation")
            os.fchmod(descriptor, 0o600)
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(descriptor, operation)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _close_lock(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _load_from_fd(self, directory_fd: int) -> dict[str, object]:
        before = self._config_stat(directory_fd)
        if before is None:
            return dict(_DEFAULT_STATE)
        try:
            descriptor = os.open(
                _CONFIG_NAME,
                os.O_RDONLY | _FILE_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise ValueError("helper config is unreadable") from exc
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            after = os.fstat(stream.fileno())
            if not stat.S_ISREG(after.st_mode) or not _same_file(before, after):
                raise ValueError("helper config changed during validation")
            try:
                value = json.load(stream)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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

    def load(self) -> dict[str, object]:
        """Load validated private state under a process-shared read lock."""
        directory_fd = self._open_app_directory(create=False)
        if directory_fd is None:
            return dict(_DEFAULT_STATE)
        lock_fd = -1
        try:
            lock_fd = self._open_lock(directory_fd, exclusive=False)
            return self._load_from_fd(directory_fd)
        finally:
            if lock_fd >= 0:
                self._close_lock(lock_fd)
            os.close(directory_fd)

    @staticmethod
    def _create_temporary_file(directory_fd: int) -> tuple[int, str]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW
        for _ in range(16):
            name = f".config-{secrets.token_hex(16)}.tmp"
            try:
                return os.open(name, flags, 0o600, dir_fd=directory_fd), name
            except FileExistsError:
                continue
        raise ValueError("could not allocate a temporary config file")

    def _write_from_fd(
        self, directory_fd: int, value: dict[str, object]
    ) -> None:
        descriptor = -1
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = self._create_temporary_file(directory_fd)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(value, stream, sort_keys=True, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(
                temporary_name,
                _CONFIG_NAME,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_name = None
            os.fsync(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass

    def _update(self, change: Callable[[dict[str, object]], None]) -> None:
        directory_fd = self._open_app_directory(create=True)
        if directory_fd is None:
            raise ValueError("helper app directory could not be created")
        lock_fd = -1
        try:
            lock_fd = self._open_lock(directory_fd, exclusive=True)
            state = self._load_from_fd(directory_fd)
            change(state)
            self._write_from_fd(directory_fd, state)
        finally:
            if lock_fd >= 0:
                self._close_lock(lock_fd)
            os.close(directory_fd)

    def save_destination(self, destination: Path | None) -> None:
        """Persist an absolute host-owned destination, or clear it."""
        if destination is not None:
            if not isinstance(destination, Path):
                raise TypeError("destination must be a Path or None")
            if not destination.is_absolute():
                raise ValueError("destination must be absolute")

        def change(state: dict[str, object]) -> None:
            state["destination"] = None if destination is None else str(destination)

        self._update(change)

    def save_recent_job(self, snapshot: dict[str, object] | None) -> None:
        """Persist at most one bounded snapshot containing safe scalar fields."""
        if snapshot is not None and not isinstance(snapshot, dict):
            raise TypeError("recent job must be an object or None")
        normalized = _normalize_recent_job(snapshot)

        def change(state: dict[str, object]) -> None:
            state["recent_job"] = normalized

        self._update(change)

    def get_destination(self) -> Path | None:
        """Return the stored destination as a path, when configured."""
        destination = self.load()["destination"]
        return None if destination is None else Path(destination)
