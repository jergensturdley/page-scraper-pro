"""Hash-locked, user-local macOS Native Messaging host installer."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterator

from .config import HelperConfig
from .constants import HELPER_VERSION, HOST_NAME, PROTOCOL_VERSION
from .healthcheck import EXPECTED_YT_DLP_VERSION, check_health

_EXTENSION_ID_RE = re.compile(r"^[a-p]{32}$")
_MANIFEST_DESCRIPTION = "Page Scraper Pro local yt-dlp helper"
_RUNTIME_PARENT_NAME = "Page Scraper Pro"
_RUNTIME_NAME = "native"
_LAUNCHER_NAME = "page-scraper-pro-native-host"
_MANIFEST_NAME = f"{HOST_NAME}.json"
_INSTALL_LOCK_NAME = ".native-install.lock"
_OWNER_MARKER_NAME = ".ownership.json"
_REQUIREMENTS_TEXT = """yt-dlp==2026.7.4 \\
    --hash=sha256:b094813404f87a9dd2186f00815231df32e5fd8a5403be0f807b3bb2d21a4432 \\
    --hash=sha256:f11f2b11d5a8ac4059f9bdf29fa4407dc7c6bb00c5097e95ca22a7a9db518266
"""
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

# Fake yt_dlp stub written to the venv in --offline-test-fixture mode.
# This module never ships with the real package.
_FAKE_YT_DLP_STUB = """
# Fake yt_dlp module for --offline-test-fixture mode.
# Written by the installer; never appears in production installs.
__version__ = "2026.7.4"
version = ["2026.07.04"]

class YoutubeDL:
    def __init__(self, params=None, auto_init=True):
        self.params = params or {}
        self.to_stderr = lambda *a, **kw: None
        self.to_console_header = lambda *a, **kw: None
        if auto_init:
            pass

    def to_stdout(self, message, *args, **kwargs):
        pass

    def to_screen(self, message, *args, **kwargs):
        pass

    def report_warning(self, message, *args, **kwargs):
        pass

    def report_error(self, message, *args, **kwargs):
        pass

def main(argv=None):
    raise SystemExit(0)
"""


class InstallError(RuntimeError):
    """Raised when installation cannot complete without weakening confinement."""


@dataclass(frozen=True)
class InstallPaths:
    runtime_dir: Path
    launcher_path: Path
    manifest_path: Path


def validate_extension_id(value: object) -> str:
    if not isinstance(value, str) or _EXTENSION_ID_RE.fullmatch(value) is None:
        raise ValueError("extension ID must be 32 lowercase characters from a through p")
    return value


def _lstat_real_directory(path: Path, message: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise InstallError(message) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise InstallError(message)
    if metadata.st_uid != os.getuid():
        raise InstallError(f"{message}; directory is not owned by the current user")
    return metadata


def _validate_root(root: Path, label: str) -> Path:
    if not isinstance(root, Path):
        root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise InstallError(f"{label} must be an absolute confined path")
    _lstat_real_directory(root, f"{label} must be a real directory")
    return root


def _ensure_descendant(
    root: Path,
    path: Path,
    *,
    mode: int = 0o700,
    enforce_leaf: bool = True,
) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise InstallError("installation path escapes its injected user root") from exc
    current = root
    for component in relative.parts:
        current = current / component
        created = False
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            try:
                current.mkdir(mode=mode)
                created = True
            except OSError as exc:
                raise InstallError(
                    "private installation directory could not be created"
                ) from exc
            metadata = os.lstat(current)
        except OSError as exc:
            raise InstallError("private installation directory is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise InstallError("installation directory components must be real directories")
        if metadata.st_uid != os.getuid():
            raise InstallError("installation directory is not owned by the current user")
        if created or (enforce_leaf and current == path):
            try:
                os.chmod(current, mode)
            except OSError as exc:
                raise InstallError(
                    "private installation directory permissions could not be set"
                ) from exc
    return path


def _owned_regular(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise InstallError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise InstallError(f"{label} must be a real regular file")
    if metadata.st_uid != os.getuid():
        raise InstallError(f"{label} is not owned by the current user")
    return metadata


def _launcher_text() -> str:
    # Self-location remains correct while staged and after the sibling rename.
    return """#!/bin/sh
