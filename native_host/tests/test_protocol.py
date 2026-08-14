import io
import json
import struct
import unittest

from native_host.constants import MAX_MESSAGE_BYTES
from native_host.protocol import (
    EndOfStream,
    FrameTooLarge,
    ProtocolError,
    read_message,
    write_message,
)


class FragmentedBytesIO(io.BytesIO):
    def read(self, size=-1):
        if size < 0:
            size = 1
        return super().read(min(size, 1))


class FlushTrackingBytesIO(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.was_flushed = False

    def flush(self):
        self.was_flushed = True
        super().flush()


class ProtocolTests(unittest.TestCase):
    def test_round_trip_message(self):
        message = {"version": 1, "id": "req_1", "type": "health", "payload": {}}
        wire = io.BytesIO()
        write_message(wire, message)
        wire.seek(0)
        self.assertEqual(read_message(wire), message)

    def test_reads_fragmented_frame(self):
        message = {"version": 1, "id": "req_fragment", "type": "status", "payload": {}}
        payload = json.dumps(message).encode("utf-8")
        wire = FragmentedBytesIO(struct.pack("<I", len(payload)) + payload)
        self.assertEqual(read_message(wire), message)

    def test_reads_two_consecutive_frames(self):
        first = {"version": 1, "id": "req_1", "type": "health", "payload": {}}
        second = {"version": 1, "id": "req_2", "type": "status", "payload": {}}
        wire = io.BytesIO()
        write_message(wire, first)
        write_message(wire, second)
        wire.seek(0)
        self.assertEqual(read_message(wire), first)
        self.assertEqual(read_message(wire), second)

    def test_rejects_invalid_utf8(self):
        wire = io.BytesIO(struct.pack("<I", 1) + b"\xff")
        with self.assertRaises(ProtocolError):
            read_message(wire)

    def test_rejects_malformed_json(self):
        payload = b"{not json}"
        wire = io.BytesIO(struct.pack("<I", len(payload)) + payload)
        with self.assertRaises(ProtocolError):
            read_message(wire)

    def test_empty_input_is_end_of_stream(self):
        with self.assertRaises(EndOfStream):
            read_message(io.BytesIO())

    def test_rejects_zero_length_frame(self):
        with self.assertRaises(ProtocolError):
            read_message(io.BytesIO(struct.pack("<I", 0)))

    def test_rejects_partial_header(self):
        with self.assertRaises(ProtocolError):
            read_message(io.BytesIO(b"\x01\x00"))

    def test_rejects_truncated_payload(self):
        wire = io.BytesIO(struct.pack("<I", 5) + b"{}")
        with self.assertRaises(ProtocolError):
            read_message(wire)

    def test_rejects_declared_oversized_frame_without_reading_payload(self):
        wire = io.BytesIO(struct.pack("<I", MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(FrameTooLarge):
            read_message(wire)

    def test_rejects_non_object_json(self):
        payload = b"[]"
        wire = io.BytesIO(struct.pack("<I", len(payload)) + payload)
        with self.assertRaises(ProtocolError):
            read_message(wire)

    def test_write_rejects_oversized_message(self):
        message = {"data": "x" * MAX_MESSAGE_BYTES}
        with self.assertRaises(FrameTooLarge):
            write_message(io.BytesIO(), message)

    def test_write_rejects_non_object_and_flushes_valid_frame(self):
        with self.assertRaises(ProtocolError):
            write_message(io.BytesIO(), ["not", "an", "object"])
        wire = FlushTrackingBytesIO()
        write_message(wire, {"ok": True})
        self.assertTrue(wire.was_flushed)


if __name__ == "__main__":
    unittest.main()
