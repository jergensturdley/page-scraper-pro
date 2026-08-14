import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from native_host.worker import AnalysisError, download_media, run_worker


class FakeYoutubeDL:
    instances = []
    error = None
    mutate = None
    source_outside = None

    def __init__(self, options):
        self.options = options
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download=False):
        self.options["progress_hooks"][0]({
            "status": "downloading",
            "downloaded_bytes": 75,
            "total_bytes": None,
            "total_bytes_estimate": 100,
            "speed": float("inf"),
            "eta": -4,
            "filename": "/private/source.mp4",
            "url": "https://secret.example/token",
        })
        self.options["progress_hooks"][0]({
            "status": "downloading",
            "downloaded_bytes": 200,
            "total_bytes": 100,
            "speed": 10**30,
            "eta": 10**30,
        })
        self.options["postprocessor_hooks"][0]({
            "status": "started",
            "postprocessor": "FFmpegMergerPP",
            "filename": "/private/source.mp4",
        })
        temp_dir = Path(self.options["outtmpl"]).parent
        source = self.source_outside or (temp_dir / "source.mp4")
        source.write_bytes(b"media")
        (temp_dir / "source.part").write_bytes(b"partial")
        if type(self).mutate is not None:
            type(self).mutate()
        if self.error is not None:
            raise self.error
        self.options["progress_hooks"][0]({"status": "finished", "filename": str(source)})
        return {
            "id": "fixture-id",
            "title": "My Video",
            "ext": "mp4",
            "requested_downloads": [{"filepath": str(source)}],
        }


