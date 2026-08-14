import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from native_host.config import HelperConfig
from native_host.jobs import JobManager


class RecordingInput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.written = ""
        self.was_flushed = False
        self.was_closed = False

    def write(self, value):
        self.written += value
        return super().write(value)

    def flush(self):
        self.was_flushed = True
        return super().flush()

    def close(self):
        self.was_closed = True
        return super().close()


class FakeProcess:
    next_pid = 4000

    def __init__(self, lines=(), returncode=0, wait_timeout=False):
        self.stdin = RecordingInput()
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.returncode = None
        self.final_returncode = returncode
        self.wait_timeout = wait_timeout
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.wait_timeout and timeout == 2:
            raise subprocess.TimeoutExpired("worker", timeout)
        self.returncode = self.final_returncode
        return self.returncode


class FakeConfig:
    def __init__(self, recent=None):
        self.saved = []
        self.recent = recent

    def load(self):
        return {"destination": None, "recent_job": self.recent}

    def save_recent_job(self, snapshot):
        self.saved.append(snapshot)


def analysis_result(media_id="a" * 24):
    return {
        "media_id": media_id,
        "title": "Public video",
        "provider": "example",
        "duration": 120,
        "thumbnail": "https://example.com/thumb.jpg",
        "subtitles": ["en"],
        "is_playlist": False,
        "playlist_count": 0,
        "live_status": "not_live",
        "available_presets": ["best"],
    }


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.temp_root = Path(self.temporary_directory.name)
        self.events = []
        self.config = FakeConfig()
        self.calls = []
        self.processes = []

    def popen(self, args, **kwargs):
        self.calls.append((args, kwargs))
        process = FakeProcess()
        self.processes.append(process)
        return process

    def manager(self, **kwargs):
        return JobManager(
            config=self.config,
            event_callback=self.events.append,
            popen=kwargs.pop("popen", self.popen),
            temp_root=self.temp_root,
            timeouts=kwargs.pop("timeouts", {"analyze": 60, "download": 60}),
            **kwargs,
        )

    def wait_for(self, predicate):
        deadline = time.time() + 2
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for supervisor")

    def test_accepts_only_one_active_job_and_uses_fixed_worker_command(self):
        process = FakeProcess(lines=[])
        process.stdout = BlockingOutput()
        calls = []
        manager = self.manager(popen=lambda args, **kwargs: calls.append((args, kwargs)) or process)
        self.addCleanup(manager.close)

        first = manager.start_analyze("req_one", "https://example.com/video")
        second = manager.start_analyze("req_two", "https://example.com/other")

        self.assertTrue(first["accepted"])
        self.assertEqual(second["error"]["code"], "busy")
        args, kwargs = calls[0]
        self.assertEqual(args, [sys.executable, "-m", "native_host.worker"])
        self.assertNotEqual(first["job_id"], "req_one")
        self.assertRegex(first["job_id"], r"^job_[a-f0-9]+$")
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["start_new_session"])
        self.assertIs(kwargs["stdin"], subprocess.PIPE)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "strict")
        sent = json.loads(process.stdin.written)
        self.assertEqual(sent, {"operation": "analyze", "url": "https://example.com/video"})
        self.assertTrue(process.stdin.was_flushed)
        self.assertTrue(process.stdin.was_closed)

    def test_valid_worker_events_are_correlated_and_terminal_snapshot_is_saved(self):
        result = analysis_result()
        process = FakeProcess(lines=[
            json.dumps({"type": "phase", "phase": "analyzing"}),
            json.dumps({"type": "completed", "result": result}),
        ])
        manager = self.manager(popen=lambda *args, **kwargs: process)

        accepted = manager.start_analyze("req_analysis", "https://example.com/video")
        self.wait_for(
            lambda: len(self.events) == 2
            and manager.snapshot()["current"] is None
        )

        self.assertEqual([event["id"] for event in self.events], ["req_analysis"] * 2)
        self.assertEqual(self.events[0]["payload"], {
            "job_id": accepted["job_id"], "phase": "analyzing"
        })
        self.assertEqual(self.events[1]["type"], "completed")
        self.assertEqual(self.events[1]["payload"]["result"], result)
        self.assertEqual(self.config.saved[-1]["state"], "completed")
        self.assertNotIn("url", self.config.saved[-1])
        self.assertIsNone(manager.snapshot()["current"])

    def test_wait_error_terminates_group_before_releasing_active_slot(self):
        class WaitErrorProcess(FakeProcess):
            def __init__(inner_self):
                super().__init__(lines=[json.dumps({
                    "type": "completed", "result": analysis_result()
                })])
                inner_self.termination_wait = threading.Event()
                inner_self.allow_exit = threading.Event()

            def wait(inner_self, timeout=None):
                inner_self.wait_calls.append(timeout)
                if len(inner_self.wait_calls) == 1:
                    raise OSError("synthetic wait failure")
                inner_self.termination_wait.set()
                inner_self.allow_exit.wait(1)
                inner_self.returncode = 0
                return 0

        process = WaitErrorProcess()
        manager = self.manager(popen=lambda *args, **kwargs: process)
        self.addCleanup(manager.close)
        with mock.patch("native_host.jobs.os.killpg") as killpg:
            manager.start_analyze("req_wait_error", "https://example.com/video")
            self.assertTrue(process.termination_wait.wait(1))
            second = manager.start_analyze(
                "req_wait_error_two", "https://example.com/other"
            )
            self.assertEqual(second["error"]["code"], "busy")
            killpg.assert_called_with(process.pid, signal.SIGTERM)
            process.allow_exit.set()
            self.wait_for(lambda: bool(self.events))

    def test_terminal_event_keeps_slot_busy_until_worker_is_reaped(self):
        class WaitingProcess(FakeProcess):
            def __init__(inner_self):
                super().__init__(lines=[json.dumps({
                    "type": "completed", "result": analysis_result()
                })])
                inner_self.wait_started = threading.Event()
                inner_self.allow_exit = threading.Event()

            def wait(inner_self, timeout=None):
                inner_self.wait_calls.append(timeout)
                inner_self.wait_started.set()
                inner_self.allow_exit.wait(1)
                inner_self.returncode = 0
                return 0

        process = WaitingProcess()
        manager = self.manager(popen=lambda *args, **kwargs: process)
        self.addCleanup(manager.close)
        first = manager.start_analyze("req_first", "https://example.com/video")
        self.assertTrue(first["accepted"])
        self.assertTrue(process.wait_started.wait(1))

        second = manager.start_analyze("req_second", "https://example.com/other")
        self.assertEqual(second["error"]["code"], "busy")
        self.assertEqual(self.events, [])

        process.allow_exit.set()
        self.wait_for(lambda: bool(self.events))
        self.assertEqual(self.events[-1]["type"], "completed")

    def test_operation_specific_completion_rejects_extra_worker_fields(self):
        result = analysis_result()
        result["raw_formats"] = [{"cookie": "secret"}]
        process = FakeProcess(lines=[json.dumps({
            "type": "completed", "result": result,
        })])
        manager = self.manager(popen=lambda *args, **kwargs: process)

        manager.start_analyze("req_strict", "https://example.com/video")
        self.wait_for(lambda: bool(self.events))

        self.assertEqual(
            self.events[-1]["payload"]["error"]["code"],
            "invalid_worker_output",
        )
        self.assertNotIn("secret", json.dumps(self.events[-1]))

    def test_worker_failure_message_is_replaced_with_host_owned_text(self):
        process = FakeProcess(lines=[json.dumps({
            "type": "failed",
            "error": {"code": "analysis_failed", "message": "token=secret"},
        })])
        manager = self.manager(popen=lambda *args, **kwargs: process)

        manager.start_analyze("req_failure_text", "https://example.com/video")
        self.wait_for(lambda: bool(self.events))

        error = self.events[-1]["payload"]["error"]
        self.assertEqual(error, {
            "code": "analysis_failed", "message": "Media analysis failed"
        })

    def test_recent_snapshot_round_trips_through_real_helper_config(self):
        trusted_root = self.temp_root / "trusted"
        trusted_root.mkdir()
        config = HelperConfig(trusted_root / "native", trusted_root)
        process = FakeProcess(lines=[json.dumps({
            "type": "completed", "result": analysis_result(),
        })])
        manager = JobManager(
            config=config,
            event_callback=self.events.append,
            popen=lambda *args, **kwargs: process,
            temp_root=self.temp_root,
            timeouts={"analyze": 60, "download": 60},
            clock=lambda: 123.5,
        )

        manager.start_analyze("req_persist", "https://example.com/private?token=x")
        self.wait_for(lambda: bool(self.events))

        recent = config.load()["recent_job"]
        self.assertEqual(recent["state"], "completed")
        self.assertEqual(recent["operation"], "analyze")
        self.assertLessEqual(len(recent), 12)
        self.assertNotIn("url", recent)
        self.assertNotIn("result", recent)

    def test_invalid_stdout_becomes_stable_failure(self):
        process = FakeProcess(lines=['{"type":"completed","result":[],"secret":"x"}'])
        manager = self.manager(popen=lambda *args, **kwargs: process)

        accepted = manager.start_analyze("req_invalid", "https://example.com/video")
        self.wait_for(lambda: bool(self.events))

        event = self.events[-1]
        self.assertEqual(event["type"], "failed")
        self.assertEqual(event["payload"]["job_id"], accepted["job_id"])
        self.assertEqual(event["payload"]["error"]["code"], "invalid_worker_output")
        self.assertNotIn("secret", json.dumps(event))
        self.assertEqual(self.config.saved[-1]["state"], "failed")

    def test_completed_line_with_nonzero_exit_becomes_worker_failure(self):
        process = FakeProcess(
            lines=[json.dumps({"type": "completed", "result": analysis_result()})],
            returncode=9,
        )
        manager = self.manager(popen=lambda *args, **kwargs: process)

        manager.start_analyze("req_bad_exit", "https://example.com/video")
        self.wait_for(lambda: bool(self.events))

        self.assertEqual(self.events[-1]["type"], "failed")
        self.assertEqual(
            self.events[-1]["payload"]["error"]["code"], "worker_failed"
        )
        self.assertEqual(self.config.saved[-1]["state"], "failed")

    def test_finalization_finishes_before_a_new_job_can_persist(self):
        class BlockingConfig(FakeConfig):
            def __init__(inner_self):
                super().__init__()
                inner_self.entered = threading.Event()
                inner_self.release = threading.Event()

            def save_recent_job(inner_self, snapshot):
                if not inner_self.saved:
                    inner_self.entered.set()
                    inner_self.release.wait(1)
                super().save_recent_job(snapshot)

        config = BlockingConfig()
        processes = [
            FakeProcess(lines=[json.dumps({
                "type": "completed", "result": analysis_result("a" * 24),
            })]),
            FakeProcess(lines=[json.dumps({
                "type": "completed", "result": analysis_result("b" * 24),
            })]),
        ]
        manager = JobManager(
            config=config,
            event_callback=self.events.append,
            popen=lambda *args, **kwargs: processes.pop(0),
            temp_root=self.temp_root,
            timeouts={"analyze": 60, "download": 60},
        )
        self.addCleanup(manager.close)
        first = manager.start_analyze("req_first_save", "https://example.com/one")
        self.assertTrue(first["accepted"])
        self.assertTrue(config.entered.wait(1))

        blocked = manager.start_analyze("req_second_save", "https://example.com/two")
        self.assertEqual(blocked["error"]["code"], "busy")
        config.release.set()
        self.wait_for(lambda: len(self.events) == 1 and manager.snapshot()["current"] is None)

        second = manager.start_analyze("req_second_save", "https://example.com/two")
        self.assertTrue(second["accepted"])
        self.wait_for(lambda: len(self.events) == 2 and manager.snapshot()["current"] is None)
        self.assertEqual(
            [snapshot["request_id"] for snapshot in config.saved],
            ["req_first_save", "req_second_save"],
        )
        self.assertEqual(
            [event["id"] for event in self.events],
            ["req_first_save", "req_second_save"],
        )

    def test_nonzero_process_without_terminal_event_is_stable_failure(self):
        process = FakeProcess(lines=[], returncode=9)
        manager = self.manager(popen=lambda *args, **kwargs: process)

        manager.start_analyze("req_failure", "https://example.com/video")
        self.wait_for(lambda: bool(self.events))

        self.assertEqual(self.events[-1]["payload"]["error"]["code"], "worker_failed")

    def test_download_creates_private_empty_host_temp_and_removes_it(self):
        process = FakeProcess(returncode=1)
        process.stdout = GatedOutput([json.dumps({"type": "failed", "error": {
            "code": "download_failed", "message": "Media download failed"
        }})])
        manager = self.manager(popen=lambda *args, **kwargs: process)

        accepted = manager.start_download("req_download", {
            "url": "https://example.com/video", "media_id": "media_1",
            "preset": "best", "destination": str(self.temp_root),
        })
        sent = json.loads(process.stdin.written)
        temp_dir = Path(sent["temp_dir"])
        self.assertTrue(accepted["accepted"])
        self.assertEqual(sent["operation"], "download")
        self.assertEqual(oct(temp_dir.stat().st_mode & 0o777), "0o700")
        self.assertEqual(list(temp_dir.iterdir()), [])
        process.stdout.release.set()
        self.wait_for(lambda: bool(self.events))
        self.assertFalse(temp_dir.exists())

    def test_download_rejects_a_caller_temp_path(self):
        manager = self.manager()
        response = manager.start_download("req_download", {
            "url": "https://example.com/video", "media_id": "media_1",
            "preset": "best", "destination": str(self.temp_root),
            "temp_dir": "/tmp/caller-controlled",
        })
        self.assertEqual(response["error"]["code"], "invalid_job")
        self.assertEqual(self.calls, [])

    def test_cancel_terminates_group_escalates_cleans_and_emits_once(self):
        partial_process = FakeProcess(wait_timeout=True)
        partial_process.stdout = BlockingOutput()
        manager = self.manager(popen=lambda *args, **kwargs: partial_process)
        accepted = manager.start_download("req_cancel", {
            "url": "https://example.com/video", "media_id": "media_1",
            "preset": "best", "destination": str(self.temp_root),
        })
        sent = json.loads(partial_process.stdin.written)
        temp_dir = Path(sent["temp_dir"])
        (temp_dir / "partial").write_bytes(b"partial")

        with mock.patch("native_host.jobs.os.killpg") as killpg:
            result = manager.cancel(accepted["job_id"])

        self.assertTrue(result["cancelled"])
        self.assertEqual(killpg.call_args_list, [
            mock.call(partial_process.pid, signal.SIGTERM),
            mock.call(partial_process.pid, signal.SIGKILL),
        ])
        self.assertIn(2, partial_process.wait_calls)
        self.assertFalse(temp_dir.exists())
        self.assertEqual(len([e for e in self.events if e["type"] == "cancelled"]), 1)
        self.assertEqual(self.config.saved[-1]["state"], "cancelled")

    def test_download_progress_is_strictly_validated_and_given_a_phase(self):
        process = FakeProcess(lines=[
            json.dumps({
                "type": "progress", "phase": "processing",
                "percent": 25, "speed": 10,
            }),
            json.dumps({
                "type": "completed",
                "result": {
                    "filename": "safe.mp4", "directory": str(self.temp_root),
                },
            }),
        ])
        manager = self.manager(popen=lambda *args, **kwargs: process)

        manager.start_download("req_progress", {
            "url": "https://example.com/video", "media_id": "media_1",
            "preset": "best", "destination": str(self.temp_root),
        })
        self.wait_for(lambda: len(self.events) == 2)

        self.assertEqual(self.events[0]["type"], "progress")
        self.assertEqual(self.events[0]["payload"]["phase"], "processing")
        self.assertEqual(self.events[0]["payload"]["percent"], 25)

    def test_analyze_and_download_timeouts_terminate_process_groups(self):
        for operation in ("analyze", "download"):
            with self.subTest(operation=operation):
                process = FakeProcess()
                process.stdout = BlockingOutput()
                manager = self.manager(
                    popen=lambda *args, **kwargs: process,
                    timeouts={"analyze": 0.02, "download": 0.02},
                )
                with mock.patch("native_host.jobs.os.killpg") as killpg:
                    if operation == "analyze":
                        accepted = manager.start_analyze(
                            "req_timeout", "https://example.com/video"
                        )
                    else:
                        accepted = manager.start_download("req_timeout", {
                            "url": "https://example.com/video",
                            "media_id": "media_1", "preset": "best",
                            "destination": str(self.temp_root),
                        })
                    self.wait_for(lambda: bool(self.events))
                self.assertTrue(accepted["accepted"])
                killpg.assert_called_with(process.pid, signal.SIGTERM)
                self.assertEqual(self.events[-1]["type"], "failed")
                self.assertEqual(
                    self.events[-1]["payload"]["error"]["code"], "timeout"
                )
                self.events.clear()
                manager.close()

    def test_close_waits_for_terminal_cleanup_to_finish(self):
        process = FakeProcess(lines=[json.dumps({
            "type": "completed", "result": analysis_result(),
        })])
        manager = self.manager(popen=lambda *args, **kwargs: process)
        cleanup_entered = threading.Event()
        release_cleanup = threading.Event()
        original_cleanup = manager._cleanup_temp

        def blocking_cleanup(temp_dir):
            cleanup_entered.set()
            release_cleanup.wait(1)
            original_cleanup(temp_dir)

        manager._cleanup_temp = blocking_cleanup
        manager.start_analyze("req_close_cleanup", "https://example.com/video")
        self.assertTrue(cleanup_entered.wait(1))
        close_finished = threading.Event()
        closer = threading.Thread(
            target=lambda: (manager.close(), close_finished.set()), daemon=True
        )
        closer.start()
        self.assertFalse(close_finished.wait(0.05))

        release_cleanup.set()
        self.assertTrue(close_finished.wait(1))
        self.assertIsNone(manager.snapshot()["current"])

    def test_close_cancels_active_job(self):
        process = FakeProcess()
        process.stdout = BlockingOutput()
        manager = self.manager(popen=lambda *args, **kwargs: process)
        accepted = manager.start_analyze("req_close", "https://example.com/video")
        with mock.patch("native_host.jobs.os.killpg") as killpg:
            manager.close()
        killpg.assert_called_with(process.pid, signal.SIGTERM)
        self.assertEqual(self.events[-1]["type"], "cancelled")
        self.assertEqual(self.events[-1]["payload"]["job_id"], accepted["job_id"])


class GatedOutput:
    def __init__(self, lines):
        self.stream = io.StringIO("".join(line + "\n" for line in lines))
        self.release = threading.Event()

    def readline(self, *args):
        self.release.wait(5)
        return self.stream.readline(*args)

    def close(self):
        self.release.set()
        self.stream.close()


class BlockingOutput:
    def __init__(self):
        self.closed = False
        self.release = threading.Event()

    def readline(self, *args):
        self.release.wait(5)
        return ""

    def close(self):
        self.closed = True
        self.release.set()
