import unittest

from native_host.constants import (
    ALLOWED_PRESETS,
    MAX_MESSAGE_BYTES,
    MAX_URL_LENGTH,
    PROTOCOL_VERSION,
)
from native_host.validation import (
    make_error,
    make_response,
    normalize_http_url,
    validate_command,
    validate_event,
    validate_request_id,
)


def command(command_type, payload, request_id="req_1", version=PROTOCOL_VERSION):
    return {
        "version": version,
        "id": request_id,
        "type": command_type,
        "payload": payload,
    }


class CommandValidationTests(unittest.TestCase):
    def test_accepts_all_six_exact_command_shapes(self):
        messages = [
            command("health", {}),
            command("analyze", {"url": "HTTPS://Example.COM/watch?v=1#player"}),
            command("choose_directory", {}),
            command(
                "download",
                {"media_id": "media_abc", "preset": "1080p", "confirmed": True},
                request_id="req_download_1",
            ),
            command("cancel", {"job_id": "job_abc"}),
            command("status", {}),
        ]

        validated = [validate_command(message) for message in messages]

        self.assertEqual(validated[1]["payload"]["url"], "https://example.com/watch?v=1")
        self.assertEqual(validated[3], messages[3])

    def test_rejects_unknown_and_extra_top_level_fields(self):
        unknown = command("erase_everything", {})
        extra = {**command("health", {}), "extra": True}
        for message in (unknown, extra):
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    validate_command(message)

    def test_rejects_extra_or_missing_payload_fields(self):
        messages = [
            command("health", {"extra": True}),
            command("analyze", {"url": "https://example.com", "cookies": "secret"}),
            command("analyze", {}),
            command("choose_directory", {"path": "/tmp"}),
            command("cancel", {"job_id": "job_abc", "signal": 9}),
            command("status", {"verbose": True}),
        ]
        for message in messages:
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    validate_command(message)

    def test_rejects_protocol_versions_other_than_integer_one(self):
        for version in (0, 2, "1", True, None):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    validate_command(command("health", {}, version=version))

    def test_rejects_request_ids_outside_pattern(self):
        invalid_ids = (None, 1, "", "has space", "slash/value", "a" * 65)
        for request_id in invalid_ids:
            with self.subTest(request_id=request_id):
                with self.assertRaises(ValueError):
                    validate_command(command("health", {}, request_id=request_id))

    def test_rejects_unsafe_urls(self):
        urls = (
            "ftp://example.com/video",
            "file:///tmp/video",
            "javascript:alert(1)",
            "https:///missing-host",
            "https://user@example.com/video",
            "https://user:pass@example.com/video",
            "https://example.com/" + ("x" * MAX_URL_LENGTH),
            "https://example.com/a path",
            "https://example.com/a\\path",
            "https://example.com/<script>",
            "https://example.com/%zz",
        )
        for url in urls:
            with self.subTest(url=url[:80]):
                with self.assertRaises(ValueError):
                    validate_command(command("analyze", {"url": url}))

    def test_normalizes_url_without_resolving_path_or_query(self):
        url = "HTTP://EXAMPLE.COM/A/../Video?Token=A%2FB&x=1#fragment"
        self.assertEqual(
            normalize_http_url(url),
            "http://example.com/A/../Video?Token=A%2FB&x=1",
        )

    def test_rejects_local_and_non_global_network_targets(self):
        blocked = (
            "http://localhost/video",
            "http://media.localhost/video",
            "http://10.0.0.1/video",
            "http://172.16.1.2/video",
            "http://192.168.1.2/video",
            "http://127.23.4.5/video",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/video",
            "http://[fe80::1]/video",
            "http://[fc00::1]/video",
            "http://0.0.0.0/video",
            "http://224.0.0.1/video",
            "http://localhost./video",
            "http://127.1/video",
            "http://2130706433/video",
            "http://0x7f000001/video",
            "http://127.000.000.001/video",
            "http://127.0x0.0.1/video",
            "http://127.0.0x0.1/video",
            "http://127.0.0.0x1/video",
            "http://１２７.０.０.１/video",
            "http://127。0。0。1/video",
            "http://ＬＯＣＡＬＨＯＳＴ/video",
            "http://localhost。/video",
            "http://%31%32%37.0.0.1/video",
            "http://%6cocalhost/video",
            "http://127%2e0%2e0%2e1/video",
        )
        for url in blocked:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    normalize_http_url(url)

    def test_accepts_public_dns_and_ip_targets(self):
        self.assertEqual(
            normalize_http_url("HTTPS://VIDEO.EXAMPLE.COM/watch?q=1#part"),
            "https://video.example.com/watch?q=1",
        )
        self.assertEqual(
            normalize_http_url("https://public-media.example/video"),
            "https://public-media.example/video",
        )
        self.assertEqual(
            normalize_http_url("HTTPS://BÜCHER.Example./Video?q=1#part"),
            "https://xn--bcher-kva.example/Video?q=1",
        )
        self.assertEqual(normalize_http_url("http://8.8.8.8/video"), "http://8.8.8.8/video")
        self.assertEqual(
            normalize_http_url("https://[2606:4700:4700::1111]/video"),
            "https://[2606:4700:4700::1111]/video",
        )

    def test_rejects_invalid_idna_hostname(self):
        with self.assertRaises(ValueError):
            normalize_http_url("https://" + ("a" * 64) + ".example/video")

    def test_rejects_dangerous_download_fields(self):
        base = {"media_id": "media_abc", "preset": "best", "confirmed": True}
        for field in ("path", "format", "args", "output_template", "cookies"):
            with self.subTest(field=field):
                payload = {**base, field: "attacker-controlled"}
                with self.assertRaises(ValueError):
                    validate_command(command("download", payload))

    def test_accepts_only_four_preset_enums(self):
        for preset in ALLOWED_PRESETS:
            with self.subTest(preset=preset):
                message = command(
                    "download",
                    {"media_id": "media_abc", "preset": preset, "confirmed": True},
                )
                self.assertEqual(validate_command(message), message)
        for preset in ("4k", "mp3", "BEST", "", None, 1080):
            with self.subTest(preset=preset):
                with self.assertRaises(ValueError):
                    validate_command(
                        command(
                            "download",
                            {"media_id": "media_abc", "preset": preset, "confirmed": True},
                        )
                    )

    def test_download_requires_media_reference_and_allows_optional_confirmation(self):
        without_confirmation = command(
            "download", {"media_id": "media_abc", "preset": "720p"}
        )
        self.assertEqual(validate_command(without_confirmation), without_confirmation)

        invalid_payloads = (
            {"preset": "720p", "confirmed": True},
            {"media_id": "bad media", "preset": "720p", "confirmed": True},
            {"media_id": "media_abc", "preset": "720p", "confirmed": "yes"},
            {"media_id": "media_abc", "preset": "720p", "path": "/tmp"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_command(command("download", payload))

    def test_cancel_requires_a_safe_job_id(self):
        for payload in ({}, {"job_id": "bad job"}, {"job_id": 12}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_command(command("cancel", payload))

    def test_rejects_non_object_message_and_payload(self):
        for message in (None, [], "message", command("health", [])):
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    validate_command(message)


class EnvelopeValidationTests(unittest.TestCase):
    def event(self, event_type, payload, event_id="event_1"):
        return {
            "version": 1,
            "id": event_id,
            "type": event_type,
            "payload": payload,
        }

    def test_validates_request_id(self):
        self.assertEqual(validate_request_id("A_b-9"), "A_b-9")
        with self.assertRaises(ValueError):
            validate_request_id("not valid")

    def test_builds_response_and_error_envelopes(self):
        success = {
            "version": 1,
            "id": "req_1",
            "type": "response",
            "payload": {"ok": True, "data": {"healthy": True}},
        }
        failure = {
            "version": 1,
            "id": "req_1",
            "type": "response",
            "payload": {
                "ok": False,
                "error": {"code": "invalid_request", "message": "Request rejected"},
            },
        }
        self.assertEqual(make_response("req_1", {"healthy": True}), success)
        self.assertEqual(
            make_error("req_1", "invalid_request", "Request rejected"), failure
        )
        self.assertEqual(validate_event(success), success)
        self.assertEqual(validate_event(failure), failure)

    def test_accepts_all_exact_event_payload_schemas(self):
        events = (
            self.event(
                "progress",
                {
                    "job_id": "job_1",
                    "phase": "downloading",
                    "percent": 42.5,
                    "speed": 1_024.0,
                    "eta": 12,
                },
            ),
            self.event("completed", {"job_id": "job_1", "result": {"file": "video.mp4"}}),
            self.event(
                "failed",
                {
                    "job_id": "job_1",
                    "error": {"code": "network_error", "message": "Download failed"},
                },
            ),
            self.event("cancelled", {"job_id": "job_1"}),
        )
        for event in events:
            with self.subTest(event_type=event["type"]):
                self.assertEqual(validate_event(event), event)

    def test_rejects_unknown_or_invalid_outer_event_envelopes(self):
        event = self.event("cancelled", {"job_id": "job_1"})
        invalid_events = (
            {**event, "type": "debug"},
            {**event, "version": 2},
            {**event, "payload": []},
            {**event, "extra": True},
            {**event, "id": "bad id"},
        )
        for invalid in invalid_events:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_event(invalid)

    def test_rejects_extra_missing_and_wrong_event_payload_fields(self):
        invalid_events = (
            self.event("response", {"ok": True, "data": {}, "extra": True}),
            self.event("response", {"ok": False, "error": {"code": "bad"}}),
            self.event("response", {"ok": "yes", "data": {}}),
            self.event("progress", {"phase": "downloading"}),
            self.event("progress", {"job_id": "job_1", "phase": "downloading", "x": 1}),
            self.event("progress", {"job_id": "bad id", "phase": "downloading"}),
            self.event("progress", {"job_id": "job_1", "phase": 1}),
            self.event("progress", {"job_id": "job_1", "phase": "downloading", "percent": 101}),
            self.event("progress", {"job_id": "job_1", "phase": "downloading", "speed": -1}),
            self.event("completed", {"job_id": "job_1"}),
            self.event("completed", {"job_id": "job_1", "result": []}),
            self.event("failed", {"job_id": "job_1", "error": "failure"}),
            self.event(
                "failed",
                {"job_id": "job_1", "error": {"code": "bad", "message": "x", "trace": "secret"}},
            ),
            self.event("cancelled", {"job_id": "job_1", "result": {}}),
        )
        for event in invalid_events:
            with self.subTest(event=event):
                with self.assertRaises(ValueError):
                    validate_event(event)

    def test_rejects_nonfinite_non_json_deep_or_oversized_event_data(self):
        deep = {}
        current = deep
        for _ in range(20):
            current["nested"] = {}
            current = current["nested"]
        invalid_data = (
            {"number": float("nan")},
            {"number": float("inf")},
            {"number": 10**400},
            {"tuple": (1, 2)},
            {1: "non-string key"},
            deep,
            {"large": "x" * (MAX_MESSAGE_BYTES + 1)},
        )
        for data in invalid_data:
            with self.subTest(data_type=type(data)):
                with self.assertRaises(ValueError):
                    validate_event(
                        self.event("response", {"ok": True, "data": data})
                    )

    def test_rejects_nonfinite_progress_numbers(self):
        for field in ("percent", "speed", "eta"):
            for value in (
                float("nan"),
                float("inf"),
                float("-inf"),
                True,
                "1",
                10**400,
            ):
                with self.subTest(field=field, value=value):
                    payload = {"job_id": "job_1", "phase": "downloading", field: value}
                    with self.assertRaises(ValueError):
                        validate_event(self.event("progress", payload))

    def test_response_builders_validate_arguments(self):
        invalid_calls = (
            lambda: make_response("bad id", {}),
            lambda: make_response("req_1", []),
            lambda: make_response("req_1", {"bad": float("nan")}),
            lambda: make_error("req_1", "bad code!", "message"),
            lambda: make_error("req_1", "bad_code", 12),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()


if __name__ == "__main__":
    unittest.main()
