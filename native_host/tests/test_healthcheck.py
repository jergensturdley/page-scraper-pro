import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

from native_host.healthcheck import HealthCheckError, check_health


class HealthCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_launcher(self, data=None, *, delay=0, malformed=False):
        script = self.root / f"host-{len(list(self.root.iterdir()))}.py"
        response = {
            "version": 1,
            "id": "healthcheck",
            "type": "response",
            "payload": {"ok": True, "data": data or {
                "helper_version": "1.0.0",
                "protocol_version": 1,
                "yt_dlp_version": "2026.7.4",
                "ffmpeg_version": "unavailable",
            }},
        }
        script.write_text(
            "import json, struct, sys, time\n"
            "header = sys.stdin.buffer.read(4)\n"
            "length = struct.unpack('<I', header)[0]\n"
            "request = json.loads(sys.stdin.buffer.read(length))\n"
            "assert request == {'version': 1, 'id': 'healthcheck', 'type': 'health', 'payload': {}}\n"
            f"time.sleep({delay!r})\n"
            + ("sys.stdout.buffer.write(b'bad!')\n" if malformed else
               f"payload = json.dumps({response!r}, separators=(',', ':')).encode()\n"
               "sys.stdout.buffer.write(struct.pack('<I', len(payload)) + payload)\n"
               "sys.stdout.buffer.flush()\n")
            + "time.sleep(30)\n",
            encoding="utf-8",
        )
        launcher = self.root / f"launcher-{len(list(self.root.iterdir()))}"
        launcher.write_text(
            f"#!/bin/sh\nexec {json.dumps(sys.executable)} {json.dumps(str(script))}\n",
            encoding="utf-8",
        )
        launcher.chmod(0o700)
        return launcher

    def test_one_framed_health_command_returns_strict_versions_and_tears_down(self):
        launcher = self.make_launcher()
        started = time.monotonic()
        result = check_health(launcher, timeout=2)
        elapsed = time.monotonic() - started
        self.assertEqual(result["helper_version"], "1.0.0")
        self.assertEqual(result["protocol_version"], 1)
        self.assertEqual(result["yt_dlp_version"], "2026.7.4")
        self.assertLess(elapsed, 3)

    def test_health_times_out_after_bounded_wait_and_kills_process_group(self):
        launcher = self.make_launcher(delay=30)
        started = time.monotonic()
        with self.assertRaisesRegex(HealthCheckError, "timed out"):
            check_health(launcher, timeout=0.15)
        self.assertLess(time.monotonic() - started, 2)

    def test_health_kills_descendant_when_session_leader_already_exited(self):
        pid_file = self.root / "descendant.pid"
        response = {
            "version": 1,
            "id": "healthcheck",
            "type": "response",
            "payload": {"ok": True, "data": {
                "helper_version": "1.0.0",
                "protocol_version": 1,
                "yt_dlp_version": "2026.7.4",
                "ffmpeg_version": "unavailable",
            }},
        }
        script = self.root / "exiting-leader.py"
        child_code = (
            "import signal,time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "time.sleep(30)"
        )
        script.write_text(
            "import json, os, struct, subprocess, sys\n"
            "header = sys.stdin.buffer.read(4)\n"
            "length = struct.unpack('<I', header)[0]\n"
            "sys.stdin.buffer.read(length)\n"
            f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}], "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL, close_fds=True)\n"
            f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
            f"payload = json.dumps({response!r}, separators=(',', ':')).encode()\n"
            "sys.stdout.buffer.write(struct.pack('<I', len(payload)) + payload)\n"
            "sys.stdout.buffer.flush()\n"
            "os._exit(0)\n",
            encoding="utf-8",
        )
        launcher = self.root / "exiting-leader"
        launcher.write_text(
            f"#!/bin/sh\nexec {json.dumps(sys.executable)} {json.dumps(str(script))}\n",
            encoding="utf-8",
        )
        launcher.chmod(0o700)

        check_health(launcher, timeout=2)
        child_pid = int(pid_file.read_text("utf-8"))
        alive = True
        for _ in range(50):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.02)
        if alive:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
        self.assertFalse(alive)

    def test_health_rejects_invalid_or_nonfinite_timeouts_before_launch(self):
        launcher = self.make_launcher()
        for timeout in (0, -1, float("nan"), float("inf"), 5.1, True):
            with self.subTest(timeout=timeout):
                with self.assertRaises(HealthCheckError):
                    check_health(launcher, timeout=timeout)

    def test_health_rejects_malformed_frame(self):
        with self.assertRaises(HealthCheckError):
            check_health(self.make_launcher(malformed=True), timeout=1)

    def test_health_rejects_any_version_or_shape_mismatch(self):
        valid = {
            "helper_version": "1.0.0",
            "protocol_version": 1,
            "yt_dlp_version": "2026.7.4",
            "ffmpeg_version": "unavailable",
        }
        bad_values = [
            {**valid, "helper_version": "1.0.1"},
            {**valid, "protocol_version": 2},
            {**valid, "yt_dlp_version": "2026.7.3"},
            {**valid, "extra": "field"},
            {key: value for key, value in valid.items() if key != "ffmpeg_version"},
        ]
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(HealthCheckError):
                    check_health(self.make_launcher(value), timeout=1)

    def test_health_rejects_symlink_or_non_executable_launcher(self):
        launcher = self.make_launcher()
        link = self.root / "link"
        link.symlink_to(launcher)
        with self.assertRaises(HealthCheckError):
            check_health(link, timeout=1)
        launcher.chmod(0o600)
        with self.assertRaises(HealthCheckError):
            check_health(launcher, timeout=1)
