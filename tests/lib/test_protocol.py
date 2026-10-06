"""Tests for framing."""

from __future__ import annotations

import pytest

from froeling_s3100.protocol import Frame, FrameParser, bcd, checksum, decode_text, encode_frame


def test_login_frame_matches_radiator() -> None:
    """The service login from Radiator is 52 61 03 00 FF F9 + sum."""
    frame = encode_frame(b"Ra", b"\x00\xff\xf9")
    assert frame[:6] == bytes.fromhex("526103 00fff9")
    assert int.from_bytes(frame[6:], "big") == checksum(frame[:6])


def test_roundtrip_and_split_delivery() -> None:
    data = encode_frame(b"M2", bytes(7)) + encode_frame(b"MZ", b"A")
    parser = FrameParser()
    frames: list[Frame] = []
    for byte in data:
        frames += parser.feed(bytes([byte]))
    assert frames == [Frame("M2", bytes(7)), Frame("MZ", b"A")]
    assert parser.pending == 0


def test_resync_after_garbage() -> None:
    parser = FrameParser()
    frames = parser.feed(b"\x13\x37\xff" + encode_frame(b"MZ", b"B"))
    assert frames == [Frame("MZ", b"B")]
    assert parser.discarded_bytes == 3


def test_corrupted_checksum_is_dropped() -> None:
    bad = bytearray(encode_frame(b"M2", bytes(7)))
    bad[-1] ^= 0xFF
    parser = FrameParser()
    assert parser.feed(bytes(bad) + encode_frame(b"MZ", b"C")) == [Frame("MZ", b"C")]


def test_ack_detection() -> None:
    assert Frame("Ra", b"\x01").is_ack
    assert Frame("RI", b"\x00").is_nack
    assert not Frame("MZ", b"A").is_ack


def test_payload_limit() -> None:
    with pytest.raises(ValueError):
        encode_frame(b"M1", bytes(256))


def test_text_and_bcd() -> None:
    assert decode_text(b"Au\xe1entemp\nfehlerhaft ") == "Außentemp fehlerhaft"
    assert bcd(0x59) == 59
