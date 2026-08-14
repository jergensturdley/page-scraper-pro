"""Bounded JSON framing for Chrome Native Messaging."""

import json
import struct
from typing import BinaryIO

from .constants import MAX_MESSAGE_BYTES

_MAX_JSON_NESTING = 100


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


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_excessive_nesting(value: object) -> None:
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict):
            if depth > _MAX_JSON_NESTING:
                raise ProtocolError("JSON nesting exceeds maximum depth")
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, (list, tuple)):
            if depth > _MAX_JSON_NESTING:
                raise ProtocolError("JSON nesting exceeds maximum depth")
            stack.extend((child, depth + 1) for child in current)


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
        message = json.loads(decoded, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("invalid JSON payload") from exc
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    _reject_excessive_nesting(message)
    return message


def _write_all(stream: BinaryIO, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = stream.write(data[offset:])
        remaining = len(data) - offset
        if type(written) is not int or written <= 0 or written > remaining:
            raise ProtocolError("stream returned an invalid write count")
        offset += written


def write_message(stream: BinaryIO, message: object) -> None:
    """Encode and flush one bounded Native Messaging JSON object."""
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    _reject_excessive_nesting(message)
    try:
        payload = json.dumps(
            message,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError("message is not JSON serializable") from exc
    if not payload:
        raise ProtocolError("frame length must be positive")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise FrameTooLarge("frame exceeds maximum size")
    _write_all(stream, struct.pack("<I", len(payload)) + payload)
    stream.flush()
