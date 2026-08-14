import unittest

from native_host.constants import ALLOWED_PRESETS, MAX_URL_LENGTH, PROTOCOL_VERSION
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
    def test_validates_request_id(self):
        self.assertEqual(validate_request_id("A_b-9"), "A_b-9")
        with self.assertRaises(ValueError):
            validate_request_id("not valid")

    def test_builds_response_and_error_envelopes(self):
        self.assertEqual(
            make_response("req_1", {"healthy": True}),
            {
                "version": 1,
                "id": "req_1",
                "type": "response",
                "payload": {"healthy": True},
            },
        )
        self.assertEqual(
            make_error("req_1", "invalid_request", "Request rejected"),
            {
                "version": 1,
                "id": "req_1",
                "type": "response",
                "payload": {
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "Request rejected"},
                },
            },
        )

    def test_validates_event_envelope_and_rejects_unknown_shapes(self):
        event = {
            "version": 1,
            "id": "job_1",
            "type": "progress",
            "payload": {"phase": "downloading"},
        }
        self.assertEqual(validate_event(event), event)
        invalid_events = (
            {**event, "type": "debug"},
            {**event, "version": 2},
            {**event, "payload": []},
            {**event, "extra": True},
        )
        for invalid in invalid_events:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_event(invalid)

    def test_response_builders_validate_arguments(self):
        invalid_calls = (
            lambda: make_response("bad id", {}),
            lambda: make_response("req_1", []),
            lambda: make_error("req_1", "bad code!", "message"),
            lambda: make_error("req_1", "bad_code", 12),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()


if __name__ == "__main__":
    unittest.main()
