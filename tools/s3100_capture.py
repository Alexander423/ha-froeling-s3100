"""Read-only capture tool for the Fröling Lambdatronic S3100 serial protocol.

Connects to a transparent TCP/serial bridge, logs in, ACKs every frame and
records all traffic. It only ever sends Ra (login), Rb (start M1) and ACKs.
It never sends RI (parameter write).

Frame: 2 ASCII cmd bytes, 1 length byte, payload, 16-bit sum (big endian).
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from collections import Counter
from pathlib import Path

USER_CUSTOMER = b"\x00\x00\x01"
USER_SERVICE = b"\x00\xff\xf9"


def checksum(data: bytes) -> int:
    return sum(data) & 0xFFFF


def build_frame(cmd: bytes, payload: bytes) -> bytes:
    body = cmd + bytes([len(payload)]) + payload
    return body + checksum(body).to_bytes(2, "big")


def text(payload: bytes) -> str:
    return payload.decode("cp850", errors="replace").replace("\n", " | ")


class Capture:
    def __init__(self, host: str, port: int, out_dir: Path, service: bool) -> None:
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.5)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        self.raw = open(out_dir / f"capture-{stamp}.bin", "wb")
        self.log = open(out_dir / f"capture-{stamp}.jsonl", "w", encoding="utf-8")
        self.buf = b""
        self.t0 = time.monotonic()
        self.counts: Counter[str] = Counter()
        self.bad_checksums = 0
        self.login = USER_SERVICE if service else USER_CUSTOMER

    def record(self, direction: str, frame: bytes, note: str = "") -> None:
        cmd = frame[:2].decode("latin-1")
        payload = frame[3:-2]
        entry = {
            "t": round(time.monotonic() - self.t0, 3),
            "dir": direction,
            "cmd": cmd,
            "len": frame[2],
            "payload": payload.hex(),
        }
        if note:
            entry["note"] = note
        self.log.write(json.dumps(entry) + "\n")
        self.log.flush()

    def send(self, cmd: bytes, payload: bytes, note: str = "") -> None:
        frame = build_frame(cmd, payload)
        self.sock.sendall(frame)
        self.record("tx", frame, note)

    def read_frames(self) -> list[bytes]:
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout:
            return []
        if not chunk:
            raise ConnectionError("bridge closed the connection")
        self.raw.write(chunk)
        self.raw.flush()
        self.buf += chunk
        frames = []
        while len(self.buf) >= 5:
            total = 5 + self.buf[2]
            if len(self.buf) < total:
                break
            frame = self.buf[:total]
            expected = checksum(frame[:-2])
            if int.from_bytes(frame[-2:], "big") != expected:
                self.bad_checksums += 1
                self.log.write(json.dumps({"t": round(time.monotonic() - self.t0, 3),
                                           "dir": "rx", "error": "checksum",
                                           "bytes": self.buf[:16].hex()}) + "\n")
                self.buf = self.buf[1:]  # resync by sliding one byte
                continue
            self.buf = self.buf[total:]
            frames.append(frame)
        return frames

    def run(self, duration: float, m1_target: int) -> None:
        self.send(b"Ra", self.login, "login")
        rb_sent = False
        last_rx = time.monotonic()
        while time.monotonic() - self.t0 < duration:
            frames = self.read_frames()
            if not frames and time.monotonic() - last_rx > 5:
                print("no data for 5 s, resending login")
                self.send(b"Ra", self.login, "login retry")
                last_rx = time.monotonic()
            for frame in frames:
                last_rx = time.monotonic()
                cmd = frame[:2]
                payload = frame[3:-2]
                is_ack = len(payload) == 1 and payload[0] in (0x00, 0x01)
                self.record("rx", frame, "ack" if is_ack else "")
                name = cmd.decode("latin-1")
                self.counts[name + (" ack" if is_ack else "")] += 1
                if is_ack:
                    print(f"{name} {'ACK' if payload[0] else 'NACK'}")
                    continue
                self.send(cmd, b"\x01", "ack")
                self.describe(name, payload)
                if name == "M2" and not rb_sent:
                    self.send(b"Rb", b"\x00\x00\x00", "start M1")
                    rb_sent = True
            if self.counts["M1"] >= m1_target:
                break

    def describe(self, name: str, p: bytes) -> None:
        if name == "MZ":
            print(f"-- end of block M{chr(p[0])}")
        elif name == "M2" and len(p) == 7:
            print(f"M2 time 20{p[6]:02x}-{p[4]:02x}-{p[3]:02x} {p[2]:02x}:{p[1]:02x}:{p[0]:02x}")
        elif name == "M1":
            print(f"M1 {len(p) // 2} values")
        elif name in ("MA",) and len(p) > 5:
            print(f"MA {chr(p[0])} idx={int.from_bytes(p[1:3], 'big')} {text(p[5:]).strip()}")
        elif name in ("MB", "MF", "MT", "MD", "ML", "MM", "MW"):
            print(f"{name} {p.hex()[:24]}  {text(p).strip()}")
        else:
            print(f"{name} len={len(p)} {p.hex()}")

    def close(self) -> None:
        self.sock.close()
        self.raw.close()
        self.log.close()
        print("frame counts:", dict(sorted(self.counts.items())))
        print("checksum errors:", self.bad_checksums)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=4196)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--m1", type=int, default=5, help="stop after this many M1 frames")
    parser.add_argument("--service", action="store_true", help="log in as service (-7) instead of customer")
    parser.add_argument("--out", type=Path, default=Path("captures"))
    args = parser.parse_args()
    cap = Capture(args.host, args.port, args.out, args.service)
    try:
        cap.run(args.duration, args.m1)
    finally:
        cap.close()


if __name__ == "__main__":
    main()
