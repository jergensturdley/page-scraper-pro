import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from native_host.config import HelperConfig


class HelperConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.trusted_root = self.root / "trusted"
        self.trusted_root.mkdir()
        self.app_dir = self.trusted_root / "private" / "helper"
        self.config = HelperConfig(self.app_dir, self.trusted_root)

    def write_config(self, value):
        self.app_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        (self.app_dir / "config.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def test_missing_config_returns_empty_private_state(self):
        self.assertEqual(
            self.config.load(), {"destination": None, "recent_job": None}
        )

    def test_save_uses_confined_temporary_file_and_os_replace(self):
        destination = self.root / "downloads"
        destination.mkdir()
        replacements = []
        real_replace = os.replace

        def tracking_replace(
            source, target, *, src_dir_fd=None, dst_dir_fd=None
        ):
            source_path = self.app_dir / source
            target_path = self.app_dir / target
            replacements.append(
                (
                    source,
                    target,
                    source_path.exists(),
                    src_dir_fd,
                    dst_dir_fd,
                )
            )
            real_replace(
                source,
                target,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        with mock.patch("native_host.config.os.replace", side_effect=tracking_replace):
            self.config.save_destination(destination)

        self.assertEqual(len(replacements), 1)
        source, target, source_existed, source_fd, target_fd = replacements[0]
        self.assertTrue(source_existed)
        self.assertNotEqual(source, target)
        self.assertEqual(target, "config.json")
        self.assertIsInstance(source_fd, int)
        self.assertEqual(source_fd, target_fd)
        self.assertFalse((self.app_dir / source).exists())

    def test_save_sets_private_directory_and_file_modes(self):
        self.config.save_destination(None)

        directory_mode = stat.S_IMODE(self.app_dir.stat().st_mode)
        config_mode = stat.S_IMODE((self.app_dir / "config.json").stat().st_mode)
        self.assertEqual(directory_mode, 0o700)
        self.assertEqual(config_mode, 0o600)

    def test_save_persists_only_destination_and_normalized_recent_job(self):
        destination = self.root / "downloads"
        destination.mkdir()
        self.config.save_destination(destination)
        self.config.save_recent_job(
            {
                "job_id": "job_123",
                "state": "completed",
                "error_code": "none",
                "extractor": "PublicVideo",
                "phase": "finished",
                "percent": 100.0,
                "filename": "private-video.mp4",
                "headers": {"Authorization": "secret"},
                "unknown": "not persisted",
            }
        )

        persisted = json.loads((self.app_dir / "config.json").read_text("utf-8"))
        self.assertEqual(set(persisted), {"destination", "recent_job"})
        self.assertEqual(persisted["destination"], str(destination))
        self.assertEqual(
            persisted["recent_job"],
            {
                "error_code": "none",
                "extractor": "PublicVideo",
                "job_id": "job_123",
                "percent": 100.0,
                "phase": "finished",
                "state": "completed",
            },
        )
        self.assertNotIn("private-video.mp4", (self.app_dir / "config.json").read_text("utf-8"))

    def test_recent_job_is_bounded_and_only_one_snapshot_is_retained(self):
        self.config.save_recent_job(
            {"job_id": "first", "state": "completed", "extractor": "x" * 2_000}
        )
        self.config.save_recent_job({"job_id": "second", "state": "failed"})

        state = self.config.load()
        self.assertEqual(
            state["recent_job"], {"job_id": "second", "state": "failed"}
        )
        encoded = json.dumps(state["recent_job"], ensure_ascii=False)
        self.assertLessEqual(len(encoded), 2_048)

    def test_none_clears_destination_and_recent_job(self):
        destination = self.root / "downloads"
        destination.mkdir()
        self.config.save_destination(destination)
        self.config.save_recent_job({"job_id": "job_1", "state": "completed"})

        self.config.save_destination(None)
        self.config.save_recent_job(None)

        self.assertEqual(
            self.config.load(), {"destination": None, "recent_job": None}
        )
        self.assertIsNone(self.config.get_destination())

    def test_get_destination_returns_a_path_for_an_absolute_stored_path(self):
        destination = self.root / "downloads"
        destination.mkdir()
        self.config.save_destination(destination)
        self.assertEqual(self.config.get_destination(), destination)

    def test_malformed_json_is_rejected(self):
        self.app_dir.mkdir(parents=True)
        (self.app_dir / "config.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.config.load()

    def test_config_symlink_is_rejected_without_following_it(self):
        outside = self.root / "outside.json"
        outside.write_text(
            json.dumps({"destination": None, "recent_job": None}), encoding="utf-8"
        )
        self.app_dir.mkdir(parents=True)
        (self.app_dir / "config.json").symlink_to(outside)

        with self.assertRaises(ValueError):
            self.config.load()
        with self.assertRaises(ValueError):
            self.config.save_destination(None)
        self.assertEqual(outside.read_text("utf-8"), json.dumps({"destination": None, "recent_job": None}))

    def test_symlinked_app_directory_outside_injected_root_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        linked_app_dir = self.trusted_root / "linked-helper"
        linked_app_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ValueError):
            HelperConfig(linked_app_dir, self.trusted_root).save_destination(None)
        self.assertEqual(list(outside.iterdir()), [])

    def test_direct_parent_symlink_is_rejected_without_escape(self):
        outside = self.root / "outside-parent"
        outside.mkdir()
        linked_parent = self.trusted_root / "linked-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        config = HelperConfig(linked_parent / "helper", self.trusted_root)

        with self.assertRaises(ValueError):
            config.save_destination(None)
        self.assertEqual(list(outside.iterdir()), [])

    def test_nested_ancestor_symlink_is_rejected_without_escape(self):
        outside = self.root / "outside-nested"
        outside.mkdir()
        real_parent = self.trusted_root / "real-parent"
        real_parent.mkdir()
        linked_ancestor = real_parent / "linked-ancestor"
        linked_ancestor.symlink_to(outside, target_is_directory=True)
        config = HelperConfig(linked_ancestor / "helper", self.trusted_root)

        with self.assertRaises(ValueError):
            config.save_destination(None)
        self.assertEqual(list(outside.iterdir()), [])

    def test_app_directory_outside_trusted_root_is_rejected_lexically(self):
        outside = self.root / "outside-root" / "helper"
        with self.assertRaises(ValueError):
            HelperConfig(outside, self.trusted_root)

    def test_wrong_top_level_and_field_types_are_rejected(self):
        invalid_values = (
            [],
            {"destination": 4, "recent_job": None},
            {"destination": None, "recent_job": []},
            {"destination": "relative/path", "recent_job": None},
            {"destination": None},
            {"destination": None, "recent_job": None, "extra": True},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                self.write_config(value)
                with self.assertRaises(ValueError):
                    self.config.load()

    def test_save_rejects_wrong_api_types(self):
        with self.assertRaises(TypeError):
            self.config.save_destination("/tmp/not-a-path")
        with self.assertRaises(TypeError):
            self.config.save_recent_job([{"job_id": "job_1"}])

    def test_json_output_is_sorted_and_preserves_unicode(self):
        self.config.save_recent_job(
            {"state": "completed", "extractor": "公開動画", "job_id": "job_1"}
        )
        raw = (self.app_dir / "config.json").read_text("utf-8")
        self.assertNotIn("\\u", raw)
        self.assertEqual(raw, json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
