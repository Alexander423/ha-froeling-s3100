"""Low-level framing of the Fröling Lambdatronic S3100 serial protocol.

Every frame is::

    cmd[2]  len[1]  payload[len]  checksum[2]

``cmd`` is two ASCII characters (``M?`` from the controller, ``R?`` from the
host), ``checksum`` is the 16-bit sum of all preceding bytes, big endian.
There are no sync bytes and no escaping. A frame whose payload is the single
byte ``0x01`` (``0x00``) acknowledges (rejects) the frame with the same
command.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_PAYLOAD = 255
HEADER_LEN = 3
CHECKSUM_LEN = 2

ACK = b"\x01"
NACK = b"\x00"

# Host -> controller commands.
CMD_LOGIN = b"Ra"
CMD_START = b"Rb"
CMD_WRITE = b"RI"

LOGIN_CUSTOMER = b"\x00\x00\x01"
LOGIN_SERVICE = b"\x00\xff\xf9"
START_PAYLOAD = b"\x00\x00\x00"

TEXT_ENCODING = "cp850"


def checksum(data: bytes) -> int:
    """Return the 16-bit additive checksum of ``data``."""
    return sum(data) & 0xFFFF


@dataclass(frozen=True, slots=True)
class Frame:
    """A single protocol frame."""

    command: str
    payload: bytes

    @property
    def is_ack(self) -> bool:
        """Return True if this frame acknowledges a frame we sent."""
        return self.payload == ACK

    @property
    def is_nack(self) -> bool:
        """Return True if this frame rejects a frame we sent."""
        return self.payload == NACK

    def encode(self) -> bytes:
        """Serialize the frame including checksum."""
        return encode_frame(self.command.encode("ascii"), self.payload)


def encode_frame(command: bytes, payload: bytes) -> bytes:
    """Build the wire representation of a frame."""
    if len(command) != 2:
        raise ValueError("command must be exactly two bytes")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    body = command + bytes([len(payload)]) + payload
    return body + checksum(body).to_bytes(2, "big")


class FrameParser:
    """Incremental parser turning a byte stream into frames.

    On a checksum mismatch the parser drops one byte and retries, which lets
    it resynchronise on a stream that was joined mid-frame.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.checksum_errors = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        """Add received bytes and return all complete frames."""
        self._buffer += data
        frames: list[Frame] = []
        buf = self._buffer
        while buf:
            if not _is_command_prefix(buf):
                # Cannot be the start of a frame; skip without waiting for a
                # possibly bogus length to fill up.
                self.discarded_bytes += 1
                del buf[0]
                continue
            if len(buf) < HEADER_LEN + CHECKSUM_LEN:
                break
            total = HEADER_LEN + buf[2] + CHECKSUM_LEN
            if len(buf) < total:
                break
            body = bytes(buf[: total - CHECKSUM_LEN])
            received = int.from_bytes(buf[total - CHECKSUM_LEN : total], "big")
            if received != checksum(body) or not _is_command(body[:2]):
                self.checksum_errors += 1
                self.discarded_bytes += 1
                del buf[0]
                continue
            del buf[:total]
            frames.append(Frame(body[:2].decode("ascii"), body[HEADER_LEN:]))
        return frames

    def reset(self) -> None:
        """Drop any partially received data."""
        self._buffer.clear()

    @property
    def pending(self) -> int:
        """Number of buffered bytes not yet forming a frame."""
        return len(self._buffer)


def _is_command(command: bytes) -> bool:
    return command[0] in (0x4D, 0x52) and 0x21 <= command[1] <= 0x7E


def _is_command_prefix(buf: bytearray) -> bool:
    """Return False if ``buf`` cannot start with a frame."""
    if buf[0] not in (0x4D, 0x52):  # 'M' or 'R'
        return False
    return len(buf) < 2 or 0x21 <= buf[1] <= 0x7E


def decode_text(data: bytes) -> str:
    """Decode a controller text (CP850, ``\\n`` separates display lines)."""
    return " ".join(data.decode(TEXT_ENCODING, errors="replace").split())


def u16(data: bytes, offset: int) -> int:
    """Read an unsigned big-endian 16-bit integer."""
    return int.from_bytes(data[offset : offset + 2], "big")


def s16(data: bytes, offset: int) -> int:
    """Read a signed big-endian 16-bit integer."""
    return int.from_bytes(data[offset : offset + 2], "big", signed=True)


def bcd(value: int) -> int:
    """Convert a packed BCD byte to an integer."""
    return (value >> 4) * 10 + (value & 0x0F)
