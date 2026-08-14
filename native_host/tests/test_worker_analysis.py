import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import unittest
import urllib.request
from unittest import mock

from native_host.constants import MAX_MESSAGE_BYTES
from native_host.worker import (
    AnalysisError,
    PRESET_SELECTORS,
    analyze_url,
    available_presets,
    normalize_media_info,
    run_worker,
)


_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "public_info.json").read_text()
)


class RecordingYoutubeDL:
    info = _FIXTURE["media"]
    exception = None
    redirect_url = None
    resolve_host = None
    instances = []

    def __init__(self, options):
        self.options = options
        self.extract_calls = []
        self.urlopen_calls = []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def urlopen(self, request):
        self.urlopen_calls.append(request)
        return object()

    def extract_info(self, url, download=False):
        self.extract_calls.append((url, download))
        if self.resolve_host:
            socket.getaddrinfo(self.resolve_host, 443, type=socket.SOCK_STREAM)
        if self.redirect_url:
            urllib.request.HTTPRedirectHandler().redirect_request(
                None, None, 302, "Found", {}, self.redirect_url
            )
        if self.exception:
            raise self.exception
        return copy.deepcopy(self.info)


class UnsupportedError(Exception):
    pass


class DRMError(Exception):
    pass


class WorkerAnalysisTests(unittest.TestCase):
    def setUp(self):
        RecordingYoutubeDL.instances = []
        RecordingYoutubeDL.info = _FIXTURE["media"]
        RecordingYoutubeDL.exception = None
        RecordingYoutubeDL.redirect_url = None
        RecordingYoutubeDL.resolve_host = None

    def test_analyzer_uses_cookie_free_non_downloading_options_once(self):
        result = analyze_url(
            "HTTPS://Media.Example/watch?v=public-001#player",
            ydl_factory=RecordingYoutubeDL,
        )

        instance = RecordingYoutubeDL.instances[0]
        self.assertEqual(instance.extract_calls, [(
            "https://media.example/watch?v=public-001", False
        )])
        for key, expected in {
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 15,
            "quiet": True,
            "proxy": "",
        }.items():
            self.assertIs(instance.options.get(key), expected) if isinstance(expected, bool) else self.assertEqual(instance.options.get(key), expected)
        forbidden = {"cookiefile", "cookies", "cookiesfrombrowser"}
        self.assertTrue(forbidden.isdisjoint(instance.options))
        self.assertEqual(result["title"], "Synthetic Public Media")

    def test_normalizes_to_a_small_deterministic_allowlisted_media_model(self):
        result = analyze_url(
            "https://media.example/watch?v=public-001",
            ydl_factory=RecordingYoutubeDL,
        )
        expected_id = hashlib.sha256(
            b"https://media.example/watch?v=public-001\nsynthetic\npublic-001"
        ).hexdigest()[:24]

        self.assertEqual(result, {
            "media_id": expected_id,
            "title": "Synthetic Public Media",
            "provider": "synthetic",
            "duration": 123,
            "thumbnail": "https://cdn.example/thumbnail.jpg",
            "subtitles": ["en", "es"],
            "is_playlist": False,
            "playlist_count": 0,
            "live_status": "not_live",
            "available_presets": ["best", "1080p", "720p", "audio"],
        })
        encoded = json.dumps(result, separators=(",", ":")).encode()
        self.assertLess(len(encoded), MAX_MESSAGE_BYTES)
        serialized = json.dumps(result)
        for secret in (
            "formats", "http_headers", "cookies", "description", "comments",
            "webpage", "NOISY", "Authorization", "session=secret",
        ):
            self.assertNotIn(secret, serialized)

    def test_bounds_text_subtitles_duration_and_rejects_unsafe_thumbnail(self):
        info = copy.deepcopy(_FIXTURE["media"])
        info.update({
            "title": "T" * 500,
            "extractor": "P" * 100,
            "duration": 10**20,
            "thumbnail": "file:///tmp/private-thumbnail",
            "subtitles": {f"language-{index:03d}-" + "x" * 50: [] for index in range(150)},
        })

        result = normalize_media_info(info)

        self.assertEqual(len(result["title"]), 300)
        self.assertEqual(len(result["provider"]), 80)
        self.assertLessEqual(result["duration"], 3155760000)
        self.assertIsNone(result["thumbnail"])
        self.assertEqual(len(result["subtitles"]), 100)
        self.assertTrue(all(len(language) <= 32 for language in result["subtitles"]))

    def test_playlist_is_reported_without_expanding_entries(self):
        result = normalize_media_info(copy.deepcopy(_FIXTURE["playlist"]))

        self.assertTrue(result["is_playlist"])
        self.assertEqual(result["playlist_count"], 2)
        self.assertEqual(result["available_presets"], [])
        self.assertNotIn("entries", result)
        self.assertNotIn("entry-1", json.dumps(result))

    def test_available_presets_follow_fixed_selector_order(self):
        self.assertEqual(list(PRESET_SELECTORS), ["best", "1080p", "720p", "audio"])
        self.assertEqual(
            available_presets(copy.deepcopy(_FIXTURE["media"]["formats"])),
            ["best", "1080p", "720p", "audio"],
        )
        self.assertEqual(available_presets([]), [])

    def test_maps_extractor_failures_to_stable_codes(self):
        cases = (
            (UnsupportedError("unsupported site"), "unsupported_url"),
            (DRMError("This media is DRM protected"), "drm_protected"),
            (TimeoutError("timed out"), "timeout"),
            (OSError("network is unreachable"), "network_error"),
        )
        for exception, expected_code in cases:
            with self.subTest(exception=exception):
                RecordingYoutubeDL.exception = exception
                with self.assertRaises(AnalysisError) as raised:
                    analyze_url("https://media.example/video", RecordingYoutubeDL)
                self.assertEqual(raised.exception.code, expected_code)

    def test_allows_public_dns_resolution_inside_analysis(self):
        RecordingYoutubeDL.resolve_host = "media.example"
        original = socket.getaddrinfo
        public_answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with mock.patch("socket.getaddrinfo", return_value=public_answer) as resolver:
            analyze_url("https://media.example/video", RecordingYoutubeDL)
        resolver.assert_called_once()
        self.assertIs(socket.getaddrinfo, original)

    def test_blocks_private_loopback_and_metadata_dns_answers(self):
        RecordingYoutubeDL.resolve_host = "media.example"
        for address in ("10.0.0.8", "127.0.0.1", "169.254.169.254", "::1"):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            answer = [(family, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), mock.patch(
                "socket.getaddrinfo", return_value=answer
            ):
                with self.assertRaises(AnalysisError) as raised:
                    analyze_url("https://media.example/video", RecordingYoutubeDL)
                self.assertEqual(raised.exception.code, "unsafe_network_target")

    def test_rejects_non_http_redirect_attempt(self):
        RecordingYoutubeDL.redirect_url = "file:///tmp/not-allowed"
        with self.assertRaises(AnalysisError) as raised:
            analyze_url("https://media.example/video", RecordingYoutubeDL)
        self.assertEqual(raised.exception.code, "invalid_url")
        self.assertEqual(RecordingYoutubeDL.instances[0].urlopen_calls, [])

    def test_restores_dns_and_proxy_environment_after_guard_exits(self):
        original_resolver = socket.getaddrinfo
        proxy_values = {
            "HTTP_PROXY": "http://proxy.example:8080",
            "https_proxy": "http://proxy.example:8443",
            "NO_PROXY": "localhost",
        }
        seen = {}

        class EnvironmentYoutubeDL(RecordingYoutubeDL):
            def extract_info(self, url, download=False):
                seen.update({key: os.environ.get(key) for key in proxy_values})
                return super().extract_info(url, download)

        with mock.patch.dict(os.environ, proxy_values, clear=False):
            analyze_url("https://media.example/video", EnvironmentYoutubeDL)
            self.assertEqual(
                {key: os.environ.get(key) for key in proxy_values}, proxy_values
            )
        self.assertEqual(seen, {key: None for key in proxy_values})
        self.assertIs(socket.getaddrinfo, original_resolver)

    def test_normalize_url_remains_dns_free(self):
        from native_host.validation import normalize_http_url

        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("DNS called")):
            self.assertEqual(
                normalize_http_url("https://media.example/video"),
                "https://media.example/video",
            )


