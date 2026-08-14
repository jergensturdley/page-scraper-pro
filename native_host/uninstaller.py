"""Confined removal of the Page Scraper Pro Native Messaging helper."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import sys

from .config import HelperConfig
from .installer import (
    InstallPaths,
    _INSTALL_LOCK_NAME,
    _LAUNCHER_NAME,
    _MANIFEST_NAME,
    _OWNER_MARKER_NAME,
    _RUNTIME_NAME,
    _RUNTIME_PARENT_NAME,
    _launcher_text,
    _manifest_document,
    _owner_document,
    _owned_regular,
    _validate_root,
    validate_extension_id,
)

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_KNOWN_RUNTIME_ENTRIES = frozenset({
    "venv",
    "package",
    "requirements.lock",
    _LAUNCHER_NAME,
    _OWNER_MARKER_NAME,
    "config.json",
    ".config.lock",
    "host.log",
    "host.log.1",
    "host.log.2",
})


class UninstallError(RuntimeError):
    """Raised rather than deleting an artifact that is not exactly helper-owned."""


def _validate_optional_root(root: Path, label: str) -> Path:
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise UninstallError(f"{label} must be an absolute confined path")
    if not root.exists() and not root.is_symlink():
        return root
    try:
        return _validate_root(root, label)
    except Exception as exc:
        raise UninstallError(str(exc)) from exc


class Uninstaller:
    def __init__(self, application_support_root: Path, manifest_root: Path):
        try:
            self.application_support_root = _validate_root(
                application_support_root, "application support root"
            )
        except Exception as exc:
            raise UninstallError(str(exc)) from exc
        self.manifest_root = _validate_optional_root(manifest_root, "manifest root")
        self.runtime_parent = self.application_support_root / _RUNTIME_PARENT_NAME
        self.runtime_dir = self.runtime_parent / _RUNTIME_NAME
        self.launcher_path = self.runtime_dir / _LAUNCHER_NAME
        self.owner_marker_path = self.runtime_dir / _OWNER_MARKER_NAME
        self.install_lock_path = self.runtime_parent / _INSTALL_LOCK_NAME
        self.manifest_path = self.manifest_root / _MANIFEST_NAME
        self.paths = InstallPaths(self.runtime_dir, self.launcher_path, self.manifest_path)

    @staticmethod
    def _exists(path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def _validate_manifest(self, extension_id: str) -> None:
        if not self._exists(self.manifest_path):
            return
        try:
            _owned_regular(self.manifest_path, "native manifest")
            value = json.loads(self.manifest_path.read_text("utf-8"))
        except Exception as exc:
            raise UninstallError("native manifest is invalid") from exc
        if value != _manifest_document(extension_id, self.launcher_path):
            raise UninstallError("native manifest is not the exact requested helper manifest")

    def _runtime_kind(self, *, allow_partial: bool = False) -> str:
        if not self._exists(self.runtime_dir):
            return "absent"
        try:
            metadata = os.lstat(self.runtime_dir)
        except OSError as exc:
            raise UninstallError("runtime is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise UninstallError("runtime must be a real directory")
        if metadata.st_uid != os.getuid():
            raise UninstallError("runtime is not owned by the current user")
        try:
            entries = {entry.name for entry in self.runtime_dir.iterdir()}
        except OSError as exc:
            raise UninstallError("runtime cannot be inspected") from exc
        marker_exists = self._exists(self.owner_marker_path)
        if not marker_exists:
            if {"config.json"} <= entries <= {"config.json", ".config.lock"}:
                for name in entries:
                    config_metadata = os.lstat(self.runtime_dir / name)
                    if (
                        stat.S_ISLNK(config_metadata.st_mode)
                        or not stat.S_ISREG(config_metadata.st_mode)
                        or config_metadata.st_uid != os.getuid()
                        or stat.S_IMODE(config_metadata.st_mode) != 0o600
                    ):
                        raise UninstallError("retained config files are unsafe")
                return "retained_config"
            if allow_partial and entries <= (
                _KNOWN_RUNTIME_ENTRIES - {_OWNER_MARKER_NAME}
            ):
                return "installed_partial"
            raise UninstallError("runtime has no exact ownership marker")
        try:
            marker_metadata = _owned_regular(
                self.owner_marker_path, "ownership marker"
            )
            if stat.S_IMODE(marker_metadata.st_mode) != 0o600:
                raise UninstallError("ownership marker must be private")
            marker = json.loads(self.owner_marker_path.read_text("utf-8"))
        except Exception as exc:
            raise UninstallError("runtime ownership marker is invalid") from exc
        if marker != _owner_document(self.paths):
            raise UninstallError("ownership marker does not match this helper")
        try:
            launcher_metadata = os.lstat(self.launcher_path)
        except FileNotFoundError:
            if allow_partial and entries <= _KNOWN_RUNTIME_ENTRIES:
                return "installed_partial"
            raise UninstallError("launcher is missing")
        except OSError as exc:
            raise UninstallError("launcher is unavailable") from exc
        if (
            stat.S_ISLNK(launcher_metadata.st_mode)
            or not stat.S_ISREG(launcher_metadata.st_mode)
            or launcher_metadata.st_uid != os.getuid()
            or stat.S_IMODE(launcher_metadata.st_mode) != 0o700
        ):
            raise UninstallError("launcher is not the exact private helper launcher")
        try:
            launcher_text = self.launcher_path.read_text("utf-8")
        except (OSError, UnicodeError) as exc:
            raise UninstallError("launcher is unavailable") from exc
        if launcher_text != _launcher_text():
            raise UninstallError("launcher is not the exact private helper launcher")
        config_path = self.runtime_dir / "config.json"
        if self._exists(config_path):
            config_metadata = os.lstat(config_path)
            if stat.S_ISLNK(config_metadata.st_mode) or not stat.S_ISREG(config_metadata.st_mode):
                raise UninstallError("helper config must not be a symlink")
            if config_metadata.st_uid != os.getuid():
                raise UninstallError("helper config is not owned by the current user")
        return "installed"

    def _open_lock(self) -> int:
        try:
            if not self.runtime_parent.exists():
                self.runtime_parent.mkdir(mode=0o700)
            parent = os.lstat(self.runtime_parent)
            if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid():
                raise UninstallError("runtime parent is unsafe")
            if self._exists(self.install_lock_path):
                _owned_regular(self.install_lock_path, "installer lock")
            descriptor = os.open(
                self.install_lock_path,
                os.O_RDWR | os.O_CREAT | _NOFOLLOW,
                0o600,
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid():
                raise UninstallError("installer lock changed during validation")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return descriptor
        except Exception as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            if isinstance(exc, UninstallError):
                raise
            raise UninstallError("uninstaller lock could not be acquired") from exc

    def uninstall(self, extension_id: str, *, keep_config: bool = False) -> None:
        try:
            extension_id = validate_extension_id(extension_id)
        except ValueError as exc:
            raise UninstallError(str(exc)) from exc
        if not self._exists(self.runtime_dir) and not self._exists(self.manifest_path):
            return
        descriptor = self._open_lock()
        try:
            self._validate_manifest(extension_id)
            manifest_exists = self._exists(self.manifest_path)
            runtime_kind = self._runtime_kind(allow_partial=manifest_exists)
            state = None
            if keep_config and runtime_kind != "absent":
                try:
                    state = HelperConfig(self.runtime_dir, self.application_support_root).load()
                except Exception as exc:
                    raise UninstallError("private config could not be normalized") from exc
            if runtime_kind != "absent":
                shutil.rmtree(self.runtime_dir)
            if keep_config and state is not None:
                self.runtime_dir.mkdir(mode=0o700)
                config = HelperConfig(self.runtime_dir, self.application_support_root)
                destination = state.get("destination")
                config.save_destination(None if destination is None else Path(destination))
                config.save_recent_job(state.get("recent_job"))
                try:
                    (self.runtime_dir / ".config.lock").unlink()
                except FileNotFoundError:
                    pass
            if self._exists(self.manifest_path):
                self.manifest_path.unlink()
        except UninstallError:
            raise
        except Exception as exc:
            raise UninstallError("confined uninstall failed") from exc
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)



def _default_home() -> Path:
    return Path.home()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove the Page Scraper Pro native helper")
    parser.add_argument("--extension-id", required=True)
    parser.add_argument("--keep-config", action="store_true")
    arguments = parser.parse_args(argv)
    home = _default_home()
    app_root = home / "Library" / "Application Support"
    manifest_root = app_root / "Google" / "Chrome" / "NativeMessagingHosts"
    if not app_root.exists() and not app_root.is_symlink():
        return 0
    try:
        Uninstaller(app_root, manifest_root).uninstall(
            arguments.extension_id, keep_config=arguments.keep_config
        )
    except UninstallError as exc:
        print(f"uninstall failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
