import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from native_host.constants import HELPER_VERSION, PROTOCOL_VERSION
from native_host.host import HostDispatcher, configure_logging, main
from native_host.protocol import EndOfStream, read_message, write_message


class FakeConfig:
    def __init__(self, destination=None, recent=None):
        self.destination = destination
        self.recent = recent
        self.saved_destinations = []

    def load(self):
        return {
            "destination": None if self.destination is None else str(self.destination),
            "recent_job": self.recent,
        }

    def get_destination(self):
        return self.destination

    def save_destination(self, destination):
        self.destination = destination
        self.saved_destinations.append(destination)


class FakeJobs:
    def __init__(self):
        self.analyzes = []
        self.downloads = []
        self.cancels = []
        self.closed = False
        self.current = None
        self.recent = None

    def start_analyze(self, request_id, url):
        self.analyzes.append((request_id, url))
        self.current = {"job_id": "job_analyze", "operation": "analyze", "state": "running"}
        return {"accepted": True, "job_id": "job_analyze"}

    def start_download(self, request_id, job):
        self.downloads.append((request_id, job))
        self.current = {"job_id": "job_download", "operation": "download", "state": "running"}
        return {"accepted": True, "job_id": "job_download"}

    def cancel(self, job_id):
        self.cancels.append(job_id)
        self.current = None
        return {"cancelled": True, "job_id": job_id}

    def snapshot(self):
        return {"current": self.current, "recent": self.recent}

    def close(self):
        self.closed = True


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def error(self, message):
        self.messages.append(message)

    def warning(self, message):
        self.messages.append(message)

    def info(self, message):
        self.messages.append(message)


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


def command(request_id, command_type, payload=None):
    return {
        "version": PROTOCOL_VERSION,
        "id": request_id,
        "type": command_type,
        "payload": {} if payload is None else payload,
    }


def response_data(event):
    if event["payload"]["ok"]:
        return event["payload"]["data"]
    return event["payload"]["error"]


def frame_messages(*messages):
    stream = io.BytesIO()
    for message in messages:
        write_message(stream, message)
    stream.seek(0)
    return stream


def decode_messages(stream):
    stream.seek(0)
    values = []
    while True:
        try:
            values.append(read_message(stream))
        except EndOfStream:
            return values


class HostDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.app_dir = Path(self.temporary_directory.name) / "private"
        self.destination = Path(self.temporary_directory.name) / "Downloads"
        self.destination.mkdir()
        self.config = FakeConfig()
        self.jobs = FakeJobs()
        self.clock = Clock()
        self.events = []
        self.validations = []
        self.logger = RecordingLogger()
        self.dispatcher = HostDispatcher(
            config=self.config,
            jobs=self.jobs,
            app_dir=self.app_dir,
            choose_directory_func=lambda **kwargs: self.destination,
            validate_destination_func=self.validate_destination,
            yt_dlp_version_func=lambda: "2026.7.4",
            ffmpeg_version_func=lambda: "7.1",
            clock=self.clock,
            event_writer=self.events.append,
            logger=self.logger,
        )

    def validate_destination(self, destination, app_dir):
        self.validations.append((destination, app_dir))
        if destination != self.destination:
            raise ValueError("unsafe destination /Users/private/secret")
        return destination

    def assert_error(self, result, code):
        self.assertEqual(result["type"], "response")
        self.assertFalse(result["payload"]["ok"])
        self.assertEqual(result["payload"]["error"]["code"], code)

    def complete_analysis(self, request_id="req_analyze", media_id="media_1", presets=None):
        presets = ["best", "720p"] if presets is None else presets
        event = {
            "version": PROTOCOL_VERSION,
            "id": request_id,
            "type": "completed",
            "payload": {
                "job_id": "job_analyze",
                "result": {
                    "media_id": media_id,
                    "title": "Public video",
                    "available_presets": presets,
                },
            },
        }
        self.dispatcher.handle_job_event(event)
        return event

    def test_health_returns_exact_dependency_versions(self):
        event = self.dispatcher.dispatch(command("req_health", "health"))
        self.assertEqual(event, {
            "version": PROTOCOL_VERSION,
            "id": "req_health",
            "type": "response",
            "payload": {"ok": True, "data": {
                "helper_version": HELPER_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "yt_dlp_version": "2026.7.4",
                "ffmpeg_version": "7.1",
            }},
        })

    def test_analyze_starts_job_and_only_completion_populates_host_cache(self):
        started = self.dispatcher.dispatch(command(
            "req_analyze", "analyze", {"url": "https://EXAMPLE.com/video#fragment"}
        ))
        self.assertEqual(response_data(started), {"accepted": True, "job_id": "job_analyze"})
        self.assertEqual(self.jobs.analyzes, [("req_analyze", "https://example.com/video")])

        before = self.dispatcher.dispatch(command(
            "before", "download", {"media_id": "media_1", "preset": "best", "confirmed": True}
        ))
        self.assert_error(before, "unknown_media")

        completed = self.complete_analysis()
        self.assertEqual(self.events[-1], completed)

    def test_duplicate_inflight_request_id_cannot_replace_pending_analysis(self):
        first = self.dispatcher.dispatch(command(
            "same", "analyze", {"url": "https://example.com/first"}
        ))
        second = self.dispatcher.dispatch(command(
            "same", "analyze", {"url": "https://example.com/second"}
        ))
        self.assertTrue(first["payload"]["ok"])
        self.assert_error(second, "busy")
        self.assertEqual(self.jobs.analyzes, [
            ("same", "https://example.com/first")
        ])

        self.complete_analysis(request_id="same")
        self.config.destination = self.destination
        download = self.dispatcher.dispatch(command(
            "download_after_same", "download", {
                "media_id": "media_1", "preset": "best", "confirmed": True,
            }
        ))
        self.assertTrue(download["payload"]["ok"])
        self.assertEqual(
            self.jobs.downloads[-1][1]["url"], "https://example.com/first"
        )

    def test_download_uses_only_cached_url_and_revalidated_config_destination(self):
        self.config.destination = self.destination
        self.dispatcher.dispatch(command(
            "req_analyze", "analyze", {"url": "https://example.com/watch?token=secret"}
        ))
        self.complete_analysis()

        result = self.dispatcher.dispatch(command(
            "req_download", "download",
            {"media_id": "media_1", "preset": "720p", "confirmed": True},
        ))

        self.assertEqual(response_data(result), {"accepted": True, "job_id": "job_download"})
        self.assertEqual(self.jobs.downloads, [("req_download", {
            "url": "https://example.com/watch?token=secret",
            "media_id": "media_1",
            "preset": "720p",
            "destination": str(self.destination),
        })])
        self.assertEqual(self.validations[-1], (self.destination, self.app_dir))

    def test_download_rejects_confirmation_preset_destination_unknown_and_stale_media(self):
        self.dispatcher.dispatch(command(
            "req_analyze", "analyze", {"url": "https://example.com/video"}
        ))
        self.complete_analysis()
        cases = [
            ("not_confirmed", {"media_id": "media_1", "preset": "best"}, "confirmation_required"),
            ("false_confirmed", {"media_id": "media_1", "preset": "best", "confirmed": False}, "confirmation_required"),
            ("not_advertised", {"media_id": "media_1", "preset": "1080p", "confirmed": True}, "preset_unavailable"),
            ("unknown", {"media_id": "other", "preset": "best", "confirmed": True}, "unknown_media"),
            ("no_destination", {"media_id": "media_1", "preset": "best", "confirmed": True}, "destination_required"),
        ]
        for request_id, payload, code in cases:
            with self.subTest(code=code):
                self.assert_error(self.dispatcher.dispatch(command(request_id, "download", payload)), code)

        self.clock.value += 1801
        stale = self.dispatcher.dispatch(command(
            "stale", "download", {"media_id": "media_1", "preset": "best", "confirmed": True}
        ))
        self.assert_error(stale, "stale_media")
        self.assertEqual(self.jobs.downloads, [])

    def test_analysis_cache_is_lru_bounded_to_32_records(self):
        self.config.destination = self.destination
        for index in range(33):
            request_id = f"req_{index}"
            self.dispatcher.dispatch(command(
                request_id, "analyze", {"url": f"https://example.com/{index}"}
            ))
            self.complete_analysis(request_id, f"media_{index}", ["best"])
        old = self.dispatcher.dispatch(command(
            "old", "download", {"media_id": "media_0", "preset": "best", "confirmed": True}
        ))
        self.assert_error(old, "unknown_media")
        latest = self.dispatcher.dispatch(command(
            "latest", "download", {"media_id": "media_32", "preset": "best", "confirmed": True}
        ))
        self.assertTrue(latest["payload"]["ok"])

    def test_choose_directory_revalidates_before_persisting_and_returns_display_path(self):
        result = self.dispatcher.dispatch(command("req_choose", "choose_directory"))
        self.assertEqual(response_data(result), {"destination": str(self.destination)})
        self.assertEqual(self.config.saved_destinations, [self.destination])
        self.assertEqual(self.validations, [(self.destination, self.app_dir)])

    def test_cancel_and_status_have_exact_host_owned_shapes(self):
        self.config.destination = self.destination
        self.jobs.current = {"job_id": "job_download", "operation": "download", "state": "running"}
        self.jobs.recent = {"job_id": "job_old", "state": "completed"}

        status = response_data(self.dispatcher.dispatch(command("req_status", "status")))
        self.assertEqual(status, {
            "helper_version": HELPER_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "yt_dlp_version": "2026.7.4",
            "ffmpeg_version": "7.1",
            "destination": str(self.destination),
            "current_job": self.jobs.current,
            "recent_job": self.jobs.recent,
        })
        cancelled = self.dispatcher.dispatch(command(
            "req_cancel", "cancel", {"job_id": "job_download"}
        ))
        self.assertEqual(response_data(cancelled), {"cancelled": True, "job_id": "job_download"})
        self.assertEqual(self.jobs.cancels, ["job_download"])

    def test_dependency_exception_becomes_stable_error_and_log_context_is_redacted(self):
        self.dispatcher.choose_directory_func = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("token=abc https://example.com/watch?secret=yes /Users/alice/private")
        )
        result = self.dispatcher.dispatch(command("req_choose", "choose_directory"))
        self.assert_error(result, "folder_unavailable")
        serialized = "\n".join(self.logger.messages)
        self.assertNotIn("secret=yes", serialized)
        self.assertNotIn("/Users/alice/private", serialized)
        self.assertNotIn("token=abc", json.dumps(result))


class HostLoopTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.destination = self.root / "Downloads"
        self.destination.mkdir()
        self.config = FakeConfig(self.destination)
        self.jobs = FakeJobs()
        self.dispatcher = HostDispatcher(
            config=self.config,
            jobs=self.jobs,
            app_dir=self.root / "private",
            choose_directory_func=lambda **kwargs: self.destination,
            validate_destination_func=lambda path, app_dir: path,
            yt_dlp_version_func=lambda: "test-ytdlp",
            ffmpeg_version_func=lambda: "test-ffmpeg",
            clock=lambda: 10,
            logger=RecordingLogger(),
        )

    def test_multiple_framed_commands_keep_request_correlation_and_active_status(self):
        source = frame_messages(
            command("one", "health"),
            command("two", "analyze", {"url": "https://example.com/video"}),
            command("three", "status"),
        )
        output = io.BytesIO()

        code = main(stdin=source, stdout=output, dispatcher=self.dispatcher)

        messages = decode_messages(output)
        self.assertEqual(code, 0)
        self.assertEqual([message["id"] for message in messages], ["one", "two", "three"])
        self.assertEqual(response_data(messages[2])["current_job"]["job_id"], "job_analyze")
        self.assertTrue(self.jobs.closed)

    def test_popup_reopen_status_recovers_an_active_fake_download(self):
        self.dispatcher.dispatch(command(
            "prime_analyze", "analyze", {"url": "https://example.com/video"}
        ))
        self.dispatcher.handle_job_event({
            "version": PROTOCOL_VERSION,
            "id": "prime_analyze",
            "type": "completed",
            "payload": {
                "job_id": "job_analyze",
                "result": {
                    "media_id": "media_1",
                    "title": "Public video",
                    "available_presets": ["best"],
                },
            },
        })
        source = frame_messages(
            command("start_download", "download", {
                "media_id": "media_1", "preset": "best", "confirmed": True,
            }),
            command("popup_reopen", "status"),
        )
        output = io.BytesIO()

        main(stdin=source, stdout=output, dispatcher=self.dispatcher)

        messages = decode_messages(output)
        self.assertEqual(
            [message["id"] for message in messages],
            ["start_download", "popup_reopen"],
        )
        status = response_data(messages[1])
        self.assertEqual(status["current_job"]["job_id"], "job_download")
        self.assertEqual(status["current_job"]["operation"], "download")
        self.assertEqual(self.jobs.downloads[0][1]["url"], "https://example.com/video")

    def test_framed_choose_directory_uses_injected_finder_and_persists(self):
        self.config.destination = None
        source = frame_messages(command("choose", "choose_directory"))
        output = io.BytesIO()

        main(stdin=source, stdout=output, dispatcher=self.dispatcher)

        messages = decode_messages(output)
        self.assertEqual(response_data(messages[0]), {
            "destination": str(self.destination)
        })
        self.assertEqual(self.config.saved_destinations, [self.destination])

    def test_schema_error_writes_safe_correlated_error_and_continues(self):
        invalid = command("bad_request", "health")
        invalid["payload"] = {"extra": True}
        source = frame_messages(invalid, command("good_request", "health"))
        output = io.BytesIO()

        main(stdin=source, stdout=output, dispatcher=self.dispatcher)

        messages = decode_messages(output)
        self.assertEqual([item["id"] for item in messages], ["bad_request", "good_request"])
        self.assertFalse(messages[0]["payload"]["ok"])
        self.assertTrue(messages[1]["payload"]["ok"])

    def test_malformed_frame_emits_safe_error_then_terminates_without_reading_followup(self):
        malformed = struct.pack("<I", 5) + b"{nope"
        followup_stream = frame_messages(command("never", "health"))
        source = io.BytesIO(malformed + followup_stream.read())
        output = io.BytesIO()

        code = main(stdin=source, stdout=output, dispatcher=self.dispatcher)

        messages = decode_messages(output)
        self.assertEqual(code, 1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["id"], "host_error")
        self.assertEqual(messages[0]["payload"]["error"]["code"], "invalid_frame")
        self.assertTrue(self.jobs.closed)

    def test_async_event_uses_same_writer_and_follows_start_response(self):
        class ImmediateJobs(FakeJobs):
            def __init__(self):
                super().__init__()
                self.callback = None

            def start_analyze(inner_self, request_id, url):
                result = super(ImmediateJobs, inner_self).start_analyze(request_id, url)
                self.dispatcher.handle_job_event({
                    "version": PROTOCOL_VERSION,
                    "id": request_id,
                    "type": "progress",
                    "payload": {"job_id": "job_analyze", "phase": "analyzing"},
                })
                return result

        jobs = ImmediateJobs()
        self.dispatcher.jobs = jobs
        source = frame_messages(command("scan", "analyze", {"url": "https://example.com/video"}))
        output = io.BytesIO()

        main(stdin=source, stdout=output, dispatcher=self.dispatcher)

        messages = decode_messages(output)
        self.assertEqual([item["type"] for item in messages], ["response", "progress"])


class LoggingTests(unittest.TestCase):
    def test_configure_logging_refuses_symlink_log_path_and_uses_rotation_limits(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_dir = Path(temporary_directory) / "private"
            app_dir.mkdir(mode=0o700)
            target = Path(temporary_directory) / "target.log"
            target.write_text("outside")
            (app_dir / "host.log").symlink_to(target)
            with self.assertRaises(ValueError):
                configure_logging(app_dir)

            (app_dir / "host.log").unlink()
            logger = configure_logging(app_dir)
            handler = logger.handlers[0]
            self.assertEqual(handler.maxBytes, 512 * 1024)
            self.assertEqual(handler.backupCount, 2)
            handler.close()
            logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