class WorkerEventModeTests(unittest.TestCase):
    def test_emits_phase_then_completed_for_one_analyze_line(self):
        input_stream = io.StringIO(
            '{"operation":"analyze","url":"https://media.example/video"}\n'
        )
        output_stream = io.StringIO()
        result = {"media_id": "abc", "title": "Synthetic"}

        with mock.patch("native_host.worker.analyze_url", return_value=result) as analyze:
            exit_code = run_worker(input_stream, output_stream)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [json.loads(line) for line in output_stream.getvalue().splitlines()],
            [
                {"type": "phase", "phase": "analyzing"},
                {"type": "completed", "result": result},
            ],
        )
        analyze.assert_called_once_with("https://media.example/video")

    def test_emits_stable_failure_and_nonzero_exit_for_invalid_url(self):
        output_stream = io.StringIO()

        exit_code = run_worker(
            io.StringIO('{"operation":"analyze","url":"file:///tmp/no"}\n'),
            output_stream,
        )

        self.assertEqual(exit_code, 1)
        event = json.loads(output_stream.getvalue())
        self.assertEqual(event["type"], "failed")
        self.assertEqual(event["error"]["code"], "invalid_url")

    def test_rejects_more_than_one_input_line(self):
        output_stream = io.StringIO()
        exit_code = run_worker(
            io.StringIO(
                '{"operation":"analyze","url":"https://media.example/one"}\n'
                '{"operation":"analyze","url":"https://media.example/two"}\n'
            ),
            output_stream,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            json.loads(output_stream.getvalue())["error"]["code"], "invalid_job"
        )


if __name__ == "__main__":
    unittest.main()
