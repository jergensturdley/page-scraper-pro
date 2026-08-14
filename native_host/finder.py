"""Constant Finder folder selection and destination validation."""

import os
import subprocess
from pathlib import Path
from typing import Callable

CHOOSE_FOLDER_SCRIPT = (
    'tell application "Finder" to activate\n'
    'POSIX path of (choose folder with prompt "Choose a Page Scraper Pro download folder")'
)

_DEFAULT_APP_DIR = Path.home() / "Library" / "Application Support" / "Page Scraper Pro"
_OSASCRIPT_COMMAND = ["/usr/bin/osascript", "-e", CHOOSE_FOLDER_SCRIPT]


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


def validate_destination(destination: Path, app_dir: Path) -> Path:
    """Resolve and validate a writable directory outside helper-private storage."""
    if not isinstance(destination, Path) or not isinstance(app_dir, Path):
        raise FolderSelectionError("destination paths must be Path objects")
    if not destination.is_absolute() or not app_dir.is_absolute():
        raise FolderSelectionError("destination must be absolute")
    if ".." in destination.parts or "." in destination.parts:
        raise FolderSelectionError("destination traversal is not allowed")
    if destination.is_symlink():
        raise FolderSelectionError("destination symlinks are not allowed")
    try:
        resolved = destination.resolve(strict=True)
        private_root = app_dir.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise FolderSelectionError("destination could not be resolved") from exc
    if not resolved.is_dir():
        raise FolderSelectionError("destination must be an existing directory")
    if _is_within(resolved, private_root):
        raise FolderSelectionError("helper-private directories cannot be destinations")
    if not os.access(resolved, os.W_OK):
        raise FolderSelectionError("destination is not writable")
    return resolved


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
        if "-128" in result.stderr or "User canceled" in result.stderr:
            raise FolderSelectionCancelled("folder selection was cancelled")
        raise FolderSelectionError("folder chooser failed")

    selected = result.stdout.rstrip("\r\n")
    if not selected:
        raise FolderSelectionError("folder chooser returned no directory")
    return validate_destination(Path(selected), app_dir or _DEFAULT_APP_DIR)
