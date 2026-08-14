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

    def test_inline_authorization_and_cookie_values_are_redacted(self):
        text = (
            "request failed; Authorization: Bearer secret-token\n"
            "retry with Cookie: session=private"
        )
        self.assertEqual(
            redact_text(text, home=self.home),
            "request failed; Authorization: <redacted>\n"
            "retry with Cookie: <redacted>",
        )

    def test_absolute_paths_outside_home_are_redacted(self):
        text = "failed to move /Volumes/M.2 2TB/output/private-video.mp4"
        self.assertEqual(
            redact_text(text, home=self.home),
            "failed to move <redacted>",
        )

    def test_exact_api_and_auth_token_headers_are_redacted_inline(self):
        for header in ("X-Api-Key", "Api-Key", "X-Auth-Token"):
            with self.subTest(header=header):
                text = f"request {header}: do-not-log with network_error"
                redacted = redact_text(text, home=self.home)
                self.assertEqual(redacted, f"request {header}: <redacted>")
                self.assertNotIn("do-not-log", redacted)

    def test_filesystem_schemes_and_roots_preserve_following_context(self):
        values = (
            "copy ~/Downloads/My Video.mp4 with code network_error",
            "copy file:/Volumes/Media Disk/My Video.mp4 with code network_error",
            "copy //Volumes/Media Disk/My Video.mp4 with code network_error",
            "copy /Volumes/Media Disk/My Video.mp4 with code network_error",
        )
        expected_prefixes = (
            "copy ~/<redacted>",
            "copy file:<redacted>",
            "copy <redacted>",
            "copy <redacted>",
        )
        for value, prefix in zip(values, expected_prefixes):
            with self.subTest(value=value):
                redacted = redact_text(value, home=self.home)
                self.assertEqual(redacted, prefix + " with code network_error")
                self.assertNotIn("My Video.mp4", redacted)

    def test_arithmetic_slashes_are_not_treated_as_paths(self):
        text = "retry 1 / 2 after network_error"
        self.assertEqual(redact_text(text, home=self.home), text)

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

    def test_sensitive_key_variants_are_redacted_conservatively(self):
        source = {
            "destination_filename": "private-video.mp4",
            "accessToken": "secret-token",
            "client_secret": "secret-value",
            "databasePassword": "secret-password",
            "credentialValue": "secret-credential",
            "outputFile": "/Volumes/private.mp4",
            "resolved_path": "/tmp/private",
        }
        self.assertEqual(
            redact_mapping(source, home=self.home),
            {key: "<redacted>" for key in source},
        )

    def test_sensitive_key_components_redact_without_substring_false_positives(self):
        sensitive = {
            "apiKey": "leak-api",
            "private_key": "leak-private",
            "sessionId": "leak-session",
            "auth_token": "leak-token",
            "user_passwd": "leak-passwd",
            "destinationFilename": "leak-filename",
            "resolved path": "leak-path",
            "output_file": "leak-output",
            "filePath": "leak-filepath",
            "file": "leak-file",
        }
        benign = {
            "profile": "PublicProfile",
            "tokenizer": "standard",
            "secretary": "operator",
            "pathology": "none",
        }
        redacted = redact_mapping({**sensitive, **benign}, home=self.home)
        for key in sensitive:
            self.assertEqual(redacted[key], "<redacted>")
        for key, value in benign.items():
            self.assertEqual(redacted[key], value)
        for leak in sensitive.values():
            self.assertNotIn(leak, repr(redacted))

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
