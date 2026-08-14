"""Bounded JSON framing for Chrome Native Messaging."""

import json
import struct
from typing import BinaryIO

from .constants import MAX_MESSAGE_BYTES


class ProtocolError(Exception):
    """Raised when a Native Messaging frame is invalid."""


class FrameTooLarge(ProtocolError):
    """Raised when a frame exceeds the configured message limit."""


class EndOfStream(Exception):
    """Raised when a new frame cannot begin because the stream ended."""


def read_exact(stream: BinaryIO, size: int) -> bytes:
    """Read exactly *size* bytes or reject a truncated stream."""
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise ProtocolError("truncated frame")
        chunks.extend(chunk)
    return bytes(chunks)


def read_message(stream: BinaryIO) -> dict[str, object]:
    """Read and decode one bounded Native Messaging JSON object."""
    first_header_byte = stream.read(1)
    if not first_header_byte:
        raise EndOfStream("end of stream")
    header = first_header_byte + read_exact(stream, 3)
    (length,) = struct.unpack("<I", header)
    if length == 0:
        raise ProtocolError("frame length must be positive")
    if length > MAX_MESSAGE_BYTES:
        raise FrameTooLarge("frame exceeds maximum size")

    payload = read_exact(stream, length)
    try:
        decoded = payload.decode("utf-8", errors="strict")
        message = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON payload") from exc
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    return message


def write_message(stream: BinaryIO, message: object) -> None:
    """Encode and flush one bounded Native Messaging JSON object."""
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    try:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("message is not JSON serializable") from exc
    if not payload:
        raise ProtocolError("frame length must be positive")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise FrameTooLarge("frame exceeds maximum size")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()
