"""Constant Finder folder selection and destination validation."""

import os
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Callable

CHOOSE_FOLDER_SCRIPT = (
    'tell application "Finder" to activate\n'
    'POSIX path of (choose folder with prompt "Choose a Page Scraper Pro download folder")'
)

_DEFAULT_APP_DIR = Path.home() / "Library" / "Application Support" / "Page Scraper Pro"
_OSASCRIPT_COMMAND = ["/usr/bin/osascript", "-e", CHOOSE_FOLDER_SCRIPT]
_DIRECTORY_FLAGS = (
    getattr(os, "O_SEARCH", os.O_RDONLY)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_PROBE_PREFIX = ".pagescraper-pro-probe-"


class FolderSelectionError(Exception):
    """Raised when a folder cannot be selected or safely used."""


class FolderSelectionCancelled(FolderSelectionError):
    """Raised when the user cancels the standard folder chooser."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _open_verified_directory(path: Path) -> tuple[int, os.stat_result]:
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise FolderSelectionError("destination must be a real directory")
        descriptor = os.open(path, _DIRECTORY_FLAGS)
    except FolderSelectionError:
        raise
    except OSError as exc:
        raise FolderSelectionError("destination could not be opened") from exc
    try:
        opened = os.fstat(descriptor)
        after = os.lstat(path)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_file(before, opened)
            or not _same_file(opened, after)
        ):
            raise FolderSelectionError("destination changed during validation")
        return descriptor, opened
    except Exception:
        os.close(descriptor)
        raise


def _probe_directory(directory_fd: int) -> None:
    descriptor = -1
    probe_name: str | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW
    try:
        for _ in range(16):
            candidate = _PROBE_PREFIX + secrets.token_hex(16)
            try:
                descriptor = os.open(
                    candidate, flags, 0o600, dir_fd=directory_fd
                )
                probe_name = candidate
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise FolderSelectionError("destination probe could not be created")
    except FolderSelectionError:
        raise
    except OSError as exc:
        raise FolderSelectionError(
            "destination is not searchable and writable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if probe_name is not None:
            try:
                os.unlink(probe_name, dir_fd=directory_fd)
            except OSError as exc:
                raise FolderSelectionError(
                    "destination probe could not be removed"
                ) from exc


def validate_destination(destination: Path, app_dir: Path) -> Path:
    """Validate a destination snapshot through a no-follow directory handle.

    The returned path is only a validation snapshot. Task 4 must call this
    function again immediately before every filesystem use of the destination.
    """
    if not isinstance(destination, Path) or not isinstance(app_dir, Path):
        raise FolderSelectionError("destination paths must be Path objects")
    if not destination.is_absolute() or not app_dir.is_absolute():
        raise FolderSelectionError("destination must be absolute")
    if ".." in destination.parts or "." in destination.parts:
        raise FolderSelectionError("destination traversal is not allowed")
    try:
        supplied = os.lstat(destination)
        if stat.S_ISLNK(supplied.st_mode):
            raise FolderSelectionError("destination symlinks are not allowed")
        canonical = destination.resolve(strict=True)
        private_root = app_dir.resolve(strict=False)
    except FolderSelectionError:
        raise
    except (OSError, RuntimeError) as exc:
        raise FolderSelectionError("destination could not be resolved") from exc

    directory_fd, opened = _open_verified_directory(canonical)
    try:
        mode = stat.S_IMODE(opened.st_mode)
        if mode & 0o111 == 0 or mode & 0o222 == 0:
            raise FolderSelectionError(
                "destination is not searchable and writable"
            )

        private_fd = -1
        try:
            if private_root.exists():
                private_fd, private_opened = _open_verified_directory(private_root)
                if _same_file(opened, private_opened):
                    raise FolderSelectionError(
                        "helper-private directories cannot be destinations"
                    )
            if _is_within(canonical, private_root):
                raise FolderSelectionError(
                    "helper-private directories cannot be destinations"
                )
        finally:
            if private_fd >= 0:
                os.close(private_fd)

        _probe_directory(directory_fd)
        after = os.lstat(canonical)
        if not _same_file(opened, after):
            raise FolderSelectionError("destination changed during validation")
        return canonical
    finally:
        os.close(directory_fd)


def choose_directory(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    app_dir: Path | None = None,
) -> Path:
    """Open the constant Finder chooser and return a validated directory."""
    try:
        result = run(
            _OSASCRIPT_COMMAND,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise FolderSelectionError("folder chooser failed") from exc

    if result.returncode != 0:
        stderr = result.stderr or ""
        if "-128" in stderr or "User canceled" in stderr:
            raise FolderSelectionCancelled("folder selection was cancelled")
        raise FolderSelectionError("folder chooser failed")

    selected = (result.stdout or "").rstrip("\r\n")
    if not selected:
        raise FolderSelectionError("folder chooser returned no directory")
    return validate_destination(Path(selected), app_dir or _DEFAULT_APP_DIR)