class DownloadWorkerTests(unittest.TestCase):
    def setUp(self):
        FakeYoutubeDL.instances = []
        FakeYoutubeDL.error = None
        FakeYoutubeDL.mutate = None
        FakeYoutubeDL.source_outside = None

    def test_download_emits_sanitized_progress_and_moves_without_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()
            (destination / "My Video.mp4").write_bytes(b"old")
            events = []

            real_link = os.link
            raced = False

            def race_first_atomic_link(
                source, target, *, src_dir_fd=None, dst_dir_fd=None,
                follow_symlinks=True,
            ):
                nonlocal raced
                if not raced:
                    try:
                        os.stat(target, dir_fd=dst_dir_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        raced = True
                        descriptor = os.open(
                            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                            dir_fd=dst_dir_fd,
                        )
                        os.write(descriptor, b"racer")
                        os.close(descriptor)
                return real_link(
                    source, target, src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks,
                )

            with mock.patch(
                "native_host.worker.os.link", side_effect=race_first_atomic_link
            ):
                result = download_media(
                    "https://media.example/video",
                    "media_abc",
                    "720p",
                    temp_dir,
                    destination,
                    events.append,
                    FakeYoutubeDL,
                )

            self.assertEqual(result, {
                "filename": "My Video (2).mp4",
                "directory": str(destination.resolve()),
            })
            self.assertEqual((destination / result["filename"]).read_bytes(), b"media")
            self.assertEqual((destination / "My Video.mp4").read_bytes(), b"old")
            self.assertEqual((destination / "My Video (1).mp4").read_bytes(), b"racer")
            self.assertFalse(any(temp_dir.iterdir()))
            self.assertEqual(
                [event["phase"] for event in events],
                ["downloading", "downloading", "processing", "finalizing"],
            )
            for event in events:
                self.assertEqual(event["type"], "progress")
                self.assertTrue({"type", "phase", "percent", "speed", "eta"} >= set(event))
                encoded = json.dumps(event)
                self.assertNotIn("secret.example", encoded)
                self.assertNotIn("source.mp4", encoded)
            self.assertEqual(events[0]["percent"], 75)
            self.assertNotIn("speed", events[0])
            self.assertNotIn("eta", events[0])
            self.assertEqual(events[1]["percent"], 100)
            self.assertEqual(events[1]["speed"], 10**15)
            self.assertEqual(events[1]["eta"], 3_155_760_000)
            self.assertEqual(FakeYoutubeDL.instances[0].options["format"], "bv*[height<=720]+ba/b[height<=720]/b")

    def test_rejects_nonempty_untrusted_temp_directory_without_deleting_contents(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()
            unrelated = temp_dir / "keep.txt"
            unrelated.write_bytes(b"keep")

            with self.assertRaises(AnalysisError) as raised:
                download_media(
                    "https://media.example/video", "media_abc", "best",
                    temp_dir, destination, lambda event: None, FakeYoutubeDL,
                )

            self.assertEqual(raised.exception.code, "invalid_job")
            self.assertEqual(unrelated.read_bytes(), b"keep")
            self.assertEqual(list(destination.iterdir()), [])

    def test_ffmpeg_failure_is_stable_and_partial_files_are_cleaned(self):
        FakeYoutubeDL.error = RuntimeError("ffmpeg not found; required to merge formats")
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()

            with self.assertRaises(AnalysisError) as raised:
                download_media(
                    "https://media.example/video", "media_abc", "best",
                    temp_dir, destination, lambda event: None, FakeYoutubeDL,
                )

            self.assertEqual(raised.exception.code, "ffmpeg_required")
            self.assertFalse(any(temp_dir.iterdir()))

    def test_generic_extractor_failure_is_stable_and_partial_files_are_cleaned(self):
        FakeYoutubeDL.error = RuntimeError("synthetic extractor failure")
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()

            with self.assertRaises(AnalysisError) as raised:
                download_media(
                    "https://media.example/video", "media_abc", "best",
                    temp_dir, destination, lambda event: None, FakeYoutubeDL,
                )

            self.assertEqual(raised.exception.code, "download_failed")
            self.assertFalse(any(temp_dir.iterdir()))
            self.assertEqual(list(destination.iterdir()), [])

    def test_revalidates_destination_after_download_and_rejects_symlink_swap(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            moved_destination = root_path / "downloads-old"
            outside = root_path / "outside"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()
            outside.mkdir()

            def swap_destination():
                destination.rename(moved_destination)
                destination.symlink_to(outside, target_is_directory=True)

            FakeYoutubeDL.mutate = swap_destination
            with self.assertRaises(AnalysisError) as raised:
                download_media(
                    "https://media.example/video", "media_abc", "best",
                    temp_dir, destination, lambda event: None, FakeYoutubeDL,
                )

            self.assertEqual(raised.exception.code, "unsafe_destination")
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse(any(temp_dir.iterdir()))

    def test_does_not_delete_or_accept_replacement_after_atomic_link(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()
            real_link = os.link

            def replace_created_link(
                source, target, *, src_dir_fd=None, dst_dir_fd=None,
                follow_symlinks=True,
            ):
                result = real_link(
                    source, target, src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks,
                )
                os.unlink(target, dir_fd=dst_dir_fd)
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                    dir_fd=dst_dir_fd,
                )
                os.write(descriptor, b"replacement")
                os.close(descriptor)
                return result

            with mock.patch(
                "native_host.worker.os.link", side_effect=replace_created_link
            ):
                with self.assertRaises(AnalysisError) as raised:
                    download_media(
                        "https://media.example/video", "media_abc", "best",
                        temp_dir, destination, lambda event: None, FakeYoutubeDL,
                    )

            self.assertEqual(raised.exception.code, "unsafe_destination")
            self.assertEqual(
                (destination / "My Video.mp4").read_bytes(), b"replacement"
            )
            self.assertFalse(any(temp_dir.iterdir()))

    def test_rejects_destination_that_becomes_unwritable_before_move(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()

            def make_unwritable():
                destination.chmod(0o500)

            FakeYoutubeDL.mutate = make_unwritable
            try:
                with self.assertRaises(AnalysisError) as raised:
                    download_media(
                        "https://media.example/video", "media_abc", "best",
                        temp_dir, destination, lambda event: None, FakeYoutubeDL,
                    )
                self.assertEqual(raised.exception.code, "unsafe_destination")
                self.assertEqual(list(destination.iterdir()), [])
            finally:
                destination.chmod(0o700)

    def test_rejects_downloaded_file_outside_trusted_temp_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            temp_dir = root_path / "job"
            destination = root_path / "downloads"
            outside = root_path / "outside.mp4"
            temp_dir.mkdir(mode=0o700)
            destination.mkdir()
            FakeYoutubeDL.source_outside = outside

            with self.assertRaises(AnalysisError) as raised:
                download_media(
                    "https://media.example/video", "media_abc", "audio",
                    temp_dir, destination, lambda event: None, FakeYoutubeDL,
                )

            self.assertEqual(raised.exception.code, "unsafe_download_output")
            self.assertEqual(list(destination.iterdir()), [])
            self.assertTrue(outside.exists())


class DownloadWorkerEventModeTests(unittest.TestCase):
    def test_download_cli_emits_progress_then_completed(self):
        payload = {
            "operation": "download",
            "url": "https://media.example/video",
            "media_id": "media_abc",
            "preset": "720p",
            "temp_dir": "/trusted/generated/job",
            "destination": "/trusted/downloads",
        }
        output = io.StringIO()

        def fake_download(url, media_id, preset, temp_dir, destination, emit):
            self.assertEqual(temp_dir, Path("/trusted/generated/job"))
            self.assertEqual(destination, Path("/trusted/downloads"))
            emit({"type": "progress", "phase": "downloading", "percent": 50})
            return {"filename": "video.mp4", "directory": "/trusted/downloads"}

        with mock.patch("native_host.worker.download_media", side_effect=fake_download):
            exit_code = run_worker(io.StringIO(json.dumps(payload) + "\n"), output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [json.loads(line) for line in output.getvalue().splitlines()],
            [
                {"type": "progress", "phase": "downloading", "percent": 50},
                {"type": "completed", "result": {"filename": "video.mp4", "directory": "/trusted/downloads"}},
            ],
        )

    def test_download_cli_emits_cancelled_for_cancelled_download(self):
        payload = {
            "operation": "download",
            "url": "https://media.example/video",
            "media_id": "media_abc",
            "preset": "720p",
            "temp_dir": "/trusted/generated/job",
            "destination": "/trusted/downloads",
        }
        output = io.StringIO()
        with mock.patch(
            "native_host.worker.download_media",
            side_effect=AnalysisError("cancelled", "Download cancelled"),
        ):
            exit_code = run_worker(io.StringIO(json.dumps(payload) + "\n"), output)
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            [json.loads(line) for line in output.getvalue().splitlines()],
            [
                {"type": "cancelled"},
            ],
        )

    def test_download_cli_rejects_extra_fields(self):
        payload = {
            "operation": "download",
            "url": "https://media.example/video",
            "media_id": "media_abc",
            "preset": "720p",
            "temp_dir": "/trusted/generated/job",
            "destination": "/trusted/downloads",
            "format": "evil",
        }
        output = io.StringIO()
        exit_code = run_worker(io.StringIO(json.dumps(payload) + "\n"), output)
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_job")


if __name__ == "__main__":
    unittest.main()
