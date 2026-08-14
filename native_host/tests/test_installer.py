import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from native_host.config import HelperConfig
from native_host.constants import HOST_NAME
from native_host.installer import (
    Installer,
    InstallError,
    _ensure_cli_roots,
    validate_extension_id,
)
from native_host.uninstaller import Uninstaller, UninstallError


EXTENSION_ID = "a" * 32


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app_root = self.root / "application-support"
        self.manifest_root = self.root / "native-messaging-hosts"
        self.app_root.mkdir()
        self.manifest_root.mkdir()
        self.events = []

        def runner(command, **kwargs):
            command = [str(item) for item in command]
            self.events.append(("run", command, kwargs))
            self.assertNotIn("sudo", command)
            if command[1:4] == ["-m", "venv", command[3]]:
                pass
            if command[1:3] == ["-m", "venv"]:
                venv = Path(command[3])
                (venv / "bin").mkdir(parents=True)
                (venv / "bin" / "python").write_text("python", encoding="utf-8")
            if command[1:4] == ["-m", "pip", "install"]:
                lock_path = Path(command[command.index("-r") + 1])
                self.pip_lock_path = lock_path
                self.pip_lock_text = lock_path.read_text("utf-8")
            return object()

        def copier(source, destination):
            self.events.append(("copy", Path(source), Path(destination)))
            return shutil.copytree(source, destination)

        def health(launcher, **kwargs):
            self.events.append(("health", Path(launcher), kwargs))
            self.assertTrue(Path(launcher).is_file())
            return {
                "helper_version": "1.0.0",
                "protocol_version": 1,
                "yt_dlp_version": "2026.7.4",
                "ffmpeg_version": "unavailable",
            }

        self.installer = Installer(
            application_support_root=self.app_root,
            manifest_root=self.manifest_root,
            source_package=Path(__file__).resolve().parents[1],
            python_executable=Path(sys.executable),
            python_version=(3, 11, 0),
            runner=runner,
            copy_tree=copier,
            health_probe=health,
        )

    def test_extension_id_is_exact_lowercase_chrome_alphabet(self):
        self.assertEqual(validate_extension_id(EXTENSION_ID), EXTENSION_ID)
        for invalid in ("a" * 31, "a" * 33, "q" * 32, "A" * 32, "0" * 32, None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_extension_id(invalid)

    def test_cli_root_creation_does_not_chmod_existing_shared_directories(self):
        home = self.root / "home"
        application_support = home / "Library" / "Application Support"
        application_support.mkdir(parents=True)
        (home / "Library").chmod(0o755)
        application_support.chmod(0o755)

        app_root, manifest_root = _ensure_cli_roots(home)

        self.assertEqual(app_root, application_support)
        self.assertTrue(manifest_root.is_dir())
        self.assertEqual(stat.S_IMODE((home / "Library").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(application_support.stat().st_mode), 0o755)

    def test_python_below_310_is_rejected_before_files_or_commands(self):
        installer = Installer(
            application_support_root=self.app_root,
            manifest_root=self.manifest_root,
            source_package=Path(__file__).resolve().parents[1],
            python_executable=Path(sys.executable),
            python_version=(3, 9, 18),
            runner=lambda *args, **kwargs: self.fail("runner called"),
            copy_tree=lambda *args: self.fail("copy called"),
            health_probe=lambda *args, **kwargs: self.fail("health called"),
        )
        with self.assertRaises(InstallError):
            installer.install(EXTENSION_ID)
        self.assertFalse(installer.runtime_dir.exists())

    def test_install_orders_venv_locked_install_copy_launcher_and_health(self):
        paths = self.installer.install(EXTENSION_ID)
        kinds = [event[0] for event in self.events]
        self.assertEqual(kinds, ["run", "run", "copy", "health"])
        venv_command = self.events[0][1]
        pip_command = self.events[1][1]
        self.assertEqual(venv_command[:3], [str(Path(sys.executable)), "-m", "venv"])
        self.assertEqual(pip_command[1:6], ["-m", "pip", "install", "--require-hashes", "--no-deps"])
        self.assertEqual(pip_command[6], "-r")
        self.assertEqual(Path(pip_command[7]).name, "requirements.lock")
        self.assertTrue(self.pip_lock_path.is_relative_to(paths.runtime_dir.parent))
        self.assertEqual(
            self.pip_lock_text,
            (Path(__file__).resolve().parents[1] / "requirements.lock").read_text("utf-8"),
        )
        self.assertFalse(any("sudo" in part for event in self.events if event[0] == "run" for part in event[1]))

        launcher = paths.launcher_path
        manifest = paths.manifest_path
        self.assertTrue(launcher.is_absolute())
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o700)
        launcher_text = launcher.read_text("utf-8")
        self.assertIn('dirname -- "$0"', launcher_text)
        self.assertIn('cd -- "$RUNTIME_DIR/package"', launcher_text)
        self.assertIn('main(app_dir=Path(sys.argv[1]))', launcher_text)
        self.assertNotIn(str(paths.runtime_dir.parent) + "/.", launcher_text)
        self.assertEqual(stat.S_IMODE(paths.runtime_dir.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((paths.runtime_dir / ".ownership.json").stat().st_mode),
            0o600,
        )

        document = json.loads(manifest.read_text("utf-8"))
        self.assertEqual(document, {
            "name": HOST_NAME,
            "description": "Page Scraper Pro local yt-dlp helper",
            "path": str(launcher),
            "type": "stdio",
            "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
        })
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
        self.assertTrue(paths.runtime_dir.is_relative_to(self.app_root))
        self.assertTrue(manifest.is_relative_to(self.manifest_root))
        self.assertFalse(any(path.name.startswith(".native-stage-") for path in paths.runtime_dir.parent.iterdir()))

    def test_repair_preserves_validated_helper_config(self):
        self.installer.install(EXTENSION_ID)
        destination = self.root / "downloads"
        destination.mkdir()
        config = HelperConfig(self.installer.runtime_dir, self.app_root)
        config.save_destination(destination)
        config.save_recent_job({"job_id": "job_old", "state": "completed", "filename": "discard"})

        self.installer.install(EXTENSION_ID)

        repaired = HelperConfig(self.installer.runtime_dir, self.app_root).load()
        self.assertEqual(repaired["destination"], str(destination))
        self.assertEqual(repaired["recent_job"], {"job_id": "job_old", "state": "completed"})
        self.assertEqual(sum(1 for p in self.installer.runtime_dir.rglob("config.json")), 1)

    def test_wrong_existing_origin_for_explicit_id_is_atomically_replaced(self):
        self.manifest_root.joinpath(f"{HOST_NAME}.json").write_text(json.dumps({
            "name": HOST_NAME,
            "description": "Page Scraper Pro local yt-dlp helper",
            "path": str(self.installer.launcher_path),
            "type": "stdio",
            "allowed_origins": ["chrome-extension://" + "b" * 32 + "/"],
        }), encoding="utf-8")
        paths = self.installer.install(EXTENSION_ID)
        manifest = json.loads(paths.manifest_path.read_text("utf-8"))
        self.assertEqual(manifest["allowed_origins"], [f"chrome-extension://{EXTENSION_ID}/"])
        self.assertEqual(manifest["path"], str(paths.launcher_path))

    def test_failed_fresh_stage_removes_all_generated_runtime_directories(self):
        self.installer.copy_tree = lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("copy failed")
        )

        with self.assertRaises(InstallError):
            self.installer.install(EXTENSION_ID)

        self.assertFalse(self.installer.runtime_dir.exists())
        self.assertFalse(self.installer.manifest_path.exists())
        self.assertFalse(any(
            path.name.startswith((".native-stage-", ".native-backup-"))
            for path in self.installer.runtime_parent.iterdir()
        ))

    def test_failed_staged_health_leaves_prior_install_and_config_intact(self):
        paths = self.installer.install(EXTENSION_ID)
        marker = paths.runtime_dir / "prior-marker"
        marker.write_text("healthy", encoding="utf-8")
        config = HelperConfig(paths.runtime_dir, self.app_root)
        destination = self.root / "downloads"
        destination.mkdir()
        config.save_destination(destination)
        old_manifest = paths.manifest_path.read_bytes()
        self.installer.health_probe = lambda *args, **kwargs: (_ for _ in ()).throw(InstallError("bad health"))

        with self.assertRaises(InstallError):
            self.installer.install(EXTENSION_ID)

        self.assertEqual(marker.read_text("utf-8"), "healthy")
        self.assertEqual(HelperConfig(paths.runtime_dir, self.app_root).get_destination(), destination)
        self.assertEqual(paths.manifest_path.read_bytes(), old_manifest)
        self.assertFalse(any(path.name.startswith(".native-stage-") for path in paths.runtime_dir.parent.iterdir()))


    def test_existing_manifest_with_wrong_fixed_path_is_not_overwritten(self):
        manifest_path = self.installer.manifest_path
        manifest_path.write_text(json.dumps({
            "name": HOST_NAME,
            "description": "Page Scraper Pro local yt-dlp helper",
            "path": "/tmp/not-owned",
            "type": "stdio",
            "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
        }), encoding="utf-8")
        old_bytes = manifest_path.read_bytes()

        with self.assertRaises(InstallError):
            self.installer.install(EXTENSION_ID)

        self.assertEqual(manifest_path.read_bytes(), old_bytes)
        self.assertFalse(self.installer.runtime_dir.exists())

    def test_publish_failure_rolls_back_exact_runtime_config_and_manifest(self):
        paths = self.installer.install(EXTENSION_ID)
        marker = paths.runtime_dir / "prior-marker"
        marker.write_bytes(b"prior runtime")
        destination = self.root / "downloads"
        destination.mkdir()
        HelperConfig(paths.runtime_dir, self.app_root).save_destination(destination)
        old_manifest = paths.manifest_path.read_bytes()
        old_manifest_mode = stat.S_IMODE(paths.manifest_path.stat().st_mode)

        from native_host import installer as installer_module
        real_publish = installer_module._atomic_private_json

        def publish_then_fail(path, value):
            real_publish(path, value)
            if Path(path) == paths.manifest_path:
                raise OSError("simulated post-publish failure")

        with mock.patch("native_host.installer._atomic_private_json", side_effect=publish_then_fail):
            with self.assertRaises(InstallError):
                self.installer.install(EXTENSION_ID)

        self.assertEqual(marker.read_bytes(), b"prior runtime")
        self.assertEqual(HelperConfig(paths.runtime_dir, self.app_root).get_destination(), destination)
        self.assertEqual(paths.manifest_path.read_bytes(), old_manifest)
        self.assertEqual(stat.S_IMODE(paths.manifest_path.stat().st_mode), old_manifest_mode)

    def test_symlinked_user_root_or_existing_runtime_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(InstallError):
            Installer(
                application_support_root=linked,
                manifest_root=self.manifest_root,
                source_package=Path(__file__).resolve().parents[1],
            )

        runtime = self.app_root / "Page Scraper Pro" / "native"
        runtime.parent.mkdir()
        runtime.symlink_to(real, target_is_directory=True)
        with self.assertRaises(InstallError):
            self.installer.install(EXTENSION_ID)


class UninstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app_root = self.root / "app"
        self.manifest_root = self.root / "manifests"
        self.app_root.mkdir()
        self.manifest_root.mkdir()
        source = Path(__file__).resolve().parents[1]

        def runner(command, **kwargs):
            command = [str(x) for x in command]
            if command[1:3] == ["-m", "venv"]:
                (Path(command[3]) / "bin").mkdir(parents=True)
                (Path(command[3]) / "bin" / "python").write_text("python", encoding="utf-8")
            return object()

        self.installer = Installer(
            application_support_root=self.app_root,
            manifest_root=self.manifest_root,
            source_package=source,
            python_executable=Path(sys.executable),
            python_version=(3, 11),
            runner=runner,
            copy_tree=shutil.copytree,
            health_probe=lambda *args, **kwargs: {},
        )
        self.paths = self.installer.install(EXTENSION_ID)
        self.uninstaller = Uninstaller(self.app_root, self.manifest_root)

    def test_uninstall_removes_only_exact_runtime_and_manifest_not_downloads(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        (downloads / "media.mp4").write_bytes(b"media")
        HelperConfig(self.paths.runtime_dir, self.app_root).save_destination(downloads)

        self.uninstaller.uninstall(EXTENSION_ID)

        self.assertFalse(self.paths.runtime_dir.exists())
        self.assertFalse(self.paths.manifest_path.exists())
        self.assertEqual((downloads / "media.mp4").read_bytes(), b"media")

    def test_keep_config_retains_only_normalized_private_config(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        config = HelperConfig(self.paths.runtime_dir, self.app_root)
        config.save_destination(downloads)
        config.save_recent_job({"job_id": "job", "state": "completed", "filename": "secret.mp4"})
        (self.paths.runtime_dir / "host.log").write_text("private", encoding="utf-8")

        self.uninstaller.uninstall(EXTENSION_ID, keep_config=True)

        self.assertFalse(self.paths.manifest_path.exists())
        self.assertEqual({p.name for p in self.paths.runtime_dir.iterdir()}, {"config.json"})
        retained = HelperConfig(self.paths.runtime_dir, self.app_root).load()
        self.assertEqual(retained, {
            "destination": str(downloads),
            "recent_job": {"job_id": "job", "state": "completed"},
        })
        self.assertEqual(stat.S_IMODE((self.paths.runtime_dir / "config.json").stat().st_mode), 0o600)

        # Reading retained config may recreate its private lock; a later default
        # uninstall must still recognize and remove only this strict shape.
        self.uninstaller.uninstall(EXTENSION_ID)
        self.assertFalse(self.paths.runtime_dir.exists())

    def test_uninstall_rejects_wrong_origin_manifest_and_symlink_launcher(self):
        document = json.loads(self.paths.manifest_path.read_text("utf-8"))
        document["allowed_origins"] = ["chrome-extension://" + "b" * 32 + "/"]
        self.paths.manifest_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(UninstallError):
            self.uninstaller.uninstall(EXTENSION_ID)
        self.assertTrue(self.paths.runtime_dir.exists())

        document["allowed_origins"] = [f"chrome-extension://{EXTENSION_ID}/"]
        self.paths.manifest_path.write_text(json.dumps(document), encoding="utf-8")
        self.paths.launcher_path.unlink()
        self.paths.launcher_path.symlink_to(Path(sys.executable))
        with self.assertRaises(UninstallError):
            self.uninstaller.uninstall(EXTENSION_ID)
        self.assertTrue(self.paths.runtime_dir.exists())

    def test_uninstall_recovers_when_chrome_manifest_directory_is_absent(self):
        self.paths.manifest_path.unlink()
        self.manifest_root.rmdir()
        uninstaller = Uninstaller(self.app_root, self.manifest_root)

        uninstaller.uninstall(EXTENSION_ID)

        self.assertFalse(self.paths.runtime_dir.exists())
        self.assertFalse(self.manifest_root.exists())

    def test_partial_runtime_removal_keeps_manifest_and_can_be_retried(self):
        def remove_marker_then_fail(path):
            (Path(path) / ".ownership.json").unlink()
            raise OSError("simulated removal failure")

        with mock.patch(
            "native_host.uninstaller.shutil.rmtree",
            side_effect=remove_marker_then_fail,
        ):
            with self.assertRaises(UninstallError):
                self.uninstaller.uninstall(EXTENSION_ID)

        self.assertTrue(self.paths.manifest_path.exists())
        self.assertTrue(self.paths.runtime_dir.exists())
        self.uninstaller.uninstall(EXTENSION_ID)
        self.assertFalse(self.paths.manifest_path.exists())
        self.assertFalse(self.paths.runtime_dir.exists())

    def test_partial_removal_with_marker_but_no_launcher_can_be_retried(self):
        def remove_launcher_then_fail(path):
            (Path(path) / "page-scraper-pro-native-host").unlink()
            raise OSError("simulated removal failure")

        with mock.patch(
            "native_host.uninstaller.shutil.rmtree",
            side_effect=remove_launcher_then_fail,
        ):
            with self.assertRaises(UninstallError):
                self.uninstaller.uninstall(EXTENSION_ID)

        self.assertTrue(self.paths.manifest_path.exists())
        self.uninstaller.uninstall(EXTENSION_ID)
        self.assertFalse(self.paths.runtime_dir.exists())
        self.assertFalse(self.paths.manifest_path.exists())

    def test_uninstall_is_idempotent_when_both_owned_artifacts_are_absent(self):
        self.uninstaller.uninstall(EXTENSION_ID)
        lock_path = self.uninstaller.install_lock_path
        first_inode = lock_path.stat().st_ino
        self.uninstaller.uninstall(EXTENSION_ID)
        self.assertEqual(lock_path.stat().st_ino, first_inode)
        self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)
