import tempfile
import unittest
from pathlib import Path

from native_host.redaction import redact_mapping, redact_text, redact_url


class UrlRedactionTests(unittest.TestCase):
    def test_removes_credentials_query_values_and_fragment(self):
        self.assertEqual(
            redact_url("https://user:pass@example.com/watch?v=secret#token"),
            "https://example.com/watch?<redacted>",
        )

    def test_fragment_without_query_is_redacted(self):
        self.assertEqual(
            redact_url("https://example.com/watch#private"),
            "https://example.com/watch#<redacted>",
        )

    def test_plain_url_remains_useful(self):
        self.assertEqual(
            redact_url("https://video.example/public/watch"),
            "https://video.example/public/watch",
        )


class TextRedactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.home = Path(self.temporary_directory.name) / "home"
        self.home.mkdir()

    def test_authorization_and_cookie_header_values_are_redacted(self):
        text = "Authorization: Bearer secret-token\nCookie: session=private; id=42"
        redacted = redact_text(text, home=self.home)
        self.assertEqual(
            redacted,
            "Authorization: <redacted>\nCookie: <redacted>",
        )

    def test_home_paths_are_replaced_without_disclosing_filename(self):
        private_path = self.home / "Downloads" / "private-video.mp4"
        text = f"failed to move {private_path} during completion"
        self.assertEqual(
            redact_text(text, home=self.home),
            "failed to move ~/<redacted> during completion",
        )

    def test_embedded_urls_are_redacted(self):
        text = "request https://user:pass@example.com/watch?token=secret#part failed"
        self.assertEqual(
            redact_text(text, home=self.home),
            "request https://example.com/watch?<redacted> failed",
        )

    def test_ordinary_error_codes_and_extractor_names_remain_useful(self):
        text = "network_error from PublicVideoExtractor"
        self.assertEqual(redact_text(text, home=self.home), text)


class MappingRedactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.home = Path(self.temporary_directory.name) / "home"
        self.home.mkdir()

    def test_sensitive_mapping_values_are_redacted_by_key(self):
        source = {
            "authorization": "Bearer secret",
            "Cookie": "session=private",
            "set-cookie": "id=secret",
            "destination": "/tmp/output/private-video.mp4",
            "filename": "private-video.mp4",
            "traceback": "Traceback: secret internals",
            "url": "https://user:pass@example.com/watch?v=secret#token",
        }
        self.assertEqual(
            redact_mapping(source, home=self.home),
            {
                "authorization": "<redacted>",
                "Cookie": "<redacted>",
                "set-cookie": "<redacted>",
                "destination": "<redacted>",
                "filename": "<redacted>",
                "traceback": "<redacted>",
                "url": "https://example.com/watch?<redacted>",
            },
        )

    def test_useful_scalar_context_is_preserved(self):
        source = {
            "extractor": "PublicVideoExtractor",
            "error_code": "network_error",
            "attempt": 2,
            "retryable": True,
            "detail": None,
        }
        self.assertEqual(redact_mapping(source, home=self.home), source)

    def test_recursive_dictionaries_lists_and_strings_are_bounded(self):
        nested = {"secret": "deep"}
        for _ in range(20):
            nested = {"nested": nested}
        source = {
            "mapping": {f"key_{index}": index for index in range(100)},
            "items": list(range(100)),
            "long": "x" * 10_000,
            "nested": nested,
        }

        redacted = redact_mapping(source, home=self.home)

        self.assertLessEqual(len(redacted["mapping"]), 33)
        self.assertLessEqual(len(redacted["items"]), 33)
        self.assertLessEqual(len(redacted["long"]), 520)
        self.assertIn("<truncated>", repr(redacted))
        self.assertNotIn("'deep'", repr(redacted))

    def test_nested_headers_are_redacted(self):
        source = {
            "headers": {
                "Authorization": "Basic dXNlcjpwYXNz",
                "cookie": "session=private",
                "User-Agent": "PageScraperPro/1.0",
            }
        }
        redacted = redact_mapping(source, home=self.home)
        self.assertEqual(redacted["headers"]["Authorization"], "<redacted>")
        self.assertEqual(redacted["headers"]["cookie"], "<redacted>")
        self.assertEqual(redacted["headers"]["User-Agent"], "PageScraperPro/1.0")


if __name__ == "__main__":
    unittest.main()
