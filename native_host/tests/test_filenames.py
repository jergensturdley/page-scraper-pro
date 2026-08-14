import os
from pathlib import Path
import tempfile
import unittest

from native_host.worker import (
    PRESET_SELECTORS,
    build_download_options,
    collision_safe_path,
    sanitize_filename,
)


class FilenameTests(unittest.TestCase):
    def test_sanitizes_blank_unicode_separators_dots_controls_and_hidden_prefixes(self):
        cases = (
            ("", "media_abc", "mp4"),
            ("雪の映像 café", "media_abc", ".webm"),
            ("folder/name:clip\\take", "media_abc", "mkv"),
            ("../..", "media_abc", "mp4"),
            ("bad\x00name\nvideo", "media_abc", "mp4"),
            (".hidden", "media_abc", "mp4"),
            ("x" * 255, "media_abc", "mp4"),
        )
        for title, media_id, extension in cases:
            with self.subTest(title=title):
                result = sanitize_filename(title, media_id, extension)
                self.assertEqual(result, Path(result).name)
                self.assertNotIn("/", result)
                self.assertNotIn("\\", result)
                self.assertNotIn("..", result)
                self.assertFalse(result.startswith("."))
                self.assertFalse(any(ord(character) < 32 for character in result))
                self.assertLessEqual(len(result.encode("utf-8")), 180)
                self.assertTrue(result)
        self.assertIn("雪の映像", sanitize_filename("雪の映像 café", "media_abc", "mp4"))
        self.assertTrue(sanitize_filename("", "media_abc", "mp4").startswith("media_abc"))

    def test_collision_path_stays_in_directory_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root).resolve()
            (directory / "video.mp4").write_bytes(b"old")
            (directory / "video (1).mp4").write_bytes(b"older")

            result = collision_safe_path(directory, "video.mp4")

            self.assertEqual(result, directory / "video (2).mp4")
            self.assertEqual(result.parent.resolve(), directory)
            self.assertLessEqual(len(result.name.encode("utf-8")), 180)

    def test_collision_path_rejects_non_basename(self):
        with tempfile.TemporaryDirectory() as root:
            for filename in ("../escape.mp4", "folder/video.mp4", ".", ""):
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    collision_safe_path(Path(root), filename)

    def test_collision_suffix_preserves_byte_bound_for_long_unicode_name(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            filename = sanitize_filename("雪" * 255, "media_abc", "webm")
            (directory / filename).write_bytes(b"old")

            result = collision_safe_path(directory, filename)

            self.assertLessEqual(len(result.name.encode("utf-8")), 180)
            self.assertNotEqual(result.name, filename)


class DownloadOptionsTests(unittest.TestCase):
    def test_builds_only_fixed_preset_options_beneath_temp_directory(self):
        hook = object()
        with tempfile.TemporaryDirectory() as root:
            temp_dir = Path(root).resolve()
            for preset, selector in PRESET_SELECTORS.items():
                with self.subTest(preset=preset):
                    options = build_download_options(preset, temp_dir, hook)
                    self.assertEqual(options["format"], selector)
                    self.assertEqual(options["outtmpl"], str(temp_dir / "%(id)s.%(ext)s"))
                    self.assertIs(options["noplaylist"], True)
                    self.assertIs(options["continuedl"], True)
                    self.assertIs(options["overwrites"], False)
                    self.assertEqual(options["progress_hooks"], [hook])
                    self.assertEqual(options["postprocessor_hooks"], [hook])
                    forbidden = {
                        "cookiefile", "cookies", "cookiesfrombrowser", "proxy",
                        "exec_cmd", "exec_before_dl_cmd", "postprocessor_args",
                        "paths", "outtmpl_na_placeholder",
                    }
                    self.assertTrue(forbidden.isdisjoint(options))

    def test_rejects_any_non_enum_preset_and_untrusted_temp_directory(self):
        hook = lambda event: None
        with tempfile.TemporaryDirectory() as root:
            temp_dir = Path(root)
            for preset in ("", "BEST", "best[height<100]", None, {"format": "best"}):
                with self.subTest(preset=preset), self.assertRaises(ValueError):
                    build_download_options(preset, temp_dir, hook)
            with self.assertRaises(ValueError):
                build_download_options("best", temp_dir / "missing", hook)


if __name__ == "__main__":
    unittest.main()
