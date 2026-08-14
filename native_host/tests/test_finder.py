import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from native_host.finder import (
    CHOOSE_FOLDER_SCRIPT,
    FolderSelectionCancelled,
    FolderSelectionError,
    choose_directory,
    validate_destination,
)


EXPECTED_COMMAND = ["/usr/bin/osascript", "-e", CHOOSE_FOLDER_SCRIPT]


class FinderSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.destination = self.root / "downloads"
        self.destination.mkdir()

    def completed(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            EXPECTED_COMMAND, returncode, stdout=stdout, stderr=stderr
        )

    def test_choose_directory_returns_resolved_posix_output(self):
        fake_run = mock.Mock(return_value=self.completed(stdout=f"{self.destination}\n"))

        selected = choose_directory(run=fake_run)

        self.assertEqual(selected, self.destination.resolve())

    def test_choose_directory_calls_only_constant_applescript_command(self):
        fake_run = mock.Mock(return_value=self.completed(stdout=f"{self.destination}\n"))

        choose_directory(run=fake_run)

        fake_run.assert_called_once_with(
            EXPECTED_COMMAND, capture_output=True, text=True, check=False
        )

    def test_user_cancellation_has_a_distinct_exception(self):
        fake_run = mock.Mock(
            return_value=self.completed(
                returncode=1,
                stderr="execution error: User canceled. (-128)\n",
            )
        )

        with self.assertRaises(FolderSelectionCancelled):
            choose_directory(run=fake_run)

    def test_applescript_failure_is_reported_without_exposing_stderr(self):
        fake_run = mock.Mock(
            return_value=self.completed(
                returncode=2,
                stderr="private diagnostic /Users/example/secret",
            )
        )

        with self.assertRaisesRegex(FolderSelectionError, "folder chooser failed") as caught:
            choose_directory(run=fake_run)
        self.assertNotIn("private diagnostic", str(caught.exception))

    def test_only_newline_characters_are_stripped_from_posix_output(self):
        destination_with_space = self.root / "folder "
        destination_with_space.mkdir()
        fake_run = mock.Mock(
            return_value=self.completed(stdout=f"{destination_with_space}\r\n\n")
        )

        selected = choose_directory(run=fake_run)

        self.assertEqual(selected, destination_with_space.resolve())

    def test_empty_success_output_is_an_error(self):
        fake_run = mock.Mock(return_value=self.completed(stdout="\n"))
        with self.assertRaises(FolderSelectionError):
            choose_directory(run=fake_run)


class DestinationValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.app_dir = self.root / "private-helper"
        self.app_dir.mkdir()
        self.destination = self.root / "downloads"
        self.destination.mkdir()

    def test_accepts_a_writable_real_directory(self):
        self.assertEqual(
            validate_destination(self.destination, self.app_dir),
            self.destination.resolve(),
        )

    def test_rejects_nonexistent_paths_and_files(self):
        regular_file = self.root / "video.mp4"
        regular_file.write_text("not a directory", encoding="utf-8")
        for path in (self.root / "missing", regular_file):
            with self.subTest(path=path):
                with self.assertRaises(FolderSelectionError):
                    validate_destination(path, self.app_dir)

    def test_rejects_a_symlink_even_when_it_targets_a_writable_directory(self):
        linked = self.root / "linked-downloads"
        linked.symlink_to(self.destination, target_is_directory=True)
        with self.assertRaises(FolderSelectionError):
            validate_destination(linked, self.app_dir)

    def test_accepts_searchable_writable_directory_without_read_permission(self):
        self.destination.chmod(0o300)
        try:
            self.assertEqual(
                validate_destination(self.destination, self.app_dir),
                self.destination.resolve(),
            )
        finally:
            self.destination.chmod(0o700)

    def test_rejects_real_write_only_directory_without_search_permission(self):
        self.destination.chmod(0o200)
        try:
            with self.assertRaises(FolderSelectionError):
                validate_destination(self.destination, self.app_dir)
        finally:
            self.destination.chmod(0o700)

    def test_successful_probe_is_removed(self):
        validate_destination(self.destination, self.app_dir)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_detects_directory_swap_after_no_follow_open(self):
        moved = self.root / "downloads-before-swap"
        real_open = os.open
        swapped = False

        def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if (
                not swapped
                and dir_fd is None
                and Path(path) == self.destination.resolve()
            ):
                self.destination.rename(moved)
                self.destination.mkdir()
                swapped = True
            return descriptor

        with mock.patch("native_host.finder.os.open", side_effect=swapping_open):
            with self.assertRaises(FolderSelectionError):
                validate_destination(self.destination, self.app_dir)

        self.assertTrue(swapped)
        for directory in (moved, self.destination):
            self.assertFalse(
                any(child.name.startswith(".pagescraper-pro-probe-") for child in directory.iterdir())
            )

    def test_rejects_helper_app_directory_and_its_descendants(self):
        private_child = self.app_dir / "downloads"
        private_child.mkdir()
        for path in (self.app_dir, private_child):
            with self.subTest(path=path):
                with self.assertRaises(FolderSelectionError):
                    validate_destination(path, self.app_dir)

    def test_rejects_resolved_traversal(self):
        traversing = self.destination / ".." / self.destination.name
        self.assertTrue(traversing.exists())
        with self.assertRaises(FolderSelectionError):
            validate_destination(traversing, self.app_dir)

    def test_rejects_relative_paths(self):
        with self.assertRaises(FolderSelectionError):
            validate_destination(Path("downloads"), self.app_dir)


if __name__ == "__main__":
    unittest.main()