set -eu
RUNTIME_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd -- "$RUNTIME_DIR/package"
exec "$RUNTIME_DIR/venv/bin/python" -c 'from pathlib import Path; import sys; from native_host.host import main; raise SystemExit(main(app_dir=Path(sys.argv[1])))' "$RUNTIME_DIR"
"""


def _manifest_document(extension_id: str, launcher: Path) -> dict[str, object]:
    return {
        "name": HOST_NAME,
        "description": _MANIFEST_DESCRIPTION,
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def _owner_document(paths: InstallPaths) -> dict[str, object]:
    return {
        "host_name": HOST_NAME,
        "runtime_path": str(paths.runtime_dir),
        "manifest_path": str(paths.manifest_path),
        "uid": os.getuid(),
    }


def _read_json_regular(path: Path, label: str) -> dict[str, object]:
    _owned_regular(path, label)
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise InstallError(f"{label} is invalid")
    return value


def _atomic_private_json(path: Path, value: dict[str, object]) -> None:
    parent = path.parent
    temporary = parent / f".{path.name}.{secrets.token_hex(12)}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        encoded = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class Installer:
    """Perform a staged, rollback-safe install beneath injected user roots."""

    def __init__(
        self,
        *,
        application_support_root: Path,
        manifest_root: Path,
        source_package: Path | None = None,
        python_executable: Path | None = None,
        python_version: tuple[int, ...] | None = None,
        runner: Callable[..., object] = subprocess.run,
        copy_tree: Callable[..., object] = shutil.copytree,
        health_probe: Callable[..., dict[str, object]] = check_health,
        offline_test_fixture: bool = False,
    ):
        self.application_support_root = _validate_root(application_support_root, "application support root")
        self.manifest_root = _validate_root(manifest_root, "manifest root")
        self.source_package = Path(source_package or Path(__file__).resolve().parent)
        if not self.source_package.is_absolute():
            self.source_package = self.source_package.resolve()
        try:
            source_metadata = os.lstat(self.source_package)
        except OSError as exc:
            raise InstallError("source package is unavailable") from exc
        if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISDIR(source_metadata.st_mode):
            raise InstallError("source package must be a real directory")
        self.python_executable = Path(python_executable or sys.executable)
        if not self.python_executable.is_absolute():
            raise InstallError("Python executable must be absolute")
        self.python_version = tuple(python_version or sys.version_info[:3])
        self.runner = runner
        self.copy_tree = copy_tree
        self.health_probe = health_probe
        self.offline_test_fixture = offline_test_fixture
        self.runtime_parent = self.application_support_root / _RUNTIME_PARENT_NAME
        self.runtime_dir = self.runtime_parent / _RUNTIME_NAME
        self.launcher_path = self.runtime_dir / _LAUNCHER_NAME
        self.manifest_path = self.manifest_root / _MANIFEST_NAME
        self.install_lock_path = self.runtime_parent / _INSTALL_LOCK_NAME
        self.owner_marker_path = self.runtime_dir / _OWNER_MARKER_NAME

    @property
    def paths(self) -> InstallPaths:
        return InstallPaths(self.runtime_dir, self.launcher_path, self.manifest_path)

    def _validate_requirements(self) -> Path:
        path = self.source_package / "requirements.lock"
        try:
            metadata = os.lstat(path)
            contents = path.read_text("utf-8")
        except (OSError, UnicodeError) as exc:
            raise InstallError("reviewed dependency lock is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise InstallError("reviewed dependency lock must be a real file")
        if contents != _REQUIREMENTS_TEXT:
            raise InstallError("dependency lock does not match the reviewed hashes")
        return path

    def _validate_existing_target(self) -> bool:
        try:
            metadata = os.lstat(self.runtime_dir)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise InstallError("existing runtime is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise InstallError("existing runtime must be a real directory")
        if metadata.st_uid != os.getuid():
            raise InstallError("existing runtime is not owned by the current user")
        return True

    def _validate_existing_manifest(self) -> None:
        if not self.manifest_path.exists() and not self.manifest_path.is_symlink():
            return
        value = _read_json_regular(self.manifest_path, "existing manifest")
        expected = _manifest_document("a" * 32, self.launcher_path)
        fixed_keys = {"name", "description", "path", "type"}
        if set(value) != set(expected) or any(value.get(key) != expected[key] for key in fixed_keys):
            raise InstallError("existing manifest is not owned by this helper")
        origins = value.get("allowed_origins")
        if (
            not isinstance(origins, list)
            or len(origins) != 1
            or not isinstance(origins[0], str)
            or re.fullmatch(r"chrome-extension://[a-p]{32}/", origins[0]) is None
        ):
            raise InstallError("existing manifest origin is invalid")

    def _is_retained_config_runtime(self) -> bool:
        try:
            entries = {entry.name for entry in self.runtime_dir.iterdir()}
        except OSError as exc:
            raise InstallError("existing runtime could not be inspected") from exc
        if not {"config.json"} <= entries <= {"config.json", ".config.lock"}:
            return False
        for name in entries:
            try:
                metadata = os.lstat(self.runtime_dir / name)
            except OSError:
                return False
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                return False
        return True

    def _validate_owner_marker(self, *, target_exists: bool) -> None:
        if not target_exists:
            return
        marker_exists = self.owner_marker_path.exists() or self.owner_marker_path.is_symlink()
        if not marker_exists:
            if self._is_retained_config_runtime():
                # --keep-config deliberately leaves only this normalized private file.
                HelperConfig(self.runtime_dir, self.application_support_root).load()
                try:
                    (self.runtime_dir / ".config.lock").unlink()
                except FileNotFoundError:
                    pass
                return
            raise InstallError("existing runtime has no ownership marker")
        marker_metadata = _owned_regular(
            self.owner_marker_path, "ownership marker"
        )
        if stat.S_IMODE(marker_metadata.st_mode) != 0o600:
            raise InstallError("ownership marker must be private")
        value = _read_json_regular(self.owner_marker_path, "ownership marker")
        if value != _owner_document(self.paths):
            raise InstallError("ownership marker does not match this helper")

    @contextmanager
    def _locked_owner(self) -> Iterator[int]:
        _ensure_descendant(self.application_support_root, self.runtime_parent)
        existed = self.install_lock_path.exists() or self.install_lock_path.is_symlink()
        if existed:
            _owned_regular(self.install_lock_path, "installer lock")
        try:
            descriptor = os.open(
                self.install_lock_path,
                os.O_RDWR | os.O_CREAT | _NOFOLLOW,
                0o600,
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise InstallError("installer lock is unsafe")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except (OSError, InstallError) as exc:
            if isinstance(exc, InstallError):
                raise
            raise InstallError("installer lock could not be acquired") from exc
        try:
            yield descriptor
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _capture_config(self, target_exists: bool) -> dict[str, object] | None:
        if not target_exists:
            return None
        return HelperConfig(self.runtime_dir, self.application_support_root).load()

    def _stage(self, config_state: dict[str, object] | None) -> Path:
        staging = self.runtime_parent / f".native-stage-{secrets.token_hex(12)}"
        try:
            staging.mkdir(mode=0o700)
            os.chmod(staging, 0o700)
            venv = staging / "venv"
            venv_command = [str(self.python_executable), "-m", "venv", str(venv)]
            if any(part == "sudo" for part in venv_command):
                raise InstallError("privilege escalation is forbidden")
            self.runner(venv_command, check=True)

            # pip must consume installer-owned bytes, not reopen the reviewed
            # source lock after a validation-to-use race.
            staged_requirements = staging / "requirements.lock"
            lock_descriptor = os.open(
                staged_requirements,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                0o600,
            )
            try:
                lock_bytes = _REQUIREMENTS_TEXT.encode("utf-8")
                offset = 0
                while offset < len(lock_bytes):
                    written = os.write(lock_descriptor, lock_bytes[offset:])
                    if written <= 0:
                        raise OSError("short requirements write")
                    offset += written
                os.fsync(lock_descriptor)
            finally:
                os.close(lock_descriptor)
            pip_command = [
                str(venv / "bin" / "python"),
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--no-deps",
                "-r",
                str(staged_requirements),
            ]
            if any(part == "sudo" for part in pip_command):
                raise InstallError("privilege escalation is forbidden")
            if self.offline_test_fixture:
                # Write a fake yt_dlp stub package with proper dist-info metadata
                # so importlib.metadata.version("yt-dlp") returns the expected version.
                import sys as _sys
                major, minor = _sys.version_info[:2]
                site_packages = (
                    venv / "lib" / ("python" + str(major) + "." + str(minor)) / "site-packages"
                )
                yt_dlp_pkg = site_packages / "yt_dlp"
                yt_dlp_pkg.mkdir(parents=True, exist_ok=True)
                (yt_dlp_pkg / "__init__.py").write_text(_FAKE_YT_DLP_STUB, encoding="utf-8")
                # Write dist-info so importlib.metadata can find the package.
                dist_info_dir = site_packages / "yt_dlp-2026.7.4.dist-info"
                dist_info_dir.mkdir(parents=True, exist_ok=True)
                (dist_info_dir / "METADATA").write_text(
                    "Metadata-Version: 2.1\n"
                    "Name: yt-dlp\n"
                    "Version: 2026.7.4\n"
                    "Summary: A feature-rich command-line audio/video downloader.\n",
                    encoding="utf-8",
                )
                (dist_info_dir / "top_level.txt").write_text("yt_dlp\n", encoding="utf-8")
                (dist_info_dir / "RECORD").write_text(
                    "yt_dlp/__init__.py,sha256=placeholder,0\n",
                    encoding="utf-8",
                )
            else:
                self.runner(pip_command, check=True)
            package_root = staging / "package"
            package_root.mkdir(mode=0o700)
            self.copy_tree(self.source_package, package_root / "native_host")
            launcher = staging / _LAUNCHER_NAME
            descriptor = os.open(launcher, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o700)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(_launcher_text())
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(launcher, 0o700)
            if config_state is not None:
                config = HelperConfig(staging, self.application_support_root)
                destination = config_state.get("destination")
                config.save_destination(None if destination is None else Path(destination))
                config.save_recent_job(config_state.get("recent_job"))
            staged_paths = InstallPaths(self.runtime_dir, self.launcher_path, self.manifest_path)
            _atomic_private_json(staging / _OWNER_MARKER_NAME, _owner_document(staged_paths))
            self.health_probe(launcher)
            return staging
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _rollback_manifest(self, old_bytes: bytes | None, old_mode: int | None) -> None:
        if old_bytes is None:
            try:
                self.manifest_path.unlink()
            except FileNotFoundError:
                pass
            return
        temporary = self.manifest_path.parent / f".{self.manifest_path.name}.rollback-{secrets.token_hex(8)}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, old_mode or 0o600)
        try:
            offset = 0
            while offset < len(old_bytes):
                written = os.write(descriptor, old_bytes[offset:])
                if written <= 0:
                    raise OSError("short rollback write")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.manifest_path)
        os.chmod(self.manifest_path, old_mode or 0o600)

    def install(self, extension_id: str) -> InstallPaths:
        try:
            extension_id = validate_extension_id(extension_id)
        except ValueError as exc:
            raise InstallError(str(exc)) from exc
        if self.python_version < (3, 10):
            raise InstallError("Python 3.10 or newer is required")
        self._validate_requirements()
        _ensure_descendant(self.application_support_root, self.runtime_parent)

        with self._locked_owner():
            target_exists = self._validate_existing_target()
            self._validate_owner_marker(target_exists=target_exists)
            self._validate_existing_manifest()
            config_state = self._capture_config(target_exists)
            try:
                staging = self._stage(config_state)
            except InstallError:
                raise
            except Exception as exc:
                raise InstallError("staged installation failed") from exc

            backup = self.runtime_parent / f".native-backup-{secrets.token_hex(12)}"
            old_manifest_bytes: bytes | None = None
            old_manifest_mode: int | None = None
            if self.manifest_path.exists():
                metadata = _owned_regular(self.manifest_path, "existing manifest")
                old_manifest_bytes = self.manifest_path.read_bytes()
                old_manifest_mode = stat.S_IMODE(metadata.st_mode)
            runtime_swapped = False
            manifest_attempted = False
            try:
                if target_exists:
                    os.replace(self.runtime_dir, backup)
                os.replace(staging, self.runtime_dir)
                runtime_swapped = True
                manifest_attempted = True
                _atomic_private_json(
                    self.manifest_path,
                    _manifest_document(extension_id, self.launcher_path),
                )
            except Exception as exc:
                rollback_error: Exception | None = None
                if manifest_attempted:
                    try:
                        self._rollback_manifest(old_manifest_bytes, old_manifest_mode)
                    except Exception as restore_exc:
                        rollback_error = restore_exc
                try:
                    if runtime_swapped:
                        os.replace(self.runtime_dir, staging)
                    if target_exists and backup.exists():
                        os.replace(backup, self.runtime_dir)
                except Exception as restore_exc:
                    rollback_error = rollback_error or restore_exc
                try:
                    if staging.exists():
                        shutil.rmtree(staging)
                except Exception as cleanup_exc:
                    rollback_error = rollback_error or cleanup_exc
                if rollback_error is not None:
                    raise InstallError(
                        "installation failed and rollback was incomplete"
                    ) from rollback_error
                raise InstallError(
                    "installation publish failed; prior install restored"
                ) from exc
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            return self.paths


def _default_home() -> Path:
    # Allow tests to isolate the install from the real user home.
    test_home = os.environ.get("PAGE_SCRAPER_TEST_HOME")
    if test_home:
        return Path(test_home)
    return Path.home()


def _ensure_cli_roots(home: Path) -> tuple[Path, Path]:
    home = _validate_root(home, "user home")
    app_root = home / "Library" / "Application Support"
    chrome_root = app_root / "Google" / "Chrome" / "NativeMessagingHosts"
    _ensure_descendant(home, app_root, mode=0o700, enforce_leaf=False)
    _ensure_descendant(home, chrome_root, mode=0o700, enforce_leaf=False)
    return app_root, chrome_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the Page Scraper Pro native helper")
    parser.add_argument("--extension-id", required=True)
    parser.add_argument(
        "--offline-test-fixture",
        action="store_true",
        help="Install a fake yt_dlp stub instead of fetching real packages (test mode only)",
    )
    arguments = parser.parse_args(argv)
    try:
        app_root, manifest_root = _ensure_cli_roots(_default_home())
        Installer(
            application_support_root=app_root,
            manifest_root=manifest_root,
            offline_test_fixture=arguments.offline_test_fixture,
        ).install(arguments.extension_id)
    except (InstallError, ValueError) as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
